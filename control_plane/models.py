# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""业务数据结构。

刻意不定义「Principal / 身份对象」：身份由统一网关注入，业务内核只消费
调用方传进来的 ``actor``（不透明 subject）、``tenant`` 与 ``scopes``，
不反向查库、不解释 token 格式、不验证身份真伪。

下面三个 Protocol **只用于类型提示**：运行时不做 isinstance 校验、
不注册、不扫描、不自动发现。传进来的就是普通对象。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Resource:
    """资源台账中的一条记录。只存外部系统标识，不存连接串或凭据。"""

    kind: str
    external_id: str
    owner_subject: str
    tenant: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """一条操作事实。只记元数据，不记凭据明文或请求体全文。"""

    actor_subject: str
    action: str
    target_kind: str
    target_ref: str
    outcome: str  # ok / denied / failed
    detail: Mapping[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: str | None = None


class Policy(Protocol):
    """授权策略。策略内容完全由注入方决定，本层不内置角色模型。"""

    def allowed(self, actor: str, action: str,
                target: Mapping[str, Any], scopes: Sequence[str]) -> bool:
        ...


class ResourceAdapter(Protocol):
    """一种资源类型对应一个普通类。"""

    kind: str

    def provision(self, spec: Mapping[str, Any]) -> str:
        """在外部系统真实创建资源，返回外部标识。"""

    def deprovision(self, external_id: str) -> None:
        """真删：物理移除，不留归档、不设软删标记。"""

    def status(self, external_id: str) -> Mapping[str, Any]:
        ...


class AdapterResolver(Protocol):
    """按 kind 取适配器。怎么注册由注入方决定，本层不扫描目录。"""

    def resolve(self, kind: str) -> ResourceAdapter | None:
        ...


__all__ = [
    'Resource', 'AuditEvent',
    'Policy', 'ResourceAdapter', 'AdapterResolver',
]
