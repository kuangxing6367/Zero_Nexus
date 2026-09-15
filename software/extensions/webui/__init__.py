# -*- coding: utf-8 -*-
"""Web 应用层包（官方扩展 webui 后端）

负责 Web 管理后台 / REST API 的构建与路由注册。
从内核迁出：core/api → extensions/webui（2026-09-14 内核化改造）。
"""
from .webapp import create_web_app, WebServer
from . import (
    registry, context, auth, admins, apikeys, dashboard, plugins, commands,
    users_groups, tasks, logs, config, db_gateway, framework_ops, webui,
    files, stats, perm_api, static_routes,
)

__all__ = [
    'create_web_app', 'WebServer',
    'registry', 'context', 'auth', 'admins', 'apikeys', 'dashboard',
    'plugins', 'commands', 'users_groups', 'tasks', 'logs', 'config',
    'db_gateway', 'framework_ops', 'webui', 'files', 'stats', 'perm_api',
    'static_routes',
]
