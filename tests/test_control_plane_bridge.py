# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""框架壳回归测试：验证「壳只承载」且能真跑。

用内核真实的 dialect 翻译函数 + 真 SQLite 连接，把 MySQL 风格 DDL
翻译成 SQLite 执行，跑通 register → install → health → 业务 SQL。
不启动框架、不连 MySQL、不联网。

    python tests/test_control_plane_bridge.py
"""
import importlib.util
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.storage.dialect import _convert_placeholders, _translate_sql_for_sqlite  # noqa: E402

_passed = 0
_failed = 0


def check(name, ok, extra=''):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f'  ok   {name}')
    else:
        _failed += 1
        print(f'  FAIL {name}' + (f'  <- {extra}' if extra else ''))


class _Services:
    def __init__(self):
        self.items = {}

    def register(self, name, value):
        self.items[name] = value

    def get(self, name):
        return self.items.get(name)


class _Framework:
    def __init__(self, enabled=True, expose=False):
        self.config = {'resource_control': {
            'enabled': enabled, 'expose_readonly_api': expose}}
        self.services = _Services()
        self._pending_api_routes = []
        self.api_registry = None


class _Ctx:
    """桥到真 SQLite，DDL 走内核真实翻译函数。"""

    def __init__(self, enabled=True, expose=False):
        self._framework = _Framework(enabled, expose)
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.routes = []
        self.logs = []

    def create_table(self, ddl):
        self.conn.execute(_translate_sql_for_sqlite(ddl))
        self.conn.commit()

    def db_execute(self, sql, params=None):
        cur = self.conn.execute(_convert_placeholders(sql), tuple(params or ()))
        self.conn.commit()
        return cur.rowcount

    def db_insert(self, sql, params=None):
        cur = self.conn.execute(_convert_placeholders(sql), tuple(params or ()))
        self.conn.commit()
        return int(cur.lastrowid)

    def db_query(self, sql, params=None):
        cur = self.conn.execute(_convert_placeholders(sql), tuple(params or ()))
        return [dict(r) for r in cur.fetchall()]

    def db_query_one(self, sql, params=None):
        rows = self.db_query(sql, params)
        return rows[0] if rows else None

    def register_api(self, path, handler, methods=None, auth=True, description=None):
        self.routes.append((path, handler, methods, auth))
        return True

    def log(self, msg, level='info'):
        self.logs.append(msg)


def _load_main():
    path = os.path.join(ROOT, 'software', 'extensions',
                        'resource_control', 'main.py')
    spec = importlib.util.spec_from_file_location('cp_shell_main', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['cp_shell_main'] = mod
    spec.loader.exec_module(mod)
    return mod


print('== 框架壳：只承载 ==')
mod = _load_main()
check('壳可按内核方式装载', mod.__plugin_meta__['official'] is True)

off = _Ctx(enabled=False)
check('未启用时 register 返回 None', mod.register(off) is None)
check('未启用时不注册路由 / 不挂服务',
      off.routes == [] and off._framework.services.items == {})

ctx = _Ctx(enabled=True)
store = mod.register(ctx)
check('启用后返回 ResourceStore', type(store).__name__ == 'ResourceStore')
check('store 挂到 fw.services',
      ctx._framework.services.get('control_plane_store') is store)
check('默认只注册一条只读 health 路由',
      len(ctx.routes) == 1 and ctx.routes[0][0] == 'control-plane/health'
      and ctx.routes[0][2] == ['GET'] and ctx.routes[0][3] is True)
check('register 未自动建表', not store.health()['tables']['rc_resources'])
check('壳未构造 ControlPlane（services 里只有 store）',
      list(ctx._framework.services.items) == ['control_plane_store'])
check('FrameworkBackend 诚实声明不支持原子审计',
      store.capabilities()['atomic_audit'] is False
      and store.health()['capabilities']['backend'] == 'FrameworkBackend')

health_before = ctx.routes[0][1]({})
check('建表前健康探测返回未就绪', health_before['ok'] is False)

print('== 框架壳：真跑业务 SQL ==')
status = mod.install(ctx)
check('install 幂等建表', all(status['tables'].values()))
check('install 重复调用安全', all(mod.install(ctx)['tables'].values()))
health = ctx.routes[0][1]({})
check('健康路由返回 ok 与两张表',
      health['ok'] is True and health['schema_version'] == 2)

store.add_resource('hosting', 'ext-shell-1', 'sub-9', 't1')
check('经 ctx 写入台账', store.count_resources() == 1)
check('经 ctx 取回台账',
      store.get_resource('hosting', 'ext-shell-1').owner_subject == 'sub-9')
store.append_audit('sub-9', 'provision', 'hosting', 'ext-shell-1', 'ok',
                   {'password': 'must-not-appear'})
check('经 ctx 写审计且脱敏',
      'must-not-appear' not in str(store.tail_audit()[0].detail)
      and store.tail_audit()[0].outcome == 'ok')
check('真删生效', store.delete_resource('hosting', 'ext-shell-1')
      and store.count_resources() == 0)

print('== 框架壳：非原子后端的显式降级 ==')
degrade = store.add_resource_with_audit('hosting', 'ext-degrade', 'sub-9', 't1',
                                        'sub-9')
check('降级路径仍能登记资源', degrade.external_id == 'ext-degrade')
check('降级路径审计明确标记 atomic=False',
      store.tail_audit()[0].detail.get('atomic') is False)

print('== 框架壳：显式开启只读路由 ==')
ctx2 = _Ctx(enabled=True, expose=True)
store2 = mod.register(ctx2)
check('开启后注册三条路由', len(ctx2.routes) == 3)
check('三条路由均为 GET + 鉴权',
      all(r[2] == ['GET'] and r[3] is True for r in ctx2.routes))
check('额外两条为 resources / audit',
      [r[0] for r in ctx2.routes[1:]] == ['control-plane/resources',
                                          'control-plane/audit'])
mod.install(ctx2)
store2.add_resource('hosting', 'ext-ro-1', 'sub-1', 'tA')
store2.append_audit('sub-1', 'provision', 'hosting', 'ext-ro-1', 'ok',
                    {'token': 'must-not-appear'})

resources_handler = ctx2.routes[1][1]
audit_handler = ctx2.routes[2][1]
r_res = resources_handler({'args': {'tenant': 'tA', 'limit': '10'}})
check('只读台账路由返回记录',
      r_res['ok'] is True and r_res['count'] == 1
      and r_res['resources'][0]['external_id'] == 'ext-ro-1')
check('只读台账路由按 tenant 过滤',
      resources_handler({'args': {'tenant': 'other'}})['count'] == 0)
check('limit 被夹在合法范围',
      resources_handler({'args': {'limit': '999999'}})['count'] == 1)
r_audit = audit_handler({'args': {'limit': '5'}})
check('只读审计路由返回已脱敏事件',
      r_audit['ok'] is True and r_audit['count'] == 1
      and 'must-not-appear' not in str(r_audit))
check('无 args 时路由用默认参数',
      resources_handler({})['ok'] is True and audit_handler({})['ok'] is True)

print('== 框架壳：卸载 ==')
mod.unregister()
check('unregister 后健康路由返回未启用', ctx.routes[0][1]({})['ok'] is False)
check('unregister 后只读路由也返回未启用',
      resources_handler({})['ok'] is False and audit_handler({})['ok'] is False)

print()
print(f'通过 {_passed} 项，失败 {_failed} 项')
sys.exit(1 if _failed else 0)
