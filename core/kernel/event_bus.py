"""事件总线（内核最小原语，全仓唯一实现）。

对外 API：
- subscribe(event_name, plugin_name, handler) / unsubscribe(...) / unsubscribe_plugin(...)
- aemit(event_name, payload)  异步发布，handler 支持 async def（直接 await）与普通 def（转线程）
- emit(event_name, payload)   同步桥接，供旧插件/非 loop 线程使用

订阅与退订线程安全（插件可能在 executor 线程中注册）。
``core.messaging.event_bus`` 对本模块做透明重导出，不另存实现。
"""
import asyncio
import logging
import threading
from typing import Callable, Dict, List

logger = logging.getLogger('zernus')


class EventBus:
    """轻量级事件总线"""

    def __init__(self):
        self._subscribers: Dict[str, List[dict]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_name: str, plugin_name: str, handler: Callable):
        """订阅事件（同一插件同一 handler 重复订阅自动去重）"""
        with self._lock:
            subs = self._subscribers.setdefault(event_name, [])
            for s in subs:
                if s['plugin_name'] == plugin_name and s['handler'] == handler:
                    return
            subs.append({
                'plugin_name': plugin_name,
                'handler': handler
            })
        logger.debug(f"事件订阅: [{plugin_name}] → {event_name}")

    def unsubscribe_plugin(self, plugin_name: str):
        """移除某插件的所有订阅"""
        with self._lock:
            for event_name in list(self._subscribers.keys()):
                self._subscribers[event_name] = [
                    s for s in self._subscribers[event_name]
                    if s['plugin_name'] != plugin_name
                ]
                if not self._subscribers[event_name]:
                    del self._subscribers[event_name]

    def unsubscribe(self, event_name: str, plugin_name: str, handler: Callable):
        """退订某插件在某个事件上的单个 handler（once/off 用）"""
        with self._lock:
            subs = self._subscribers.get(event_name)
            if not subs:
                return
            self._subscribers[event_name] = [
                s for s in subs
                if not (s['plugin_name'] == plugin_name and s['handler'] == handler)
            ]
            if not self._subscribers[event_name]:
                del self._subscribers[event_name]

    async def aemit(self, event_name: str, payload: dict = None) -> bool:
        """异步发布，返回 bool：任一 handler 返回 True 视为已处理"""
        with self._lock:
            subscribers = list(self._subscribers.get(event_name, []))
        if not subscribers:
            return False

        payload = payload or {}
        logger.debug(f"事件触发: {event_name} → {len(subscribers)} 个订阅者")

        handled = False
        for sub in subscribers:
            try:
                handler = sub['handler']
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(payload)
                else:
                    result = await asyncio.to_thread(handler, payload)
                if result is True:
                    handled = True
            except Exception as e:
                logger.error(f"事件处理异常: [{sub['plugin_name']}] {event_name} - {e}")
        return handled

    def emit(self, event_name: str, payload: dict = None):
        """同步桥接发布：主事件循环内 fire-and-forget，其他线程用临时循环执行"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            try:
                loop.create_task(self.aemit(event_name, payload))
                return
            except RuntimeError:
                pass
        asyncio.run(self.aemit(event_name, payload))
