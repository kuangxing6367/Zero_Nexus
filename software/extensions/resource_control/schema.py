# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 极薄持久化层

刻意保持薄：
- **只有两张表**：资源索引与审计日志。身份、角色、权限、套餐、计费、
  凭据明文一律不建表——身份由网关注入 claims，策略由注入的实现持有。
- **不跨库**：本模块只接受框架注入的 ``ctx``，不接受连接串、库名或第二个
  数据库句柄；DDL 不带库名前缀、不 JOIN 框架身份表（users / admin_users /
  perm_*）。网关授权将来接入时，改的是 identity 槽位，不需要动这里的表。
- **真删**：资源删除走物理 DELETE，不设 deleted_at / is_deleted，不归档。
- 审计只记元数据（谁 / 做了什么 / 对哪个资源 / 结果），不记凭据明文。

DDL 为 MySQL 5.7 风格，经 ``ctx.create_table`` 的方言翻译层适配 SQLite；
占位符由 dialect 统一处理，业务 SQL 一律使用参数化查询。
"""
from __future__ import annotations

SCHEMA_VERSION = 1

TABLE_RESOURCES = 'rc_resources'
TABLE_AUDIT = 'rc_audit'

# 显式列举，供自检与运维核对「没有悄悄多出别的表」。
ALL_TABLES = (TABLE_RESOURCES, TABLE_AUDIT)

SCHEMA_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS `{TABLE_RESOURCES}` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `kind` VARCHAR(64) NOT NULL,
      `external_id` VARCHAR(191) NOT NULL,
      `owner_subject` VARCHAR(191) NOT NULL,
      `tenant` VARCHAR(191) DEFAULT NULL,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_kind_external` (`kind`, `external_id`),
      KEY `idx_owner` (`owner_subject`),
      KEY `idx_tenant` (`tenant`)
    ) DEFAULT CHARSET=utf8mb4""",
    f"""CREATE TABLE IF NOT EXISTS `{TABLE_AUDIT}` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `actor_subject` VARCHAR(191) NOT NULL,
      `action` VARCHAR(64) NOT NULL,
      `target_kind` VARCHAR(64) NOT NULL,
      `target_ref` VARCHAR(191) NOT NULL,
      `outcome` VARCHAR(32) NOT NULL,
      `detail_json` TEXT DEFAULT NULL,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_actor` (`actor_subject`),
      KEY `idx_created` (`created_at`)
    ) DEFAULT CHARSET=utf8mb4""",
)


def install_schema(ctx) -> int:
    """幂等安装：重复调用不报错、不产生副作用。

    只走 ``ctx.create_table``（内核方言适配入口），不自行拼接连接、
    不执行 ``USE``、不跨库。返回执行的 DDL 条数。
    """
    for ddl in SCHEMA_STATEMENTS:
        ctx.create_table(ddl)
    return len(SCHEMA_STATEMENTS)


def schema_status(ctx) -> dict:
    """只读探测两张表是否可用。不修改任何数据。"""
    present = {}
    for table in ALL_TABLES:
        try:
            ctx.db_query_one(f"SELECT 1 FROM `{table}` LIMIT 1")
            present[table] = True
        except Exception:
            present[table] = False
    return {'schema_version': SCHEMA_VERSION, 'tables': present}


__all__ = [
    'SCHEMA_VERSION', 'TABLE_RESOURCES', 'TABLE_AUDIT', 'ALL_TABLES',
    'SCHEMA_STATEMENTS', 'install_schema', 'schema_status',
]
