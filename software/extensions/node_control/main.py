# -*- coding: utf-8 -*-
"""
节点控制通道（官方扩展，多机管理 L2/L3：hub 侧）

星型拓扑的中心侧控制面：监听 TCP 端口，接受节点主动外连（节点可在 NAT 后）。
传输复用 service/transport/framed.py（整帧 HMAC、防重放、超时均已加固），
鉴权用 per-node 预共享密钥——HELLO 帧载荷携带节点名，hub 按名取密钥验签，
伪造节点名无法通过整帧 HMAC 校验。

帧类型（本扩展自定义语义，帧格式见 framed.py）：
  1 HELLO     node→hub  握手注册，载荷 JSON {"name","version","capabilities"}
  2 HEARTBEAT node→hub  心跳/状态上报，载荷 JSON（同 status_panel /health 结构）
  3 CMD       hub→node  命令下发，载荷 JSON {"id","cmd","args"}
  4 RESULT    node→hub  命令回执，载荷 JSON {"id","ok","data"}

启用（默认关，仅中心机需要开）：
  extensions.yaml → node_control:
    enabled: true
    host: 0.0.0.0
    port: 37010
    nodes:                # 允许接入的节点与预共享密钥（务必改掉示例值）
      - name: node-1
        secret: change-me-node-1

分组与 L3 编排（可选，默认无分组）：
  extensions.yaml → node_control:
    groups:                    # 逻辑分组，成员须在上面的 nodes 清单里
      edge: [node-1, node-2]   # 灰度发布可先发 edge，再发 core
      core: [node-0]

进程内访问（任意线程可调，内部跨线程投递到 asyncio 循环）：
  ctrl = fw.services.get('node_control')
  ctrl.snapshot()                             # [{'name','version','status','last_seen_at',...}]
  ctrl.send_cmd('node-1', 'ping', {}, 10)     # → {'ok': True, 'data': 'pong'}
  ctrl.list_groups()                          # {'edge': {'members': [...], 'online': [...]}}
  ctrl.send_group('edge', 'health', {}, timeout=10, concurrency=2)
  ctrl.send_group(None, 'ping', {}, concurrency=1, stagger=1.0)  # 全量滚动
  ctrl.push_config('edge', {'node_agent': {'interval': 60}})    # 托管覆盖层下发
  ctrl.config_status('edge')                                    # 只读查询覆盖层
  ctrl.reload_config('edge')                                    # 仅重读配置

push_config 在 hub 侧把 dict 规范化为 YAML 并算 sha256，节点侧重算比对后
原子写入 ``config.managed.yaml``（加载时最后合并、优先级最高，不改用户
config.yaml）并热重载；节点必须显式 ``node_agent.allow_config: true``。

框架自升级（需节点 ``allow_upgrade: true``）：
  ctrl.push_framework('edge', 'Zero_Nexus-1.2.0.zip', dry_run=True)  # 先校验
  ctrl.push_framework('edge', 'Zero_Nexus-1.2.0.zip', concurrency=1, stagger=30)
  ctrl.framework_status('edge')          # 版本 + 可回滚备份
  ctrl.rollback_framework('edge')        # 回到最新备份；升级/回滚后需重启
升级包带 sha256 强校验，节点侧会先暂存并编译检查，再备份旧文件后替换，
失败自动回滚；不会自动重启进程。

send_group 是**同步聚合**接口：并发上限由 concurrency 控制（默认 4），
stagger 为相邻两次下发的间隔秒数（配合 concurrency=1 即逐台灰度）。
返回 {'group','cmd','total','ok','failed','offline','elapsed','results'}；
单个节点失败不会中断整组，结果里逐台给出 ok/data。

安全边界：hub 只接受节点出站连接，从不反向连接节点；命令在节点侧按白名单执行，
包同步（pkg.sync）还需节点侧显式 allow_packages: true。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from service.transport.framed import FrameCodec, HEADER_LEN, DEFAULT_REPLAY_WINDOW

logger = logging.getLogger('zernus')

# 节点侧 node_agent 的框架升级包上限；hub 侧提前拦截，避免白传大包。
_MAX_UPDATE_BYTES = 8 * 1024 * 1024

__plugin_meta__ = {
    "name": "节点控制通道",
    "version": "0.1.5",
    "author": "Zeronus",
    "desc": "多机管理 L2/L3：hub 侧控制通道（分组、滚动命令、配置下发、框架升级、PTY 流）",
    "priority": 30,
    "official": True,
}

# 帧类型
F_HELLO = 1
F_HEARTBEAT = 2
F_CMD = 3
F_RESULT = 4
F_STREAM_OPEN = 5     # hub→node：开启 PTY 流
F_STREAM_DATA = 6     # 双向：流数据块
F_STREAM_CLOSE = 7    # 双向：关闭流
F_STREAM_RESIZE = 8   # hub→node：调整 PTY 窗口大小

_READ_TIMEOUT = 90.0      # 握手/读帧兜底超时（秒），远大于心跳周期即可
_MAX_PAYLOAD = 1024 * 1024


def _log_async_failure(what: str):
    """给 run_coroutine_threadsafe 的 future 挂回调：协程抛异常时打日志。

    这类「火忘型」任务没人 await，异常会静静躺在 future 里，
    表现就是「功能没反应、日志里什么都没有」——加这个才不会白排查。
    """
    def _cb(fut):
        try:
            exc = fut.exception()
        except Exception:      # 已取消 / 取异常本身失败
            return
        if exc is not None:
            logger.error("[node_control] %s 协程异常: %r", what, exc,
                         exc_info=exc)
    return _cb


class NodeControl:
    """hub 侧控制通道：接受节点外连、心跳聚合、命令下发。"""

    def __init__(self, fw, ctx_log=None):
        self.fw = fw
        self._log = ctx_log or (lambda m: logger.info(m))
        cfg = fw.config.get('node_control', {}) or {}
        self.host = str(cfg.get('host', '0.0.0.0'))
        self.port = int(cfg.get('port', 37010))
        # name -> secret(bytes)
        self._secrets = {}
        for n in (cfg.get('nodes', []) or []):
            if isinstance(n, dict) and n.get('name') and n.get('secret'):
                self._secrets[str(n['name'])] = str(n['secret']).encode('utf-8')
        # 逻辑分组：group -> [node name]。只做成员解析，不改变鉴权边界。
        self.groups = {}
        for g, members in (cfg.get('groups') or {}).items():
            if isinstance(members, (list, tuple)):
                self.groups[str(g)] = [str(m) for m in members if m]
        for g, members in self.groups.items():
            unknown = [m for m in members if m not in self._secrets]
            if unknown:
                logger.warning(
                    "[node_control] 分组 %s 含未在 nodes 中声明的节点 %s，"
                    "这些成员不会被接入（分组不绕过密钥清单）", g, unknown)
        # name -> 连接信息
        self.conns = {}
        self._lock = threading.Lock()          # 保护 conns / pending
        self._pending = {}                     # cmd id -> future
        self._cmd_seq = 0                      # 命令载荷 id（回执匹配用）
        self._out_seq = 0                      # 出站帧序号：全帧型统一单调递增（防重放配对）
        self._streams = {}                     # stream id -> {name,on_data,on_close,exp}
        self._stream_sid = 0
        self._loop = None
        self._server = None
        self._stop_evt = None
        self._thread = None

    # ── 生命周期 ──────────────────────────────────────────
    def start(self):
        if not self._secrets:
            self._log("节点控制通道已启动，但 node_control.nodes 为空，暂无可信节点")
        self._thread = threading.Thread(target=self._run_loop,
                                        name='node-control', daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_evt = asyncio.Event()
        try:
            self._loop.run_until_complete(self._serve())
        except Exception:
            logger.exception("[node_control] 服务循环异常退出")

    async def _serve(self):
        self._server = await asyncio.start_server(self._on_conn, self.host, self.port)
        self._log(f"节点控制通道监听 {self.host}:{self.port}，"
                  f"可信节点 {len(self._secrets)} 个")
        try:
            await self._stop_evt.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)

    # ── 帧读写 ────────────────────────────────────────────
    @staticmethod
    async def _read_frame(reader) -> bytes:
        """读一整帧（带头），超时/超限抛异常。"""
        head = await asyncio.wait_for(reader.readexactly(HEADER_LEN), _READ_TIMEOUT)
        plen = struct.unpack_from(">I", head, 21)[0]
        if plen > _MAX_PAYLOAD:
            raise ValueError(f"载荷超限 {plen}B")
        payload = await asyncio.wait_for(reader.readexactly(plen),
                                         _READ_TIMEOUT) if plen else b""
        return head + payload

    async def _on_conn(self, reader, writer):
        peer = writer.get_extra_info("peername")
        name = None
        try:
            # ── 握手：第一帧必须是 HELLO，按载荷中的节点名取密钥验签 ──
            raw = await self._read_frame(reader)
            hello_frame = FrameCodec(b'probe').decode(raw)   # 仅解头部，token 后验
            if hello_frame.type != F_HELLO:
                raise ValueError("首帧非 HELLO")
            info = json.loads(hello_frame.payload.decode('utf-8', 'replace'))
            name = str(info.get('name', ''))
            secret = self._secrets.get(name)
            if not secret:
                raise ValueError(f"未知节点 {name!r}")
            codec = FrameCodec(secret, replay_window=DEFAULT_REPLAY_WINDOW)
            expect = codec.make_token(hello_frame.ts, hello_frame.type,
                                      hello_frame.seq, hello_frame.payload)
            if not hmac.compare_digest(hello_frame.token, expect):
                raise ValueError(f"节点 {name!r} 握手验签失败")

            # ── 注册连接 ──
            conn = {'writer': writer, 'codec': codec, 'last_seq': hello_frame.seq,
                    'version': str(info.get('version', '')),
                    'capabilities': dict(info.get('capabilities') or {}),
                    'status': dict(info), 'last_seen_at': time.time(),
                    'peer': f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)}
            with self._lock:
                old = self.conns.get(name)
                self.conns[name] = conn
            if old is not None:
                try:
                    old['writer'].close()
                except Exception:
                    pass
            self._log(f"节点 {name} ({conn['peer']}) 已接入控制通道 "
                      f"v{conn['version'] or '?'}")

            # ── 帧循环 ──
            while True:
                raw = await self._read_frame(reader)
                fr = codec.decode(raw)
                ok, reason = codec.verify_seq(fr, conn['last_seq'])
                if not ok:
                    logger.warning("[node_control] 丢弃来自 %s 的帧: %s", name, reason)
                    continue
                conn['last_seq'] = fr.seq
                conn['last_seen_at'] = time.time()
                if fr.type == F_HEARTBEAT:
                    try:
                        conn['status'] = json.loads(fr.text())
                    except Exception:
                        pass
                elif fr.type == F_RESULT:
                    try:
                        res = json.loads(fr.text())
                        fut = self._pending.pop(int(res.get('id', -1)), None)
                        if fut and not fut.done():
                            fut.set_result({'ok': bool(res.get('ok')),
                                            'data': res.get('data')})
                    except Exception:
                        logger.exception("[node_control] RESULT 处理异常")
                elif fr.type == F_STREAM_DATA:
                    try:
                        d = json.loads(fr.text())
                        sid = int(d.get('sid', -1))
                        data = d.get('data')
                        st = self._streams.get(sid)
                        if st and data is not None and st.get('on_data'):
                            st['on_data'](base64.b64decode(data))
                    except Exception:
                        logger.exception("[node_control] STREAM_DATA 处理异常")
                elif fr.type == F_STREAM_CLOSE:
                    try:
                        d = json.loads(fr.text())
                        sid = int(d.get('sid', -1))
                        st = self._streams.pop(sid, None)
                        if st and st.get('on_close'):
                            st['on_close'](sid)
                    except Exception:
                        logger.exception("[node_control] STREAM_CLOSE 处理异常")
                # 其他类型忽略（协议向前兼容）
        except (asyncio.IncompleteReadError, asyncio.TimeoutError,
                ConnectionError, ValueError, OSError, json.JSONDecodeError) as e:
            if name:
                self._log(f"节点 {name} 连接断开: {e}")
            elif not isinstance(e, asyncio.IncompleteReadError):
                logger.warning("[node_control] 未完成握手的连接 %s 断开: %s", peer, e)
        finally:
            if name:
                with self._lock:
                    if self.conns.get(name, {}).get('writer') is writer:
                        self.conns.pop(name, None)
                self._log(f"节点 {name} 已离线")
            try:
                writer.close()
            except Exception:
                pass

    # ── 对外接口 ──────────────────────────────────────────
    def snapshot(self) -> list:
        """当前在线节点快照（不做网络请求）。"""
        with self._lock:
            return [{'name': n, 'peer': c['peer'], 'version': c['version'],
                     'capabilities': dict(c.get('capabilities') or {}),
                     'groups': [g for g, ms in sorted(self.groups.items()) if n in ms],
                     'last_seen_at': time.strftime('%Y-%m-%d %H:%M:%S',
                                                   time.localtime(c['last_seen_at'])),
                     'status': dict(c['status'])}
                    for n, c in sorted(self.conns.items())]

    # ── 分组与批量编排（L3）────────────────────────────────
    def list_groups(self) -> dict:
        """分组配置与当前在线成员（只读，不发命令）。"""
        with self._lock:
            online = set(self.conns)
        return {g: {'members': list(ms),
                    'online': [m for m in ms if m in online],
                    'offline': [m for m in ms if m not in online]}
                for g, ms in sorted(self.groups.items())}

    def group_members(self, group=None):
        """解析分组成员。

        ``None`` / ``''`` / ``*`` / ``all`` 表示全部在线节点；
        未知分组返回 ``None``（与「已知但为空」区分），避免静默全量下发。
        """
        if group in (None, '', '*', 'all'):
            with self._lock:
                return sorted(self.conns)
        if group in self.groups:
            return list(self.groups[group])
        return None

    def send_group(self, group=None, cmd: str = 'ping', args: dict = None,
                   timeout: float = 30.0, concurrency: int = 4,
                   stagger: float = 0.0) -> dict:
        """向分组并发/滚动下发命令并聚合回执（线程安全）。

        :param group: 分组名；``None`` / ``'all'`` 表示全部在线节点
        :param concurrency: 同时在途的命令数，默认 4
        :param stagger: 相邻两次下发的间隔秒数；配合 concurrency=1 即逐台灰度
        :return: ``{'group','cmd','total','ok','failed','offline','elapsed','results'}``
                 未知分组额外带 ``error``，且不向任何节点下发。
        """
        label = 'all' if group in (None, '', '*', 'all') else str(group)
        members = self.group_members(group)
        if members is None:
            return {'group': label, 'cmd': cmd, 'error': f'未知分组 {group!r}',
                    'total': 0, 'ok': 0, 'failed': 0, 'offline': [],
                    'elapsed': 0.0, 'results': []}
        with self._lock:
            online = set(self.conns)
        offline = [m for m in members if m not in online]
        if not members:
            return {'group': label, 'cmd': cmd, 'total': 0, 'ok': 0,
                    'failed': 0, 'offline': [], 'elapsed': 0.0, 'results': []}

        concurrency = max(1, int(concurrency))
        stagger = max(0.0, float(stagger))
        results = []
        started = time.time()
        with ThreadPoolExecutor(max_workers=concurrency,
                                thread_name_prefix='node-rollout') as pool:
            futures = {}
            for i, name in enumerate(members):
                if stagger and i:
                    time.sleep(stagger)
                futures[pool.submit(self.send_cmd, name, cmd, args or {},
                                    timeout)] = name
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:      # 单个节点异常不拖垮整组
                    res = {'ok': False, 'data': f'{type(e).__name__}: {e}'}
                results.append({'node': name, 'ok': bool(res.get('ok')),
                                'data': res.get('data')})
        order = {n: i for i, n in enumerate(members)}
        results.sort(key=lambda r: order.get(r['node'], 0))
        ok_count = sum(1 for r in results if r['ok'])
        return {'group': label, 'cmd': cmd, 'total': len(members),
                'ok': ok_count, 'failed': len(members) - ok_count,
                'offline': offline, 'elapsed': round(time.time() - started, 3),
                'results': results}

    def broadcast(self, cmd: str = 'ping', args: dict = None,
                  timeout: float = 30.0, concurrency: int = 4,
                  stagger: float = 0.0) -> dict:
        """向全部在线节点下发（send_group(None) 的语义别名）。"""
        return self.send_group(None, cmd, args, timeout, concurrency, stagger)

    # ── 配置下发（L3 收尾）─────────────────────────────────
    def push_config(self, group=None, config: dict = None,
                    timeout: float = 60.0, concurrency: int = 1,
                    stagger: float = 0.0, dry_run: bool = False,
                    reload: bool = True) -> dict:
        """把配置片段下发为节点侧的托管覆盖层（config.managed.yaml）。

        在 hub 侧把 ``config`` 规范化为 YAML 原文并计算 sha256，节点侧用同一
        份字节重算比对（强校验），随后原子写入覆盖层并热重载。节点必须显式
        ``allow_config: true``，否则整组回执都是 ``ok=False``（不会静默跳过）。

        :param group: 分组名；None / 'all' 表示全部在线节点
        :param config: 覆盖层配置 dict（非空；只写覆盖层，不动用户 config.yaml）
        :param concurrency: 默认 1（配置下发默认逐台，便于观察）
        :param stagger: 相邻下发间隔秒数（concurrency=1 时即逐台灰度）
        :param dry_run: True 时节点只校验不落盘
        :param reload: 写盘后是否立即热重载（端口/数据库等启动期项仍需重启）
        """
        if not isinstance(config, dict) or not config:
            raise ValueError('config 必须是非空 dict')
        raw = yaml.safe_dump(config, allow_unicode=True, sort_keys=True,
                             default_flow_style=False)
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        return self.send_group(
            group, 'config.push',
            {'yaml': raw, 'sha256': digest,
             'dry_run': bool(dry_run), 'reload': bool(reload)},
            timeout=timeout, concurrency=concurrency, stagger=stagger)

    def reload_config(self, group=None, timeout: float = 60.0,
                      concurrency: int = 1, stagger: float = 0.0) -> dict:
        """让节点重读配置文件（含托管覆盖层），不写入任何内容。"""
        return self.send_group(group, 'config.reload', {},
                               timeout=timeout, concurrency=concurrency,
                               stagger=stagger)

    def config_status(self, group=None, timeout: float = 30.0,
                      concurrency: int = 4) -> dict:
        """只读查询节点覆盖层状态（是否开启 allow_config、文件大小/sha256 等）。"""
        return self.send_group(group, 'config.status', {},
                               timeout=timeout, concurrency=concurrency)

    # ── 框架自升级（L3 收尾）─────────────────────────────
    def push_framework(self, group=None, zip_path: str = '',
                       timeout: float = 180.0, concurrency: int = 1,
                       stagger: float = 0.0, dry_run: bool = False) -> dict:
        """把本地框架源码 ZIP 下发给节点（base64 + sha256）。

        节点侧必须显式 ``allow_upgrade: true``。节点收到后先校验 sha256，
        再暂存、编译检查、备份旧文件、替换；失败自动回滚。**升级后需重启
        节点进程**，当前命令不会自动重启。

        :param zip_path: 本地 ZIP 路径（通常是 GitHub Release 的源码包）
        :param concurrency: 默认 1（逐台灰度）
        :param stagger: 相邻下发间隔秒数
        :param dry_run: 节点只校验/编译，不落盘
        """
        path = str(zip_path or '')
        if not path or not os.path.isfile(path):
            raise ValueError(f'升级包不存在: {zip_path!r}')
        size = os.path.getsize(path)
        if size > _MAX_UPDATE_BYTES:
            raise ValueError(f'升级包过大：{size}B > 上限 {_MAX_UPDATE_BYTES}B')
        with open(path, 'rb') as f:
            data = f.read()
        digest = hashlib.sha256(data).hexdigest()
        return self.send_group(
            group, 'framework.update',
            {'zip_b64': base64.b64encode(data).decode('ascii'),
             'sha256': digest, 'dry_run': bool(dry_run)},
            timeout=timeout, concurrency=concurrency, stagger=stagger)

    def rollback_framework(self, group=None, backup: str = '',
                           timeout: float = 120.0, concurrency: int = 1,
                           stagger: float = 0.0) -> dict:
        """让节点回滚到指定备份（空字符串 = 最新一份）。回滚后需重启。"""
        return self.send_group(group, 'framework.rollback',
                               {'backup': str(backup or '')},
                               timeout=timeout, concurrency=concurrency,
                               stagger=stagger)

    def framework_status(self, group=None, timeout: float = 30.0,
                         concurrency: int = 4) -> dict:
        """只读查询节点版本与可回滚备份。"""
        return self.send_group(group, 'framework.status', {},
                               timeout=timeout, concurrency=concurrency)

    def send_cmd(self, name: str, cmd: str, args: dict = None,
                 timeout: float = 30.0) -> dict:
        """向在线节点下发命令并等待回执（线程安全，可从任意线程调用）。

        :return: {'ok': bool, 'data': ...}；节点离线/超时/拒绝时 ok=False
        """
        if not self._loop:
            return {'ok': False, 'data': '控制通道未启动'}
        fut = asyncio.run_coroutine_threadsafe(
            self._send_cmd_coro(name, cmd, args or {}, timeout), self._loop)
        try:
            return fut.result(timeout + 10)
        except Exception as e:
            return {'ok': False, 'data': f'{type(e).__name__}: {e}'}

    async def _send_cmd_coro(self, name, cmd, args, timeout):
        with self._lock:
            conn = self.conns.get(name)
            if conn is None:
                return {'ok': False, 'data': f'节点 {name} 不在线'}
            self._cmd_seq += 1
            cid = self._cmd_seq
            self._out_seq += 1
            seq = self._out_seq
            fut = self._loop.create_future()
            self._pending[cid] = fut
        try:
            payload = json.dumps({'id': cid, 'cmd': cmd, 'args': args},
                                 ensure_ascii=False).encode('utf-8')
            conn['codec']  # per-node 密钥
            frame = conn['codec'].encode(F_CMD, int(time.time()), seq, payload)
            conn['writer'].write(frame)
            await conn['writer'].drain()
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {'ok': False, 'data': f'命令回执超时（{timeout}s）'}
        except (ConnectionError, OSError) as e:
            return {'ok': False, 'data': f'连接异常: {e}'}
        finally:
            with self._lock:
                self._pending.pop(cid, None)

    # ── 流式会话（PTY）────────────────────────────────────
    def open_stream(self, name: str, cmd: str, args: dict,
                    on_data, on_close, timeout: float = 300.0):
        """开启一个到节点的 PTY 流会话（线程安全，可从任意线程调用）。

        :param on_data: 回调(bytes) —— 节点推送的流数据（在 node_control 循环线程执行，
                                  调用方需自行投递到自己的事件循环）
        :param on_close: 回调(sid) —— 流结束/远端关闭时触发
        :return: stream id（>0），失败返回 None
        """
        if not self._loop:
            return None
        fut = asyncio.run_coroutine_threadsafe(
            self._open_stream_coro(name, cmd, args, on_data, on_close, timeout),
            self._loop)
        try:
            return fut.result(5)
        except Exception as e:
            logger.warning("[node_control] open_stream 失败: %s", e)
            return None

    async def _open_stream_coro(self, name, cmd, args, on_data, on_close, timeout):
        with self._lock:
            conn = self.conns.get(name)
            if conn is None:
                return None
            self._stream_sid += 1
            sid = self._stream_sid
            self._out_seq += 1
            seq = self._out_seq
            self._streams[sid] = {'name': name, 'on_data': on_data,
                                  'on_close': on_close,
                                  'exp': time.time() + timeout}
        payload = json.dumps({'id': sid, 'cmd': cmd, 'args': args},
                             ensure_ascii=False).encode('utf-8')
        frame = conn['codec'].encode(F_STREAM_OPEN, int(time.time()), seq, payload)
        conn['writer'].write(frame)
        await conn['writer'].drain()
        return sid

    def stream_write(self, sid: int, data_b64: str):
        """向流写入数据（hub→node，用户在终端里敲的字）。"""
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._stream_write_coro(sid, data_b64), self._loop)

    async def _stream_write_coro(self, sid, data_b64):
        st = self._streams.get(sid)
        if not st:
            return
        with self._lock:
            conn = self.conns.get(st['name'])
            if conn is None:
                return
            self._out_seq += 1
            seq = self._out_seq
        payload = json.dumps({'sid': sid, 'data': data_b64},
                             ensure_ascii=False).encode('utf-8')
        frame = conn['codec'].encode(F_STREAM_DATA, int(time.time()), seq, payload)
        conn['writer'].write(frame)
        await conn['writer'].drain()

    def resize_stream(self, sid: int, cols: int, rows: int):
        """调整流的 PTY 窗口大小（hub→node；前端终端尺寸变化时调用）。"""
        if not self._loop:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._resize_stream_coro(sid, cols, rows), self._loop)
        # 火忘型协程抛异常是没人接的：必须自己捞出来打日志，
        # 否则 resize 静默失效、只看得到「没反应」。
        fut.add_done_callback(_log_async_failure("resize"))

    async def _resize_stream_coro(self, sid, cols, rows):
        st = self._streams.get(sid)
        if not st:
            return
        with self._lock:
            conn = self.conns.get(st['name'])
            if conn is None:
                return
            self._out_seq += 1
            seq = self._out_seq
        payload = json.dumps({'sid': sid, 'cols': int(cols), 'rows': int(rows)},
                             ensure_ascii=False).encode('utf-8')
        frame = conn['codec'].encode(F_STREAM_RESIZE, int(time.time()), seq, payload)
        conn['writer'].write(frame)
        await conn['writer'].drain()

    def close_stream(self, sid: int):
        """关闭流（hub→node）。"""
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._close_stream_coro(sid), self._loop)

    async def _close_stream_coro(self, sid):
        st = self._streams.pop(sid, None)
        if not st:
            return
        with self._lock:
            conn = self.conns.get(st['name'])
            if conn is None:
                return
            self._out_seq += 1
            seq = self._out_seq
        payload = json.dumps({'sid': sid}, ensure_ascii=False).encode('utf-8')
        frame = conn['codec'].encode(F_STREAM_CLOSE, int(time.time()), seq, payload)
        conn['writer'].write(frame)
        await conn['writer'].drain()
        try:
            if st.get('on_close'):
                st['on_close'](sid)
        except Exception:
            logger.exception("[node_control] on_close 异常")


def register(ctx):
    """启动控制通道服务（node_control.enabled 默认 false，仅 hub 开启）"""
    global _ctrl
    fw = ctx._framework
    cfg = fw.config.get('node_control', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log("节点控制通道未启用 (node_control.enabled: false)")
        return
    _ctrl = NodeControl(fw, ctx_log=ctx.log)
    fw.services.register('node_control', _ctrl)
    _ctrl.start()


def unregister():
    global _ctrl
    if _ctrl:
        _ctrl.stop()
        _ctrl = None


_ctrl = None
