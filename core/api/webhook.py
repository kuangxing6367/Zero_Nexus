# -*- coding: utf-8 -*-
"""
Webhook 出站管理（新功能域）

REST 管理订阅：GET/POST /api/webhooks、DELETE /api/webhooks/<id>。
底层用 core.webhook.WebhookDispatcher（挂在 framework 上，全部插件共享）。

创建订阅时，dispatcher 会为 events 中每个事件在事件总线注册 handler，
事件触发即向目标 URL 出站 POST（带 X-Zeronus-Event 与可选 HMAC 签名）。
"""
from flask import jsonify, request

logger = __import__('logging').getLogger('zernus')


def _dispatcher(framework):
    disp = getattr(framework, '_webhook_dispatcher', None)
    if disp is None:
        from core.webhook import WebhookDispatcher
        disp = WebhookDispatcher(framework)
        framework._webhook_dispatcher = disp
    return disp


def register(ctx):
    app = ctx.app
    framework = ctx.framework
    require_auth = ctx.require_auth

    @app.route('/api/webhooks', methods=['GET'])
    @require_auth
    def list_webhooks():
        return jsonify({'code': 0, 'data': _dispatcher(framework).list_subs()})

    @app.route('/api/webhooks', methods=['POST'])
    @require_auth
    def create_webhook():
        payload = request.get_json(silent=True) or {}
        url = payload.get('url')
        events = payload.get('events') or []
        secret = payload.get('secret')
        if not url:
            return jsonify({'code': 400, 'msg': 'url 必填'}), 400
        if not isinstance(events, list) or not events:
            return jsonify({'code': 400, 'msg': 'events 必须是非空数组'}), 400
        sub_id = _dispatcher(framework).add(url, events, secret)
        return jsonify({'code': 0, 'id': sub_id})

    @app.route('/api/webhooks/<sub_id>', methods=['DELETE'])
    @require_auth
    def delete_webhook(sub_id):
        _dispatcher(framework).remove(sub_id)
        return jsonify({'code': 0, 'msg': '已删除'})
