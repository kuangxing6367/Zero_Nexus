# -*- coding: utf-8 -*-
"""
安全二进制帧传输原语（协议无关）。

导出：
- FrameCodec  : 定长头 + 变长载荷的编解码，HMAC 令牌 + 时间戳窗口 + 单调序号防重放。
- Frame       : 解码后的单帧数据。
- FramedServer: 基于 asyncio 的认证 TCP 帧服务端，自动处理粘包/拆包/失步重同步/超大载荷防护。
- FramedClient: 配套客户端。

源自 ZCBOT / minecraftconsole 的 MC agent 协议，已剥离 MC 专属字段
（tps / players / 命令语义 / 心跳含义），保留可复用的通用传输层能力。
"""
from .framed import Frame, FrameCodec, FramedClient, FramedServer

__all__ = ["FrameCodec", "Frame", "FramedServer", "FramedClient"]
