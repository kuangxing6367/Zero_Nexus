# -*- coding: utf-8 -*-
"""
节点代理（官方扩展，多机管理 L2：节点侧）

跑在每个被管节点上：主动外连 hub 的 node_control 控制通道（节点可在 NAT 后），
注册握手（HELLO）→ 周期心跳（HEARTBEAT，状态同 status_panel /health 结构）
→ 接收命令（CMD）→ 白名单执行 → 回执（RESULT）。

传输复用 service/transport/framed.py（整帧 HMAC、防重放、超时）；
密钥为 per-node 预共享密钥，与 hub 侧 node_control.nodes 配置一致。

启用（默认关，仅被管节点开）：
  extensions.yaml → node_agent:
    enabled: true
    hub_host: 10.0.0.1        # hub 地址
    hub_port: 37010
    name: node-1              # 本节点名（须在 hub 的可信清单里）
    secret: change-me-node-1  # 预共享密钥
    interval: 30              # 心跳周期（秒）
    allow_shell: false        # 是否允许 shell 命令（默认禁，安全开关）
    allow_packages: false     # 是否允许 pkg.sync（按 zkg 源同步机制包，默认禁）
    allow_config: false       # 是否允许 config.push / config.reload（配置下发，默认禁）
    allow_upgrade: false      # 是否允许 framework.update / framework.rollback（框架自升级，默认禁）

命令白名单（hub 下发 cmd 字段）：
  ping / health / pkg.list 恒可用；
  shell 需 allow_shell: true（args: {"cmd": "..."}）；
  docker.ws（含容器 exec）与 docker.pty / PTY 流需 allow_docker: true
  —— docker exec / PTY 与 shell 等价（可在容器内获得交互 shell），
  拿到 hub 控制权不等于默认获得节点 shell，必须显式开闸；
  pkg.sync 需 allow_packages: true —— 会按 zkg 源拉取并加载机制包
  （sha256 / HMAC 签名校验仍在 loader 内强制执行），同样是「执行远端代码」
  的入口，因此不与只读的 pkg.list 同级；
  config.status 恒可用（只读）；config.reload / config.push 需 allow_config: true
  —— 写入的是 config.yaml 同目录的托管覆盖层 config.managed.yaml，加载时最后
  合并、优先级最高，不改用户原配置文件；推送带 sha256 强校验，落盘原子替换，
  热重载失败自动回滚。端口 / 数据库连接 / SSL 等启动期绑定项仍需重启才生效；
  framework.status 恒可用（只读）；framework.update / framework.rollback 需
  allow_upgrade: true —— 升级包同样带 sha256 强校验，先暂存并编译检查，再备份
  旧文件后替换，失败自动回滚；升级/回滚后都必须重启进程才加载新代码。

HELLO 载荷携带 capabilities（命令 → 是否启用），hub 可在 snapshot() 中看到；
能力声明只用于展示，实际执行仍由本文件的白名单与安全开关兜底。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

import yaml

try:                        # pty 仅 POSIX；Windows 上无 pty，PTY 功能不可用但其余正常
    import pty
except ImportError:         # pragma: no cover
    pty = None

try:                        # 仅 Linux 需要；导入失败也不影响其它功能
    import fcntl
    import termios
except ImportError:         # pragma: no cover
    fcntl = None
    termios = None

from service.transport.framed import FramedClient, DEFAULT_REPLAY_WINDOW

logger = logging.getLogger('zernus')

# 配置下发（config.push）的硬上限与强校验格式：覆盖层单文件不超过 1MiB，
# sha256 必须是 64 位小写十六进制（防止空校验 / 截断校验）。
_MAX_CONFIG_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
# 框架源码升级包上限（ZIP 原文件，base64 后约为其 4/3）；framed 载荷上限 16MiB。
_MAX_UPDATE_BYTES = 8 * 1024 * 1024

__plugin_meta__ = {
    "name": "节点代理",
    "version": "0.1.5",
    "author": "Zeronus",
    "desc": "多机管理 L2/L3：节点侧代理（心跳+能力上报+命令+PTY+包同步+配置下发+框架升级）",
    "priority": 30,
    "official": True,
}

F_HELLO = 1
F_HEARTBEAT = 2
F_CMD = 3
F_RESULT = 4
F_STREAM_OPEN = 5     # hub→node：开启一个 PTY 流（载荷 {"id","cmd","args"}）
F_STREAM_DATA = 6     # 双向：流式数据块（载荷 {"sid","data":base64}）
F_STREAM_CLOSE = 7    # 双向：关闭流（载荷 {"sid"}）
F_STREAM_RESIZE = 8   # hub→node：调整 PTY 窗口大小（载荷 {"sid","cols","rows"}）


class NodeAgent:
    """节点侧代理：维持到 hub 的长连接，心跳 + 命令执行。"""

    def __init__(self, fw, ctx_log=None):
        self.fw = fw
        self._log = ctx_log or (lambda m: logger.info(m))
        cfg = fw.config.get('node_agent', {}) or {}
        self.name = str(cfg.get('name', ''))
        self.secret = str(cfg.get('secret', '')).encode('utf-8')
        self.hub_host = str(cfg.get('hub_host', '127.0.0.1'))
        self.hub_port = int(cfg.get('hub_port', 37010))
        self.interval = max(5, int(cfg.get('interval', 30)))
        self.allow_shell = bool(cfg.get('allow_shell', False))
        self.allow_docker = bool(cfg.get('allow_docker', False))
        self.allow_packages = bool(cfg.get('allow_packages', False))
        self.allow_config = bool(cfg.get('allow_config', False))
        self.allow_upgrade = bool(cfg.get('allow_upgrade', False))
        self._config_lock = threading.Lock()   # 覆盖层读写互斥（防并发 push 撕裂）
        self._seq = int(time.time()) & 0x7FFFFFFF
        self._loop = None
        self._thread = None
        self._client = None
        self._task = None               # _forever 协程任务（stop 用）
        self._stop_evt = None           # asyncio.Event（_run_loop 内创建）
        self._streams = {}               # sid -> {'master', 'proc', 'ws'}
        self._streams_lock = threading.Lock()
        self._send_lock = None           # asyncio.Lock，_run_loop 内创建

    # ── 状态采集（与 status_panel /health 同构，尽力而为） ──
    def _status(self) -> dict:
        fw = self.fw
        now = time.time()
        uptime = max(0, int(now - getattr(fw, '_start_time', now)))
        mem_mb = None
        try:
            import psutil
            mem_mb = round(psutil.Process().memory_info().rss / 1048576, 1)
        except Exception:
            pass
        return {'ok': True, 'node': self.name, 'version': _fw_version(fw),
                'uptime_seconds': uptime, 'memory_mb': mem_mb,
                'ts': time.strftime('%Y-%m-%d %H:%M:%S')}

    def capabilities(self) -> dict:
        """命令 → 当前是否启用（随 HELLO 上报，hub 只做展示，不替代执行侧校验）。"""
        return {'ping': True, 'health': True, 'pkg.list': True,
                'config.status': True, 'framework.status': True,
                'shell': self.allow_shell,
                'docker.ws': self.allow_docker,
                'docker.pty': self.allow_docker,
                'pkg.sync': self.allow_packages,
                'config.reload': self.allow_config,
                'config.push': self.allow_config,
                'framework.update': self.allow_upgrade,
                'framework.rollback': self.allow_upgrade}

    def _pkg_list(self) -> dict:
        """只读包清单：已加载的 zkg 工具 + 本地 repo 包目录。"""
        loaded = sorted((getattr(self.fw, 'zkg_tools', {}) or {}).keys())
        repo_ids = []
        try:
            from service.zkg.defaults import PROJECT_ROOT
            repo = os.path.join(PROJECT_ROOT, 'repo')
            if os.path.isdir(repo):
                # 只认带入口的包目录，排除 dist / pool / __pycache__ 等仓库基础设施
                repo_ids = sorted(
                    d for d in os.listdir(repo)
                    if os.path.isfile(os.path.join(repo, d, 'main.py')))
        except Exception:
            logger.exception("[node_agent] 读取本地 repo 清单失败")
        return {'ok': True, 'data': {'loaded_tools': loaded,
                                     'repo_packages': repo_ids}}

    # ── 配置下发（托管覆盖层 config.managed.yaml）──────────
    #
    # 设计取舍：
    #   - 只写 config.yaml 同目录的 config.managed.yaml，不碰用户原配置文件；
    #     core.config.load_config 最后合并覆盖层（优先级最高）。
    #   - extensions.yaml 会在 _autoload_extensions 里覆盖 config.yaml 的扩展段，
    #     因此写 config.yaml 无法可靠下发扩展配置，必须走覆盖层。
    #   - 强校验：调用方传 yaml 原文 + sha256（64 位十六进制），节点本地重算比对。
    #   - 写入用「同目录临时文件 + os.replace」原子替换；reload 失败自动回滚。
    def _config_path(self) -> str:
        path = getattr(self.fw, 'config_path', None)
        if path:
            return os.path.abspath(path)
        from core.kernel.paths import project_root
        return os.path.join(project_root(), 'config.yaml')

    def _managed_path(self) -> str:
        from core.config import managed_config_path
        return managed_config_path(self._config_path())

    def _config_status(self) -> dict:
        """只读：覆盖层是否存在、大小、sha256、顶层键；不做任何写入。"""
        path = self._config_path()
        mpath = self._managed_path()
        info = {'allow_config': self.allow_config,
                'config_path': path, 'managed_path': mpath,
                'managed_exists': os.path.isfile(mpath),
                'managed_bytes': 0, 'managed_sha256': None,
                'managed_mtime': None, 'managed_top_keys': [],
                'max_bytes': _MAX_CONFIG_BYTES}
        if info['managed_exists']:
            try:
                size = os.path.getsize(mpath)
                info['managed_bytes'] = size
                info['managed_mtime'] = time.strftime(
                    '%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(mpath)))
                if size > _MAX_CONFIG_BYTES:
                    # 不读取异常大的文件，避免只读查询变成内存放大入口。
                    info['error'] = (f'覆盖层超过上限 {_MAX_CONFIG_BYTES}B，'
                                     f'实际 {size}B')
                    return {'ok': True, 'data': info}
                with open(mpath, 'rb') as f:
                    data = f.read()
                info['managed_sha256'] = hashlib.sha256(data).hexdigest()
                doc = yaml.safe_load(data.decode('utf-8')) or {}
                if isinstance(doc, dict):
                    info['managed_top_keys'] = sorted(str(k) for k in doc)
            except Exception as e:
                info['error'] = f'{type(e).__name__}: {e}'
        return {'ok': True, 'data': info}

    def _config_reload(self) -> dict:
        """受 allow_config 闸门的配置热重载（不写文件，只重读+触发扩展点）。"""
        if not self.allow_config:
            return {'ok': False,
                    'data': '配置热重载被禁用（node_agent.allow_config: false）'}
        try:
            return {'ok': True, 'data': self._do_reload()}
        except Exception as e:
            return {'ok': False,
                    'data': f'配置重载失败: {type(e).__name__}: {e}'}

    def _do_reload(self) -> dict:
        """调用框架热重载入口；框架不支持时如实返回「需重启」。"""
        reload = getattr(self.fw, 'reload_config', None)
        if not callable(reload):
            return {'reloaded': False,
                    'reason': '当前运行时不支持热重载，需重启进程'}
        info = reload()
        return {'reloaded': True, 'detail': info if isinstance(info, dict) else {}}

    @staticmethod
    def _atomic_write(path: str, data: bytes):
        """同目录临时文件 + fsync + os.replace 原子替换（不产生半截文件）。"""
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.config-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _config_push(self, args: dict) -> dict:
        """受 allow_config 闸门的配置下发：校验 → 原子写覆盖层 → 可选重载 → 失败回滚。

        args:
          yaml      (str)   覆盖层 YAML 原文（必填，除非 clear=true）
          sha256    (str)   yaml 原文 UTF-8 字节的 sha256，64 位十六进制（必填，除非 clear）
          dry_run   (bool)  只校验不落盘（默认 false）
          reload    (bool)  写盘后立即热重载（默认 true）
          clear     (bool)  删除覆盖层（恢复用户原配置；可与 reload 组合）
        """
        if not self.allow_config:
            return {'ok': False,
                    'data': '配置下发被禁用（node_agent.allow_config: false）'}

        dry_run = bool(args.get('dry_run', False))
        do_reload = bool(args.get('reload', True))
        clear = bool(args.get('clear', False))
        mpath = self._managed_path()

        if clear:
            if args.get('yaml') is not None:
                return {'ok': False, 'data': 'clear 与 yaml 不能同时使用'}
            if not os.path.isfile(mpath):
                return {'ok': True, 'data': {'cleared': False,
                                             'reason': '覆盖层不存在，无需清理',
                                             'managed_path': mpath}}
            if dry_run:
                return {'ok': True, 'data': {'dry_run': True, 'cleared': True,
                                             'managed_path': mpath}}
            with self._config_lock:
                try:
                    with open(mpath, 'rb') as f:
                        prev = f.read()
                except OSError:
                    prev = None
                try:
                    os.unlink(mpath)
                    reloaded = self._do_reload() if do_reload else None
                except Exception as e:
                    reason = f'清理覆盖层失败: {type(e).__name__}: {e}'
                    if prev is not None:      # 回滚：原覆盖层写回
                        try:
                            self._atomic_write(mpath, prev)
                        except Exception:
                            logger.exception('[node_agent] 覆盖层回滚失败')
                    return {'ok': False, 'data': reason}
            return {'ok': True, 'data': {'cleared': True, 'managed_path': mpath,
                                         'reloaded': reloaded}}

        raw = args.get('yaml')
        if isinstance(raw, (dict, list)):
            return {'ok': False,
                    'data': 'args.yaml 必须是 YAML 原文字符串（不接受已解析对象，'
                            '否则无法用 sha256 绑定传输内容）'}
        if not isinstance(raw, str) or not raw.strip():
            return {'ok': False, 'data': '缺少 args.yaml（覆盖层 YAML 原文）'}

        expect = str(args.get('sha256') or '').strip().lower()
        if not _SHA256_RE.match(expect):
            return {'ok': False,
                    'data': 'args.sha256 必须是 64 位小写十六进制 sha256'}

        data = raw.encode('utf-8')
        if len(data) > _MAX_CONFIG_BYTES:
            return {'ok': False,
                    'data': f'配置过大：{len(data)}B > 上限 {_MAX_CONFIG_BYTES}B'}
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual, expect):
            return {'ok': False,
                    'data': f'sha256 校验失败：期望 {expect}，实际 {actual}'}

        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            return {'ok': False, 'data': f'YAML 解析失败: {e}'}
        if not isinstance(doc, dict) or not doc:
            return {'ok': False,
                    'data': '覆盖层根节点必须是非空映射（dict），不能为空或标量/列表'}

        summary = {'managed_path': mpath, 'sha256': actual, 'bytes': len(data),
                   'top_keys': sorted(str(k) for k in doc)}
        if dry_run:
            return {'ok': True, 'data': dict(summary, dry_run=True,
                                             valid=True, written=False)}

        with self._config_lock:
            prev = None
            existed = os.path.isfile(mpath)
            if existed:
                try:
                    with open(mpath, 'rb') as f:
                        prev = f.read()
                except OSError as e:
                    return {'ok': False, 'data': f'读取现有覆盖层失败: {e}'}
            try:
                self._atomic_write(mpath, data)
                reloaded = self._do_reload() if do_reload else None
            except Exception as e:
                # 写入或重载失败：恢复到推送前的状态，避免节点停在半配置。
                try:
                    if prev is not None:
                        self._atomic_write(mpath, prev)
                    elif os.path.isfile(mpath):
                        os.unlink(mpath)
                    if do_reload:
                        self._do_reload()      # 回滚后重载，恢复旧运行时状态
                except Exception:
                    logger.exception('[node_agent] 配置回滚失败，节点可能处于异常配置')
                return {'ok': False,
                        'data': f'配置下发失败（已回滚）: {type(e).__name__}: {e}'}

        return {'ok': True, 'data': dict(summary, written=True,
                                         reloaded=reloaded)}

    # ── 框架自升级（受 allow_upgrade 闸门）─────────────────
    def _framework_status(self) -> dict:
        """只读：当前版本、可回滚备份、是否开启升级。"""
        from core.kernel.paths import project_root
        from service.updater import list_backups
        root = project_root()
        return {'ok': True, 'data': {
            'allow_upgrade': self.allow_upgrade,
            'version': _fw_version(self.fw),
            'root': root,
            'backups': list_backups(root),
            'max_bytes': _MAX_UPDATE_BYTES,
            'restart_required_after_update': True,
        }}

    def _framework_update(self, args: dict) -> dict:
        """受 allow_upgrade 闸门的框架升级：base64 ZIP + sha256 → 校验/暂存/备份/替换。

        args:
          zip_b64  (str)  框架源码 ZIP 的 base64（必填；上限 _MAX_UPDATE_BYTES）
          sha256   (str)  ZIP 原始字节的 sha256，64 位小写十六进制（必填）
          dry_run  (bool) 只校验/解压/编译，不改动线上文件（默认 false）

        始终要求 sha256，不提供 allow_unsigned：hub 侧必须显式绑定包体。
        升级后需重启进程，当前进程不会热加载新代码。
        """
        if not self.allow_upgrade:
            return {'ok': False,
                    'data': '框架升级被禁用（node_agent.allow_upgrade: false）'}
        from service.updater import UpdateError, apply_source_zip
        from core.kernel.paths import project_root

        raw_b64 = args.get('zip_b64')
        if not isinstance(raw_b64, str) or not raw_b64.strip():
            return {'ok': False, 'data': '缺少 args.zip_b64'}
        expect = str(args.get('sha256') or '').strip().lower()
        if not _SHA256_RE.match(expect):
            return {'ok': False,
                    'data': 'args.sha256 必须是 64 位小写十六进制 sha256'}
        try:
            data = base64.b64decode(raw_b64, validate=True)
        except Exception as e:
            return {'ok': False, 'data': f'base64 解码失败: {type(e).__name__}: {e}'}
        if not data:
            return {'ok': False, 'data': '升级包为空'}
        if len(data) > _MAX_UPDATE_BYTES:
            return {'ok': False,
                    'data': f'升级包过大：{len(data)}B > 上限 {_MAX_UPDATE_BYTES}B'}
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual, expect):
            return {'ok': False,
                    'data': f'sha256 校验失败：期望 {expect}，实际 {actual}'}

        fd, tmp = tempfile.mkstemp(prefix='zernus_fw_upload_', suffix='.zip')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            report = apply_source_zip(
                tmp, project_root(), expected_sha256=expect,
                dry_run=bool(args.get('dry_run')))
            return {'ok': True, 'data': report}
        except UpdateError as e:
            return {'ok': False, 'data': f'框架更新失败: {e}'}
        except Exception as e:
            return {'ok': False,
                    'data': f'框架更新异常: {type(e).__name__}: {e}'}
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _framework_rollback(self, args: dict) -> dict:
        """受 allow_upgrade 闸门的回滚：按备份名（默认最新）恢复旧文件。"""
        if not self.allow_upgrade:
            return {'ok': False,
                    'data': '框架回滚被禁用（node_agent.allow_upgrade: false）'}
        from service.updater import UpdateError, rollback_source_update
        from core.kernel.paths import project_root
        try:
            report = rollback_source_update(
                project_root(), str(args.get('backup') or ''))
            return {'ok': True, 'data': report}
        except UpdateError as e:
            return {'ok': False, 'data': f'框架回滚失败: {e}'}
        except Exception as e:
            return {'ok': False,
                    'data': f'框架回滚异常: {type(e).__name__}: {e}'}

    # ── 命令白名单执行 ────────────────────────────────────
    def _exec(self, cmd: str, args: dict) -> dict:
        if cmd == 'ping':
            return {'ok': True, 'data': 'pong'}
        if cmd == 'health':
            return {'ok': True, 'data': self._status()}
        if cmd == 'pkg.list':
            return self._pkg_list()
        if cmd == 'config.status':
            return self._config_status()
        if cmd == 'config.reload':
            return self._config_reload()
        if cmd == 'config.push':
            return self._config_push(args or {})
        if cmd == 'framework.status':
            return self._framework_status()
        if cmd == 'framework.update':
            return self._framework_update(args or {})
        if cmd == 'framework.rollback':
            return self._framework_rollback(args or {})
        if cmd == 'pkg.sync':
            if not self.allow_packages:
                return {'ok': False,
                        'data': '包同步被禁用（node_agent.allow_packages: false）'}
            try:
                # 与 sys 服务启动时同一套 zkg 流程：源拉取 + sha256/签名校验 + 按需加载。
                from service.startup import run_zkg
                result = run_zkg(self.fw)
                return {'ok': True, 'data': {
                    'loaded_tools': result.get('loaded_tools') or [],
                    'plugin_count': result.get('plugin_count', 0),
                    'api_incompatible': result.get('api_incompatible') or [],
                }}
            except Exception as e:
                return {'ok': False, 'data': f'包同步失败: {type(e).__name__}: {e}'}
        if cmd == 'docker.ws':
            # 云 Docker 沙箱：固定白名单操作，不使用 shell，容器名强校验。
            # 与 shell 同级的危险面（op=exec 等价容器内 shell）→ 走 allow_docker 门禁。
            if not self.allow_docker:
                return {'ok': False,
                        'data': 'docker 操作被禁用（node_agent.allow_docker: false）'}
            # 按文件路径导入（扩展被合成包加载，相对/绝对导入均取不到同目录模块）
            import os as _os
            import sys as _sys
            _here = _os.path.dirname(_os.path.abspath(__file__))
            if _here not in _sys.path:
                _sys.path.insert(0, _here)
            import docker_ws as _dw
            return _dw.handle(args or {})
        if cmd == 'shell':
            if not self.allow_shell:
                return {'ok': False, 'data': 'shell 命令被禁用（node_agent.allow_shell: false）'}
            line = str(args.get('cmd', '')).strip()
            if not line:
                return {'ok': False, 'data': '缺少 args.cmd'}
            try:
                r = subprocess.run(line, shell=True, capture_output=True,
                                   text=True, timeout=60)
                return {'ok': r.returncode == 0,
                        'data': {'code': r.returncode,
                                 'stdout': (r.stdout or '')[-4000:],
                                 'stderr': (r.stderr or '')[-4000:]}}
            except subprocess.TimeoutExpired:
                return {'ok': False, 'data': '命令超时（60s）'}
            except Exception as e:
                return {'ok': False, 'data': f'{type(e).__name__}: {e}'}
        return {'ok': False, 'data': f'未知命令: {cmd}'}

    # ── 连接循环 ──────────────────────────────────────────
    def start(self):
        if not self.name or not self.secret:
            self._log("节点代理未启动：node_agent.name / secret 未配置")
            return
        # 云沙箱内存弹性调节器：随代理常驻（按文件路径导入，与 _exec 同法）。
        # 起不来不影响主链路，只记一条警告，docker.ws 被调用时还会兜底再拉一次。
        try:
            import os as _os
            import sys as _sys
            _here = _os.path.dirname(_os.path.abspath(__file__))
            if _here not in _sys.path:
                _sys.path.insert(0, _here)
            import docker_ws as _dw
            _dw.start_governor()
        except Exception:
            logger.warning("[node_agent] 弹性内存调节器启动失败", exc_info=True)
        self._thread = threading.Thread(target=self._run_loop,
                                        name='node-agent', daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_evt = asyncio.Event()
        self._send_lock = asyncio.Lock()
        try:
            self._task = self._loop.create_task(self._forever())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[node_agent] 代理循环异常退出")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _forever(self):
        backoff = 2
        while not self._stop_evt.is_set():
            try:
                self._client = FramedClient(self.secret,
                                            replay_window=DEFAULT_REPLAY_WINDOW)
                await self._client.connect(self.hub_host, self.hub_port, timeout=10)
                self._log(f"已连接 hub {self.hub_host}:{self.hub_port}")
                backoff = 2
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[node_agent] 连接异常: %s，%ss 后重连",
                               e, backoff)
            if self._stop_evt.is_set():
                return
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, 120)

    async def _session(self):
        # 握手
        self._next_seq()
        await self._client.send(F_HELLO, self._seq,
                                json.dumps({'name': self.name,
                                            'version': _fw_version(self.fw),
                                            'capabilities': self.capabilities()},
                                           ensure_ascii=False).encode('utf-8'))
        last_hb = 0.0
        last_in_seq = 0          # 入站帧单调序号（与 hub 出站序号配对，防重放）
        while not self._stop_evt.is_set():
            # 心跳：到点就发（与服务端读帧共用一个协程，用短超时轮转）
            now = time.time()
            wait = max(0.5, self.interval - (now - last_hb))
            try:
                frame = await asyncio.wait_for(self._recv_frame(last_in_seq),
                                               timeout=wait)
            except asyncio.TimeoutError:
                frame = None
            now = time.time()
            if now - last_hb >= self.interval:
                self._next_seq()
                await self._client.send(F_HEARTBEAT, self._seq,
                                        json.dumps(self._status(),
                                                   ensure_ascii=False).encode('utf-8'))
                last_hb = now
            if frame is None:
                continue
            last_in_seq = frame.seq
            if frame.type == F_CMD:
                try:
                    req = json.loads(frame.text())
                    cmd = str(req.get('cmd', ''))
                    # PTY 流：开流是长驻会话，不回 F_RESULT，由泵线程推流
                    if cmd == 'docker.pty':
                        await self._handle_pty_open(req)
                        continue
                    args = req.get('args') or {}
                    if cmd in ('shell', 'pkg.sync', 'config.status',
                               'config.reload', 'config.push',
                               'framework.update', 'framework.rollback'):
                        # 可能阻塞数十秒：放线程里跑，别把心跳/PTY 共用的事件循环卡死
                        # （hub 侧读帧超时 90s，长时间无帧会被判定断线）。
                        res = await asyncio.to_thread(self._exec, cmd, args)
                    else:
                        res = self._exec(cmd, args)
                    res['id'] = int(req.get('id', -1))
                except Exception as e:
                    res = {'id': -1, 'ok': False,
                           'data': f'命令处理异常: {type(e).__name__}: {e}'}
                await self._send_locked(F_RESULT, self._next_seq(),
                                        json.dumps(res, ensure_ascii=False)
                                        .encode('utf-8'))
            elif frame.type == F_STREAM_OPEN:
                # hub 用专用帧开流（与 F_CMD docker.pty 等价，语义更清晰）。
                # 之前漏了这个分支，导致 F_STREAM_OPEN 被“其他帧忽略”，PTY 从未真正开启。
                try:
                    req = json.loads(frame.text())
                    await self._handle_pty_open(req)
                except Exception:
                    logger.exception("[node_agent] STREAM_OPEN 处理异常")
            elif frame.type == F_STREAM_DATA:
                try:
                    d = json.loads(frame.text())
                    sid = int(d.get('sid', -1))
                    data = d.get('data')
                    if data is not None:
                        self._pty_input(sid, data)
                except Exception:
                    logger.exception("[node_agent] STREAM_DATA 处理异常")
            elif frame.type == F_STREAM_RESIZE:
                try:
                    d = json.loads(frame.text())
                    self._pty_resize(int(d.get('sid', -1)),
                                     int(d.get('cols', 0)), int(d.get('rows', 0)))
                except Exception:
                    logger.exception("[node_agent] STREAM_RESIZE 处理异常")
            elif frame.type == F_STREAM_CLOSE:
                try:
                    d = json.loads(frame.text())
                    self._close_stream(int(d.get('sid', -1)))
                except Exception:
                    pass
            # 其他帧忽略

    async def _recv_frame(self, last_seq: int = 0):
        """从客户端读一整帧并验签（FramedClient 无读接口，直接用底层 reader）。

        校验与 hub 对 agent 出站帧的校验完全对称：时间戳窗口 + 整帧 HMAC +
        单调序号。失败抛 ConnectionError → 由 _forever 断开重连（fail closed）。
        """
        from service.transport.framed import HEADER_LEN
        import struct as _s
        reader = self._client._reader
        if reader is None:
            raise ConnectionError("未连接")
        head = await reader.readexactly(HEADER_LEN)
        plen = _s.unpack_from(">I", head, 21)[0]
        if plen > 1024 * 1024:
            raise ValueError(f"载荷超限 {plen}B")
        payload = await reader.readexactly(plen) if plen else b""
        frame = self._client._codec.decode(head + payload)
        ok, reason = self._client._codec.verify_seq(frame, last_seq)
        if not ok:
            raise ConnectionError(f"入站帧校验失败（{reason}），断开重连")
        return frame

    def _next_seq(self):
        self._seq = (self._seq + 1) & 0x7FFFFFFF
        return self._seq

    # ── 流式会话（PTY）────────────────────────────────────
    async def _send_locked(self, ftype, seq, payload):
        """带锁发送任意帧（帧循环与泵线程共享底层 writer，必须串行化）。"""
        async with self._send_lock:
            await self._client.send(ftype, seq, payload)

    async def _handle_pty_open(self, req: dict):
        """hub 请求开启容器内 PTY 流：先登记占位 → 校验/spawn → 回填 master → 起泵。

        先登记占位可消除竞态：open 帧与用户按键(F_STREAM_DATA)可能并发到达，
        若晚登记，早到的按键会被 _pty_input 丢弃。故 spawn 前的按键先进 pending 缓冲。
        """
        args = req.get('args') or {}
        ws = str(args.get('ws', ''))
        sid = int(req.get('id', -1))     # hub 复用 cmd id 作为流 sid
        # 安全门禁：PTY = 容器内交互 shell，与 shell 同级，必须显式开闸
        if not self.allow_docker:
            await self._pty_fail(sid, 'PTY/docker 操作被禁用（node_agent.allow_docker: false）')
            return
        # docker_ws 与 main.py 同目录；扩展被合成包加载，必须按文件路径导入（同 _exec）
        import os as _os
        import sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import docker_ws
        # 占位登记（master 就绪前到达的按键先缓存，避免竞态丢键）
        with self._streams_lock:
            self._streams[sid] = {'master': None, 'proc': None, 'ws': ws,
                                  'pending': bytearray()}
        res = docker_ws.op_pty(ws, args)
        if not res.get('ok'):
            # 开流失败：把原因当流数据推回去再关流。
            # 早先这里回的是 F_RESULT，hub 侧无人在等它 → 流永远挂着，浏览器只是「连上了没反应」。
            # 改成 DATA+CLOSE，前端能立刻看到原因，socket 也会正常收尾。
            await self._pty_fail(sid, str(res.get('data') or '无法开启终端'))
            return
        cname = res['data']['cname']
        cmd = res['data']['cmd']
        workdir = res['data']['workdir']
        cols = int(res['data'].get('cols') or 80)
        rows = int(res['data'].get('rows') or 24)
        try:
            if pty is None:
                raise OSError('当前平台无 pty（PTY 功能不可用）')
            master, slave = pty.openpty()
            # 先把窗口尺寸设好再 spawn，docker 会把它当成容器 tty 的初始尺寸
            self._set_winsize(master, cols, rows)
            # 必须带 -t：容器内才会有真正的 tty。
            # 只写 -i 时 docker 把 stdin 当管道转进容器，容器内 `[ -t 0 ]` 为假 ——
            # stty 报「Inappropriate ioctl for device」、Ctrl-C 无效、vim/top 起不来、
            # 窗口尺寸也传不进去（实测 TTY0=no TTY1=no）。
            # 带上 -t 后实测 TTY0=yes、stty size 返回前端给的尺寸；docker CLI 自己会
            # 监听本机 pty 的 SIGWINCH 并把尺寸转发给容器，resize 因此天然可用。
            proc = await asyncio.create_subprocess_exec(
                'docker', 'exec', '-i', '-t', '-e', 'TERM=xterm',
                '-w', workdir, cname, *cmd,
                stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
        except Exception as e:
            with self._streams_lock:
                self._streams.pop(sid, None)
            await self._send_locked(F_RESULT, self._next_seq(),
                                    json.dumps({'id': sid, 'ok': False,
                                                'data': f'PTY 启动失败: {type(e).__name__}: {e}'},
                                               ensure_ascii=False).encode('utf-8'))
            return
        with self._streams_lock:
            st = self._streams.get(sid)
            if st is None:              # 期间已被关闭
                try:
                    os.close(master)
                    proc.terminate()
                except Exception:
                    pass
                return
            st['master'] = master
            st['proc'] = proc
            pend = bytes(st.pop('pending', b'') or b'')
        if pend:                        # 回放 spawn 期间到达的按键
            try:
                os.write(master, pend)
            except OSError:
                pass
        threading.Thread(target=self._pty_pump, args=(sid, master),
                         name=f'pty-{sid}', daemon=True).start()

    async def _pty_fail(self, sid: int, message: str):
        """开流失败：先推一段人类可读的提示（走流数据），再关流。"""
        with self._streams_lock:
            self._streams.pop(sid, None)
        text = '\r\n\x1b[31m[终端] ' + message + '\x1b[0m\r\n'
        try:
            await self._send_locked(
                F_STREAM_DATA, self._next_seq(),
                json.dumps({'sid': sid,
                            'data': base64.b64encode(text.encode('utf-8')).decode('ascii')},
                           ensure_ascii=False).encode('utf-8'))
            await self._send_locked(
                F_STREAM_CLOSE, self._next_seq(),
                json.dumps({'sid': sid}).encode('utf-8'))
        except Exception:
            logger.exception("[node_agent] 回传开流失败原因异常 sid=%s", sid)

    def _pty_pump(self, sid: int, master: int):
        """泵线程：阻塞读 PTY 主端，按块推到 hub（base64 防二进制损坏）。"""
        try:
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                self._push(sid, F_STREAM_DATA,
                           {'sid': sid,
                            'data': base64.b64encode(data).decode('ascii')})
        finally:
            self._push(sid, F_STREAM_CLOSE, {'sid': sid})
            self._cleanup_stream(sid)

    def _push(self, sid: int, ftype: int, payload: dict):
        """跨线程把帧投递到 asyncio 循环发送（泵线程 / 关闭路径用）。"""
        try:
            self._next_seq()
            asyncio.run_coroutine_threadsafe(
                self._send_locked(ftype, self._seq,
                                  json.dumps(payload, ensure_ascii=False).encode('utf-8')),
                self._loop)
        except Exception:
            logger.exception("[node_agent] 推流失败 sid=%s", sid)

    @staticmethod
    def _set_winsize(fd: int, cols: int, rows: int) -> bool:
        """设置 PTY 窗口尺寸；内核会因此给前台进程组发 SIGWINCH。"""
        if fcntl is None or termios is None:
            return False
        try:
            ws = struct.pack('HHHH', max(1, rows), max(1, cols), 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, ws)
            return True
        except Exception:
            logger.exception("[node_agent] 设置 PTY 尺寸失败 %sx%s", cols, rows)
            return False

    def _pty_resize(self, sid: int, cols: int, rows: int):
        """hub→node：调整已开 PTY 的窗口大小（前端 xterm 尺寸变化时调用）。

        两步都要做：
          1) 改本机 pty 的 winsize —— 让 docker CLI 之后读到的尺寸是新的；
          2) 显式给 docker CLI 发 SIGWINCH —— 它收到才回去 ioctl 读尺寸，
             再调 Docker API 把容器内 tty 一起改掉。
        只做 1 不做 2 是没用的：本机这条 pty 没有 session/控制终端关系，
        内核不会自动把 TIOCSWINSZ 变成 SIGWINCH 送到 docker 进程。
        """
        if cols <= 0 or rows <= 0:
            return
        with self._streams_lock:
            st = self._streams.get(sid)
        if not st:
            return
        master = st.get('master')
        if not master:              # spawn 还没完成：等 master 就绪时用的是开流时给的尺寸
            return
        if not self._set_winsize(master, cols, rows):
            return
        proc = st.get('proc')
        if proc is not None and proc.returncode is None:
            sigwinch = getattr(signal, 'SIGWINCH', None)
            if sigwinch is None:            # Windows 无 SIGWINCH：跳过通知
                return
            try:
                os.kill(proc.pid, sigwinch)
            except Exception:
                logger.warning("[node_agent] 通知 docker 改尺寸失败 sid=%s", sid)

    def _pty_input(self, sid: int, data_b64: str):
        """hub→node 的流数据：写入 PTY 主端（用户在终端里敲的字）。未就绪先缓存。"""
        with self._streams_lock:
            st = self._streams.get(sid)
        if not st:
            return
        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            return
        master = st.get('master')
        if not master:                  # spawn 尚未完成：先进缓冲，待 master 就绪回放
            buf = st.get('pending')
            if buf is not None:
                buf.extend(raw)
            return
        try:
            os.write(master, raw)
        except Exception:
            logger.exception("[node_agent] PTY 写入失败 sid=%s", sid)

    def _close_stream(self, sid: int):
        """hub→node 的关闭信号：终止子进程并清理，回一个 F_STREAM_CLOSE 通知 hub。"""
        with self._streams_lock:
            st = self._streams.get(sid)
        if not st:
            return
        try:
            if st['proc'] and st['proc'].returncode is None:
                st['proc'].terminate()
        except Exception:
            pass
        self._push(sid, F_STREAM_CLOSE, {'sid': sid})
        self._cleanup_stream(sid)

    def _cleanup_stream(self, sid: int):
        with self._streams_lock:
            st = self._streams.pop(sid, None)
        if not st:
            return
        try:
            if st['proc'] and st['proc'].returncode is None:
                st['proc'].terminate()
        except Exception:
            pass
        try:
            os.close(st['master'])
        except Exception:
            pass

    def stop(self):
        """停止代理（可从任意线程调用）：置停止位 → 关连接 → 取消循环任务。

        此前 stop() 取消的是一个从未赋值的 self._task（恒为 None），
        实际什么都停不掉；热卸载/停机后连接与线程全部残留。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _shutdown():
            if self._stop_evt is not None:
                self._stop_evt.set()
            if self._task is not None and not self._task.done():
                self._task.cancel()
            client = self._client
            if client is not None:
                asyncio.ensure_future(client.close())

        try:
            loop.call_soon_threadsafe(_shutdown)
        except RuntimeError:
            pass    # 循环已在别处关闭


def _fw_version(fw) -> str:
    """读项目根 VERSION 文件（与 README/CHANGELOG 同源）。"""
    try:
        from service.zkg.defaults import PROJECT_ROOT
        with open(os.path.join(PROJECT_ROOT, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def _apply_hot_config(agent, old_config, new_config, log=None):
    """配置热重载回调：只热更新运行期安全的字段。

    ``allow_*`` 与 ``interval`` 随即可生效；``name / secret / hub_host /
    hub_port`` 绑定在当前已建立的连接上，热改会让 hub 验签失败或连错地址，
    因此这里不热改，只提示需要重启。
    """
    if agent is None:
        return []
    log = log or (lambda m: logger.info(m))
    old = (old_config or {}).get('node_agent') or {}
    new = (new_config or {}).get('node_agent') or {}
    agent.allow_shell = bool(new.get('allow_shell', False))
    agent.allow_docker = bool(new.get('allow_docker', False))
    agent.allow_packages = bool(new.get('allow_packages', False))
    agent.allow_config = bool(new.get('allow_config', False))
    agent.allow_upgrade = bool(new.get('allow_upgrade', False))
    try:
        agent.interval = max(5, int(new.get('interval', agent.interval)))
    except (TypeError, ValueError):
        log('配置热重载: node_agent.interval 非法，保留原值')
    restart = [k for k in ('name', 'secret', 'hub_host', 'hub_port')
               if old.get(k) != new.get(k)]
    if restart:
        log(f"配置热重载: {', '.join(restart)} 需重启节点代理后生效")
    return restart


def register(ctx):
    """启动节点代理（node_agent.enabled 默认 false，仅被管节点开启）"""
    global _agent
    fw = ctx._framework
    cfg = fw.config.get('node_agent', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log("节点代理未启用 (node_agent.enabled: false)")
        return
    _agent = NodeAgent(fw, ctx_log=ctx.log)
    ctx.hook('config.reload',
             lambda old, new: _apply_hot_config(_agent, old, new, ctx.log))
    _agent.start()
    ctx.log(f"节点代理已启动: {cfg.get('hub_host', '?')}:{cfg.get('hub_port', '?')}"
            f"（本节点名 {_agent.name or '未设置'}）")


def unregister():
    global _agent
    if _agent:
        _agent.stop()
        _agent = None


_agent = None
