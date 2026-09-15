# -*- coding: utf-8 -*-
"""
GraphQL 对外 API 功能域（core/api 新功能域）。

用 ariadne 暴露框架内省查询与事件发布，端点：
    POST /api/graphql  body: {"query": "...", "variables": {...}}
    GET  /api/graphql  返回 SDL（便于工具/ introspection）

查询能力：
    query { info { name version uptimeSeconds startTime }
            health
            plugins { name enabled }
            services { name type } }
变更能力：
    mutation { emitEvent(name: String!, payload: JSON) { ok listeners } }

依赖：ariadne + graphql-core（已在 managed python 安装）。
"""
import logging
import time

from flask import Response, jsonify, request
from ariadne import (
    MutationType,
    QueryType,
    ScalarType,
    gql,
    graphql_sync,
    make_executable_schema,
)

logger = logging.getLogger('zernus')

# 注册期注入的上下文（framework / 工具函数），供 resolver 闭包读取
_CTX = None


type_defs = gql('''
scalar JSON

type Query {
    info: FrameworkInfo!
    health: String!
    plugins: [Plugin!]!
    services: [Service!]!
}

type Mutation {
    emitEvent(name: String!, payload: JSON): EmitResult!
}

type FrameworkInfo {
    name: String!
    version: String!
    uptimeSeconds: Float!
    startTime: Float!
}

type Plugin {
    name: String!
    enabled: Boolean!
}

type Service {
    name: String!
    type: String!
}

type EmitResult {
    ok: Boolean!
    listeners: Int!
}
''')

query = QueryType()
mutation = MutationType()
json_scalar = ScalarType("JSON")


@json_scalar.serializer
def _serialize_json(value):
    return value


@json_scalar.value_parser
def _parse_json(value):
    return value


def _fw():
    return _CTX.framework


def _listeners_of(name: str) -> int:
    try:
        subs = getattr(_fw().event_bus, '_subscribers', {}) or {}
        return len(subs.get(name, []))
    except Exception:
        return 0


@query.field('info')
def resolve_info(*_):
    fw = _fw()
    start = getattr(fw, 'start_time', None) or getattr(fw, '_start_time', None)
    now = time.time()
    return {
        'name': 'Zeronus',
        'version': _CTX._get_framework_local_version() or 'dev',
        'uptimeSeconds': float(now - start) if start else 0.0,
        'startTime': float(start) if start else 0.0,
    }


@query.field('health')
def resolve_health(*_):
    return 'ok'


@query.field('plugins')
def resolve_plugins(*_):
    try:
        loaded = _fw().plugin_loader.get_loaded_plugins() or {}
    except Exception:
        loaded = {}
    return [{'name': n, 'enabled': True} for n in loaded.keys()]


@query.field('services')
def resolve_services(*_):
    try:
        svcs = _fw().services.all() or {}
    except Exception:
        svcs = {}
    return [{'name': n, 'type': type(s).__name__} for n, s in svcs.items()]


@mutation.field('emitEvent')
def resolve_emit_event(*_, name, payload=None):
    payload = payload if isinstance(payload, dict) else {}
    listeners = _listeners_of(name)
    try:
        _fw().event_bus.emit(name, payload)
    except Exception as e:
        logger.error(f"GraphQL emitEvent 失败: {e}")
    return {'ok': True, 'listeners': listeners}


schema = make_executable_schema(type_defs, query, mutation, json_scalar)


def register(ctx):
    """挂载 GraphQL 路由到 Flask app（core/api 域统一 register(ctx) 约定）"""
    global _CTX
    _CTX = ctx
    app = ctx.app
    require_auth = ctx.require_auth

    @app.route('/api/graphql', methods=['POST', 'OPTIONS'])
    @require_auth
    def graphql_view():
        if request.method == 'OPTIONS':
            return ('', 204)
        body = request.get_json(silent=True) or {}
        q = body.get('query')
        variables = body.get('variables')
        operation = body.get('operationName')
        if not q:
            return jsonify({'errors': [{'message': 'missing query'}]}), 400
        success, result = graphql_sync(
            schema,
            {
                'query': q,
                'variables': variables,
                'operationName': operation,
            },
            context_value={'framework': ctx.framework},
        )
        return jsonify(result), (200 if success else 400)

    @app.route('/api/graphql', methods=['GET'])
    @require_auth
    def graphql_schema_view():
        return Response(type_defs, mimetype='text/plain; charset=utf-8')
