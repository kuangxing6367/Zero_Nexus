# -*- coding: utf-8 -*-
"""WebSocket 实时推送（新功能域）

框架事件实时推送到浏览器/外部客户端：客户端连上 ws://host:port/ws 后，
服务器在指定框架事件发生时主动推送 JSON：{"event": <事件名>, "payload": <载荷>}。

与 SSE（/api/logs/sse 仅日志）不同，这里是通用事件通道，覆盖任意框架事件
（默认白名单：plugin.load / plugin.unload / cron.task.trigger.after /
lifecycle.startup / lifecycle.shutdown，可在 config.ws.events 调整）。

基于 websockets 库（依赖已装：websockets 17.x），独立于 Flask，独立端口服务。
零额外第三方依赖（websockets 已在环境内）。
"""
import asyncio
import json
import logging

logger = logging.getLogger('zernus')

# 事件总线 handler 是同步回调，推送需切回事件循环
_DEFAULT_EVENTS = [
    'plugin.load', 'plugin.unload',
    'cron.task.trigger.after',
    'lifecycle.startup', 'lifecycle.shutdown',
]


class WsEventHub:
    """WebSocket 客户端集 + 广播。"""

    def __init__(self):
        self._clients = set()
        self._lock = asyncio.Lock()
        self.loop = None

    async def add_client(self, ws):
        self._clients.add(ws)
        logger.info(f"WS 客户端接入，当前 {len(self._clients)}")

    async def remove_client(self, ws):
        self._clients.discard(ws)
        logger.info(f"WS 客户端断开，剩余 {len(self._clients)}")

    async def publish(self, event: str, payload):
        """向所有已连接客户端广播事件（JSON）。"""
        if not self._clients:
            return
        msg = json.dumps({'event': event, 'payload': payload},
                         ensure_ascii=False, default=str)
        # 复制一份避免迭代中被改
        clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

    def publish_sync(self, event: str, payload):
        """供同步上下文（事件总线 handler）调用：切回 loop 异步广播。"""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.publish(event, payload), self.loop)


async def _ws_handler(hub, ws):
    await hub.add_client(ws)
    try:
        # 客户端可发送 {"action":"subscribe","events":[...]} 做客户端过滤（本版服务端全推）
        async for _msg in ws:
            pass
    except Exception:
        pass
    finally:
        await hub.remove_client(ws)


async def start_ws_server(framework, host: str = '0.0.0.0', port: int = 6840,
                          events=None, loop=None):
    """启动 WS 事件推送服务（在 host 进程事件循环内 await）。"""
    import websockets

    hub = WsEventHub()
    hub.loop = loop or asyncio.get_running_loop()
    events = events or _DEFAULT_EVENTS

    bus = getattr(framework, 'event_bus', None)
    if bus is not None:
        for ev in events:
            bus.subscribe(ev, 'ws_events',
                          lambda p, _ev=ev: hub.publish_sync(_ev, p))

    logger.info(f"WebSocket 事件推送服务启动: ws://{host}:{port}/ws")
    async with websockets.serve(lambda ws: _ws_handler(hub, ws), host, port):
        await asyncio.Future()  # 常驻
