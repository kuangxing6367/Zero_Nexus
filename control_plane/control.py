# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""资源生命周期编排 —— 业务内核本体。

这里只做「流程」，不做「策略」：

- 授权怎么判、claims 怎么验、凭据怎么加密、外部系统怎么开通，
  全部由构造参数传入的普通对象决定，本模块一行实现都不提供；
- 内核不接触凭据明文：``spec`` 原样交给适配器，审计 detail 只放
  kind / external_id 这类元数据，不写 spec 全文；
- 缺依赖、授权拒绝、适配器缺失一律明确抛错，不静默降级；
- 删除即真删，失败时台账不删，保证可重试；
- 成功路径的「台账 + 终态审计」由 store 一次原子提交。事务只覆盖
  本地库，**不覆盖外部真实资源**——这条边界在所有失败文案里都写清楚。

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
from control_plane.store import (
    AuditCommitError, DuplicateResource, ResourceStore, StoreError,
)

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


class DuplicateExternalId(ControlPlaneError):
    """适配器返回的外部标识已登记。

    此时**不会**回滚真实资源：该标识可能指向台账里已有的另一条资源，
    调用 deprovision 会把别人的真实实例删掉。
    """


class AuditWriteError(ControlPlaneError):
    """终态审计没有写成功。

    支持事务的后端上，台账与审计一起回滚；不支持事务的后端上，
    台账已落地而审计缺失。两种情况都不能盲目重试或重建资源，
    必须按外部标识核对台账与真实资源状态后再决定补偿动作。
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
        _check_shape(store, ('add_resource', 'add_resource_with_audit',
                             'get_resource', 'delete_resource',
                             'delete_resource_with_audit', 'list_resources',
                             'append_audit', 'tail_audit'), 'store')
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
                f'{exc}；请按外部标识核对台账与真实资源状态后再决定重试，'
                f'不要盲目重建'
            ) from exc

    def _best_effort_audit(self, actor: str, action: str, target_kind: str,
                           target_ref: str, outcome: str,
                           detail: Mapping[str, Any] | None = None) -> None:
        """失败路径上的补偿审计：写不进去只记日志，不覆盖原始失败原因。"""
        try:
            self._audit(actor, action, target_kind, target_ref, outcome, detail)
        except AuditWriteError as exc:
            logger.error('补偿审计写入失败（原失败原因保持不变）: %s', exc)

    def _authorize(self, actor: str, action: str,
                   target: Mapping[str, Any], scopes: Sequence[str]) -> None:
        # 只接受布尔真值 True：返回 None / 0 / 字符串一律视为未放行。
        allowed = self._policy.allowed(actor, action, target, tuple(scopes or ()))
        if allowed is not True:
            # 拒绝结论是安全语义，绝不能被审计故障覆盖成别的异常。
            self._best_effort_audit(actor, action, str(target.get('kind') or '-'),
                                    str(target.get('external_id') or '-'),
                                    'denied', {'reason': 'policy_not_allowed'})
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
        """创建资源：授权 → 适配器开通 → (台账 + 审计) 原子写。

        台账与审计同事务；事务失败时两者一起回滚，外部真实资源需尽力
        清理。**唯一例外**：外部标识已登记时绝不清理，避免误删已有实例。
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
            self._best_effort_audit(actor, 'provision', kind, '-', 'failed',
                                    {'reason': 'adapter_provision_failed'})
            raise ControlPlaneError(f'适配器开通失败: {exc}') from exc
        if not isinstance(external_id, str) or not external_id.strip():
            self._best_effort_audit(actor, 'provision', kind, '-', 'failed',
                                    {'reason': 'invalid_external_id'})
            raise ControlPlaneError('适配器必须返回非空的 external_id 字符串')

        try:
            resource = self._store.add_resource_with_audit(
                kind, external_id, actor, tenant, actor)
        except DuplicateResource as exc:
            # 关键：该 external_id 已属于台账里另一条资源，调用
            # adapter.deprovision 会把已有资源的真实实例删掉，绝对不能做。
            self._best_effort_audit(actor, 'provision', kind, external_id,
                                    'failed', {'reason': 'duplicate_external_id'})
            raise DuplicateExternalId(
                f'{exc}；未回滚真实资源（该标识可能属于已有资源）'
            ) from exc
        except AuditCommitError as exc:
            # 非原子后端：台账已落地，只缺终态审计，不能回滚真实资源。
            raise AuditWriteError(
                f'{exc}；台账已落地，请按外部标识核对台账后做审计补偿，'
                f'不要盲目重建'
            ) from exc
        except StoreError as exc:
            # 原子后端：事务已回滚，台账没落地 → 外部资源成了孤儿，需清理。
            try:
                adapter.deprovision(external_id)
            except Exception as rollback_exc:
                logger.error('原子写入失败后回滚真实资源也失败: %s', rollback_exc)
            self._best_effort_audit(actor, 'provision', kind, external_id,
                                    'failed', {'reason': 'atomic_write_failed'})
            raise ControlPlaneError(
                f'台账与审计原子写入失败，已尝试回滚真实资源: {exc}'
            ) from exc

        return resource

    def deprovision(self, kind: str, external_id: str, *, actor: str,
                    scopes: Sequence[str] = ()) -> bool:
        """真删：授权 → 外部系统物理删除 → (台账 DELETE + 审计) 原子写。

        顺序是刻意的：外部删除失败时台账保留，运维可重试；
        本地提交失败时台账回滚（记录仍在）但真实资源已消失，
        因此必须提示人工核对，不能当作「可安全重试」。
        """
        self._check_actor(actor)
        record = self._store.get_resource(kind, external_id)
        if record is None:
            self._best_effort_audit(actor, 'deprovision', kind, external_id,
                                    'failed', {'reason': 'not_found'})
            raise ResourceNotFound(f'资源不存在: {kind}/{external_id}')

        target = {'kind': kind, 'external_id': external_id,
                  'owner_subject': record.owner_subject}
        self._authorize(actor, 'deprovision', target, scopes)
        adapter = self._adapter(kind)

        try:
            adapter.deprovision(external_id)
        except Exception as exc:
            self._best_effort_audit(actor, 'deprovision', kind, external_id,
                                    'failed',
                                    {'reason': 'adapter_deprovision_failed'})
            raise ControlPlaneError(f'外部删除失败，台账已保留可重试: {exc}') from exc

        try:
            self._store.delete_resource_with_audit(kind, external_id, actor)
        except AuditCommitError as exc:
            raise AuditWriteError(
                f'{exc}；台账已物理删除，请按外部标识核对真实资源状态，'
                f'不要盲目重试'
            ) from exc
        except StoreError as exc:
            self._best_effort_audit(actor, 'deprovision', kind, external_id,
                                    'failed',
                                    {'reason': 'atomic_delete_failed'})
            raise ControlPlaneError(
                f'台账与审计原子删除失败：真实资源已删除、台账已回滚，'
                f'请人工核对，不要直接重试: {exc}'
            ) from exc

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
    'PolicyDenied', 'AdapterUnavailable', 'ResourceNotFound',
    'DuplicateExternalId', 'AuditWriteError',
]
