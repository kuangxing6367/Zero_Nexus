# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""业务内核自检：静态边界 + 真 SQLite 端到端闭环。

    python -m control_plane.selftest

不启动 Zeronus 框架、不连真实 MySQL 服务端、不联网。测试用的
policy / adapter / backend 是**本文件内的 `_TestingOnly*` 类**，不导出、
不注入生产路径、不作为默认实现；生产里缺依赖一律抛 DependencyMissing。

覆盖：依赖缺失拒绝、授权拒绝（含非布尔返回值）、适配器缺失与失败、
原子事务提交/回滚、重复 external_id 不误删已有真实资源、真删无残留、
审计脱敏、SQL 参数化、后端能力声明与显式降级、CLI 真跑、源码卫生。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from control_plane.control import (  # noqa: E402
    AdapterUnavailable, AuditWriteError, ControlPlane, ControlPlaneError,
    DependencyMissing, DuplicateExternalId, PolicyDenied, ResourceNotFound,
)
from control_plane.store import (  # noqa: E402
    AuditCommitError, DDL_MYSQL, DDL_SQLITE, DuplicateResource,
    MysqlBackend, ResourceStore, SqliteBackend, StoreError,
    TransactionError, scrub_detail,
)

_passed = 0
_failed = 0


def check(name: str, ok: bool, extra: str = '') -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f'  ok   {name}')
    else:
        _failed += 1
        print(f'  FAIL {name}' + (f'  <- {extra}' if extra else ''))


def expect_raises(name: str, exc_type, fn) -> None:
    try:
        fn()
    except exc_type:
        check(name, True)
    except Exception as exc:
        check(name, False, f'抛出了 {type(exc).__name__}: {exc}')
    else:
        check(name, False, '未抛异常')


# ---------------------------------------------------------------- 测试夹具
# 仅自检使用；不导出、不注册、不作为任何默认实现。

class _TestingOnlyPolicy:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def allowed(self, actor, action, target, scopes):
        self.calls.append((actor, action, dict(target), tuple(scopes)))
        return self.result


class _TestingOnlyAdapter:
    kind = 'hosting'

    def __init__(self):
        self.created = []
        self.deleted = []
        self.seq = 0
        self.fail_provision = False
        self.fail_deprovision = False
        self.fixed_id = None

    def provision(self, spec):
        if self.fail_provision:
            raise RuntimeError('外部系统不可用')
        if self.fixed_id is not None:
            return self.fixed_id
        self.seq += 1
        eid = f'ext-{self.seq:03d}'
        self.created.append((eid, dict(spec)))
        return eid

    def deprovision(self, external_id):
        if self.fail_deprovision:
            raise RuntimeError('外部删除被拒绝')
        self.deleted.append(external_id)

    def status(self, external_id):
        return {'state': 'running', 'external_id': external_id}


class _TestingOnlyAdapters:
    def __init__(self, adapter=None):
        self.map = {adapter.kind: adapter} if adapter else {}

    def resolve(self, kind):
        return self.map.get(kind)


class _TestingOnlyBrokenStore(ResourceStore):
    """原子写入失败（模拟台账不可写），用于验证真实资源回滚。"""

    def add_resource_with_audit(self, *a, **kw):
        raise StoreError('模拟台账原子写失败')


class _TestingOnlyFailAuditBackend(SqliteBackend):
    """事务内审计 INSERT 失败，用于真验证事务回滚（不是抛异常假装）。

    只拦事务内的审计写入，事务外的补偿审计仍可落库，这样能真验证
    「原子失败 → 整体回滚 → 补偿审计独立写入」这条完整路径。
    """

    def __init__(self, path=':memory:'):
        super().__init__(path)
        self.fail = False

    def insert(self, sql, params=None):
        if self.fail and self.in_transaction and 'rc_audit' in sql:
            raise sqlite3.OperationalError('模拟事务内审计表不可写')
        return super().insert(sql, params)


class _TestingOnlyNoTxBackend(SqliteBackend):
    """声明不支持事务，用于验证显式降级而非静默假装原子。"""

    supports_transactions = False

    def __init__(self, path=':memory:'):
        super().__init__(path)
        self.fail_audit = False

    def insert(self, sql, params=None):
        if self.fail_audit and 'rc_audit' in sql:
            raise sqlite3.OperationalError('模拟审计表不可写')
        return super().insert(sql, params)


def _new_store():
    store = ResourceStore(SqliteBackend(':memory:'))
    store.install()
    return store


def _new_cp(policy_result=True, store=None, adapter=None):
    adapter = adapter or _TestingOnlyAdapter()
    cp = ControlPlane(store or _new_store(),
                      policy=_TestingOnlyPolicy(policy_result),
                      adapters=_TestingOnlyAdapters(adapter))
    return cp, adapter, cp._store


# ---------------------------------------------------------------- 1. 源码卫生
print('== 1. 独立性与源码卫生 ==')
_src_files = ('__init__.py', 'models.py', 'store.py', 'control.py', 'cli.py')
_src = {}
for fn in _src_files:
    with open(os.path.join(ROOT, 'control_plane', fn), encoding='utf-8') as f:
        _src[fn] = f.read()
_all_src = '\n'.join(_src.values())

check('全部交付源码带 SPDX 双协议头',
      all('SPDX-License-Identifier: MIT OR Apache-2.0' in v for v in _src.values()))
check('零框架依赖：不 import core / software / zernus',
      not re.search(r'^\s*(from\s+(core|software|service|zernus)\b|import\s+(core|software|service|zernus)\b)',
                    _all_src, re.M))
check('源码无硬编码生产 IP',
      not re.search(r'\b(?!127\.0\.0\.1|0\.0\.0\.0)(\d{1,3}\.){3}\d{1,3}\b', _all_src))
check('源码无明文口令 / 密钥赋值',
      not re.search(r"(password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
                    _all_src, re.I))
check('源码不含授权码 / 商业许可校验',
      not any(k in _all_src for k in ('authcode', '商业授权', '禁止二次开发', 'license_check')))
check('DDL 无软删标记 / 归档表',
      not any(k in '\n'.join(DDL_SQLITE + DDL_MYSQL)
              for k in ('deleted_at', 'is_deleted', 'archive')))
check('SQL 不带库名前缀 / 不跨库',
      'USE ' not in _all_src and not re.search(r'`\w+`\s*\.\s*`', _all_src))
check('DDL 只声明两张表',
      all(t in '\n'.join(DDL_SQLITE) for t in ('rc_resources', 'rc_audit'))
      and len([s for s in DDL_SQLITE if 'CREATE TABLE' in s]) == 2)
check('MySQL DDL 为 5.7 风格且带唯一约束',
      'AUTO_INCREMENT' in DDL_MYSQL[0] and 'UNIQUE KEY' in DDL_MYSQL[0])
check('持久化层提供事务与原子写入口',
      'def transaction(' in _src['store.py']
      and 'add_resource_with_audit' in _src['store.py']
      and 'delete_resource_with_audit' in _src['store.py'])
check('后端能力对外显式声明（不假装原子）',
      'atomic_audit' in _src['store.py'] and 'supports_transactions' in _src['store.py'])
check('编排层区分重复标识与写入失败',
      'DuplicateResource' in _src['control.py']
      and 'AuditCommitError' in _src['control.py'])

# ---------------------------------------------------------------- 2. 持久化
print('== 2. 持久化与幂等 ==')
store = _new_store()
check('建表幂等（第二次不报错）', store.install() == len(DDL_SQLITE))
check('health 两张表可用', all(store.health()['tables'].values()))
check('初始资源 / 审计均为 0',
      store.count_resources() == 0 and store.count_audit() == 0)
check('SqliteBackend 声明原子能力', store.capabilities()['atomic_audit'] is True)
check('health 暴露能力字段', store.health()['capabilities']['backend'] == 'SqliteBackend')

evil = "x' OR 1=1 --"
store.add_resource('hosting', evil, 'actor-a', 't1')
check('注入串作为 external_id 被参数化（不改变表记录数）',
      store.count_resources() == 1)
check('注入串能原样取回',
      store.get_resource('hosting', evil).external_id == evil)
expect_raises('重复 external_id 抛 DuplicateResource', DuplicateResource,
              lambda: store.add_resource('hosting', evil, 'actor-b', 't1'))
check('重复登记不覆盖原记录',
      store.get_resource('hosting', evil).owner_subject == 'actor-a')
store.delete_resource('hosting', evil)
check('真删后无残留', store.count_resources() == 0)
check('真删无归档表', len([s for s in DDL_SQLITE if 'CREATE TABLE' in s]) == 2)

print('== 3. 事务边界 ==')
tx_backend = SqliteBackend(':memory:')
tx_store = ResourceStore(tx_backend)
tx_store.install()
check('事务外 in_transaction 为 False', tx_backend.in_transaction is False)


def _nested_tx():
    with tx_backend.transaction():
        with tx_backend.transaction():
            pass


expect_raises('嵌套事务明确拒绝', TransactionError, _nested_tx)
check('嵌套失败后事务状态已复位', tx_backend.in_transaction is False)


def _boom_tx():
    with tx_backend.transaction():
        tx_backend.insert(
            "INSERT INTO `rc_resources` (`kind`,`external_id`,`owner_subject`,`tenant`) "
            "VALUES (%s,%s,%s,%s)", ('hosting', 'rollback-1', 'u1', None))
        raise RuntimeError('boom')


expect_raises('事务内异常向上冒泡', RuntimeError, _boom_tx)
check('事务异常后台账已回滚', tx_store.count_resources() == 0)
check('回滚后连接可用', tx_store.count_resources() == 0)

res_atomic = tx_store.add_resource_with_audit('hosting', 'atomic-1', 'u1', 't1', 'u1')
check('原子写入返回资源记录', res_atomic.external_id == 'atomic-1')
check('原子写入后台账与审计各 1 条',
      tx_store.count_resources() == 1 and tx_store.count_audit() == 1)
check('原子成功审计 outcome=ok 且不带 atomic=False',
      tx_store.tail_audit()[0].outcome == 'ok'
      and tx_store.tail_audit()[0].detail.get('atomic') is not False)
check('原子删除后台账与审计一致',
      tx_store.delete_resource_with_audit('hosting', 'atomic-1', 'u1') is True
      and tx_store.count_resources() == 0 and tx_store.count_audit() == 2)

print('== 3b. 原子写入失败必须整体回滚 ==')
fail_backend = _TestingOnlyFailAuditBackend()
fail_store = ResourceStore(fail_backend)
fail_store.install()
fail_backend.fail = True
expect_raises('审计 INSERT 失败导致原子写失败', StoreError,
              lambda: fail_store.add_resource_with_audit(
                  'hosting', 'atomic-fail-1', 'u1', None, 'u1'))
check('事务回滚后台账无残留', fail_store.count_resources() == 0)
check('事务回滚后审计也无残留', fail_store.count_audit() == 0)
fail_backend.fail = False

print('== 3c. 不支持事务的后端显式降级 ==')
notx_backend = _TestingOnlyNoTxBackend()
notx_store = ResourceStore(notx_backend)
notx_store.install()
check('capabilities 明确 atomic_audit=False',
      notx_store.capabilities()['atomic_audit'] is False)
check('health 也显示 atomic_audit=False',
      notx_store.health()['capabilities']['atomic_audit'] is False)
notx_store.add_resource_with_audit('hosting', 'notx-1', 'u1', None, 'u1')
check('降级路径台账已落地', notx_store.count_resources() == 1)
check('降级路径审计标记 atomic=False',
      notx_store.tail_audit()[0].detail.get('atomic') is False)
notx_backend.fail_audit = True
expect_raises('降级路径审计失败抛 AuditCommitError', AuditCommitError,
              lambda: notx_store.add_resource_with_audit(
                  'hosting', 'notx-2', 'u1', None, 'u1'))
check('AuditCommitError 时台账已落地（不可回滚真实资源）',
      notx_store.count_resources() == 2)

print('== 4. 审计脱敏 ==')
red = scrub_detail({'password': 'hunter2', 'api_key': 'abc123',
                    'note': 'ok', 'nested': {'token': 'zzz', 'v': 1}})
parsed = json.loads(red)
check('敏感键 password 被脱敏', parsed['password'] == '<redacted>')
check('敏感键 api_key 被脱敏', parsed['api_key'] == '<redacted>')
check('嵌套 token 被脱敏', parsed['nested']['token'] == '<redacted>')
check('普通字段保留', parsed['note'] == 'ok' and parsed['nested']['v'] == 1)
check('超长 detail 被截断', len(scrub_detail({'blob': 'x' * 9000})) <= 4000)
check('空 detail 返回空串', scrub_detail(None) == '' and scrub_detail({}) == '')

# ---------------------------------------------------------------- 5. 依赖与授权
print('== 5. fail-closed 与授权 ==')
expect_raises('缺 store 构造即拒绝', DependencyMissing,
              lambda: ControlPlane(None, policy=_TestingOnlyPolicy(),
                                   adapters=_TestingOnlyAdapters()))
expect_raises('缺 policy 构造即拒绝', DependencyMissing,
              lambda: ControlPlane(_new_store(), policy=None,
                                   adapters=_TestingOnlyAdapters()))
expect_raises('缺 adapters 构造即拒绝', DependencyMissing,
              lambda: ControlPlane(_new_store(), policy=_TestingOnlyPolicy(),
                                   adapters=None))
expect_raises('policy 形状不符被拒绝', DependencyMissing,
              lambda: ControlPlane(_new_store(), policy=object(),
                                   adapters=_TestingOnlyAdapters()))

cp, adapter, st = _new_cp(policy_result=False)
expect_raises('策略拒绝抛 PolicyDenied', PolicyDenied,
              lambda: cp.provision('hosting', {'domain': 'a.example'}, actor='u1'))
check('策略拒绝不创建真实资源', adapter.created == [])
check('策略拒绝仍写 denied 审计',
      st.tail_audit()[0].outcome == 'denied')

for fake in (None, 0, 'yes', 1):
    cp2, ad2, _ = _new_cp(policy_result=fake)
    expect_raises(f'非布尔返回值 {fake!r} 一律视为拒绝', PolicyDenied,
                  lambda c=cp2: c.provision('hosting', {}, actor='u1'))
    check(f'非布尔 {fake!r} 未创建资源', ad2.created == [])

cp3, _, _ = _new_cp()
expect_raises('未知 kind 抛 AdapterUnavailable', AdapterUnavailable,
              lambda: cp3.provision('unknown', {}, actor='u1'))
expect_raises('actor 为空被拒绝', ControlPlaneError,
              lambda: cp3.provision('hosting', {}, actor=''))
expect_raises('spec 非 Mapping 被拒绝', ControlPlaneError,
              lambda: cp3.provision('hosting', 'not-a-map', actor='u1'))


class _TestingOnlyDenyAuditStore(ResourceStore):
    def append_audit(self, *a, **kw):
        raise StoreError('模拟审计不可用')


deny_store = _TestingOnlyDenyAuditStore(SqliteBackend(':memory:'))
deny_store.install()
cp_deny = ControlPlane(deny_store, policy=_TestingOnlyPolicy(False),
                       adapters=_TestingOnlyAdapters(_TestingOnlyAdapter()))
expect_raises('审计故障时授权拒绝仍抛 PolicyDenied（不被掩盖）', PolicyDenied,
              lambda: cp_deny.provision('hosting', {}, actor='u1'))

# ---------------------------------------------------------------- 6. 完整闭环
print('== 6. 资源生命周期闭环 ==')
cp, adapter, st = _new_cp()
secret_spec = {'domain': 'a.example', 'password': 'plain-secret-DO-NOT-LOG'}
res = cp.provision('hosting', secret_spec, actor='sub-123', tenant='t1',
                   scopes=('host.create',))
check('provision 返回台账记录', res.kind == 'hosting' and res.owner_subject == 'sub-123')
check('台账写入 1 条', st.count_resources() == 1)
check('真实资源被创建 1 次', len(adapter.created) == 1)
check('策略收到 scopes', cp._policy.calls[0][3] == ('host.create',))
check('spec 原样交给适配器（内核不改凭据）',
      adapter.created[0][1]['password'] == 'plain-secret-DO-NOT-LOG')
check('审计未记录 spec 明文',
      'plain-secret-DO-NOT-LOG' not in json.dumps(
          [e.detail for e in st.tail_audit()], ensure_ascii=False))
check('成功审计为 ok', st.tail_audit()[0].outcome == 'ok')

state = cp.status('hosting', res.external_id, actor='sub-123')
check('status 返回远端状态', state['remote']['state'] == 'running')
check('list 按 tenant 隔离',
      len(cp.list_resources(actor='sub-123', tenant='t1')) == 1
      and len(cp.list_resources(actor='sub-123', tenant='other')) == 0)
check('tail_audit 可读', len(cp.tail_audit(actor='sub-123', limit=10)) >= 1)

ok = cp.deprovision('hosting', res.external_id, actor='sub-123')
check('deprovision 返回 True', ok is True)
check('真实资源被物理删除', res.external_id in adapter.deleted)
check('台账真删无残留', st.count_resources() == 0)
check('删除后不可查询', st.get_resource('hosting', res.external_id) is None)
check('删除产生 ok 审计',
      any(e.action == 'deprovision' and e.outcome == 'ok' for e in st.tail_audit()))
expect_raises('删除后再删抛 ResourceNotFound', ResourceNotFound,
              lambda: cp.deprovision('hosting', res.external_id, actor='sub-123'))

print('== 7. 重复 external_id 绝不误删已有资源 ==')
cp8, ad8, st8 = _new_cp()
first = cp8.provision('hosting', {}, actor='owner-a')
ad8.fixed_id = first.external_id
expect_raises('适配器复用已登记标识抛 DuplicateExternalId', DuplicateExternalId,
              lambda: cp8.provision('hosting', {}, actor='owner-b'))
check('未调用 deprovision（已有真实资源未被删）', ad8.deleted == [])
check('原台账记录仍在且归属未变',
      st8.get_resource('hosting', first.external_id).owner_subject == 'owner-a')
check('重复标识写 failed 审计',
      any(e.outcome == 'failed' and e.detail.get('reason') == 'duplicate_external_id'
          for e in st8.tail_audit()))
check('原资源仍可被正常删除',
      cp8.deprovision('hosting', first.external_id, actor='owner-a') is True)

print('== 8. 失败路径与回滚 ==')
cp4, ad4, st4 = _new_cp()
ad4.fail_provision = True
expect_raises('适配器开通失败抛错', ControlPlaneError,
              lambda: cp4.provision('hosting', {}, actor='u1'))
check('开通失败不写台账', st4.count_resources() == 0)
check('开通失败写 failed 审计', st4.tail_audit()[0].outcome == 'failed')

ad4.fail_provision = False
broken = _TestingOnlyBrokenStore(SqliteBackend(':memory:'))
broken.install()
cp5 = ControlPlane(broken, policy=_TestingOnlyPolicy(),
                   adapters=_TestingOnlyAdapters(ad4))
expect_raises('台账原子写失败抛错', ControlPlaneError,
              lambda: cp5.provision('hosting', {}, actor='u1'))
check('台账失败后回滚真实资源', len(ad4.deleted) >= 1)
check('台账失败不留台账记录', broken.count_resources() == 0)
check('台账失败写 failed 审计', broken.tail_audit()[0].outcome == 'failed')

cp6, ad6, st6 = _new_cp()
r6 = cp6.provision('hosting', {}, actor='u1')
ad6.fail_deprovision = True
expect_raises('外部删除失败抛错', ControlPlaneError,
              lambda: cp6.deprovision('hosting', r6.external_id, actor='u1'))
check('外部删除失败时台账保留（可重试）', st6.count_resources() == 1)
ad6.fail_deprovision = False
check('重试删除成功', cp6.deprovision('hosting', r6.external_id, actor='u1') is True)

# 原子后端：审计 INSERT 失败 → 台账与审计一起回滚，真实资源被清理。
atomic_fail_backend = _TestingOnlyFailAuditBackend()
atomic_fail_store = ResourceStore(atomic_fail_backend)
atomic_fail_store.install()
atomic_fail_ad = _TestingOnlyAdapter()
cp9 = ControlPlane(atomic_fail_store, policy=_TestingOnlyPolicy(),
                   adapters=_TestingOnlyAdapters(atomic_fail_ad))
atomic_fail_backend.fail = True
expect_raises('原子写失败抛 ControlPlaneError', ControlPlaneError,
              lambda: cp9.provision('hosting', {}, actor='u1'))
check('原子写失败后台账已回滚', atomic_fail_store.count_resources() == 0)
check('原子写失败后真实资源已被清理', len(atomic_fail_ad.deleted) == 1)
atomic_fail_backend.fail = False
check('清理补偿的 failed 审计可写',
      any(e.outcome == 'failed' for e in atomic_fail_store.tail_audit()))

# 非原子后端：审计失败时台账已落地，抛 AuditWriteError 且绝不回滚真实资源。
notx2_backend = _TestingOnlyNoTxBackend()
notx2_store = ResourceStore(notx2_backend)
notx2_store.install()
notx2_ad = _TestingOnlyAdapter()
cp10 = ControlPlane(notx2_store, policy=_TestingOnlyPolicy(),
                    adapters=_TestingOnlyAdapters(notx2_ad))
notx2_backend.fail_audit = True
audit_msg = ''
try:
    cp10.provision('hosting', {}, actor='u1')
except AuditWriteError as exc:
    audit_msg = str(exc)
check('非原子后端审计失败抛 AuditWriteError', bool(audit_msg))
check('审计失败时台账已落地', notx2_store.count_resources() == 1)
check('审计失败未误删真实资源', notx2_ad.deleted == [])
check('审计失败异常明确提示核对台账与勿盲目重建',
      '核对台账' in audit_msg and '不要盲目重建' in audit_msg, audit_msg)

# ---------------------------------------------------------------- 9. 后端与 CLI
print('== 9. 后端与 CLI 真跑 ==')
check('MysqlBackend 提供 MySQL 方言 DDL',
      MysqlBackend(None).ddl() == DDL_MYSQL)
check('MysqlBackend 声明原子能力',
      MysqlBackend.supports_transactions is True)
check('SqliteBackend 提供 SQLite 方言 DDL',
      SqliteBackend(':memory:').ddl() == DDL_SQLITE)

tmpdir = tempfile.mkdtemp(prefix='cp-selftest-')
db = os.path.join(tmpdir, 'ctl.db')
env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get('PYTHONPATH', ''))


def cli(*args):
    return subprocess.run([sys.executable, '-m', 'control_plane.cli',
                           '--db', db, *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)


r_init = cli('init')
check('CLI init 退出码 0', r_init.returncode == 0, r_init.stderr[-200:])
try:
    body = json.loads(r_init.stdout)
    check('CLI init 返回合法 JSON 且两表可用',
          all(body['health']['tables'].values()))
    check('CLI health 暴露原子能力',
          body['health']['capabilities']['atomic_audit'] is True)
except ValueError:
    check('CLI init 返回合法 JSON 且两表可用', False, r_init.stdout[-200:])
    check('CLI health 暴露原子能力', False)

r_health = cli('health')
check('CLI health 退出码 0', r_health.returncode == 0)
r_list = cli('list', '--tenant', 't1')
check('CLI list 退出码 0 且返回列表',
      r_list.returncode == 0 and isinstance(json.loads(r_list.stdout)['resources'], list))
r_audit = cli('audit', '--limit', '5')
check('CLI audit 退出码 0 且返回列表',
      r_audit.returncode == 0 and isinstance(json.loads(r_audit.stdout)['audit_events'], list))
check('CLI 未提供 provision 子命令（不塞演示实现）',
      'provision' not in cli('--help').stdout)

print()
print(f'通过 {_passed} 项，失败 {_failed} 项')
sys.exit(1 if _failed else 0)
