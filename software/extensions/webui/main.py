"""
WebUI 管理后台（官方插件）

自 2026-09-14 内核化改造起，本插件承载原 core/api 的全部 Web 后端
（Flask 仪表盘 + REST + GraphQL + gRPC + WebSocket + 静态托管），
从内核迁出，内核只经 fw.api_registry 暴露中立的路由注册原语。
"""
import asyncio
import logging

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "WebUI 管理后台",
    "version": "1.0.0",
    "author": "Zeronus",
    "desc": "Web 管理面板 + REST API（从内核迁出的官方 Web 后端）",
    "priority": 0,
    "official": True,
}

_web_server = None


def register(ctx):
    """注册 WebUI 为官方插件：启动 Web 服务并拉起 ws / gRPC（按配置）"""
    global _web_server
    fw = ctx._framework

    # 检查配置是否启用
    web_cfg = fw.config.get('web', {})
    if web_cfg.get('enabled') is False:
        ctx.log("WebUI 已禁用 (web.enabled: false)")
        # 注册空服务，防止其他插件调用时报错
        fw.services.register('web_server', None)
        return

    # 延迟导入，只在启用时加载 Flask 等重依赖（Web 是扩展，不污染内核）
    from software.extensions.webui.webapp import create_web_app, WebServer
    from software.extensions.webui import registry

    _web_server = WebServer(fw)
    fw.services.register('web_server', _web_server)
    _web_server.start()

    # 注入 Web API 注册表到内核：ctx.register_api / IPC 远程路由经此挂载，
    # 内核不再直接依赖本插件（保持层倒置为 0）。
    fw.api_registry = registry

    # 挂载 Web 启用前缓冲的自定义 API 路由
    pending = getattr(fw, '_pending_api_routes', [])
    for item in pending:
        registry.register_route(*item)
    fw._pending_api_routes = []

    host = web_cfg.get('host', '127.0.0.1')
    port = web_cfg.get('port', 8080)
    ctx.log(f"WebUI 已启动: http://{host}:{port}")

    # WebSocket 事件推送（若 config.ws.enabled）
    _ws_cfg = fw.config.get('ws') or {}
    if _ws_cfg.get('enabled'):
        try:
            from software.extensions.webui.ws_events import start_ws_server
            ws_port = int(_ws_cfg.get('port', 6840))
            ws_host = _ws_cfg.get('host', '0.0.0.0')
            ws_events = _ws_cfg.get('events')
            asyncio.ensure_future(
                start_ws_server(fw, ws_host, ws_port, ws_events, fw.loop))
            ctx.log(f"WebSocket 事件推送已请求启动: ws://{ws_host}:{ws_port}/ws")
        except Exception as e:
            logger.error(f"WebSocket 事件推送启动失败: {e}")

    # gRPC 服务（若 config.grpc.enabled）
    _grpc_cfg = fw.config.get('grpc') or {}
    if _grpc_cfg.get('enabled'):
        try:
            from software.extensions.webui.grpc import start_grpc_server
            grpc_port = int(_grpc_cfg.get('port', 50051))
            grpc_host = _grpc_cfg.get('host', '0.0.0.0')
            srv = start_grpc_server(fw, grpc_host, grpc_port)
            if srv is not None:
                fw._grpc_server = srv
                ctx.log(f"gRPC 服务已请求启动: {grpc_host}:{grpc_port}")
        except Exception as e:
            logger.error(f"gRPC 服务启动失败: {e}")


def unregister():
    """卸载时停止"""
    global _web_server
    if _web_server:
        _web_server.stop()
        _web_server = None
