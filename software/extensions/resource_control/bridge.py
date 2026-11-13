# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""框架壳与业务内核之间的极薄适配：``ctx.db_*`` → Backend。

只做方法名映射，不做任何业务判断、不缓存连接、不持有凭据。
业务内核 ``control_plane`` 不 import 本文件，也不依赖 Zeronus。
"""
from __future__ import annotations

from control_plane.store import DDL_MYSQL


class FrameworkBackend:
    """把框架 ctx 的数据库能力包装成 ResourceStore 需要的普通 Backend。"""

    def __init__(self, ctx):
        self._ctx = ctx

    def ddl(self):
        # MySQL 5.7 风格；ctx.create_table 会自动翻译为 SQLite 兼容语法。
        return DDL_MYSQL

    def execute(self, sql, params=None):
        stripped = sql.lstrip().upper()
        if stripped.startswith('CREATE TABLE') or stripped.startswith('CREATE INDEX'):
            self._ctx.create_table(sql)
            return 1
        return self._ctx.db_execute(sql, tuple(params or ()))

    def insert(self, sql, params=None):
        return self._ctx.db_insert(sql, tuple(params or ()))

    def query(self, sql, params=None):
        return self._ctx.db_query(sql, tuple(params or ()))

    def query_one(self, sql, params=None):
        return self._ctx.db_query_one(sql, tuple(params or ()))


__all__ = ['FrameworkBackend']
