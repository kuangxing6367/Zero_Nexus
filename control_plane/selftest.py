# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""业务内核自检：静态边界 + 真 SQLite 端到端闭环。

    python -m control_plane.selftest

不启动 Zeronus 框架、不连 MySQL、不联网。测试用的 policy / adapter 是
**本文件内的 `_TestingOnly*` 类**，不导出、不注入生产路径、不作为默认实现；
生产里缺依赖一律抛 DependencyMissing。

覆盖：依赖缺失拒绝、授权拒绝（含非布尔返回值）、适配器缺失与失败、
台账写入失败回滚、真删无残留、审计脱敏、SQL 参数化、CLI 真跑、源码卫生。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from control_plane.control import (  # noqa: E402
    AdapterUnavailable, AuditWriteError, ControlPlane, ControlPlaneError,
    DependencyMissing, PolicyDenied, ResourceNotFound,
)
from control_plane.store import (  # noqa: E402
    DDL_MYSQL, DDL_SQLITE, MysqlBackend, ResourceStore, SqliteBackend,
    StoreError, scrub_detail,
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

    def provision(self, spec):
        if self.fail_provision:
            raise RuntimeError('外部系统不可用')
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
    """台账写入失败，用于验证真实资源回滚。"""

    def add_resource(self, *a, **kw):
        raise StoreError('模拟台账写失败')


class _TestingOnlyBrokenAuditStore(ResourceStore):
    def append_audit(self, *a, **kw):
        raise StoreError('模拟审计写失败')


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

# ---------------------------------------------------------------- 2. 持久化
print('== 2. 持久化与幂等 ==')
store = _new_store()
check('建表幂等（第二次不报错）', store.install() == len(DDL_SQLITE))
check('health 两张表可用', all(store.health()['tables'].values()))
check('初始资源 / 审计均为 0',
      store.count_resources() == 0 and store.count_audit() == 0)

evil = "x' OR 1=1 --"
store.add_resource('hosting', evil, 'actor-a', 't1')
check('注入串作为 external_id 被参数化（不改变表记录数）',
      store.count_resources() == 1)
check('注入串能原样取回',
      store.get_resource('hosting', evil).external_id == evil)
store.delete_resource('hosting', evil)
check('真删后无残留', store.count_resources() == 0)
check('真删无归档表', len([s for s in DDL_SQLITE if 'CREATE TABLE' in s]) == 2)

print('== 3. 审计脱敏 ==')
red = scrub_detail({'password': 'hunter2', 'api_key': 'abc123',
                    'note': 'ok', 'nested': {'token': 'zzz', 'v': 1}})
parsed = json.loads(red)
check('敏感键 password 被脱敏', parsed['password'] == '<redacted>')
check('敏感键 api_key 被脱敏', parsed['api_key'] == '<redacted>')
check('嵌套 token 被脱敏', parsed['nested']['token'] == '<redacted>')
check('普通字段保留', parsed['note'] == 'ok' and parsed['nested']['v'] == 1)
check('超长 detail 被截断', len(scrub_detail({'blob': 'x' * 9000})) <= 4000)
check('空 detail 返回空串', scrub_detail(None) == '' and scrub_detail({}) == '')

# ---------------------------------------------------------------- 4. 依赖与授权
print('== 4. fail-closed 与授权 ==')
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

# ---------------------------------------------------------------- 5. 完整闭环
print('== 5. 资源生命周期闭环 ==')
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

print('== 6. 失败路径与回滚 ==')
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
expect_raises('台账写入失败抛错', ControlPlaneError,
              lambda: cp5.provision('hosting', {}, actor='u1'))
check('台账失败后回滚真实资源', len(ad4.deleted) >= 1)
check('台账失败不留台账记录', broken.count_resources() == 0)

cp6, ad6, st6 = _new_cp()
r6 = cp6.provision('hosting', {}, actor='u1')
ad6.fail_deprovision = True
expect_raises('外部删除失败抛错', ControlPlaneError,
              lambda: cp6.deprovision('hosting', r6.external_id, actor='u1'))
check('外部删除失败时台账保留（可重试）', st6.count_resources() == 1)
ad6.fail_deprovision = False
check('重试删除成功', cp6.deprovision('hosting', r6.external_id, actor='u1') is True)

bad_audit = _TestingOnlyBrokenAuditStore(SqliteBackend(':memory:'))
bad_audit.install()
cp7 = ControlPlane(bad_audit, policy=_TestingOnlyPolicy(),
                   adapters=_TestingOnlyAdapters(_TestingOnlyAdapter()))
audit_msg = ''
try:
    cp7.provision('hosting', {}, actor='u1')
except AuditWriteError as exc:
    audit_msg = str(exc)
check('审计写入失败抛 AuditWriteError', bool(audit_msg))
check('审计失败时资源与台账已落地', bad_audit.count_resources() == 1)
check('审计失败异常明确提示勿盲目重试',
      '核对台账' in audit_msg and '重试' in audit_msg, audit_msg)

# ---------------------------------------------------------------- 7. 多后端与 CLI
print('== 7. 后端与 CLI 真跑 ==')
check('MysqlBackend 提供 MySQL 方言 DDL',
      MysqlBackend(None).ddl() == DDL_MYSQL)
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
except ValueError:
    check('CLI init 返回合法 JSON 且两表可用', False, r_init.stdout[-200:])

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
