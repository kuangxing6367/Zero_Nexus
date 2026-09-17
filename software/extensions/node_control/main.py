# -*- coding: utf-8 -*-
"""
节点控制通道（官方扩展，多机管理 L2：hub 侧）

星型拓扑的中心侧控制面：监听 TCP 端口，接受节点主动外连（节点可在 NAT 后）。
传输复用 service/transport/framed.py（整帧 HMAC、防重放、超时均已加固），
鉴权用 per-node 预共享密钥——HELLO 帧载荷携带节点名，hub 按名取密钥验签，
伪造节点名无法通过整帧 HMAC 校验。

帧类型（本扩展自定义语义，帧格式见 framed.py）：
  1 HELLO     node→hub  握手注册，载荷 JSON {"name","version","plugins"}
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

进程内访问（任意线程可调，内部跨线程投递到 asyncio 循环）：
  ctrl = fw.services.get('node_control')
  ctrl.snapshot()                             # [{'name','version','status','last_seen_at',...}]
  ctrl.send_cmd('node-1', 'ping', {}, 10)     # → {'ok': True, 'data': 'pong'}

安全边界：hub 只接受节点出站连接，从不反向连接节点；命令在节点侧按白名单执行。
"""
import asyncio
import hashlib
import hmac
import json
import logging
import struct
import threading
import time

from service.transport.framed import FrameCodec, HEADER_LEN, DEFAULT_REPLAY_WINDOW

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "节点控制通道",
    "version": "0.1.0",
    "author": "Zeronus",
    "desc": "多机管理 L2：hub 侧控制通道（framed 协议，节点主动外连）",
    "priority": 30,
    "official": True,
}

# 帧类型
F_HELLO = 1
F_HEARTBEAT = 2
F_CMD = 3
F_RESULT = 4

_READ_TIMEOUT = 90.0      # 握手/读帧兜底超时（秒），远大于心跳周期即可
_MAX_PAYLOAD = 1024 * 1024


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
        # name -> 连接信息
        self.conns = {}
        self._lock = threading.Lock()          # 保护 conns / pending
        self._pending = {}                     # cmd id -> future
        self._cmd_seq = 0
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
                     'last_seen_at': time.strftime('%Y-%m-%d %H:%M:%S',
                                                   time.localtime(c['last_seen_at'])),
                     'status': dict(c['status'])}
                    for n, c in sorted(self.conns.items())]

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
            fut = self._loop.create_future()
            self._pending[cid] = fut
        try:
            payload = json.dumps({'id': cid, 'cmd': cmd, 'args': args},
                                 ensure_ascii=False).encode('utf-8')
            conn['codec']  # per-node 密钥
            seq = int(time.time() * 1000) & 0x7FFFFFFF   # 毫秒级单调序号
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
