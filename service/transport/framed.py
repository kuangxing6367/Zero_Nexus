# -*- coding: utf-8 -*-
"""
安全二进制帧传输原语（协议无关）。

------------------------------------------------------------
帧格式（大端序，所有帧共用定长头，载荷变长）：
  offset 0     : type  (1B)   帧类型，调用方自定义语义
  offset 1-4   : ts    (4B)   时间戳（秒，uint32）
  offset 5-20  : token (16B)  HMAC-SHA256(secret, ts4) 前 16 字节
  offset 21-24 : plen (4B)    载荷字节数
  offset 25-28 : seq   (4B)   单调序号（防重放）
  offset 29+   : payload      变长载荷（任意字节，约定 UTF-8 文本）

头长 HEADER_LEN = 29。
------------------------------------------------------------
安全模型（无 TLS 时走私有隧道 / 内网）：
  - 固定对称密钥 + HMAC Token + 时间戳窗口
  - 单调序号（ts 或 seq）判重放
  - 恒定时间令牌比对（hmac.compare_digest）
------------------------------------------------------------

源自 ZCBOT / minecraftconsole 的 MC agent 协议，已剥离 MC 专属字段
（tps / players / 命令语义），仅保留通用传输层能力。纯标准库实现，
无第三方依赖（与内核"极简、仅硬依赖 PyYAML"原则一致）。
"""
import asyncio
import hashlib
import hmac
import logging
import struct
import time
from typing import Awaitable, Callable, Optional, Set, Tuple

logger = logging.getLogger("zernus.transport")

# 帧头常量
HEADER_LEN = 29          # 1 + 4 + 16 + 4 + 4
TS_OFF = 1
TOKEN_OFF = 5
TOKEN_LEN = 16
PLEN_OFF = 21
SEQ_OFF = 25

# 默认参数
DEFAULT_REPLAY_WINDOW = 3.0        # 时间戳窗口（秒），超窗即丢（防重放 / DDoS）
DEFAULT_MAX_PAYLOAD = 16 * 1024 * 1024   # 16MB 载荷上限（防 DoS 无限占内存）
DEFAULT_BUF_SIZE = 8192            # 单次 read 上限


class Frame:
    """解码后的单帧。"""

    __slots__ = ("type", "ts", "token", "plen", "seq", "payload")

    def __init__(self, ftype: int, ts: int, token: bytes, plen: int, seq: int, payload: bytes):
        self.type = ftype
        self.ts = ts
        self.token = token
        self.plen = plen
        self.seq = seq
        self.payload = payload

    def text(self) -> str:
        """按 UTF-8 解读载荷（替换非法字节）。约定文本协议时使用。"""
        return self.payload.decode("utf-8", "replace")

    def __repr__(self):
        return (f"Frame(type={self.type}, ts={self.ts}, seq={self.seq}, "
                f"plen={self.plen}, payload={self.payload[:32]!r}{'...' if self.plen > 32 else ''})")


class FrameCodec:
    """定长头 + 变长载荷的编解码与校验。无状态、可热路径复用。"""

    HEADER_LEN = HEADER_LEN
    TS_OFF = TS_OFF
    TOKEN_OFF = TOKEN_OFF
    TOKEN_LEN = TOKEN_LEN
    PLEN_OFF = PLEN_OFF
    SEQ_OFF = SEQ_OFF

    def __init__(self, secret: bytes, *, replay_window: float = DEFAULT_REPLAY_WINDOW):
        if not isinstance(secret, (bytes, bytearray)) or not secret:
            raise ValueError("secret 必须为非空 bytes")
        self.secret = bytes(secret)
        self.replay_window = float(replay_window)

    # ---------------------------------------------------------- 编解码
    def make_token(self, ts: int) -> bytes:
        """基于固定对称密钥 + 时间戳的 16 字节 HMAC Token。"""
        return hmac.new(self.secret, struct.pack(">I", ts), hashlib.sha256).digest()[:self.TOKEN_LEN]

    def encode(self, ftype: int, ts: int, seq: int, payload: bytes) -> bytes:
        """编码一帧：定长头 + 变长载荷。"""
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload 必须为 bytes")
        header = bytearray(self.HEADER_LEN)
        header[0] = ftype & 0xFF
        struct.pack_into(">I", header, self.TS_OFF, ts)
        header[self.TOKEN_OFF:self.TOKEN_OFF + self.TOKEN_LEN] = self.make_token(ts)
        struct.pack_into(">I", header, self.PLEN_OFF, len(payload))
        struct.pack_into(">I", header, self.SEQ_OFF, seq)
        return bytes(header) + bytes(payload)

    def decode(self, frame: bytes) -> Frame:
        """解析一帧。frame 至少为完整 HEADER_LEN。"""
        if len(frame) < self.HEADER_LEN:
            raise ValueError(f"帧长度 {len(frame)} < 帧头长度 {self.HEADER_LEN}")
        ftype = frame[0]
        ts = struct.unpack_from(">I", frame, self.TS_OFF)[0]
        token = bytes(frame[self.TOKEN_OFF:self.TOKEN_OFF + self.TOKEN_LEN])
        plen = struct.unpack_from(">I", frame, self.PLEN_OFF)[0]
        seq = struct.unpack_from(">I", frame, self.SEQ_OFF)[0]
        payload = bytes(frame[self.HEADER_LEN:self.HEADER_LEN + plen]) if plen else b""
        return Frame(ftype, ts, token, plen, seq, payload)

    # ---------------------------------------------------------- 校验
    def verify_ts(self, ts: int, token: bytes, last_ts: int) -> Tuple[bool, str]:
        """时间戳窗口 + Token + 单调时间戳判重放。返回 (ok, reason)。"""
        if abs(time.time() - ts) > self.replay_window:
            return False, "stale-timestamp"
        if not hmac.compare_digest(token, self.make_token(ts)):
            return False, "bad-token"
        if ts <= last_ts:
            return False, "replay-seq"
        return True, ""

    def verify_seq(self, ts: int, token: bytes, seq: int, last_seq: int) -> Tuple[bool, str]:
        """时间戳窗口 + Token + 单调序号判重放。返回 (ok, reason)。

        同秒并发时 ts 相同，必须用 seq 单调判重放，避免误丢弃。
        """
        if abs(time.time() - ts) > self.replay_window:
            return False, "stale-timestamp"
        if not hmac.compare_digest(token, self.make_token(ts)):
            return False, "bad-token"
        if seq <= last_seq:
            return False, "replay-seq"
        return True, ""


# on_frame 回调签名： (peer, frame, writer) -> awaitable
FrameHandler = Callable[[Tuple, Frame, asyncio.StreamWriter], Awaitable[None]]


class FramedServer:
    """基于 asyncio 的认证 TCP 帧服务端。

    自动处理：粘包（一次 read 多帧）、拆包（帧未到齐等待）、
    超大载荷防护、失步重同步（valid_types 提供时）。
    """

    def __init__(
        self,
        secret: bytes,
        on_frame: FrameHandler,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        verify_mode: str = "seq",
        valid_types: Optional[Set[int]] = None,
        replay_window: float = DEFAULT_REPLAY_WINDOW,
        max_payload: int = DEFAULT_MAX_PAYLOAD,
        buf_size: int = DEFAULT_BUF_SIZE,
    ):
        """
        :param secret: 固定对称密钥（非空 bytes）
        :param on_frame: 收到合法帧时的回调 async (peer, frame, writer)
        :param verify_mode: "seq" 用单调序号判重放；"ts" 用单调时间戳判重放
        :param valid_types: 允许的帧类型集合；提供后遇到非法首字节即跳 1 字节重同步；
                            为 None 则不恢复失步（调用方自行保证）
        """
        self._codec = FrameCodec(secret, replay_window=replay_window)
        self._on_frame = on_frame
        self._host = host
        self._port = port
        self._verify_mode = verify_mode
        self._valid_types = valid_types
        self._max_payload = max_payload
        self._buf_size = buf_size
        self._server: Optional[asyncio.AbstractServer] = None

    @property
    def port(self) -> int:
        if self._server is None:
            return 0
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._on_connect, self._host, self._port)
        logger.info("[framed] 监听 %s:%s", self._host, self.port)

    async def stop(self):
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:
            pass
        self._server = None

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        buf = bytearray()
        last_ts = 0
        last_seq = 0
        logger.info("[framed] 对端接入 %s", peer)
        try:
            while True:
                chunk = await reader.read(self._buf_size)
                if not chunk:                       # 对端关闭
                    break
                buf.extend(chunk)
                # ---------- 粘包 / 拆包处理：变长帧，按帧头 plen 切分 ----------
                while len(buf) >= 1:
                    ftype = buf[0]
                    if self._valid_types is not None and ftype not in self._valid_types:
                        del buf[0]                  # 失步：跳过 1 字节重新同步
                        continue
                    if len(buf) < self._codec.HEADER_LEN:
                        break                       # 帧头未到齐
                    plen = struct.unpack_from(">I", buf, self._codec.PLEN_OFF)[0]
                    if plen > self._max_payload:    # 防超大载荷占用内存
                        logger.warning("[framed] 载荷超限 %dB from %s，丢弃该字节", plen, peer)
                        del buf[0]
                        continue
                    total = self._codec.HEADER_LEN + plen
                    if len(buf) < total:
                        break                       # 拆包：数据未到齐，等待更多
                    raw = bytes(buf[:total])        # 粘包：取一帧
                    del buf[:total]

                    fr = self._codec.decode(raw)
                    if self._verify_mode == "ts":
                        ok, reason = self._codec.verify_ts(fr.ts, fr.token, last_ts)
                        if ok:
                            last_ts = fr.ts
                    else:
                        ok, reason = self._codec.verify_seq(fr.ts, fr.token, fr.seq, last_seq)
                        if ok:
                            last_seq = fr.seq
                    if not ok:
                        logger.warning("[framed] 丢弃帧 from %s: %s", peer, reason)
                        continue
                    try:
                        await self._on_frame(peer, fr, writer)
                    except Exception:
                        logger.exception("[framed] on_frame 处理异常 from %s", peer)
        finally:
            try:
                writer.close()
            except Exception:
                pass
            logger.info("[framed] 对端离开 %s", peer)


class FramedClient:
    """配套客户端：连接后发送认证帧。"""

    def __init__(self, secret: bytes, *, replay_window: float = DEFAULT_REPLAY_WINDOW):
        self._codec = FrameCodec(secret, replay_window=replay_window)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self, host: str, port: int):
        self._reader, self._writer = await asyncio.open_connection(host, port)

    def encode(self, ftype: int, seq: int, payload: bytes) -> bytes:
        return self._codec.encode(ftype, int(time.time()), seq, payload)

    async def send(self, ftype: int, seq: int, payload: bytes):
        if self._writer is None:
            raise RuntimeError("未连接")
        self._writer.write(self.encode(ftype, seq, payload))
        await self._writer.drain()

    async def close(self):
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
            self._reader = None
