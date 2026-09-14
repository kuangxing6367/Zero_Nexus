"""
统一 API 入口（core/ctx 节点 6）

- register_api：在框架 Web 服务上注册自定义 REST 路由（复用框架鉴权）
- call_async：把协程安全调度到框架主事件循环
- api / aapi：同步/异步调用协议 API
"""

import asyncio
import logging

logger = logging.getLogger('zernus')


class ApiMixin:
    """ctx.register_api / call_async / api / aapi"""

    def register_api(self, path: str, handler, methods=None, auth: bool = True,
                     description: str = None):
        """
        在框架 Web 服务器上注册一条自定义 REST 路由，自动复用框架登录/API Key 鉴权。

        这是把 Zeronus 当通用服务宿主的关键接入点：外部系统/页面可通过 HTTP 与插件交互，
        而不必自己开 HTTP 服务、自己写鉴权。
        """
        from core.api import registry as _api_registry
        if methods is None:
            methods = ['GET']
        methods = [m.upper() for m in methods]

        # 双进程宿主模式：Web 在核心进程，走远程路由（handler 契约 fn(params)->dict/(status,dict)）
        rr = getattr(self._framework, '_remote_routes', None)
        if rr is not None:
            try:
                rr.register_route(path, methods, handler, auth)
            except Exception as e:
                self.log(f"远程 API 路由注册失败 {path} {methods}: {e}")
                return False
            if description:
                self.log(f"已注册远程 API 路由 {path} {methods}" + (f" ({description})" if description else ""))
            else:
                self.log(f"已注册远程 API 路由 {path} {methods}")
            return True

        route = _api_registry.register_route(path, methods, handler, auth)
        ok = route is not None
        if ok and description:
            self.log(f"已注册 API 路由 {path} {methods}" + (f" ({description})" if description else ""))
        elif not ok:
            self.log(f"已登记 API 路由 {path}（等待 Web 启用后挂载）")
        return ok

    def call_async(self, coro):
        """
        把协程安全地调度到框架主事件循环执行（可从任意线程调用）。
        :return: concurrent.futures.Future（可在原线程阻塞 .result()，或忽略让其后台运行）
        """
        loop = getattr(self._framework, 'loop', None)
        if loop is None or not loop.is_running():
            raise RuntimeError("框架主事件循环未运行")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def api(self, action: str, bot: str = None, **params):
        """
        调用协议 API（同步桥接）
        推荐 async handler 使用 aapi()，避免阻塞事件循环
        """
        if bot is None:
            bot = getattr(self, '_current_bot', None)
        caller = self._framework.services.get('api_caller')
        if caller is None:
            raise RuntimeError("无可用协议适配器")
        return caller.call(action, bot=bot, **params)

    async def aapi(self, action: str, bot: str = None, **params):
        """异步调用协议 API"""
        if bot is None:
            bot = getattr(self, '_current_bot', None)
        caller = self._framework.services.get('api_caller')
        if caller is None:
            raise RuntimeError("无可用协议适配器")
        return await caller.acall(action, bot=bot, **params)
