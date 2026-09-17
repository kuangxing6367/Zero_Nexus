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

命令白名单（hub 下发 cmd 字段）：
  ping / health 恒可用；shell 需 allow_shell: true（args: {"cmd": "..."}）。
"""
import asyncio
import json
import logging
import subprocess
import sys
import threading
import time

from service.transport.framed import FramedClient, DEFAULT_REPLAY_WINDOW

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "节点代理",
    "version": "0.1.0",
    "author": "Zeronus",
    "desc": "多机管理 L2：节点侧代理（主动外连 hub，心跳+命令执行）",
    "priority": 30,
    "official": True,
}

F_HELLO = 1
F_HEARTBEAT = 2
F_CMD = 3
F_RESULT = 4


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
        self._seq = int(time.time()) & 0x7FFFFFFF
        self._loop = None
        self._thread = None
        self._client = None

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

    # ── 命令白名单执行 ────────────────────────────────────
    def _exec(self, cmd: str, args: dict) -> dict:
        if cmd == 'ping':
            return {'ok': True, 'data': 'pong'}
        if cmd == 'health':
            return {'ok': True, 'data': self._status()}
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
        self._thread = threading.Thread(target=self._run_loop,
                                        name='node-agent', daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._forever())
        except Exception:
            logger.exception("[node_agent] 代理循环异常退出")

    async def _forever(self):
        backoff = 2
        while True:
            try:
                self._client = FramedClient(self.secret,
                                            replay_window=DEFAULT_REPLAY_WINDOW)
                await self._client.connect(self.hub_host, self.hub_port, timeout=10)
                self._log(f"已连接 hub {self.hub_host}:{self.hub_port}")
                backoff = 2
                await self._session()
            except Exception as e:
                logger.warning("[node_agent] 连接异常: %s，%ss 后重连",
                               e, backoff)
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
                                            'version': _fw_version(self.fw)},
                                           ensure_ascii=False).encode('utf-8'))
        last_hb = 0.0
        while True:
            # 心跳：到点就发（与服务端读帧共用一个协程，用短超时轮转）
            now = time.time()
            wait = max(0.5, self.interval - (now - last_hb))
            try:
                frame = await asyncio.wait_for(self._recv_frame(), timeout=wait)
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
            if frame.type == F_CMD:
                try:
                    req = json.loads(frame.text())
                    res = self._exec(str(req.get('cmd', '')),
                                     req.get('args') or {})
                    res['id'] = int(req.get('id', -1))
                except Exception as e:
                    res = {'id': -1, 'ok': False,
                           'data': f'命令处理异常: {type(e).__name__}: {e}'}
                self._next_seq()
                await self._client.send(F_RESULT, self._seq,
                                        json.dumps(res, ensure_ascii=False)
                                        .encode('utf-8'))
            # 其他帧忽略

    async def _recv_frame(self):
        """从客户端读一整帧（FramedClient 无读接口，直接用底层 reader）。"""
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
        return self._client._codec.decode(head + payload)

    def _next_seq(self):
        self._seq = (self._seq + 1) & 0x7FFFFFFF
        return self._seq

    def stop(self):
        if self._loop and getattr(self, '_task', None):
            self._loop.call_soon_threadsafe(self._task.cancel)


def _fw_version(fw) -> str:
    """读项目根 VERSION 文件（与 README/CHANGELOG 同源）。"""
    try:
        from service.zkg.defaults import PROJECT_ROOT
        with open(os.path.join(PROJECT_ROOT, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def register(ctx):
    """启动节点代理（node_agent.enabled 默认 false，仅被管节点开启）"""
    global _agent
    fw = ctx._framework
    cfg = fw.config.get('node_agent', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log("节点代理未启用 (node_agent.enabled: false)")
        return
    _agent = NodeAgent(fw, ctx_log=ctx.log)
    _agent.start()
    ctx.log(f"节点代理已启动: {cfg.get('hub_host', '?')}:{cfg.get('hub_port', '?')}"
            f"（本节点名 {_agent.name or '未设置'}）")


def unregister():
    global _agent
    if _agent:
        _agent.stop()
        _agent = None


_agent = None
