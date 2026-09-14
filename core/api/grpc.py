# -*- coding: utf-8 -*-
"""
gRPC 对外 API 功能域（core/api 新功能域）。

提供 Zeronus 的 gRPC 服务：健康检查 / 插件列表 / 服务列表 / 事件发布。
proto:     core/api/grpc_defs/zeronus.proto
stub:      core/api/grpc_defs/zeronus_pb2.py + zeronus_pb2_grpc.py（grpc_tools.protoc 生成）

启用：在 config.yaml 设置
    grpc:
      enabled: true
      host: 0.0.0.0
      port: 50051
由 core/runtime/lifecycle.start 在框架启动时拉起；stop 时优雅关闭。

依赖：grpcio + grpcio-tools（已在 managed python 安装）。缺依赖时本域自动降级：
register() 仍可挂出 /api/grpc/info 状态接口，start_grpc_server() 返回 None 并告警。
"""
import json
import logging
import os
import sys
import time
from concurrent import futures

from flask import jsonify

logger = logging.getLogger('zernus')

# protoc 生成文件用 `import zeronus_pb2`（绝对导入），把 grpc_defs 目录加入搜索路径以解析
_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'grpc_defs')
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import grpc  # noqa: F401
    import zeronus_pb2
    import zeronus_pb2_grpc
    _HAS_GRPC = True
except Exception as e:  # pragma: no cover - 依赖缺失时降级
    zeronus_pb2 = None
    zeronus_pb2_grpc = None
    _HAS_GRPC = False
    logger.warning(f"gRPC stub 未就绪（请 pip install grpcio grpcio-tools）: {e}")


def start_grpc_server(framework, host='0.0.0.0', port=50051):
    """启动 gRPC 服务，返回 grpc.Server（供生命周期优雅关闭）；不可用返回 None。"""
    if not _HAS_GRPC:
        logger.error("gRPC 依赖未安装（pip install grpcio grpcio-tools），无法启动 gRPC 服务")
        return None

    class ZeronusServicer(zeronus_pb2_grpc.ZeronusServicer):
        def Health(self, request, context):
            return zeronus_pb2.HealthReply(status='ok', timestamp=int(time.time()))

        def ListPlugins(self, request, context):
            loaded = {}
            try:
                loaded = framework.plugin_loader.get_loaded_plugins() or {}
            except Exception:
                pass
            return zeronus_pb2.PluginList(
                plugins=[zeronus_pb2.PluginInfo(name=n, enabled=True) for n in loaded.keys()]
            )

        def ListServices(self, request, context):
            svcs = {}
            try:
                svcs = framework.services.all() or {}
            except Exception:
                pass
            return zeronus_pb2.ServiceList(
                services=[zeronus_pb2.ServiceInfo(name=n, type=type(s).__name__)
                          for n, s in svcs.items()]
            )

        def EmitEvent(self, request, context):
            name = request.name
            payload = {}
            if request.payload_json:
                try:
                    payload = json.loads(request.payload_json)
                except Exception:
                    payload = {}
            listeners = 0
            try:
                subs = getattr(framework.event_bus, '_subscribers', {}) or {}
                listeners = len(subs.get(name, []))
            except Exception:
                pass
            try:
                framework.event_bus.emit(name, payload)
            except Exception as e:
                logger.error(f"gRPC EmitEvent 失败: {e}")
            return zeronus_pb2.EmitReply(ok=True, listeners=listeners)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    zeronus_pb2_grpc.add_ZeronusServicer_to_server(ZeronusServicer(), server)
    server.add_insecure_port(f"{host}:{port}")
    server.start()
    logger.info(f"gRPC 服务已启动: {host}:{port}")
    return server


def register(ctx):
    """挂出 gRPC 状态接口（core/api 域统一 register(ctx) 约定）。"""
    app = ctx.app
    framework = ctx.framework
    require_auth = ctx.require_auth

    @app.route('/api/grpc/info', methods=['GET'])
    @require_auth
    def grpc_info():
        cfg = framework.config.get('grpc') or {}
        return jsonify({'code': 0, 'data': {
            'enabled': bool(cfg.get('enabled')),
            'host': cfg.get('host', '0.0.0.0'),
            'port': int(cfg.get('port', 50051)),
            'available': _HAS_GRPC,
        }})
