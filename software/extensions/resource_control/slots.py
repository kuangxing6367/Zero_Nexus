# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 显式槽位注册表

设计要点：
- 槽位**显式绑定**，不自动发现、不扫描目录、不读 entry_points。
- 未配置的槽位返回「拒绝型」实现：任何调用都抛 ``SlotUnavailable``，
  不静默返回 None / False / 空串，也不降级为内置默认算法。
- 绑定时做接口形状校验（Protocol runtime_checkable），错配立即拒绝。
- 注册表本身不连数据库、不发网络请求、不持有任何密钥。
"""
from __future__ import annotations

from typing import Any, Mapping

from software.extensions.resource_control.contracts import (
    CONTRACT_VERSION, SLOT_NAMES, SLOT_IDENTITY, SLOT_POLICY, SLOT_VAULT,
    SLOT_ADAPTERS, SLOT_AUDIT, Principal, Decision, AuditEvent,
    IdentityGateway, AuthorizationPolicy, CredentialVault,
    AdapterResolver, AuditSink,
)


class SlotUnavailable(RuntimeError):
    """槽位未配置或被显式禁用。调用方必须捕获并拒绝请求，不得降级。"""


class SlotContractError(TypeError):
    """绑定的实现不满足该槽位的接口形状。"""


def _refuse(slot: str, method: str):
    raise SlotUnavailable(
        f'槽位 [{slot}] 未配置，拒绝调用 {method}()；'
        f'请先通过 SlotRegistry.bind() 注入实现。'
    )


class _RefusingIdentity:
    def resolve(self, request: Mapping[str, Any]) -> Principal | None:
        _refuse(SLOT_IDENTITY, 'resolve')


class _RefusingPolicy:
    def decide(self, principal: Principal, action: str,
               target: Mapping[str, Any] | None = None) -> Decision:
        _refuse(SLOT_POLICY, 'decide')


class _RefusingVault:
    def seal(self, plaintext: str, context: Mapping[str, Any]) -> str:
        _refuse(SLOT_VAULT, 'seal')

    def open(self, token: str, context: Mapping[str, Any]) -> str:
        _refuse(SLOT_VAULT, 'open')


class _RefusingAdapters:
    def resolve(self, kind: str):
        _refuse(SLOT_ADAPTERS, 'resolve')

    def kinds(self):
        _refuse(SLOT_ADAPTERS, 'kinds')


class _RefusingAudit:
    def record(self, event: AuditEvent) -> None:
        _refuse(SLOT_AUDIT, 'record')


_REFUSING = {
    SLOT_IDENTITY: _RefusingIdentity,
    SLOT_POLICY: _RefusingPolicy,
    SLOT_VAULT: _RefusingVault,
    SLOT_ADAPTERS: _RefusingAdapters,
    SLOT_AUDIT: _RefusingAudit,
}

_PROTOCOLS = {
    SLOT_IDENTITY: IdentityGateway,
    SLOT_POLICY: AuthorizationPolicy,
    SLOT_VAULT: CredentialVault,
    SLOT_ADAPTERS: AdapterResolver,
    SLOT_AUDIT: AuditSink,
}


class SlotRegistry:
    """槽位注册表：一个普通字典，不做框架、不做插件中心。"""

    def __init__(self) -> None:
        self._bound: dict[str, Any] = {}
        self._refusing = {name: cls() for name, cls in _REFUSING.items()}

    def bind(self, name: str, implementation: Any) -> None:
        """显式绑定一个实现。形状不符直接抛错，不接受「差不多能用」。"""
        if name not in SLOT_NAMES:
            raise SlotContractError(f'未知槽位: {name!r}；可用: {sorted(SLOT_NAMES)}')
        protocol = _PROTOCOLS[name]
        if not isinstance(implementation, protocol):
            raise SlotContractError(
                f'槽位 [{name}] 的实现不满足 {protocol.__name__} 接口形状'
            )
        self._bound[name] = implementation

    def unbind(self, name: str) -> None:
        """解除绑定后立即回到拒绝态，不保留任何缓存实现。"""
        if name not in SLOT_NAMES:
            raise SlotContractError(f'未知槽位: {name!r}')
        self._bound.pop(name, None)

    def get(self, name: str) -> Any:
        """取实现。未配置返回拒绝型实现（fail-closed）。"""
        if name not in SLOT_NAMES:
            raise SlotContractError(f'未知槽位: {name!r}')
        return self._bound.get(name, self._refusing[name])

    def require(self, name: str) -> Any:
        """取实现。未配置直接抛错，适用于不可降级的路径。"""
        if name not in self._bound:
            _refuse(name, '<require>')
        return self._bound[name]

    def is_configured(self, name: str) -> bool:
        return name in self._bound

    def status(self) -> dict:
        """装配状态，供自检与只读 API 使用；不泄露任何实现细节或密钥。"""
        return {
            'contract_version': CONTRACT_VERSION,
            'slots': {
                name: {
                    'configured': self.is_configured(name),
                    'mode': 'bound' if self.is_configured(name) else 'refusing',
                }
                for name in sorted(SLOT_NAMES)
            },
        }


__all__ = [
    'SlotUnavailable', 'SlotContractError', 'SlotRegistry',
]
