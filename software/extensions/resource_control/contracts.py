# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 契约层（只有边界，没有实现）

本模块只定义数据结构与接口。以下三件事**刻意不在此决定**，由部署方注入：

1. 身份如何取得（可能有统一网关授权 / SSO / 本地会话）
2. 授权策略如何判定（不写死任何角色、任务授权或权限表）
3. 凭据如何加密（加密算法、密钥生命周期、信封结构均不预设）

约束（改动前先读 README.md）：
- 数据库层保持薄且自治：只操作本扩展自己的两张表，不跨库、不 JOIN 框架
  `users` / `admin_users` / `perm_*` 等身份表，不引入第二个数据库连接。
- 身份由网关注入 claims，内核只消费 claims，不反向查库验证身份。
- 删除即真删（物理 DELETE），不设软删标记、不归档。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

CONTRACT_VERSION = 1

# 槽位名。显式列举，不做自动发现、不搞插件注册中心。
SLOT_IDENTITY = 'identity'
SLOT_POLICY = 'policy'
SLOT_VAULT = 'vault'
SLOT_ADAPTERS = 'adapters'
SLOT_AUDIT = 'audit'

SLOT_NAMES = frozenset({
    SLOT_IDENTITY, SLOT_POLICY, SLOT_VAULT, SLOT_ADAPTERS, SLOT_AUDIT,
})


@dataclass(frozen=True)
class Principal:
    """网关注入的身份。只有 claims，没有查库能力。

    subject:  外部身份主体的不透明标识（由网关签发，本层不解释其格式）
    tenant:   租户隔离键；多租户部署由网关给出，单租户可为 None
    scopes:   网关已签发的权限范围，本层只透传，不自行判定
    claims:   原始 claims，供注入的实现自行取用
    """
    subject: str
    tenant: str | None = None
    scopes: tuple[str, ...] = ()
    claims: Mapping[str, Any] = field(default_factory=dict)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True)
class ResourceRef:
    """资源引用：只存外部系统的标识，不存连接串或凭据明文。"""
    kind: str
    external_id: str
    owner_subject: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ''


@dataclass(frozen=True)
class AuditEvent:
    actor_subject: str
    action: str
    target_kind: str
    target_ref: str
    outcome: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class IdentityGateway(Protocol):
    """把请求上下文解析为 Principal。

    实现方决定：验签、token 校验、claims 白名单、新 IP 检测等。
    本层不假定任何 cookie 名、header 名或 token 格式。
    """

    def resolve(self, request: Mapping[str, Any]) -> Principal | None:
        ...


@runtime_checkable
class AuthorizationPolicy(Protocol):
    """授权判定。策略完全由实现方决定，本层不内置角色模型。"""

    def decide(self, principal: Principal, action: str,
               target: Mapping[str, Any] | None = None) -> Decision:
        ...


@runtime_checkable
class CredentialVault(Protocol):
    """凭据保险库接口。

    **加密逻辑不在此实现**：算法、密钥派生、信封结构、轮换周期、
    服务密钥与人工解锁的取舍，全部由注入的实现决定。

    seal 的返回值必须是可入库的不透明字符串；open 失败时应抛异常，
    不允许静默返回密文或空串（避免上层把密文当明文继续用）。
    """

    def seal(self, plaintext: str, context: Mapping[str, Any]) -> str:
        ...

    def open(self, token: str, context: Mapping[str, Any]) -> str:
        ...


@runtime_checkable
class ResourceAdapter(Protocol):
    """一种资源类型对应一个普通类，不做注册表泛化。"""

    kind: str

    def provision(self, spec: Mapping[str, Any], principal: Principal) -> ResourceRef:
        ...

    def deprovision(self, ref: ResourceRef, principal: Principal) -> None:
        """真删：实现方必须物理移除，不留归档、不设软删标记。"""

    def status(self, ref: ResourceRef) -> Mapping[str, Any]:
        ...


@runtime_checkable
class AuditSink(Protocol):
    """审计落点。实现方决定落库、落文件或转发到外部审计系统。

    约定：只记元数据，不记凭据明文。
    """

    def record(self, event: AuditEvent) -> None:
        ...


@runtime_checkable
class AdapterResolver(Protocol):
    """按 kind 取适配器。实现方决定怎么注册，本层不扫描目录。"""

    def resolve(self, kind: str) -> ResourceAdapter | None:
        ...

    def kinds(self) -> Sequence[str]:
        ...


__all__ = [
    'CONTRACT_VERSION', 'SLOT_NAMES',
    'SLOT_IDENTITY', 'SLOT_POLICY', 'SLOT_VAULT', 'SLOT_ADAPTERS', 'SLOT_AUDIT',
    'Principal', 'ResourceRef', 'Decision', 'AuditEvent',
    'IdentityGateway', 'AuthorizationPolicy', 'CredentialVault',
    'ResourceAdapter', 'AdapterResolver', 'AuditSink',
]
