# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 官方扩展装配入口（骨架）

本扩展只做三件事：
1. 建一个显式槽位注册表，挂到 ``fw.services['resource_control']``；
2. 注册一个只读状态 API（复用框架自身鉴权）；
3. 暴露 ``install(ctx)`` 供运维显式建表。

**不做**：不实现身份/授权/加密/适配器逻辑，不自动发现插件，不注册事件总线，
不启动监听，不联网，不在 ``register()`` 里建表或写数据。

默认关闭（extensions.yaml → resource_control.enabled: false）。启用后若未注入
任何实现，所有业务调用都会 fail-closed 抛 ``SlotUnavailable``，不会静默放行。
"""
from __future__ import annotations

import logging

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "统一资源控制面（骨架）",
    "version": "0.1.0",
    "author": "Zeronus",
    "desc": "契约层与显式槽位；身份/授权/加密/适配器均由部署方注入",
    "priority": 50,
    "official": True,
}

_SERVICE_NAME = 'resource_control'
_registry = None


def register(ctx):
    """框架装载入口。只装配，不执行业务副作用。"""
    global _registry
    from software.extensions.resource_control.slots import SlotRegistry

    fw = ctx._framework
    cfg = fw.config.get('resource_control', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log('统一资源控制面未启用 (resource_control.enabled: false)')
        return

    _registry = SlotRegistry()
    fw.services.register(_SERVICE_NAME, _registry)

    # 只读状态接口：复用框架鉴权，不自行判断角色、不接受写操作。
    ctx.register_api(
        'resource-control/status',
        _status_handler,
        methods=['GET'],
        auth=True,
        description='控制面装配状态（只读）',
    )

    ctx.log(
        '统一资源控制面已装配；槽位全未配置，业务调用将 fail-closed 拒绝。'
        ' 显式调用 install(ctx) 可幂等建表。'
    )
    return _registry


def _status_handler(req):
    """只读状态。不返回实现细节、配置值或任何凭据。"""
    from software.extensions.resource_control.contracts import CONTRACT_VERSION
    if _registry is None:
        return {'ok': False, 'error': '控制面未启用', 'contract_version': CONTRACT_VERSION}
    payload = _registry.status()
    payload['ok'] = True
    return payload


def install(ctx):
    """幂等建表（显式调用，不由 register 自动触发）。"""
    from software.extensions.resource_control import schema as _schema
    count = _schema.install_schema(ctx)
    ctx.log(f'统一资源控制面 schema 已校验/安装（{count} 条 DDL）')
    return _schema.schema_status(ctx)


def unregister():
    """热卸载时清空引用，避免残留注册表被误用。"""
    global _registry
    _registry = None


__all__ = ['register', 'unregister', 'install', '__plugin_meta__']
