# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""资源生命周期编排 —— 业务内核本体。

这里只做「流程」，不做「策略」：

- 授权怎么判、claims 怎么验、凭据怎么加密、外部系统怎么开通，
  全部由构造参数传入的普通对象决定，本模块一行实现都不提供；
- 内核不接触凭据明文：``spec`` 原样交给适配器，审计 detail 只放
  kind / external_id 这类元数据，不写 spec 全文；
- 缺依赖、授权拒绝、适配器缺失一律明确抛错，不静默降级；
- 删除即真删，失败时台账不删，保证可重试。

调用形态（壳层负责把网关注入的 claims 摊平成 actor / tenant / scopes）：

    store = ResourceStore(SqliteBackend('data/controlplane.db'))
    store.install()
    cp = ControlPlane(store, policy=my_policy, adapters=my_adapters)
    cp.provision('hosting', spec, actor='sub-123', tenant='t1', scopes=('host.create',))
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from control_plane.models import AuditEvent, Resource
from control_plane.store import ResourceStore, StoreError

logger = logging.getLogger('control_plane')

ACTIONS = ('provision', 'deprovision', 'status', 'list', 'audit')


class ControlPlaneError(RuntimeError):
    """业务内核失败的基类。"""


class DependencyMissing(ControlPlaneError):
    """必需依赖未注入。构造时或调用时立即拒绝，不提供默认实现。"""


class PolicyDenied(ControlPlaneError):
    """授权策略未放行。"""


class AdapterUnavailable(ControlPlaneError):
    """该 kind 没有对应适配器，或适配器接口不完整。"""


class ResourceNotFound(ControlPlaneError):
    """台账里没有这条资源。"""


class AuditWriteError(ControlPlaneError):
    """审计写入失败。

    注意：审计与资源写入并非同一事务，抛出本异常时业务动作可能已经落地。
    调用方应按 external_id 查询台账后再决定是否重试，**不要盲目重建资源**。
    """


def _check_shape(obj, methods: Sequence[str], label: str) -> None:
    for name in methods:
        if not callable(getattr(obj, name, None)):
            raise DependencyMissing(f'{label} 缺少可调用方法 {name}()')


class ControlPlane:
    """资源控制面。三个依赖都是普通对象，不做注册、不做发现。"""

    def __init__(self, store: ResourceStore, *, policy, adapters):
        if store is None or policy is None or adapters is None:
            missing = [n for n, v in (('store', store), ('policy', policy),
                                      ('adapters', adapters)) if v is None]
            raise DependencyMissing(f'必需依赖未注入: {", ".join(missing)}')
        _check_shape(store, ('add_resource', 'get_resource', 'delete_resource',
                             'list_resources', 'append_audit', 'tail_audit'), 'store')
        _check_shape(policy, ('allowed',), 'policy')
        _check_shape(adapters, ('resolve',), 'adapters')
        self._store = store
        self._policy = policy
        self._adapters = adapters

    # ---- 内部 ----

    def _audit(self, actor: str, action: str, target_kind: str,
               target_ref: str, outcome: str,
               detail: Mapping[str, Any] | None = None) -> None:
        try:
            self._store.append_audit(actor, action, target_kind, target_ref,
                                     outcome, detail)
        except StoreError as exc:
            raise AuditWriteError(
                f'{exc}；业务动作可能已落地，请按外部标识核对台账后再决定重试'
            ) from exc

    def _authorize(self, actor: str, action: str,
                   target: Mapping[str, Any], scopes: Sequence[str]) -> None:
        # 只接受布尔真值 True：返回 None / 0 / 字符串一律视为未放行。
        allowed = self._policy.allowed(actor, action, target, tuple(scopes or ()))
        if allowed is not True:
            self._audit(actor, action, str(target.get('kind') or '-'),
                        str(target.get('external_id') or '-'), 'denied',
                        {'reason': 'policy_not_allowed'})
            raise PolicyDenied(f'策略未放行: actor={actor} action={action}')

    def _adapter(self, kind: str):
        adapter = self._adapters.resolve(kind)
        if adapter is None:
            raise AdapterUnavailable(f'资源类型 [{kind}] 没有可用适配器')
        _check_shape(adapter, ('provision', 'deprovision', 'status'),
                     f'adapter[{kind}]')
        return adapter

    @staticmethod
    def _check_actor(actor: str) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ControlPlaneError('actor 必须是非空字符串（由网关注入）')

    # ---- 业务动作 ----

    def provision(self, kind: str, spec: Mapping[str, Any], *, actor: str,
                  tenant: str | None = None,
                  scopes: Sequence[str] = ()) -> Resource:
        """创建资源：授权 → 适配器开通 → 台账登记 → 审计。

        台账登记失败会尽力回滚已创建的真实资源，避免孤儿资源。
        """
        self._check_actor(actor)
        if not isinstance(kind, str) or not kind.strip():
            raise ControlPlaneError('kind 必须是非空字符串')
        if not isinstance(spec, Mapping):
            raise ControlPlaneError('spec 必须是 Mapping')

        target = {'kind': kind}
        self._authorize(actor, 'provision', target, scopes)
        adapter = self._adapter(kind)

        try:
            external_id = adapter.provision(spec)
        except Exception as exc:
            self._audit(actor, 'provision', kind, '-', 'failed',
                        {'reason': 'adapter_provision_failed'})
            raise ControlPlaneError(f'适配器开通失败: {exc}') from exc
        if not isinstance(external_id, str) or not external_id.strip():
            self._audit(actor, 'provision', kind, '-', 'failed',
                        {'reason': 'invalid_external_id'})
            raise ControlPlaneError('适配器必须返回非空的 external_id 字符串')

        try:
            resource = self._store.add_resource(kind, external_id, actor, tenant)
        except StoreError as exc:
            # 真实资源已创建但台账没写成：尽力回滚，不留下看不见的孤儿。
            try:
                adapter.deprovision(external_id)
            except Exception as rollback_exc:
                logger.error('台账写入失败后回滚真实资源也失败: %s', rollback_exc)
            self._audit(actor, 'provision', kind, external_id, 'failed',
                        {'reason': 'store_write_failed'})
            raise ControlPlaneError(f'资源登记失败，已尝试回滚: {exc}') from exc

        self._audit(actor, 'provision', kind, external_id, 'ok')
        return resource

    def deprovision(self, kind: str, external_id: str, *, actor: str,
                    scopes: Sequence[str] = ()) -> bool:
        """真删：授权 → 外部系统物理删除 → 台账物理 DELETE → 审计。

        顺序是刻意的：外部删除失败时台账保留，运维可重试；
        台账删除失败时真实资源已消失，审计里留 failed 供人工核对。
        """
        self._check_actor(actor)
        record = self._store.get_resource(kind, external_id)
        if record is None:
            self._audit(actor, 'deprovision', kind, external_id, 'failed',
                        {'reason': 'not_found'})
            raise ResourceNotFound(f'资源不存在: {kind}/{external_id}')

        target = {'kind': kind, 'external_id': external_id,
                  'owner_subject': record.owner_subject}
        self._authorize(actor, 'deprovision', target, scopes)
        adapter = self._adapter(kind)

        try:
            adapter.deprovision(external_id)
        except Exception as exc:
            self._audit(actor, 'deprovision', kind, external_id, 'failed',
                        {'reason': 'adapter_deprovision_failed'})
            raise ControlPlaneError(f'外部删除失败，台账已保留可重试: {exc}') from exc

        if not self._store.delete_resource(kind, external_id):
            self._audit(actor, 'deprovision', kind, external_id, 'failed',
                        {'reason': 'store_delete_failed'})
            raise StoreError('台账物理删除未生效，请人工核对')

        self._audit(actor, 'deprovision', kind, external_id, 'ok')
        return True

    def status(self, kind: str, external_id: str, *, actor: str,
               scopes: Sequence[str] = ()) -> Mapping[str, Any]:
        self._check_actor(actor)
        record = self._store.get_resource(kind, external_id)
        if record is None:
            raise ResourceNotFound(f'资源不存在: {kind}/{external_id}')
        self._authorize(actor, 'status',
                        {'kind': kind, 'external_id': external_id}, scopes)
        adapter = self._adapter(kind)
        try:
            remote = adapter.status(external_id)
        except Exception as exc:
            raise ControlPlaneError(f'状态查询失败: {exc}') from exc
        return {
            'kind': record.kind,
            'external_id': record.external_id,
            'owner_subject': record.owner_subject,
            'tenant': record.tenant,
            'remote': dict(remote) if isinstance(remote, Mapping) else {},
        }

    def list_resources(self, *, actor: str, tenant: str | None = None,
                       kind: str | None = None, limit: int = 100,
                       scopes: Sequence[str] = ()) -> list[Resource]:
        """按 tenant 强制隔离；按 actor 过滤属于授权策略，不在本层写死。"""
        self._check_actor(actor)
        self._authorize(actor, 'list', {'kind': kind or '-'}, scopes)
        return self._store.list_resources(tenant=tenant, kind=kind, limit=limit)

    def tail_audit(self, *, actor: str, limit: int = 50,
                   scopes: Sequence[str] = ()) -> list[AuditEvent]:
        self._check_actor(actor)
        self._authorize(actor, 'audit', {'kind': '-'}, scopes)
        return self._store.tail_audit(limit=limit)


__all__ = [
    'ACTIONS', 'ControlPlane', 'ControlPlaneError', 'DependencyMissing',
    'PolicyDenied', 'AdapterUnavailable', 'ResourceNotFound', 'AuditWriteError',
]
