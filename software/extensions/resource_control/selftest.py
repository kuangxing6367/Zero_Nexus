# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 独立自检（不启动框架、不连数据库、不联网）

    python software/extensions/resource_control/selftest.py

验证的是**边界与失败语义**，不是任何业务实现：
- 契约数据结构与 Protocol 形状
- 槽位默认拒绝（fail-closed），不静默返回 None / False / 密文
- 显式绑定 / 解除绑定 / 错配实现被拒
- schema 极薄、幂等、不跨库、不 JOIN 身份表、真删无软删
- 装配入口 register() 无副作用、状态接口只读
- 源码不含硬编码生产 IP、密钥或明文口令
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from software.extensions.resource_control import contracts as c
from software.extensions.resource_control import schema as s
from software.extensions.resource_control import slots as sl
from software.extensions.resource_control import main as m

passed = 0
failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}  {detail}')


def must_raise(name, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        check(name, True)
    except Exception as e:
        check(name, False, f'异常类型不符: {type(e).__name__}: {e}')
    else:
        check(name, False, '未抛异常（可能静默放行）')


print('== 1. 契约层 ==')
p = c.Principal(subject='sub-1', tenant='t-1', scopes=('resource:read',))
check('Principal 为不可变值对象', p.subject == 'sub-1' and p.has_scope('resource:read'))
check('Principal 默认无 scopes', not p.has_scope('resource:write'))
check('Principal 不提供查库能力',
      not any(hasattr(p, k) for k in ('db', 'conn', 'query', 'sql')))
r = c.ResourceRef(kind='site', external_id='ext-9', owner_subject='sub-1')
check('ResourceRef 只含标识', r.kind == 'site' and r.external_id == 'ext-9')
check('契约版本可见', c.CONTRACT_VERSION == 1)
check('槽位集合恰为 5 个', c.SLOT_NAMES == frozenset({
    'identity', 'policy', 'vault', 'adapters', 'audit'}))

print('== 2. 未配置槽位一律拒绝（fail-closed） ==')
reg = sl.SlotRegistry()
st = reg.status()
check('初始状态全部为 refusing',
      all(v['mode'] == 'refusing' and not v['configured'] for v in st['slots'].values()))
must_raise('identity.resolve 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_IDENTITY).resolve, {})
must_raise('policy.decide 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_POLICY).decide, p, 'resource:create')
must_raise('vault.seal 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_VAULT).seal, 'plaintext', {})
must_raise('vault.open 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_VAULT).open, 'token', {})
must_raise('adapters.resolve 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_ADAPTERS).resolve, 'site')
must_raise('adapters.kinds 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_ADAPTERS).kinds)
must_raise('audit.record 拒绝', sl.SlotUnavailable,
           reg.get(c.SLOT_AUDIT).record,
           c.AuditEvent('sub-1', 'create', 'site', 'ext-9', 'ok'))
must_raise('require() 未配置抛错', sl.SlotUnavailable, reg.require, c.SLOT_VAULT)

print('== 3. 显式绑定与错配拒绝 ==')
class _Identity:
    def resolve(self, request):
        return c.Principal(subject=request.get('sub', 'anon'))

class _Policy:
    def decide(self, principal, action, target=None):
        return c.Decision(allowed=principal.has_scope(action), reason='scope check')

class _Vault:
    def seal(self, plaintext, context):
        return 'opaque:' + str(len(plaintext))
    def open(self, token, context):
        if not token.startswith('opaque:'):
            raise ValueError('bad token')
        return 'x' * int(token.split(':')[1])

class _Adapters:
    def resolve(self, kind):
        return None
    def kinds(self):
        return ['site']

class _Audit:
    def __init__(self):
        self.events = []
    def record(self, event):
        self.events.append(event)

reg.bind(c.SLOT_IDENTITY, _Identity())
reg.bind(c.SLOT_POLICY, _Policy())
reg.bind(c.SLOT_VAULT, _Vault())
reg.bind(c.SLOT_ADAPTERS, _Adapters())
reg.bind(c.SLOT_AUDIT, _Audit())
check('绑定后状态全部 configured',
      all(v['configured'] for v in reg.status()['slots'].values()))
check('identity 可解析 claims',
      reg.get(c.SLOT_IDENTITY).resolve({'sub': 'alice'}).subject == 'alice')
check('policy 可判定',
      reg.get(c.SLOT_POLICY).decide(
          c.Principal('x', scopes=('resource:create',)), 'resource:create').allowed)
check('policy 拒绝无 scope',
      not reg.get(c.SLOT_POLICY).decide(c.Principal('x'), 'resource:create').allowed)
check('vault seal/open 可往返',
      reg.get(c.SLOT_VAULT).open(reg.get(c.SLOT_VAULT).seal('abcd', {}), {}) == 'xxxx')
must_raise('错配实现被拒绝', sl.SlotContractError, reg.bind, c.SLOT_VAULT, object())
must_raise('未知槽位被拒绝', sl.SlotContractError, reg.bind, 'unknown', object())
reg.unbind(c.SLOT_VAULT)
check('unbind 后立即回到拒绝态', not reg.is_configured(c.SLOT_VAULT))
must_raise('unbind 后调用抛错', sl.SlotUnavailable,
           reg.get(c.SLOT_VAULT).seal, 'p', {})

print('== 4. schema：极薄 / 幂等 / 不跨库 / 真删 ==')
check('只有两张表', s.ALL_TABLES == ('rc_resources', 'rc_audit'))
check('DDL 条数与表数一致', len(s.SCHEMA_STATEMENTS) == 2)
ddl_all = '\n'.join(s.SCHEMA_STATEMENTS).upper()
check('DDL 全部 IF NOT EXISTS（幂等）',
      ddl_all.count('CREATE TABLE IF NOT EXISTS') == 2)
check('DDL 无 USE / 跨库限定名',
      'USE ' not in ddl_all
      and not re.search(r'`[a-zA-Z0-9_]+`\.`[a-zA-Z0-9_]+`', '\n'.join(s.SCHEMA_STATEMENTS)))
check('DDL 不 JOIN 框架身份表',
      not any(t in ddl_all for t in ('ADMIN_USERS', 'PERM_', ' USERS', 'JOIN')))
check('DDL 无软删列',
      not any(k in ddl_all for k in ('DELETED_AT', 'IS_DELETED', 'ARCHIVE')))
check('审计表不设凭据列',
      not any(k in ddl_all for k in ('PASSWORD', 'SECRET', 'TOKEN', 'PRIVATE_KEY')))
check('资源表不存连接串/凭据',
      not any(k in ddl_all for k in ('CONN_STR', 'DSN', 'PASSWORD', 'API_KEY')))


class _FakeCtx:
    """假 ctx：只记录 DDL，不连任何数据库。"""
    def __init__(self):
        self.ddls = []
        self.logs = []
    def create_table(self, ddl):
        self.ddls.append(ddl)
    def db_query_one(self, sql, params=None):
        return None
    def log(self, msg, level='info'):
        self.logs.append(msg)


fake = _FakeCtx()
n1 = s.install_schema(fake)
n2 = s.install_schema(fake)
check('install_schema 返回 DDL 条数', n1 == 2)
check('重复安装不产生额外副作用（幂等）', len(fake.ddls) == 4 and n2 == 2)
status = s.schema_status(fake)
check('schema_status 只读且报告两表', set(status['tables']) == set(s.ALL_TABLES))
check('schema_status 不含实现细节', 'schema_version' in status)

print('== 5. 装配入口无副作用 ==')
class _FakeServices:
    def __init__(self):
        self.items = {}
    def register(self, name, service):
        self.items[name] = service

class _FakeFramework:
    def __init__(self, enabled):
        self.config = {'resource_control': {'enabled': enabled}}
        self.services = _FakeServices()

class _FakePluginCtx(_FakeCtx):
    def __init__(self, enabled):
        super().__init__()
        self._framework = _FakeFramework(enabled)
        self.routes = []
    def register_api(self, path, handler, methods=None, auth=True, description=None):
        self.routes.append((path, methods, auth))


ctx_off = _FakePluginCtx(False)
ret = m.register(ctx_off)
check('默认关闭时不装配、不注册路由', ret is None and not ctx_off.routes
      and not ctx_off._framework.services.items)

ctx_on = _FakePluginCtx(True)
reg2 = m.register(ctx_on)
check('启用后装配注册表到 fw.services',
      isinstance(reg2, sl.SlotRegistry)
      and 'resource_control' in ctx_on._framework.services.items)
check('只读状态路由复用框架鉴权',
      len(ctx_on.routes) == 1 and ctx_on.routes[0][2] is True
      and ctx_on.routes[0][1] == ['GET'])
status_payload = m._status_handler({})
check('状态接口只读且不含实现细节',
      status_payload['ok'] is True
      and all('impl' not in v for v in status_payload['slots'].values()))
install_status = m.install(ctx_on)
check('install(ctx) 幂等可用', set(install_status['tables']) == set(s.ALL_TABLES))
m.unregister()
check('unregister 清空引用', m._registry is None)

print('== 6. 开源与源码卫生 ==')
root = os.path.dirname(os.path.abspath(__file__))
sources = {}
for fn in ('contracts.py', 'slots.py', 'schema.py', 'main.py', 'selftest.py', '__init__.py'):
    with open(os.path.join(root, fn), encoding='utf-8') as f:
        sources[fn] = f.read()
# 卫生检查只针对交付源码，排除 selftest.py 自身（否则规则里的关键词会自指误报）
impl_src = '\n'.join(v for k, v in sources.items() if k != 'selftest.py')
check('全部源码带 SPDX 许可头',
      all('SPDX-License-Identifier: MIT OR Apache-2.0' in v for v in sources.values()))
check('源码无硬编码生产 IP',
      not re.search(r'\b(?!127\.0\.0\.1|0\.0\.0\.0)(\d{1,3}\.){3}\d{1,3}\b', impl_src))
check('源码无明文口令/密钥赋值',
      not re.search(r"(password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
                    impl_src, re.I))
check('源码不含闭源/授权码校验',
      not any(k in impl_src for k in ('authcode', '商业授权', '禁止二次开发', 'license_check')))

print()
print(f'通过 {passed} 项，失败 {failed} 项')
sys.exit(1 if failed else 0)
