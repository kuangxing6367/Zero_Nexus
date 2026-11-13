# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""控制面事务与 MySQL 后端路径回归测试（CI 入口）。

    python tests/test_control_plane_tx.py

**诚实边界**：本机没有 MySQL 服务端。这里用一个 DB-API 兼容的连接对象
（底层是标准库 sqlite3，占位符 ``%s`` → ``?``）真执行 ``MysqlBackend``
发出的每一条 SQL，验证它的代码路径、参数绑定、事务边界、游标回收与
故障回滚。它验证的是 **MysqlBackend 的实现**，不是 MySQL 服务端的
DDL / 隔离级别 / 锁行为；后者上线前仍需在真库实测。

表的列名与 SQLite 版一致，因此夹具用 DDL_SQLITE 预建表，
MysqlBackend 只负责在其上执行业务 SQL。
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from control_plane.control import (  # noqa: E402
    ControlPlane, DuplicateExternalId,
)
from control_plane.store import (  # noqa: E402
    DDL_MYSQL, DDL_SQLITE, DuplicateResource, MysqlBackend,
    ResourceStore, StoreError,
)

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


def expect_raises(name, exc_type, fn):
    try:
        fn()
    except exc_type:
        check(name, True)
    except Exception as exc:
        check(name, False, f'抛出 {type(exc).__name__}: {exc}')
    else:
        check(name, False, '未抛异常')


class _Cursor:
    """DB-API cursor：真执行 SQL，并记录 SQL/参数用于参数化断言。"""

    def __init__(self, conn):
        self._conn = conn
        self._cur = None
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self.closed = False

    def execute(self, sql, params=()):
        self._conn.log.append((sql, tuple(params or ())))
        if self._conn.fail_audit_in_tx and self._conn.in_tx and 'rc_audit' in sql \
                and sql.lstrip().upper().startswith('INSERT'):
            raise sqlite3.OperationalError('模拟事务内审计表不可写')
        translated = sql.replace('%s', '?')
        if sql.lstrip().upper() == 'BEGIN':
            self._conn.in_tx = True
        self._cur = self._conn.raw.execute(translated, tuple(params or ()))
        self.description = self._cur.description
        self.rowcount = self._cur.rowcount
        self.lastrowid = self._cur.lastrowid
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def close(self):
        self.closed = True
        self._cur = None


class _Connection:
    """DB-API 连接（sqlite3 驱动），记录 commit / rollback / cursor 生命周期。"""

    def __init__(self):
        self.raw = sqlite3.connect(':memory:', isolation_level=None)
        self.raw.row_factory = sqlite3.Row
        for stmt in DDL_SQLITE:
            self.raw.execute(stmt)
        self.log = []
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0
        self.cursors = []
        self.in_tx = False
        self.closed = False
        self.fail_audit_in_tx = False

    def cursor(self):
        cur = _Cursor(self)
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commits += 1
        self.in_tx = False
        self.raw.commit()

    def rollback(self):
        self.rollbacks += 1
        self.in_tx = False
        self.raw.rollback()

    def close(self):
        self.closed = True
        self.raw.close()

    # 便于断言的视图
    def statements(self):
        return [s for s, _ in self.log]

    def dml(self):
        return [(s, p) for s, p in self.log
                if s.lstrip().upper().startswith(('INSERT', 'DELETE', 'SELECT', 'UPDATE'))]

    def count(self, prefix):
        return sum(1 for s in self.statements()
                   if s.lstrip().upper().startswith(prefix))


class _Policy:
    def allowed(self, actor, action, target, scopes):
        return True


class _Adapter:
    kind = 'hosting'

    def __init__(self):
        self.created = []
        self.deleted = []
        self.seq = 0
        self.fixed_id = None

    def provision(self, spec):
        if self.fixed_id is not None:
            return self.fixed_id
        self.seq += 1
        eid = f'dbapi-{self.seq:03d}'
        self.created.append((eid, dict(spec)))
        return eid

    def deprovision(self, external_id):
        self.deleted.append(external_id)

    def status(self, external_id):
        return {'state': 'running', 'external_id': external_id}


class _Adapters:
    def __init__(self, adapter):
        self.map = {adapter.kind: adapter}

    def resolve(self, kind):
        return self.map.get(kind)


print('== 1. MysqlBackend 能力声明与 DDL ==')
conn = _Connection()
backend = MysqlBackend(conn)
store = ResourceStore(backend)
check('声明支持事务', backend.supports_transactions is True)
check('store 能力为 atomic_audit=True', store.capabilities()['atomic_audit'] is True)
check('health 可读且能力可见',
      all(store.health()['tables'].values())
      and store.health()['capabilities']['backend'] == 'MysqlBackend')
check('DDL 为 MySQL 5.7 风格', backend.ddl() == DDL_MYSQL
      and 'AUTO_INCREMENT' in DDL_MYSQL[0])
_ddl_all = '\n'.join(DDL_MYSQL)
check('DDL 无 USE / 库名前缀 / JOIN 框架表',
      'USE ' not in _ddl_all
      and not re.search(r'`\w+`\s*\.\s*`', _ddl_all)
      and 'JOIN' not in _ddl_all.upper())

print('== 2. 事务边界与参数化 ==')
res = store.add_resource_with_audit('hosting', 'ext-1', 'u1', 't1', 'u1')
check('原子写入返回资源', res.external_id == 'ext-1')
check('台账与审计各 1 条',
      store.count_resources() == 1 and store.count_audit() == 1)
check('BEGIN 恰好 1 次', conn.count('BEGIN') == 1, str(conn.statements()))
check('事务提交只发生在结束处（1 次 commit）', conn.commits == 1,
      f'commits={conn.commits}')
check('成功路径无 rollback', conn.rollbacks == 0)
check('事务内未提前 commit',
      conn.commits == 1 and conn.count('BEGIN') == 1)
check('所有游标均已关闭', all(c.closed for c in conn.cursors),
      f'未关闭 {sum(1 for c in conn.cursors if not c.closed)} 个')

dml = conn.dml()
check('带参数的业务 SQL 全部使用 %s 占位符',
      all('%s' in s for s, p in dml if p),
      str([s for s, p in dml if p and '%s' not in s]))
check('参数个数与占位符个数一致',
      all(s.count('%s') == len(p) for s, p in dml),
      str([(s, len(p)) for s, p in dml if s.count('%s') != len(p)]))
check('SQL 文本不含拼接进来的值字面量',
      all("'" not in s for s, _ in dml),
      str([s for s, _ in dml if "'" in s]))

evil = "y' OR 1=1 --"
store.add_resource_with_audit('hosting', evil, 'u2', 't1', 'u2')
check('注入串原样取回', store.get_resource('hosting', evil).external_id == evil)
check('注入串不出现在任何 SQL 文本里',
      all(evil not in s for s, _ in conn.dml()))
check('注入串作为参数绑定传入',
      any(evil in p for _, p in conn.dml()))

print('== 3. 重复标识与真删 ==')
expect_raises('重复 external_id 抛 DuplicateResource', DuplicateResource,
              lambda: store.add_resource_with_audit(
                  'hosting', evil, 'u3', 't1', 'u3'))
check('重复登记不改变归属',
      store.get_resource('hosting', evil).owner_subject == 'u2')
check('重复登记触发事务回滚', conn.rollbacks >= 1)
check('原子删除返回 True',
      store.delete_resource_with_audit('hosting', evil, 'u2') is True)
check('真删后台账减少', store.count_resources() == 1)
check('删除生效（rowcount > 0 才提交审计）',
      store.get_resource('hosting', evil) is None)

print('== 4. 事务内故障注入：台账与审计一起回滚 ==')
conn2 = _Connection()
b2 = MysqlBackend(conn2)
s2 = ResourceStore(b2)
conn2.fail_audit_in_tx = True
expect_raises('事务内审计失败抛 StoreError', StoreError,
              lambda: s2.add_resource_with_audit(
                  'hosting', 'fail-1', 'u1', None, 'u1'))
check('故障触发 rollback', conn2.rollbacks == 1,
      f'rollbacks={conn2.rollbacks}')
check('故障未产生 commit', conn2.commits == 0)
check('故障后台账无残留', s2.count_resources() == 0)
check('故障后审计无残留', s2.count_audit() == 0)
check('故障后游标仍全部关闭', all(c.closed for c in conn2.cursors))
conn2.fail_audit_in_tx = False
check('故障后连接可继续写入',
      s2.add_resource_with_audit('hosting', 'ok-1', 'u1', None, 'u1')
      .external_id == 'ok-1')

print('== 5. ControlPlane 走 MysqlBackend 真跑完整闭环 ==')
conn3 = _Connection()
s3 = ResourceStore(MysqlBackend(conn3))
ad3 = _Adapter()
cp = ControlPlane(s3, policy=_Policy(), adapters=_Adapters(ad3))
r3 = cp.provision('hosting', {'domain': 'a.example'}, actor='sub-9', tenant='t1')
check('provision 成功', r3.external_id == 'dbapi-001')
check('provision 产生台账 + 审计',
      s3.count_resources() == 1 and s3.count_audit() == 1)
check('provision 只提交一次', conn3.commits == 1)
check('status 可读',
      cp.status('hosting', r3.external_id, actor='sub-9')['remote']['state'] == 'running')
check('list 可读', len(cp.list_resources(actor='sub-9', tenant='t1')) == 1)
check('deprovision 成功',
      cp.deprovision('hosting', r3.external_id, actor='sub-9') is True)
check('真删无残留', s3.count_resources() == 0)
check('删除审计为 ok', s3.tail_audit()[0].outcome == 'ok')
check('deprovision 后再无提前 commit（共 2 次事务）',
      conn3.commits == 2 and conn3.count('BEGIN') == 2,
      f'commits={conn3.commits} begins={conn3.count("BEGIN")}')

print('== 6. 重复标识不误删真实资源（MysqlBackend 路径） ==')
conn4 = _Connection()
s4 = ResourceStore(MysqlBackend(conn4))
ad4 = _Adapter()
cp4 = ControlPlane(s4, policy=_Policy(), adapters=_Adapters(ad4))
first = cp4.provision('hosting', {}, actor='owner-a')
ad4.fixed_id = first.external_id
expect_raises('重复标识抛 DuplicateExternalId', DuplicateExternalId,
              lambda: cp4.provision('hosting', {}, actor='owner-b'))
check('未调用 deprovision', ad4.deleted == [])
check('原记录归属未变',
      s4.get_resource('hosting', first.external_id).owner_subject == 'owner-a')
check('仍可正常删除',
      cp4.deprovision('hosting', first.external_id, actor='owner-a') is True)

print('== 7. 连接收尾 ==')
for c in (conn, conn2, conn3, conn4):
    MysqlBackend(c).close()
check('MysqlBackend.close() 转发到 DB-API 连接',
      all(c.closed for c in (conn, conn2, conn3, conn4)))

print()
print(f'通过 {_passed} 项，失败 {_failed} 项')
sys.exit(1 if _failed else 0)
