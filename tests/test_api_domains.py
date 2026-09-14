# -*- coding: utf-8 -*-
"""HTTP API 新功能域校验：batch / webhook / ws_events 集线器。

batch：用 Flask test_client 在进程内派发子调用。
webhook：WebhookDispatcher 订阅事件总线，事件触发即出站 POST（mock urlopen）。
ws_events：WsEventHub 向模拟客户端广播。
"""
import sys
import os
import asyncio
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from core.api import batch as batch_mod
from core.webhook import WebhookDispatcher
from core.kernel.event_bus import EventBus
from core.api.ws_events import WsEventHub


def _fake_ctx(app, require_auth):
    class Ctx:
        pass
    c = Ctx()
    c.app = app
    c.require_auth = require_auth
    c.db = None
    return c


def test_batch():
    app = Flask(__name__)

    def passthrough(f):
        return f
    app.add_url_rule('/api/echo', 'echo', lambda: jsonify({'ok': True, 'q': request.args.get('q')}), methods=['GET'])
    app.add_url_rule('/api/set', 'set', lambda: jsonify({'got': (request.get_json(silent=True) or {}).get('x')}), methods=['POST'])

    batch_mod.register(_fake_ctx(app, passthrough))

    client = app.test_client()
    # 透传鉴权头
    r = client.post('/api/batch', json={'calls': [
        {'method': 'GET', 'path': '/api/echo?q=9'},
        {'method': 'POST', 'path': '/api/set', 'body': {'x': 42}},
        {'method': 'GET', 'path': 'http://evil.example/ssrf'},  # 应被拒绝
    ]}, headers={'Authorization': 'Bearer t'})
    data = r.get_json()
    assert data['code'] == 0
    assert data['count'] == 3
    # 子调用 0：GET echo
    assert data['results'][0]['status'] == 200
    assert '"q":"9"' in data['results'][0]['body']
    # 子调用 1：POST set
    assert data['results'][1]['status'] == 200
    assert '"got":42' in data['results'][1]['body']
    # 子调用 2：非 /api/ 路径被拒
    assert 'error' in data['results'][2]
    print("  [ok] batch: 进程内派发 + 鉴权透传 + 非 /api/ 路径拒绝")


def test_webhook_outbound():
    # mock urlopen 记录出站请求
    import core.webhook as wh_mod
    received = []
    ev = threading.Event()

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b''

    def fake_urlopen(req, timeout=10):
        received.append((req.full_url, req.header_items()))
        ev.set()
        return FakeResp()

    orig = wh_mod.urllib.request.urlopen
    wh_mod.urllib.request.urlopen = fake_urlopen
    try:
        fw = type('FW', (), {})()
        fw.event_bus = EventBus()
        disp = WebhookDispatcher(fw)
        sub_id = disp.add('http://hook.test/in', events=['my.event'])
        # 触发事件
        fw.event_bus.emit('my.event', {'a': 1})
        # 等待线程池出站
        ev.wait(timeout=2)
        assert received, "未产生出站 POST"
        assert received[0][0] == 'http://hook.test/in'
        assert any(k.lower() == 'x-zeronus-event' for k, _ in received[0][1])
        # 注销
        disp.remove(sub_id)
        assert disp.list_subs() == []
    finally:
        wh_mod.urllib.request.urlopen = orig
    print("  [ok] webhook: 订阅事件总线 + 事件触发出站 POST + 注销")


def test_ws_hub_broadcast():
    hub = WsEventHub()

    class FakeWs:
        def __init__(self):
            self.sent = []

        async def send(self, msg):
            self.sent.append(msg)

    async def go():
        ws = FakeWs()
        await hub.add_client(ws)
        await hub.publish('plugin.load', {'name': 'x'})
        await hub.remove_client(ws)
        return ws

    ws = asyncio.new_event_loop().run_until_complete(go())
    assert len(ws.sent) == 1
    assert '"event"' in ws.sent[0] and 'plugin.load' in ws.sent[0]
    print("  [ok] ws_events: WsEventHub 广播到客户端")


if __name__ == '__main__':
    test_batch()
    test_webhook_outbound()
    test_ws_hub_broadcast()
    print("\nALL API DOMAIN TESTS PASSED")
