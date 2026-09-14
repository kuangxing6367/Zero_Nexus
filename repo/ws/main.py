"""ws —— WebSocket 传输接口（机制包）。

框架提供的统一传输机制：下游不再各自手搓 WS 服务/鉴权
（云端工作站 _ws_main.py 700 行、uc_web 又造一遍的教训）。

稳定 API 表面：
    serve(host, port, on_message, *, on_connect=None) -> 协程
    connect(url, on_message, *, on_open=None) -> 协程
"""
from __future__ import annotations

from typing import Callable, Optional

try:
    import websockets
except ImportError:  # 延迟导入：包体可载入，缺库时仅运行期报错
    websockets = None


async def serve(host: str, port: int, on_message: Callable,
               *, on_connect: Optional[Callable] = None):
    """启动一个 WS 服务，对每个连接回调 on_connect/on_message。"""
    if websockets is None:
        raise RuntimeError("ws 机制包需要 websockets 库")
    async def _handler(ws):
        if on_connect:
            await on_connect(ws)
        async for msg in ws:
            await on_message(ws, msg)
    return await websockets.serve(_handler, host, port)


async def connect(url: str, on_message: Callable,
                 *, on_open: Optional[Callable] = None):
    """作为客户端连接一个 WS 端点，持续回调 on_message。"""
    if websockets is None:
        raise RuntimeError("ws 机制包需要 websockets 库")
    async with websockets.connect(url) as ws:
        if on_open:
            await on_open(ws)
        async for msg in ws:
            await on_message(ws, msg)
