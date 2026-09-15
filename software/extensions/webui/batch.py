# -*- coding: utf-8 -*-
"""
批量操作（新功能域）

POST /api/batch 接收一个子调用数组，逐一在进程内派发（复用 Flask test_client，
无需额外网络往返），返回每个子调用的状态码与响应体。用于前端一次往返完成
多个独立操作（如批量启停插件、批量改配置）。

安全约束：
- 仅允许内部 /api/ 路径，禁止把框架当正向代理对外发请求（防 SSRF）；
- 单次最多 50 个子调用；
- 透传调用方 Authorization，子路由鉴权照常生效。
"""
from flask import Response, jsonify, request

logger = __import__('logging').getLogger('zernus')

_MAX_CALLS = 50


def register(ctx):
    app = ctx.app
    require_auth = ctx.require_auth
    db = ctx.db

    @app.route('/api/batch', methods=['POST'])
    @require_auth
    def batch():
        """批量执行内部 API 调用。body: {"calls":[{"method","path","body","headers"}]}"""
        payload = request.get_json(silent=True) or {}
        calls = payload.get('calls')
        if not isinstance(calls, list):
            return jsonify({'code': 400, 'msg': 'calls 必须是数组'}), 400
        if len(calls) > _MAX_CALLS:
            return jsonify({'code': 400, 'msg': f'单次最多 {_MAX_CALLS} 个子调用'}), 400

        # 透传调用方鉴权头，子路由 require_auth 才能通过
        auth_header = request.headers.get('Authorization')
        client = app.test_client()
        results = []

        for i, call in enumerate(calls):
            method = str(call.get('method') or 'GET').upper()
            path = call.get('path') or call.get('url')
            if not path:
                results.append({'index': i, 'error': 'missing path'})
                continue
            if not path.startswith('/api/'):
                results.append({'index': i, 'error': '仅支持内部 /api/ 路径（禁止对外代理）'})
                continue
            if method not in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
                results.append({'index': i, 'error': f'不支持的方法 {method}'})
                continue

            headers = dict(call.get('headers') or {})
            if auth_header and 'Authorization' not in headers:
                headers['Authorization'] = auth_header

            try:
                resp = client.open(
                    path, method=method,
                    json=call.get('body'),
                    headers=headers,
                )
                text = resp.get_data(as_text=True)
                results.append({
                    'index': i,
                    'status': resp.status_code,
                    'body': text,
                })
            except Exception as e:
                results.append({'index': i, 'error': str(e)})

        return jsonify({'code': 0, 'count': len(results), 'results': results})
