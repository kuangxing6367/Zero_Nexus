# -*- coding: utf-8 -*-
"""
core.apis —— 兼容 shim（原 framework.apis）

Web 应用层已按功能域拆分到 core/api/ 包（auth / admins / apikeys /
dashboard / plugins / commands / users_groups / tasks / logs / config /
db_gateway / framework_ops / webui / files / stats / perm_api / static_routes）。

本模块仅作向后兼容入口，重导出 create_web_app / WebServer，
原 `from framework.apis import create_web_app, WebServer` 的写法现已统一为
`from core.apis import ...` 或 `from core.api.webapp import ...`。
"""
from core.api.webapp import create_web_app, WebServer

__all__ = ['create_web_app', 'WebServer']
