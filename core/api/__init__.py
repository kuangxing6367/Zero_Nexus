# -*- coding: utf-8 -*-
"""Web 应用层包（实现已迁至 core.api，原 framework.api）

负责 Web 管理后台 / REST API 的构建与路由注册。
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
