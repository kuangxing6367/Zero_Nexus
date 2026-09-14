# -*- coding: utf-8 -*-
"""扩展点（Hook Points）新增运行环节校验：service.register / plugin.load|unload /
db.query|execute / session.create|wait / cron.task.trigger。

验证策略：用记录型假 HookRegistry 注入各子系统，确认对应扩展点在运行时被触发，
且传递的 kwargs（sql/params/name/plugin_name/status 等）正确。
"""
import sys
import os
import asyncio
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hooks import HookPoints, HookRegistry
from core.adapters.protocol import ServiceRegistry
from core.storage.engine import Database


def _memory_db_path():
    """使用临时文件库做隔离（避免 :memory: 在进程中跨实例共享的 sqlite 怪行为）。"""
    fd, path = tempfile.mkstemp(suffix='.db', prefix='zk_test_')
    os.close(fd)
    return path


def _new_sqlite_db():
    p = _memory_db_path()
    return Database({'type': 'sqlite', 'path': p}), p


class Recorder:
    """记录所有 trigger_sync 调用：(point, kwargs)。"""

    def __init__(self):
        self.calls = []

    def trigger_sync(self, point, *args, **kwargs):
        self.calls.append((point, dict(kwargs)))

    async def trigger_async(self, point, *args, **kwargs):
        self.calls.append((point, dict(kwargs)))

    def points(self):
        return [c[0] for c in self.calls]

    def for_point(self, point):
        return [c[1] for c in self.calls if c[0] == point]


def test_constants_present():
    expected = [
        'service.register.before', 'service.register.after',
        'plugin.load', 'plugin.unload',
        'db.query.before', 'db.query.after',
        'db.execute.before', 'db.execute.after',
        'session.create.before', 'session.create.after',
        'session.wait.before', 'session.wait.after',
        'cron.task.trigger.before', 'cron.task.trigger.after',
    ]
    for name in expected:
        assert name in HookPoints.__dict__.values(), f"缺少扩展点常量: {name}"
    print("  [ok] 14 个新增扩展点常量齐全")


def test_service_register_hooks():
    rec = Recorder()
    fw = type('FW', (), {'hooks': rec})()
    reg = ServiceRegistry(fw)
    obj = object()
    reg.register('my_svc', obj)
    assert 'service.register.before' in rec.points()
    assert 'service.register.after' in rec.points()
    before = rec.for_point('service.register.before')[0]
    assert before['name'] == 'my_svc' and before['service'] is obj
    after = rec.for_point('service.register.after')[0]
    assert after['name'] == 'my_svc'
    print("  [ok] service.register.before/after 触发并传递 name/service")


def test_db_hooks():
    db, path = _new_sqlite_db()
    try:
        rec = Recorder()
        db._hooks = rec  # 注入假 HookRegistry

        db.query("SELECT 1")
        assert 'db.query.before' in rec.points()
        assert 'db.query.after' in rec.points()
        qb = rec.for_point('db.query.before')[0]
        assert qb['sql'] == "SELECT 1" and 'params' in qb
        assert rec.for_point('db.query.after')[0].get('result') is not None

        db.execute("CREATE TABLE t(id INTEGER)")
        assert 'db.execute.before' in rec.points()
        assert 'db.execute.after' in rec.points()
        eb = rec.for_point('db.execute.before')[0]
        assert 'CREATE TABLE' in eb['sql']

        db.execute("INSERT INTO t(id) VALUES (1)")
        db.query_one("SELECT id FROM t WHERE id=1")
        # query_one 复用 db.query.* 点位
        assert 'db.query.before' in rec.points()
        print("  [ok] db.query.* / db.execute.* 触发并传递 sql/params/result")
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            os.remove(path)
        except Exception:
            pass


def test_db_hook_reentrancy_guard():
    db, path = _new_sqlite_db()
    try:
        rec = Recorder()
        db._hooks = rec

        def handler_that_querys(**kwargs):
            # 处理器内再次查询不应二次触发（重入防护）
            db.query("SELECT 2")

        reg = HookRegistry()
        reg.register('db.query.before', 'probe:h', handler_that_querys)
        db._hooks = reg
        db.query("SELECT 1")
        # 主查询触发 1 次 before；处理器内的 SELECT 2 应被重入防护拦截（无递归崩溃即通过）
        print("  [ok] db hook 重入防护：处理器内再查询未递归触发（无异常）")
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            os.remove(path)
        except Exception:
            pass


def test_session_hooks():
    # 不依赖真实引擎，构造最小 framework + SessionManager 验证接线
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "session_ext_test",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "extensions", "session", "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rec = Recorder()
    fw = type('FW', (), {'hooks': rec})()
    mgr = mod.SessionManager(fw)

    class FakeEvent:
        group_id = 0
        user_id = 123
        is_group = False

    ev = FakeEvent()
    # create_session 是 sync，触发 session.create.before/after
    sess = mgr.create_session(None, ev, timeout=10)
    assert 'session.create.before' in rec.points()
    assert 'session.create.after' in rec.points()
    # wait_for 是 async，触发 session.wait.before/after
    async def go():
        # 不真正等待，直接验证 before 触发（after 在超时/收到消息后触发）
        task = asyncio.ensure_future(mgr.wait_for(None, ev, timeout=0.01))
        await asyncio.sleep(0.02)
        return await task
    res = asyncio.get_event_loop().run_until_complete(go()) if False else None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    assert 'session.wait.before' in rec.points(), rec.points()
    assert 'session.wait.after' in rec.points(), rec.points()
    print("  [ok] session.create.* / session.wait.* 触发")


def test_cron_hook_in_run_job():
    # 直接调用 _run_job 验证 cron.task.trigger.before/after 在事件循环内触发
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sched_ext_test",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "core", "scheduler", "__init__.py"))
    sched_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sched_mod)

    rec = Recorder()
    fw = type('FW', (), {'hooks': rec, 'plugin_loader': type('PL', (), {'get_plugin_module': lambda n: None})()})()
    s = sched_mod.TaskScheduler(fw)

    flag = {'ran': False}

    async def handler():
        flag['ran'] = True

    async def go():
        await s._run_job(handler, 'demo_plugin')
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    assert flag['ran'], "handler 未执行"
    assert 'cron.task.trigger.before' in rec.points(), rec.points()
    assert 'cron.task.trigger.after' in rec.points(), rec.points()
    after = rec.for_point('cron.task.trigger.after')[0]
    assert after['status'] == 'success' and after['plugin_name'] == 'demo_plugin'
    print("  [ok] cron.task.trigger.before/after 触发并传递 plugin_name/status")


if __name__ == '__main__':
    test_constants_present()
    test_service_register_hooks()
    test_db_hooks()
    test_db_hook_reentrancy_guard()
    test_session_hooks()
    test_cron_hook_in_run_job()
    print("\nALL HOOK EXT TESTS PASSED")
