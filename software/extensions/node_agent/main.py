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
  ping / health 恒可用；shell 需 allow_shell: true（args: {"cmd": "..."}）；
  docker.ws（含容器 exec）与 docker.pty / PTY 流需 allow_docker: true
  —— docker exec / PTY 与 shell 等价（可在容器内获得交互 shell），
  拿到 hub 控制权不等于默认获得节点 shell，必须显式开闸。
"""
import asyncio
import base64
import json
import logging
import os
import signal
import struct
import subprocess
import sys
import threading
import time

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

__plugin_meta__ = {
    "name": "节点代理",
    "version": "0.1.2",
    "author": "Zeronus",
    "desc": "多机管理 L2：节点侧代理（主动外连 hub，心跳+命令执行+PTY 流）",
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

    # ── 命令白名单执行 ────────────────────────────────────
    def _exec(self, cmd: str, args: dict) -> dict:
        if cmd == 'ping':
            return {'ok': True, 'data': 'pong'}
        if cmd == 'health':
            return {'ok': True, 'data': self._status()}
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
                                            'version': _fw_version(self.fw)},
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
                    res = self._exec(cmd, req.get('args') or {})
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
