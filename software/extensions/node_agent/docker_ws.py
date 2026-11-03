# -*- coding: utf-8 -*-
"""
云 Docker 沙箱操作（node_agent 侧白名单实现）

安全约束（改动前请先读完）：
  1. 工作区名必须匹配 ^ws_[0-9a-f]{8,32}$，其余一律拒绝 —— 杜绝路径穿越与误伤他人物。
  2. 全程 subprocess 列表参数调用，绝不使用 shell=True / 字符串拼接，杜绝命令注入。
  3. 只操作本插件自己创建的容器前缀 zernus_ws_，不碰宿主机上任何其它容器（含旧 zcbot_ws_*）。
  4. 资源限额集中在 LIMITS，不接受调用方传入覆盖值。CPU / 进程数为硬限；
     内存为「弹性」：256MB 保底、上限在 [256MB, mem_ceil_mb] 之间由调节器动态调整；
     磁盘为「4 GiB 池 + 每工作区 1 GiB 切片」——两者都是真实文件系统大小，硬限。
  5. 删除只删该名字对应的容器与其数据目录，不做任何批量/通配操作。
  6. exec 仅在容器内以 argv 列表执行（docker exec <c> sh -c <cmd>），宿主端不走 shell；
     调用方需是已验签的管理员（控制台侧 is_admin 门禁），本层只负责把命令安全送进容器。
"""
import logging
import os
import re
import subprocess
import threading
import time

PREFIX = 'zernus_ws_'
NAME_RE = re.compile(r'^ws_[0-9a-f]{8,32}$')
DATA_ROOT = '/data/zernus_ws'

log = logging.getLogger('node_agent.docker_ws')

# 资源限额：
#   CPU / 进程数是硬限；**内存为弹性**——mem_floor_mb 是保底（内核在这个线以上不做回收，
#   即官网公示的 256 MB 下限），mem_ceil_mb 是单容器硬顶 **1.5 GB**：到了就不再扩展。
#   真正生效的 memory.max 由下方调节器在 [floor, ceil] 区间按实际占用与宿主余量动态调整：
#   吃紧就往上放，空闲一会儿再收回到底线附近。回收分三层——
#     ① 内核层：memory.high = 上限的 90%，超过即回收可回收页（不等宿主紧张）；
#     ② 调节器：宿主余量告急 → 按占用从高到低收上限，硬顶之上不可能存在；
#     ③ 空闲回收：连续 4 轮低于上限 40% → 收回「占用×3」或底线。
#   撞到硬顶 1.5G 仍不够用时会 OOM 杀容器内进程，伤的是沙箱自己，宿主与控制面不受影响。
#   突发不必等采样：新建容器直接给 INIT 上限。
LIMITS = {
    # 镜像：自建精简版（原 katorlys/workspace-all 8.72GB → 约 1GB）。
    # 砍掉的是沙箱场景里「不现实或重复」的：Rust 1.86G、pyenv 重复 Python 1.1G、
    # SDKMAN JVM 全家桶 853M、容器内 docker CLI。见 deploy/zc-workspace.Dockerfile。
    'image': os.environ.get('ZERNUS_WS_IMAGE', 'zc/workspace:latest'),
    'cpu': '1',
    'mem_mb': 256,                                          # 保底下限（对外公示口径）
    'mem_floor_mb': 256,
    'mem_ceil_mb': int(os.environ.get('ZERNUS_WS_MEM_MAX_MB', '1536')),   # 单容器硬顶 1.5G
    'pids': 256,
    'uid': 10000,
    'data_dir': '/home/dev/work',
    'disk_mb': 1024,                                        # 单工作区固定 1 GiB
}

MAX_RUNNING = 5
MAX_TOTAL = 20

# ── 磁盘：4 GiB 池 + 每工作区 1 GiB 切片 ────────────────────
# 单工作区的盘是「一个 1 GiB 的独立小文件系统」（切片），平台共享一个 4 GiB 的池。
# 两者都是 loop 上的 ext4 —— 单工作区写满 → 它自己的文件系统报 ENOSPC，别人不受
# 影响；池写满 → 所有切片一起写不进去。都是内核级硬限，不是纸面值。
#
# 为什么不用 ext4 project quota：本机内核 5.15.0-30-generic 的模块目录里没有
# kernel/fs/quota/（quota_v2 / quota_tree 缺失，linux-modules-extra 在 apt 源里
# 已无安装候选），带 quota feature 的 ext4 一挂就报 "Failed to enable quota
# tracking" 并 mount failed。所以改用「各自独立小文件系统」实现单工作区硬限，
# 不依赖任何额外内核模块。
#
# 池由 scripts/zpool_setup.sh 建好（fallocate 4G + mkfs.ext4 + mount -o loop），
# 并在 /etc/fstab 里 nofail 持久化；切片由本插件按需创建/挂载，并在启动时自愈。
POOL_IMG = os.environ.get('ZERNUS_WS_POOL_IMG', '/data/zernus_pool.img')
POOL_MNT = DATA_ROOT                                    # /data/zernus_ws（池挂载点）
SLICE_DIR = '.slices'                                   # 池内的切片目录
SLICE_MB = int(os.environ.get('ZERNUS_WS_SLICE_MB', '1024'))     # 1 GiB / 工作区
POOL_MB = int(os.environ.get('ZERNUS_WS_POOL_MB', '4096'))       # 4 GiB / 平台
WS_BACKUP = '/data/zernus_ws_backup'                    # 删除时归档到池外（宿主盘）

# ── 内存弹性调节器参数 ────────────────────────────────────
GOV_INTERVAL = 15        # 采样周期（秒）
GOV_GROW_PCT = 70        # 占用超过当前上限的 70% → 往上放
GOV_SHRINK_PCT = 40      # 低于当前上限的 40% → 记账
GOV_SHRINK_TICKS = 4     # 连续 N 个周期都低 → 才回收（避免抖动）
GOV_MIN_STEP_MB = 128    # 单次放量最小步长
GOV_HARDEN_TICKS = 4     # 每 N 个周期刷一次 oom_score_adj（60s）
MEM_INIT_MB = 512        # 新建/接管容器时的初始可弹上限
# 宿主保留量：弹性放量永远不许吃掉这部分。取「总内存 25%，且不少于 320MB、不超过 2GB」——
# 小机器（1.9G）留 ~495MB 够 sshd/dockerd/agent 喘气，大机器最多留 2G，其余都能借。
RESERVE_MIN_MB = 320
RESERVE_PCT = 0.25
RESERVE_CAP_MB = 2048
# 容器进程的 OOM 优先级：正数越大越先被杀。宿主真到极限时优先杀沙箱进程，
# 而不是 sshd / dockerd / zernus-agent（否则整台机的控制面被一起带走，谁都进不去）。
OOM_SCORE_ADJ = 500


def _cname(ws: str) -> str:
    return PREFIX + ws


def _run(args, timeout=120):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()
    except subprocess.TimeoutExpired:
        return -1, '', f'超时 {timeout}s'
    except Exception as e:
        return -1, '', f'{type(e).__name__}: {e}'


def _state(ws: str) -> str:
    code, out, _ = _run(['docker', 'inspect', '-f', '{{.State.Status}}', _cname(ws)], timeout=20)
    return out if code == 0 and out else 'missing'


def _running_count(exclude: str = '') -> int:
    code, out, _ = _run(
        ['docker', 'ps', '--format', '{{.Names}}\t{{.State}}'], timeout=20)
    if code != 0:
        return 0
    n = 0
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != 2:
            continue
        name, state = parts
        if not name.startswith(PREFIX) or state != 'running':
            continue
        if exclude and name == PREFIX + exclude:
            continue
        n += 1
    return n


def _total_count() -> int:
    code, out, _ = _run(
        ['docker', 'ps', '-a', '--format', '{{.Names}}'], timeout=20)
    if code != 0:
        return 0
    return sum(1 for n in out.splitlines() if n.startswith(PREFIX))


# ── 磁盘切片 ─────────────────────────────────────────────
_slice_lock = threading.Lock()


def _mountpoint(path: str) -> bool:
    """该路径是否是一个挂载点（读 /proc/self/mountinfo，比 os.path.ismount 可靠）。"""
    try:
        with open('/proc/self/mountinfo', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and parts[4] == path:
                    return True
    except Exception:
        pass
    return os.path.ismount(path)


def _pool_mounted() -> bool:
    """池是否真的挂着。没挂上 = 工作区会退化成宿主盘上的普通目录且没有配额，必须显式告警。"""
    return _mountpoint(POOL_MNT)


def _df_mb(path: str):
    """(总量MB, 已用MB, 可用MB)，失败返回 (0, 0, 0)。"""
    try:
        st = os.statvfs(path)
        mb = 1024 * 1024
        total = st.f_blocks * st.f_frsize // mb
        free = st.f_bavail * st.f_frsize // mb
        return total, total - free, free
    except Exception:
        return 0, 0, 0


def _slice_img(ws: str) -> str:
    return os.path.join(POOL_MNT, SLICE_DIR, ws + '.img')


def _slice_mnt(ws: str) -> str:
    return os.path.join(POOL_MNT, ws)


def _ensure_slice(ws: str) -> str:
    """确保工作区切片存在且已挂载，返回容器该 bind 的宿主路径。

    切片 = 池里一个 1 GiB 的稀疏 ext4 文件，挂到 /data/zernus_ws/<ws>；容器里
    看到的就是一块 1 GiB 的独立盘（df 显示 974M 是因为 ext4 自身元数据）。
    幂等：已挂载直接返回，已有镜像只补挂载。
    """
    mnt, img = _slice_mnt(ws), _slice_img(ws)
    if not _pool_mounted():
        log.error('[ws-disk] 池未挂载（%s）：%s 会退化为宿主盘普通目录、无配额保护',
                  POOL_MNT, ws)
        os.makedirs(mnt, exist_ok=True)
        return mnt
    with _slice_lock:
        if _mountpoint(mnt):
            return mnt
        try:
            os.makedirs(os.path.join(POOL_MNT, SLICE_DIR), exist_ok=True)
        except Exception:
            log.exception('[ws-disk] 建切片目录失败')
            os.makedirs(mnt, exist_ok=True)
            return mnt
        if not os.path.exists(img):
            code, _, err = _run(['truncate', '-s', f'{SLICE_MB}M', img], timeout=60)
            if code != 0:
                log.error('[ws-disk] 创建切片失败 %s: %s', ws, err)
                os.makedirs(mnt, exist_ok=True)
                return mnt
            code, _, err = _run(['mkfs.ext4', '-m', '0', '-F', '-q', img], timeout=180)
            if code != 0:
                log.error('[ws-disk] 格式化切片失败 %s: %s', ws, err)
                os.makedirs(mnt, exist_ok=True)
                return mnt
            log.info('[ws-disk] 新建切片 %s（%sMB）', ws, SLICE_MB)
        os.makedirs(mnt, exist_ok=True)
        # ⚠️ 这里**只能写 `loop,noatime`**：`discard=async` 是 XFS 的挂载选项，
        # ext4 不认。实测内核 5.15.0-30（Ubuntu 22.04）上 dmesg 直接报
        #     EXT4-fs (loopN): Unrecognized mount option "discard=async" or missing value
        # mount 返回 "wrong fs type, bad option, bad superblock"，于是切片挂不上、
        # 容器 start 时 bind 到池上的空目录 —— 既没有配额，数据还会落到池里。
        # 空间回收改由 _trim_slices() 的周期 fstrim 负责（比同步 discard 更好：
        # 一次 FITRIM ioctl 批量回收，而不是每个删除动作都同步打洞）。
        code, _, err = _run(['mount', '-o', 'loop,noatime', img, mnt], timeout=120)
        if code != 0:
            log.error('[ws-disk] 挂载切片失败 %s: %s', ws, err)
        elif not _mountpoint(mnt):
            # 双保险：mount 返回 0 但实际没挂上（见上），必须喊出来
            log.error('[ws-disk] 切片挂载后仍未挂上 %s（%s）—— 容器会 bind 到池目录，'
                      '磁盘配额将失效', ws, mnt)
    return mnt


def _trim_slices():
    """把所有切片的空闲块还给池。

    切片是稀疏 loop 文件：往里删文件只在切片自己的文件系统里标记空闲，img 的实占
    不会自动缩（内核不会自动 punch hole）。实测「写满 1G → 删掉」之后 img 仍实占
    991M、池跟着少 991M；跑一次 fstrim 立刻回到 51M。所以必须定期回收。

    ⚠️ `fstrim` **不要加 `-q`**：util-linux 2.37（Ubuntu 22.04 自带）根本没有 -q
    （那是 2.39 才加的，2.37 里叫 `--quiet-unsupported`），会直接
    `fstrim: invalid option -- 'q'` 退出。加上早期这里不看返回码，就变成
    「周期回收静默失效」——池只涨不落，还不报错，极难查。所以：
    不带任何参数（各版本都认）+ **必须检查返回码**，失败就打日志。
    """
    if not _pool_mounted():
        return
    d = os.path.join(POOL_MNT, SLICE_DIR)
    try:
        names = [f[:-4] for f in os.listdir(d) if f.endswith('.img')]
    except Exception:
        return
    for ws in names:
        mnt = _slice_mnt(ws)
        if NAME_RE.match(ws) and _mountpoint(mnt):
            code, _, err = _run(['fstrim', mnt], timeout=180)
            if code != 0:
                log.warning('[ws-disk] fstrim 失败 %s（rc=%s）：%s', mnt, code, err)


def _pool_free_mb() -> int:
    return _df_mb(POOL_MNT)[2] if _pool_mounted() else 0


def _heal_slices():
    """自愈：把池里所有切片重新挂上。

    宿主重启后 fstab 会挂回池，但切片是动态挂载、fstab 里没有 —— 不补挂的话
    容器一 start 就会 bind 到空目录（看起来像「数据没了」）。agent 启动时与
    调节器每轮都跑一次，成本极低（只是查挂载表）。
    """
    if not _pool_mounted():
        return
    d = os.path.join(POOL_MNT, SLICE_DIR)
    try:
        names = [f[:-4] for f in os.listdir(d) if f.endswith('.img')]
    except Exception:
        return
    for ws in names:
        if NAME_RE.match(ws) and not _mountpoint(_slice_mnt(ws)):
            _ensure_slice(ws)
            log.info('[ws-disk] 自愈挂载切片 %s', ws)


def _drop_slice(ws: str) -> dict:
    """删除工作区切片：先把数据归档到池外（宿主盘），再卸载删文件，池空间立即回收。

    原来是把目录改名成 .deleted 软删 —— 但切片只有 1G、池只有 4G，原地保留会白占
    额度；改成压缩归档到 WS_BACKUP（池外），数据仍可找回，池空间立刻回池。
    """
    mnt, img = _slice_mnt(ws), _slice_img(ws)
    out = {'archived': '', 'removed': False}
    if not os.path.exists(img) and not _mountpoint(mnt):
        return out
    if _mountpoint(mnt):
        try:
            os.makedirs(WS_BACKUP, exist_ok=True)
            dst = os.path.join(WS_BACKUP, f'{ws}-{time.strftime("%Y%m%d%H%M%S")}.tar.gz')
            code, _, err = _run(['tar', '-czf', dst, '-C', mnt, '.'], timeout=900)
            if code == 0 and os.path.exists(dst):
                out['archived'] = dst
                log.info('[ws-disk] %s 数据已归档 %s', ws, dst)
            else:
                log.warning('[ws-disk] 归档失败 %s: %s（照样回收切片，数据不保留）', ws, err)
        except Exception:
            log.exception('[ws-disk] 归档异常 %s', ws)
        code, _, err = _run(['umount', mnt], timeout=120)
        if code != 0:
            code, _, err = _run(['umount', '-l', mnt], timeout=120)   # 懒卸载兜底
        if code != 0:
            log.error('[ws-disk] 卸载切片失败 %s: %s（放弃回收）', ws, err)
            return out
    try:
        os.rmdir(mnt)
    except Exception:
        pass
    if os.path.exists(img):
        try:
            os.remove(img)
            out['removed'] = True
        except Exception:
            log.exception('[ws-disk] 删除切片文件失败 %s', ws)
    return out


def _ensure_dir(ws: str):
    """确保工作区目录（= 切片挂载点）就绪并归 uid 10000，返回容器 bind 的宿主路径。"""
    path = _ensure_slice(ws)
    subprocess.run(['chown', '-R', f'{LIMITS["uid"]}:{LIMITS["uid"]}', path], timeout=60)
    return path


# ── 内存弹性调节器 ───────────────────────────────────────
_gov_lock = threading.Lock()
_gov_started = False
_gov_state = {}          # cname -> {'limit': 当前上限MB, 'low': 连续低占用计数}


def _meminfo(field: str) -> int:
    """读 /proc/meminfo 单个字段，返回 MB；读不到返回 0。"""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith(field + ':'):
                    return int(line.split()[1]) // 1024          # kB → MB
    except Exception:
        pass
    return 0


def _host_avail_mb() -> int:
    """宿主可用内存（MemAvailable，含可回收缓存），读不到返回 0 = 不放量。"""
    return _meminfo('MemAvailable')


def _host_reserve_mb() -> int:
    """宿主保留量：总内存的 25%，下限 320MB、上限 2GB。弹性放量不许吃掉这部分。"""
    total = _meminfo('MemTotal')
    if total <= 0:
        return RESERVE_MIN_MB
    return max(RESERVE_MIN_MB, min(int(total * RESERVE_PCT), RESERVE_CAP_MB))


_UNIT_MB = {'B': 1 / 1048576, 'KIB': 1 / 1024, 'MIB': 1.0, 'GIB': 1024.0, 'TIB': 1048576.0}


def _parse_mb(tok: str) -> float:
    """把 docker stats 的 '430.2MiB' 解析成 MB。"""
    m = re.match(r'^\s*([0-9.]+)\s*([A-Za-z]+)\s*$', tok or '')
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _UNIT_MB.get(m.group(2).upper(), 1.0)
    except ValueError:
        return 0.0


def _round64(mb) -> int:
    return max(64, (int(mb) // 64) * 64)


def _running_ws() -> list:
    code, out, _ = _run(
        ['docker', 'ps', '--filter', f'name={PREFIX}', '--format', '{{.Names}}'], timeout=20)
    if code != 0:
        return []
    return [n for n in out.splitlines() if n.startswith(PREFIX)]


def _cname_limit_mb(cname: str) -> int:
    """读容器当前 memory.max（MB）。"""
    code, out, _ = _run(['docker', 'inspect', '-f', '{{.HostConfig.Memory}}', cname], timeout=20)
    try:
        return int(out) // (1024 * 1024) if code == 0 else 0
    except (TypeError, ValueError):
        return 0


def _cgroup_dir(cname: str) -> str:
    """容器自己的 cgroup v2 目录（从 PID 反查，兼容 systemd / cgroupfs 两种 driver）。"""
    code, pid, _ = _run(['docker', 'inspect', '-f', '{{.State.Pid}}', cname], timeout=20)
    if code != 0 or not pid.isdigit():
        return ''
    try:
        with open(f'/proc/{pid}/cgroup', 'r') as f:
            for line in f:
                parts = line.strip().split(':', 2)
                if len(parts) == 3 and parts[0] == '0':
                    return os.path.join('/sys/fs/cgroup', parts[2].lstrip('/'))
    except Exception:
        pass
    return ''


def _set_mem_high(cname: str, limit_mb: int) -> bool:
    """写 memory.high = 当前硬顶的 90%（软上限 / 内核级自动回收）。

    memory.max 是硬顶（撞上就 OOM），memory.high 是软顶：一超过它，内核立刻开始
    回收这个容器里可回收的页（页缓存等），而不是等宿主真吃紧才动手 —— 这就是
    「用完自动还」的内核那一半，不必等调节器 15s 一轮的采样。
    """
    return _sync_mem_high(cname, {}, limit_mb)


def _sync_mem_high(cname: str, st: dict, limit_mb: int) -> bool:
    """按当前上限校正 memory.high（幂等：值已对就不写）。

    容器一旦被重启，cgroup 里的 memory.high 会回到默认 max，所以每轮都校正一次；
    cgroup 目录缓存在 st 里（容器重启后目录名不变，仍是 docker-<id>.scope）。
    """
    d = st.get('cgroup') or ''
    if not d or not os.path.isdir(d):
        d = _cgroup_dir(cname)
        if not d:
            return False
        st['cgroup'] = d
    high_mb = max(LIMITS['mem_floor_mb'] + 64, int(int(limit_mb) * 0.9))
    target = str(high_mb * 1024 * 1024)
    p = os.path.join(d, 'memory.high')
    try:
        with open(p, 'r') as f:
            if f.read().strip() == target:
                return True
    except Exception:
        return False
    try:
        with open(p, 'w') as f:
            f.write(target)
        return True
    except Exception:
        return False


def _set_mem_limit(cname: str, limit_mb: int) -> bool:
    """实时改容器内存上限，不动容器进程（cgroup v2 可直接热改）。

    --memory-swap 与 --memory 同步，等于不给 swap（宿主只有 1G swap，禁掉更稳）。
    --memory-reservation 恒为底线：cgroup v2 落到 memory.low，宿主吃紧时内核
    优先回收这一层之上的页，但不会把容器压到 256MB 以下。
    """
    lim = max(LIMITS['mem_floor_mb'], int(limit_mb))
    code, _, _ = _run(['docker', 'update',
                       '--memory', f'{lim}m',
                       '--memory-swap', f'{lim}m',
                       '--memory-reservation', f'{LIMITS["mem_floor_mb"]}m',
                       cname], timeout=60)
    if code == 0:
        _set_mem_high(cname, lim)
    return code == 0


def _harden_oom():
    """把 ws 容器内进程的 oom_score_adj 提到 +500。

    宿主内存真到极限时，内核按 oom_score_adj 挑受害者 —— 不设的话可能先杀
    sshd / dockerd / zernus-agent，整台机的控制面被一起带走，谁都进不去。
    提到 +500 等于「要杀先杀沙箱里的进程」。oom_score_adj 会被 fork 继承，
    所以覆盖 init 与 docker exec 出来的进程就够了。
    """
    for cname in _running_ws():
        pids = set()
        code, pid, _ = _run(['docker', 'inspect', '-f', '{{.State.Pid}}', cname], timeout=20)
        if code == 0 and pid.isdigit():
            pids.add(pid)
        # 注意：本机 docker 的 top 不支持 -o 参数（会报 Couldn't find PID field in ps output），
        # 只能解析默认输出，PID 是第 2 列（UID PID PPID C STIME TTY TIME CMD）。
        code, top, _ = _run(['docker', 'top', cname], timeout=20)
        if code == 0:
            for line in top.splitlines()[1:]:
                t = line.split()
                if len(t) > 1 and t[1].isdigit():
                    pids.add(t[1])
        for pid in pids:
            try:
                with open(f'/proc/{pid}/oom_score_adj', 'w') as f:
                    f.write(str(OOM_SCORE_ADJ))
            except Exception:
                pass


def _gov_tick():
    names = _running_ws()
    if not names:
        return
    code, out, _ = _run(
        ['docker', 'stats', '--no-stream', '--format', '{{.Name}}\t{{.MemUsage}}'] + names,
        timeout=60)
    if code != 0:
        return
    rows = []                                     # [[cname, 已用MB], ...]
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != 2:
            continue
        rows.append([parts[0].strip(), _parse_mb(parts[1].split('/')[0])])
    if not rows:
        return

    # 0) 首次接管：仍压在 256MB 的老容器抬到初始可弹位，否则一次构建就被 OOM。
    #    只做一次（seeded 标记）——否则会和「宿主告急压回底线」来回打架、上限抖动。
    for cname, _u in rows:
        st = _gov_state.setdefault(cname, {})
        st['limit'] = st.get('limit') or _cname_limit_mb(cname) or LIMITS['mem_floor_mb']
        if not st.get('seeded'):
            if st['limit'] < MEM_INIT_MB:
                want = _round64(min(LIMITS['mem_ceil_mb'], MEM_INIT_MB))
                if _set_mem_limit(cname, want):
                    st['limit'] = want
                    log.info('[ws-mem] %s 接管抬升 %sMB', cname, want)
            st['seeded'] = True
        _sync_mem_high(cname, st, st['limit'])      # 重启后 memory.high 会回到 max，每轮校正

    avail = _host_avail_mb()
    reserve = _host_reserve_mb()
    if avail <= 0:
        return                                    # 读不到宿主余量：一律不动，宁可少放
    spare = avail - reserve                       # 可借额度（负数 = 宿主已告急）

    if spare < 0:
        # 1) 宿主告急：按占用从高到低收，直到把缺口抹平（最多收到保底线为止）。
        #    收回上限会让内核立刻回收该容器在保底线以上的页，必要时 OOM 杀容器内进程 ——
        #    伤的是沙箱自己，换来的是 sshd / docker / 控制面活着。
        for cname, usage in sorted(rows, key=lambda r: -r[1]):
            if spare >= 0:
                break
            st = _gov_state[cname]
            cur = st['limit']
            target = max(LIMITS['mem_floor_mb'], _round64(usage + spare))
            if target < cur and _set_mem_limit(cname, target):
                log.warning('[ws-mem] 宿主余量 %sMB 告急（保留 %sMB），%s 上限 %s→%sMB',
                            avail, reserve, cname, cur, target)
                spare += (cur - target)
                st['limit'] = target
                st['low'] = 0
    else:
        # 2) 压力解除后的回弹：因告急被压到初始位以下的，宿主有余量就抬回来 ——
        #    否则它停在 256MB 上，下一次构建会在调节器采样到之前先撞硬顶被 OOM。
        for cname, _u in rows:
            st = _gov_state[cname]
            cur = st['limit']
            if cur < MEM_INIT_MB and spare > 128:
                want = _round64(min(MEM_INIT_MB, cur + spare // 2))
                if want > cur and _set_mem_limit(cname, want):
                    log.info('[ws-mem] 宿主已缓解，%s 回弹 %s→%sMB', cname, cur, want)
                    spare -= (want - cur)
                    st['limit'] = want
        # 3) 有余额：占用超 70% 的容器按需放量。单次最多借走当前余量的一半
        #    （封顶平台上限），避免多个容器同时放量把宿主吃干。
        for cname, usage in sorted(rows, key=lambda r: -r[1]):
            if spare <= 0:
                break
            st = _gov_state[cname]
            cur = st['limit']
            if cur >= LIMITS['mem_ceil_mb'] or usage < cur * GOV_GROW_PCT / 100.0:
                continue
            step = max(GOV_MIN_STEP_MB, spare // 2)
            want = _round64(min(LIMITS['mem_ceil_mb'], max(cur * 2, cur + step)))
            if want > cur and _set_mem_limit(cname, want):
                log.info('[ws-mem] %s 占用 %sMB（%.0f%%）→ 放量至 %sMB（宿主余量 %sMB）',
                         cname, int(usage), usage * 100.0 / cur, want, avail)
                spare -= (want - cur)
                st['limit'] = want
                st['low'] = 0

    # 3) 长期空闲：连续 GOV_SHRINK_TICKS 轮都低于 40% 才收，收到「占用 ×3」为止；
    #    空闲态的下限是初始可弹位（512MB）而非保底 256MB —— 长期不用也不至于一上来就卡，
    #    真要压到 256MB 只由「宿主告急」那条路决定。
    for cname, usage in rows:
        st = _gov_state[cname]
        cur = st['limit']
        if cur <= MEM_INIT_MB or spare < 0:
            st['low'] = 0
            continue
        if usage < cur * GOV_SHRINK_PCT / 100.0:
            st['low'] = st.get('low', 0) + 1
            if st['low'] >= GOV_SHRINK_TICKS:
                want = max(MEM_INIT_MB, _round64(usage * 3))
                if want < cur and _set_mem_limit(cname, want):
                    log.info('[ws-mem] %s 空闲 %sMB → 回收至 %sMB', cname, int(usage), want)
                    st['limit'] = want
                st['low'] = 0
        else:
            st['low'] = 0


def _disk_check():
    """磁盘巡检：池没挂上 / 快满就告警，自愈切片挂载，并把空闲块还给池。"""
    if not _pool_mounted():
        log.error('[ws-disk] 池未挂载（%s）—— 工作区会落到宿主盘且无配额保护，检查 %s 与 fstab',
                  POOL_MNT, POOL_IMG)
        return
    _heal_slices()
    _trim_slices()
    total, used, free = _df_mb(POOL_MNT)
    if total and used * 100 // total >= 85:
        log.warning('[ws-disk] 池将满：%s/%s MB（%d%%），剩余 %s MB',
                    used, total, used * 100 // total, free)


def _gov_loop():
    log.info('[ws-mem] 弹性内存调节器启动：保底 %sMB / 平台上限 %sMB / 周期 %ss',
             LIMITS['mem_floor_mb'], LIMITS['mem_ceil_mb'], GOV_INTERVAL)
    ticks = 0
    while True:
        try:
            with _gov_lock:
                _gov_tick()
                ticks += 1
                if ticks % GOV_HARDEN_TICKS == 1:      # 每 60s 刷一次 OOM 优先级 + 磁盘巡检
                    _harden_oom()
                    _disk_check()
        except Exception:
            log.exception('[ws-mem] 调节器单次采样异常（已忽略，下轮继续）')
        time.sleep(GOV_INTERVAL)


def start_governor() -> bool:
    """幂等启动调节器线程（agent 启动时调一次，handle() 里兜底再调）。"""
    global _gov_started
    with _gov_lock:
        if _gov_started:
            return False
        _gov_started = True
    threading.Thread(target=_gov_loop, name='ws-mem-gov', daemon=True).start()
    # 磁盘自检 + 切片自愈：宿主重启后 fstab 会把池挂回来，但切片是动态挂载，
    # 没人补挂的话容器一 start 就会 bind 到空目录（看起来像数据丢了）。
    if _pool_mounted():
        p_total, p_used, _ = _df_mb(POOL_MNT)
        log.info('[ws-disk] 池就绪 %s（%sMB，已用 %sMB，单工作区切片 %sMB）',
                 POOL_MNT, p_total, p_used, SLICE_MB)
        _heal_slices()
    else:
        log.error('[ws-disk] 池未挂载：%s（镜像 %s）—— 工作区将没有磁盘配额，请检查挂载',
                  POOL_MNT, POOL_IMG)
    return True


def op_state(ws: str, args: dict) -> dict:
    st = _state(ws)
    return {'ok': True, 'data': {'name': ws, 'state': st}}


def _slice_ready(ws: str) -> str:
    """确保切片**真的挂上了**；就绪返回空串，否则返回错误说明。

    为什么非要有这道门：docker create/start 遇到 bind 源目录不存在时会自己建空目录，
    所以「切片没挂上」不会报错，而是静默 bind 到池上的同名空目录 —— 表现是
    容器里 df 显示 3.9G（池）而不是 974M（切片），既没有配额，用户写的数据还落在池里。
    2026-09-21 真的踩到过一次（挂载参数写错导致 mount 失败）。宁可不启动，也不能这样跑。
    """
    if not _pool_mounted():
        return f'磁盘池未挂载（{POOL_MNT}，镜像 {POOL_IMG}）—— 拒绝启动'
    mnt = _ensure_dir(ws)
    if not _mountpoint(mnt):
        return f'切片未挂载（{mnt}）—— 拒绝启动容器，以免绑定到池目录'
    return ''


def op_create(ws: str, args: dict) -> dict:
    if _state(ws) != 'missing':
        return {'ok': False, 'data': '工作区已存在'}
    if _total_count() >= MAX_TOTAL:
        return {'ok': False, 'data': f'平台工作区总数已达上限 {MAX_TOTAL}'}
    notready = _slice_ready(ws)
    if notready:
        return {'ok': False, 'data': notready}
    host_dir = _slice_mnt(ws)
    verb = 'create' if _running_count() >= MAX_RUNNING else 'run'
    # 初始上限：直接给 MEM_INIT_MB（突发不必等调节器下一轮采样），保底预留 256MB
    init_mb = _round64(min(LIMITS['mem_ceil_mb'], MEM_INIT_MB))
    cmd = [
        'docker', verb, '-d', '--name', _cname(ws), '--hostname', ws,
        '--memory', f'{init_mb}m', '--memory-swap', f'{init_mb}m',
        '--memory-reservation', f'{LIMITS["mem_floor_mb"]}m',
        '--cpus', LIMITS['cpu'], '--pids-limit', str(LIMITS['pids']),
        '-v', f'{host_dir}:{LIMITS["data_dir"]}:rw',
        '--entrypoint', '/bin/bash', LIMITS['image'], '-c', 'sleep infinity',
    ]
    code, out, err = _run(cmd, timeout=180)
    if code != 0:
        return {'ok': False, 'data': f'创建失败: {err or out}'}
    # 调节器已知该容器的当前上限（无需加锁：dict 单键赋值在 CPython 下原子）
    _gov_state[_cname(ws)] = {'limit': init_mb, 'low': 0}
    _harden_oom()          # 新容器立刻挂上「宿主 OOM 时优先牺牲」的标记
    return {'ok': True, 'data': {'name': ws, 'state': _state(ws), 'mode': verb,
                                 'mem_limit_mb': init_mb}}


def op_start(ws: str, args: dict) -> dict:
    st = _state(ws)
    if st == 'missing':
        return op_create(ws, args)
    if st == 'running':
        return {'ok': True, 'data': {'name': ws, 'state': 'running'}}
    if _running_count(exclude=ws) >= MAX_RUNNING:
        return {'ok': False, 'data': f'运行中工作区已达上限 {MAX_RUNNING}'}
    # 容器 start 时会重新做 bind mount —— 必须确认切片**真的挂上了**，
    # 否则会静默 bind 到池上的空目录（无配额 + 数据落到池里）
    notready = _slice_ready(ws)
    if notready:
        return {'ok': False, 'data': notready}
    code, out, err = _run(['docker', 'start', _cname(ws)], timeout=120)
    if code != 0:
        return {'ok': False, 'data': f'启动失败: {err or out}'}
    return {'ok': True, 'data': {'name': ws, 'state': _state(ws)}}


def op_stop(ws: str, args: dict) -> dict:
    st = _state(ws)
    if st in ('missing', 'exited'):
        return {'ok': True, 'data': {'name': ws, 'state': st}}
    code, out, err = _run(['docker', 'stop', _cname(ws)], timeout=120)
    if code != 0:
        return {'ok': False, 'data': f'停止失败: {err or out}'}
    return {'ok': True, 'data': {'name': ws, 'state': _state(ws)}}


def op_remove(ws: str, args: dict) -> dict:
    st = _state(ws)
    if st == 'missing' and not os.path.exists(_slice_img(ws)) and not _mountpoint(_slice_mnt(ws)):
        return {'ok': True, 'data': '工作区不存在（无需删除）'}
    if st != 'missing':
        code, _, err = _run(['docker', 'rm', '-f', _cname(ws)], timeout=120)
        if code != 0:
            return {'ok': False, 'data': f'删除容器失败: {err}'}
    # 数据先归档到池外（宿主盘、压缩），再回收切片 —— 池只有 4G，原地软删会白占额度。
    r = _drop_slice(ws)
    return {'ok': True, 'data': {'name': ws, 'state': 'missing',
                                 'archived': r['archived'], 'slice_removed': r['removed']}}


def op_limits(ws: str, args: dict) -> dict:
    cname = _cname(ws)
    lib = 0
    if _state(ws) != 'missing':
        lib = _gov_state.get(cname, {}).get('limit') or _cname_limit_mb(cname)
    avail = _host_avail_mb()
    pool_mounted = _pool_mounted()
    ws_total, ws_used, ws_free = _df_mb(_slice_mnt(ws)) if pool_mounted else (0, 0, 0)
    p_total, p_used, p_free = _df_mb(POOL_MNT) if pool_mounted else (0, 0, 0)
    return {'ok': True, 'data': {
        'cpu': LIMITS['cpu'],
        'mem_mb': LIMITS['mem_floor_mb'],                # 兼容旧字段：保底值
        'mem_floor_mb': LIMITS['mem_floor_mb'],
        'mem_ceil_mb': LIMITS['mem_ceil_mb'],
        'mem_limit_mb': lib or LIMITS['mem_floor_mb'],   # 该工作区此刻实际可用的上限
        'mem_elastic': True,
        'pids': LIMITS['pids'],
        # 磁盘：单工作区一块 1GiB 切片（独立文件系统），平台共享 4GiB 池，都是硬限
        'disk_mb': SLICE_MB,
        'disk_enforced': True,
        'disk_slice_mb': SLICE_MB,
        'disk_used_mb': ws_used,
        'disk_free_mb': ws_free,
        'disk_pool_mb': POOL_MB,
        'disk_pool_total_mb': p_total,
        'disk_pool_used_mb': p_used,
        'disk_pool_free_mb': p_free,
        'disk_pool_mounted': pool_mounted,
        'max_running': MAX_RUNNING, 'max_total': MAX_TOTAL,
        'running': _running_count(), 'total': _total_count(),
        'host_avail_mb': avail,
    }}


def op_disk(ws: str, args: dict) -> dict:
    """磁盘总览：池容量 + 每块切片的用量（控制台/工作台展示用）。"""
    mounted = _pool_mounted()
    p_total, p_used, p_free = _df_mb(POOL_MNT) if mounted else (0, 0, 0)
    rows = []
    d = os.path.join(POOL_MNT, SLICE_DIR)
    try:
        names = sorted(f[:-4] for f in os.listdir(d) if f.endswith('.img'))
    except Exception:
        names = []
    for name in names:
        if not NAME_RE.match(name):
            continue
        mnt = _slice_mnt(name)
        used, free = (0, 0)
        if _mountpoint(mnt):
            _, used, free = _df_mb(mnt)
        rows.append({'ws': name, 'quota_mb': SLICE_MB, 'used_mb': used, 'free_mb': free,
                     'mounted': _mountpoint(mnt)})
    return {'ok': True, 'data': {
        'pool_mounted': mounted, 'pool_mb': POOL_MB,
        'pool_total_mb': p_total, 'pool_used_mb': p_used, 'pool_free_mb': p_free,
        'slice_mb': SLICE_MB, 'slices': rows,
    }}


def op_pty(ws: str, args: dict) -> dict:
    """PTY 流安全网关：仅校验工作区名 + 容器状态，并返回要在容器内启动的命令。

    真正 spawn PTY 的工作由 node_agent 完成（它持有事件循环与泵线程）。
    安全约束同 exec：工作区名必须匹配 NAME_RE，只操作本插件前缀的容器。
    cmd 默认交互式 bash（用户/AI 可在其中手动启动 opencode 等）；
    调用方若传 args.cmd（列表）则以其为容器内启动程序，仍是 argv 列表、无宿主 shell。
    """
    if not NAME_RE.match(ws):
        return {'ok': False, 'data': '非法工作区名'}
    st = _state(ws)
    if st != 'running':
        return {'ok': False, 'data': f'容器未运行（当前状态: {st}），无法开启终端'}
    cmd = args.get('cmd') or ['bash', '-li']
    if not isinstance(cmd, (list, tuple)):
        cmd = ['bash', '-li']

    def _dim(v, lo, hi, dflt):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return dflt
        return n if lo <= n <= hi else dflt

    return {'ok': True, 'data': {
        'cname': _cname(ws), 'cmd': list(cmd), 'workdir': LIMITS['data_dir'],
        'cols': _dim(args.get('cols'), 20, 500, 80),
        'rows': _dim(args.get('rows'), 5, 200, 24),
    }}


def op_exec(ws: str, args: dict) -> dict:
    st = _state(ws)
    if st != 'running':
        return {'ok': False, 'data': f'容器未运行（当前状态: {st}），无法执行命令'}
    cmd = str(args.get('cmd', '') or '').strip()
    if not cmd:
        return {'ok': False, 'data': '命令为空'}
    # 列表参数：宿主端无 shell，cmd 整体作为容器内 sh -c 的参数，杜绝宿主命令注入。
    code, out, err = _run(['docker', 'exec', _cname(ws), 'sh', '-c', cmd], timeout=30)
    cap = 8000

    def _clip(s: str) -> str:
        s = s or ''
        return s if len(s) <= cap else s[:cap] + f'\n…(输出截断，共 {len(s)} 字节)'

    return {'ok': True, 'data': {'code': code, 'stdout': _clip(out), 'stderr': _clip(err)}}


_OPS = {
    'state': op_state,
    'create': op_create,
    'start': op_start,
    'stop': op_stop,
    'remove': op_remove,
    'limits': op_limits,
    'disk': op_disk,
    'exec': op_exec,
}


def handle(args: dict) -> dict:
    """hub 下发 cmd='docker.ws' 时调用；args: {'ws': 名, 'op': 操作}"""
    start_governor()          # 兜底拉起弹性内存调节器（幂等）
    ws = str(args.get('ws', '')).strip()
    op = str(args.get('op', '')).strip()
    if not NAME_RE.match(ws):
        return {'ok': False, 'data': '非法工作区名'}
    fn = _OPS.get(op)
    if fn is None:
        return {'ok': False, 'data': f'未知操作: {op}'}
    try:
        return fn(ws, args)
    except Exception as e:
        return {'ok': False, 'data': f'{type(e).__name__}: {e}'}
