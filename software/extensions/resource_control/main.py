# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""Zeronus 框架壳：只负责承载，不提供业务能力。

壳做三件事（全部与业务策略无关）：

1. 把框架 ``ctx`` 的数据库能力适配成 Backend，构造 ``ResourceStore``；
2. 把 store 挂到 ``fw.services['control_plane_store']`` 供部署方取用；
3. 注册只读路由（复用框架自身鉴权），默认只开 health。

**不构造 ``ControlPlane``**：那需要注入真实的授权策略与资源适配器，
框架拿不到也不该猜。部署方在自己的启动代码里写：

    store = fw.services.get('control_plane_store')
    cp = ControlPlane(store, policy=my_policy, adapters=my_adapters)

业务内核在仓库顶层 ``control_plane/``，可脱离本壳独立运行。
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "统一资源控制面 · 框架壳",
    "version": "0.3.0",
    "author": "Zeronus",
    "desc": "承载独立业务内核 control_plane，仅提供 DB 适配与只读路由",
    "priority": 50,
    "official": True,
}

_SERVICE_NAME = 'control_plane_store'
_store = None


def register(ctx):
    """框架装载入口。只承载，不建表、不联网、不注册事件、不注入实现。"""
    global _store
    fw = ctx._framework
    cfg = fw.config.get('resource_control', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log('统一资源控制面框架壳未启用 (resource_control.enabled: false)')
        return

    from control_plane.store import ResourceStore
    from software.extensions.resource_control.bridge import FrameworkBackend

    _store = ResourceStore(FrameworkBackend(ctx))
    fw.services.register(_SERVICE_NAME, _store)

    ctx.register_api(
        'control-plane/health',
        _health_handler,
        methods=['GET'],
        auth=True,
        description='控制面持久化健康状态（只读）',
    )
    # 台账与审计的读取能力默认不暴露。「谁能看」属于授权策略，
    # 本壳不写死：由部署方在框架权限层决定后再显式打开这个开关。
    if cfg.get('expose_readonly_api') is True:
        ctx.register_api(
            'control-plane/resources',
            _resources_handler,
            methods=['GET'],
            auth=True,
            description='资源台账列表（只读，需显式开启）',
        )
        ctx.register_api(
            'control-plane/audit',
            _audit_handler,
            methods=['GET'],
            auth=True,
            description='最近审计事件（只读，需显式开启）',
        )
    ctx.log('统一资源控制面框架壳已承载：业务内核需由部署方注入实现后自行构造')
    return _store


def _query_args(req) -> dict:
    """框架传入的 req 是 dict，查询参数在 ``req['args']``。"""
    if isinstance(req, Mapping):
        args = req.get('args')
        if isinstance(args, Mapping):
            return dict(args)
    return {}


def _bounded_int(args, name, default, low=1, high=1000):
    raw = args.get(name)
    if raw is None:
        return default
    try:
        return max(low, min(int(raw), high))
    except (TypeError, ValueError):
        return default


def _readonly_failure(exc: Exception) -> dict:
    logger.error('控制面只读查询失败: %s', exc)
    return {'ok': False, 'error': '只读查询失败'}


def _health_handler(req):
    """只读探测。不返回配置值、实现细节或任何凭据。"""
    if _store is None:
        return {'ok': False, 'error': '框架壳未启用'}
    try:
        payload = _store.health()
    except Exception as exc:
        logger.error('控制面健康探测失败: %s', exc)
        return {'ok': False, 'error': '健康探测失败'}
    payload['ok'] = all(payload.get('tables', {}).values())
    return payload


def _resources_handler(req):
    """只读台账列表。台账本身不存凭据，因此返回全集元数据。"""
    if _store is None:
        return {'ok': False, 'error': '框架壳未启用'}
    args = _query_args(req)
    try:
        rows = _store.list_resources(
            owner_subject=args.get('owner'),
            tenant=args.get('tenant'),
            kind=args.get('kind'),
            limit=_bounded_int(args, 'limit', 100),
        )
    except Exception as exc:
        return _readonly_failure(exc)
    return {'ok': True, 'count': len(rows),
            'resources': [dataclasses.asdict(r) for r in rows]}


def _audit_handler(req):
    """只读审计尾部。detail 已由 store 脱敏后才落库。"""
    if _store is None:
        return {'ok': False, 'error': '框架壳未启用'}
    args = _query_args(req)
    try:
        rows = _store.tail_audit(limit=_bounded_int(args, 'limit', 50))
    except Exception as exc:
        return _readonly_failure(exc)
    return {'ok': True, 'count': len(rows),
            'audit_events': [dataclasses.asdict(r) for r in rows]}


def install(ctx):
    """幂等建表（显式调用，register 不自动触发）。"""
    global _store
    if _store is None:
        from control_plane.store import ResourceStore
        from software.extensions.resource_control.bridge import FrameworkBackend
        _store = ResourceStore(FrameworkBackend(ctx))
    count = _store.install()
    ctx.log(f'控制面 schema 已校验/安装（{count} 条 DDL）')
    return _store.health()


def unregister():
    """热卸载时清空引用，避免残留 store 被误用。"""
    global _store
    _store = None


__all__ = ['register', 'unregister', 'install', '__plugin_meta__']
