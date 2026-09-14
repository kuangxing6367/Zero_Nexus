# -*- coding: utf-8 -*-
"""ctx Plugin API 新增 Mixin 校验：files / http / serialize / jobs /
eventbus_ext / cache / di。

用最小假 framework 实例化 PluginContext，验证各方法可用且语义正确。
"""
import sys
import os
import asyncio
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ctx import PluginContext
from core.kernel.event_bus import EventBus
from core.hooks import HookRegistry


class FakeScheduler:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_plugin_task(self, task_info):
        self.added.append(task_info)

    def remove_job(self, task_key):
        self.removed.append(task_key)


class FakeDB:
    def query(self, *a, **k):
        return []

    def query_one(self, *a, **k):
        return None

    def execute(self, *a, **k):
        return 0


_TEST_TMP_DIRS = []


def make_ctx():
    tmp = tempfile.mkdtemp(prefix='zeronus_ctx_test_')
    _TEST_TMP_DIRS.append(tmp)
    fw = type('FW', (), {})()
    fw.db = FakeDB()
    fw.services = {}
    fw.hooks = HookRegistry()
    fw.command_bus = type('CB', (), {'register': lambda *a, **k: None})()
    fw.event_bus = EventBus()
    fw.scheduler = FakeScheduler()
    fw.plugin_loader = type('PL', (), {'plugins_dat_dir': tmp})()
    return PluginContext('demo_plugin', fw), fw


def test_file_mixin():
    ctx, fw = make_ctx()
    ctx.write_file('note.txt', 'hello')
    assert ctx.read_file('note.txt') == 'hello'
    assert 'note.txt' in ctx.list_dir()
    # 绝对路径被拒绝（安全限制）
    abs_path = os.path.abspath('secret.txt')  # 当前盘绝对路径，isabs=True
    try:
        ctx.read_file(abs_path, allow_abs=False)
        raise AssertionError("应拒绝绝对路径")
    except ValueError:
        pass
    print("  [ok] files: write/read/list + 绝对路径安全限制")


def test_serialize_mixin():
    ctx, fw = make_ctx()
    assert ctx.load_json('{"a":1}') == {'a': 1}
    assert ctx.dump_json({'a': 1}) == '{\n  "a": 1\n}'
    # yaml 可能因环境无 pyyaml 而抛 ImportError，属预期
    try:
        y = ctx.dump_yaml({'b': 2})
        assert 'b:' in y
    except ImportError:
        print("  [warn] yaml 不可用，跳过 yaml 用例")
    print("  [ok] serialize: load_json/dump_json")


def test_cache_mixin():
    ctx, fw = make_ctx()
    ctx.cache_set('demo_plugin:k', 42, ttl=10)
    assert ctx.cache_get('demo_plugin:k') == 42
    ctx.cache_delete('demo_plugin:k')
    assert ctx.cache_get('demo_plugin:k', 'miss') == 'miss'
    print("  [ok] cache: set/get/delete")


def test_di_mixin():
    ctx, fw = make_ctx()
    ctx.provide('client', object())
    assert ctx.inject('client') is not None
    assert ctx.inject('nope', 'default') == 'default'
    made = []
    ctx.provide('lazy', factory=lambda: made.append(1) or object())
    first = ctx.inject('lazy')
    second = ctx.inject('lazy')
    assert first is second and len(made) == 1  # 工厂仅构造一次（缓存为单例）
    print("  [ok] di: provide/inject + 工厂单例缓存")


def test_eventbus_ext_mixin():
    ctx, fw = make_ctx()

    fired = []
    ctx.once('ev.once', lambda p: fired.append(p) or None)
    fw.event_bus.emit('ev.once', {'x': 1})
    fw.event_bus.emit('ev.once', {'x': 2})  # 第二次不应再触发
    assert fired == [{'x': 1}], fired

    # off
    def h(p):
        fired.append(('h', p))
    ctx.on('ev.off', h)
    ctx.off('ev.off', h)
    fw.event_bus.emit('ev.off', {'y': 1})
    assert ('h', {'y': 1}) not in fired

    # await_event
    async def go():
        task = asyncio.ensure_future(ctx.await_event('ev.wait', timeout=1))
        await asyncio.sleep(0.05)
        await fw.event_bus.aemit('ev.wait', {'z': 9})
        return await task
    res = asyncio.new_event_loop().run_until_complete(go())
    assert res == {'z': 9}
    print("  [ok] eventbus_ext: once/off/await_event")


def test_job_mixin():
    ctx, fw = make_ctx()

    def my_task():
        pass

    key = ctx.add_job(my_task, '*/5 * * * *', description='demo')
    assert key == 'plugin_demo_plugin_my_task'
    assert len(fw.scheduler.added) == 1
    assert fw.scheduler.added[0]['handler'] is my_task
    ctx.remove_job('my_task')
    assert 'plugin_demo_plugin_my_task' in fw.scheduler.removed
    print("  [ok] jobs: add_job/remove_job 复用 scheduler")


def test_http_mixin_mock():
    ctx, fw = make_ctx()
    # 用假 urlopen 验证 http_get 拼接与解析，不触网
    import urllib.request
    import urllib.error
    captured = {}

    class FakeResp:
        status = 200

        def __init__(self, body):
            self._b = body.encode('utf-8')

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        captured['url'] = req.full_url
        captured['method'] = req.method
        captured['data'] = req.data
        captured['headers'] = dict(req.header_items())
        return FakeResp('{"ok":true}')

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        status, body = ctx.http_get('http://example.test/api', params={'q': '1'})
        assert 'q=1' in captured['url']
        status, body = ctx.http_post('http://example.test/api', json_body={'a': 1})
        assert captured['data'] == b'{"a": 1}'
        assert captured['headers'].get('Content-type') == 'application/json'
    finally:
        urllib.request.urlopen = orig
    print("  [ok] http: http_get 拼接参数 / http_post 序列化 json（mock，不触网）")


if __name__ == '__main__':
    try:
        test_file_mixin()
        test_serialize_mixin()
        test_cache_mixin()
        test_di_mixin()
        test_eventbus_ext_mixin()
        test_job_mixin()
        test_http_mixin_mock()
        print("\nALL CTX MIXIN TESTS PASSED")
    finally:
        for _d in _TEST_TMP_DIRS:
            shutil.rmtree(_d, ignore_errors=True)
