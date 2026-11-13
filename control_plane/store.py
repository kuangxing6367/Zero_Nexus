# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""极薄持久化层：两张表、参数化 SQL、真删、审计只记元数据。

约束（改动前请先读 docs/control-plane.md）：

- **只有两张表**：``rc_resources``（资源索引）与 ``rc_audit``（操作事实）。
  不建身份 / 角色 / 权限 / 套餐 / 计费 / 凭据表。统一网关将来接入时
  改的是 identity 实现，不需要动这里的表。
- **不跨库**：所有 SQL 不带库名前缀、无 ``USE``、不 JOIN 框架身份表，
  不引入第二个连接；Backend 由调用方注入一个。
- **真删**：资源删除走物理 ``DELETE``，无软删标记，不归档、不写备份表。
  审计日志是独立的操作事实记录，不含资源内容。
- **审计脱敏**：写入前过滤敏感键并限制长度，绝不落凭据明文或 spec 全文。
- **台账与审计同事务**（第二阶段）：支持事务的 Backend 上，成功路径的
  台账写入与终态审计是一次原子提交，异常时一起回滚。Backend 不支持
  事务时**显式降级**——health 里 ``atomic_audit=False``、审计 detail
  里 ``atomic=False``、并向 logger 发 warning，绝不假装原子。
- 所有业务 SQL 使用 ``%s`` 占位符并参数化；SqliteBackend 内部翻译为 ``?``。

本模块不 import Zeronus 内核，也不依赖任何第三方驱动。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Mapping, Sequence

from control_plane.models import AuditEvent, Resource

logger = logging.getLogger('control_plane')

SCHEMA_VERSION = 2

TABLE_RESOURCES = 'rc_resources'
TABLE_AUDIT = 'rc_audit'
ALL_TABLES = (TABLE_RESOURCES, TABLE_AUDIT)

# 敏感键：命中即脱敏。只按键名判断，不试图解析值的内容。
_SENSITIVE_KEY = re.compile(
    r'pass(word|wd)?|secret|token|credential|api_?key|private|authorization|cookie',
    re.IGNORECASE,
)
_DETAIL_MAX = 4000
_REDACTED = '<redacted>'


class StoreError(RuntimeError):
    """持久化失败。调用方必须当失败处理，不得当作「已成功」。"""


class DuplicateResource(StoreError):
    """``(kind, external_id)`` 已登记。

    这与「台账写入失败」是**不同**的失败：外部标识已属于台账里另一条
    资源，调用方绝不能据此去删除该标识对应的真实资源。
    """


class AuditCommitError(StoreError):
    """非原子后端上，台账已落地但终态审计没写成。

    调用方不得把这个错误当成「台账也没写成」去回滚真实资源。
    """


class TransactionError(StoreError):
    """事务边界本身失败（嵌套、BEGIN/COMMIT/ROLLBACK 出错）。"""


DDL_MYSQL = (
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
      `outcome` VARCHAR(16) NOT NULL,
      `detail_json` TEXT DEFAULT NULL,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_actor` (`actor_subject`),
      KEY `idx_created` (`created_at`)
    ) DEFAULT CHARSET=utf8mb4""",
)

DDL_SQLITE = (
    f"""CREATE TABLE IF NOT EXISTS `{TABLE_RESOURCES}` (
      `id` INTEGER PRIMARY KEY AUTOINCREMENT,
      `kind` TEXT NOT NULL,
      `external_id` TEXT NOT NULL,
      `owner_subject` TEXT NOT NULL,
      `tenant` TEXT DEFAULT NULL,
      `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
      `updated_at` TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE (`kind`, `external_id`)
    )""",
    f"CREATE INDEX IF NOT EXISTS `idx_owner` ON `{TABLE_RESOURCES}` (`owner_subject`)",
    f"CREATE INDEX IF NOT EXISTS `idx_tenant` ON `{TABLE_RESOURCES}` (`tenant`)",
    f"""CREATE TABLE IF NOT EXISTS `{TABLE_AUDIT}` (
      `id` INTEGER PRIMARY KEY AUTOINCREMENT,
      `actor_subject` TEXT NOT NULL,
      `action` TEXT NOT NULL,
      `target_kind` TEXT NOT NULL,
      `target_ref` TEXT NOT NULL,
      `outcome` TEXT NOT NULL,
      `detail_json` TEXT DEFAULT NULL,
      `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    f"CREATE INDEX IF NOT EXISTS `idx_actor` ON `{TABLE_AUDIT}` (`actor_subject`)",
    f"CREATE INDEX IF NOT EXISTS `idx_created` ON `{TABLE_AUDIT}` (`created_at`)",
)

_SQL_INSERT_RESOURCE = (
    f"INSERT INTO `{TABLE_RESOURCES}` "
    f"(`kind`, `external_id`, `owner_subject`, `tenant`) "
    f"VALUES (%s, %s, %s, %s)"
)
_SQL_GET_RESOURCE = (
    f"SELECT * FROM `{TABLE_RESOURCES}` "
    f"WHERE `kind` = %s AND `external_id` = %s LIMIT 1"
)
_SQL_DELETE_RESOURCE = (
    f"DELETE FROM `{TABLE_RESOURCES}` WHERE `kind` = %s AND `external_id` = %s"
)
_SQL_INSERT_AUDIT = (
    f"INSERT INTO `{TABLE_AUDIT}` "
    f"(`actor_subject`, `action`, `target_kind`, `target_ref`, "
    f"`outcome`, `detail_json`) VALUES (%s, %s, %s, %s, %s, %s)"
)


def _looks_like_unique_violation(exc: BaseException) -> bool:
    """只按异常类型名与消息判断，不 import 任何数据库驱动。"""
    text = str(exc).lower()
    return (
        type(exc).__name__ == 'IntegrityError'
        or 'unique constraint' in text
        or 'duplicate entry' in text
        or '1062' in text
    )


class SqliteBackend:
    """标准库 SQLite 后端。autocommit + 显式 BEGIN IMMEDIATE 事务。"""

    supports_transactions = True

    def __init__(self, path: str = ':memory:'):
        # isolation_level=None：DDL/DML 立即生效；事务边界由本类显式管理，
        # 避免 sqlite3 的隐式 BEGIN 与显式 BEGIN 打架。
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._tx_depth = 0

    def ddl(self) -> Sequence[str]:
        return DDL_SQLITE

    @property
    def in_transaction(self) -> bool:
        return self._tx_depth > 0

    @contextmanager
    def transaction(self):
        if self._tx_depth:
            raise TransactionError('不支持嵌套事务')
        self._tx_depth = 1
        try:
            self.conn.execute('BEGIN IMMEDIATE')
        except Exception as exc:
            self._tx_depth = 0
            raise TransactionError(f'开启事务失败: {exc}') from exc
        try:
            yield self
        except BaseException:
            try:
                self.conn.execute('ROLLBACK')
            except Exception as rb_exc:
                logger.error('SQLite 事务回滚失败: %s', rb_exc)
            raise
        else:
            try:
                self.conn.execute('COMMIT')
            except Exception as cm_exc:
                try:
                    self.conn.execute('ROLLBACK')
                except Exception:
                    pass
                raise TransactionError(f'提交事务失败: {cm_exc}') from cm_exc
        finally:
            self._tx_depth = 0

    def _exec(self, sql: str, params: Sequence[Any] | None):
        return self.conn.execute(sql.replace('%s', '?'), tuple(params or ()))

    def execute(self, sql: str, params: Sequence[Any] = None) -> int:
        return self._exec(sql, params).rowcount

    def insert(self, sql: str, params: Sequence[Any] = None) -> int:
        return int(self._exec(sql, params).lastrowid)

    def query(self, sql: str, params: Sequence[Any] = None) -> list[dict]:
        cur = self._exec(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        self.conn.close()


class MysqlBackend:
    """任意 DB-API MySQL 连接（pymysql / mysqlclient），本模块不 import 驱动。

    由部署方传入已建立的连接；占位符保持 ``%s`` 原样。事务用显式
    ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``，因此不依赖连接是否开了 autocommit。
    """

    supports_transactions = True

    def __init__(self, conn):
        self.conn = conn
        self._tx_depth = 0

    def ddl(self) -> Sequence[str]:
        return DDL_MYSQL

    @property
    def in_transaction(self) -> bool:
        return self._tx_depth > 0

    @contextmanager
    def transaction(self):
        if self._tx_depth:
            raise TransactionError('不支持嵌套事务')
        cur = self.conn.cursor()
        self._tx_depth = 1
        try:
            try:
                cur.execute('BEGIN')
            except Exception as exc:
                raise TransactionError(f'开启事务失败: {exc}') from exc
            try:
                yield self
            except BaseException:
                try:
                    self.conn.rollback()
                except Exception as rb_exc:
                    logger.error('MySQL 事务回滚失败: %s', rb_exc)
                raise
            else:
                try:
                    self.conn.commit()
                except Exception as cm_exc:
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                    raise TransactionError(f'提交事务失败: {cm_exc}') from cm_exc
        finally:
            self._tx_depth = 0
            try:
                cur.close()
            except Exception:
                pass

    def _run(self, sql: str, params: Sequence[Any] | None):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            return cur
        except Exception:
            cur.close()
            raise

    def execute(self, sql: str, params: Sequence[Any] = None) -> int:
        cur = self._run(sql, params)
        try:
            if not self._tx_depth:
                self.conn.commit()
            return cur.rowcount
        finally:
            cur.close()

    def insert(self, sql: str, params: Sequence[Any] = None) -> int:
        cur = self._run(sql, params)
        try:
            if not self._tx_depth:
                self.conn.commit()
            return int(cur.lastrowid)
        finally:
            cur.close()

    def query(self, sql: str, params: Sequence[Any] = None) -> list[dict]:
        cur = self._run(sql, params)
        try:
            cols = [d[0] for d in (cur.description or ())]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def query_one(self, sql: str, params: Sequence[Any] = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        closer = getattr(self.conn, 'close', None)
        if callable(closer):
            closer()


def _scrub(value: Any, depth: int = 0) -> Any:
    """审计 detail 脱敏：敏感键一律替换，嵌套限深，避免把凭据带进日志。"""
    if depth > 3:
        return _REDACTED
    if isinstance(value, Mapping):
        out = {}
        for key, val in value.items():
            k = str(key)
            out[k] = _REDACTED if _SENSITIVE_KEY.search(k) else _scrub(val, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value[:20]]
    if isinstance(value, str) and len(value) > 256:
        return value[:256] + '…'
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


def scrub_detail(detail: Mapping[str, Any] | None) -> str:
    """把 detail 序列化为可入库的 JSON，超长截断。供自检直接调用。"""
    if not detail:
        return ''
    try:
        text = json.dumps(_scrub(dict(detail)), ensure_ascii=False,
                          sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        raise StoreError(f'审计 detail 无法序列化: {exc}') from exc
    return text[:_DETAIL_MAX]


def _row_to_resource(row: Mapping[str, Any] | None) -> Resource | None:
    if not row:
        return None
    return Resource(
        kind=row['kind'], external_id=row['external_id'],
        owner_subject=row['owner_subject'], tenant=row.get('tenant'),
        id=row.get('id'), created_at=row.get('created_at'),
        updated_at=row.get('updated_at'),
    )


def _row_to_audit(row: Mapping[str, Any]) -> AuditEvent:
    raw = row.get('detail_json') or ''
    try:
        detail = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        detail = {}
    return AuditEvent(
        actor_subject=row['actor_subject'], action=row['action'],
        target_kind=row['target_kind'], target_ref=row['target_ref'],
        outcome=row['outcome'], detail=detail,
        id=row.get('id'), created_at=row.get('created_at'),
    )


class ResourceStore:
    """资源台账 + 审计日志的薄数据访问层。不含业务策略。"""

    def __init__(self, backend):
        if backend is None:
            raise StoreError('必须注入一个 Backend，不接受 None')
        self._db = backend

    @property
    def backend(self):
        return self._db

    def capabilities(self) -> dict:
        """对外声明后端真实能力。调用方不得假设原子审计一定可用。"""
        atomic = bool(
            getattr(self._db, 'supports_transactions', False)
            and callable(getattr(self._db, 'transaction', None))
        )
        return {
            'backend': type(self._db).__name__,
            'transaction': atomic,
            'atomic_audit': atomic,
        }

    def install(self) -> int:
        """幂等建表。DDL 由 Backend 按方言提供。"""
        for ddl in self._db.ddl():
            self._db.execute(ddl)
        return len(self._db.ddl())

    def health(self) -> dict:
        """只读探测，不修改数据。"""
        tables = {}
        for table in ALL_TABLES:
            try:
                self._db.query_one(f"SELECT 1 FROM `{table}` LIMIT 1")
                tables[table] = True
            except Exception:
                tables[table] = False
        return {
            'schema_version': SCHEMA_VERSION,
            'tables': tables,
            'resources': self.count_resources() if tables.get(TABLE_RESOURCES) else None,
            'audit_events': self.count_audit() if tables.get(TABLE_AUDIT) else None,
            'capabilities': self.capabilities(),
        }

    # ---- 内部原语（事务内/外均可调用） ----

    def _insert_resource(self, kind: str, external_id: str,
                         owner_subject: str, tenant: str | None):
        try:
            self._db.insert(_SQL_INSERT_RESOURCE,
                            (kind, external_id, owner_subject, tenant))
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                # 二次确认：只有台账里确实已有该标识，才判定为重复。
                try:
                    existing = self._db.query_one(_SQL_GET_RESOURCE,
                                                  (kind, external_id))
                except Exception:
                    existing = None
                if existing is not None:
                    raise DuplicateResource(
                        f'外部标识已登记: {kind}/{external_id}'
                    ) from exc
            raise StoreError(f'资源登记失败: {exc}') from exc
        return self._db.query_one(_SQL_GET_RESOURCE, (kind, external_id))

    def _insert_audit(self, actor_subject: str, action: str, target_kind: str,
                      target_ref: str, outcome: str,
                      detail: Mapping[str, Any] | None = None) -> None:
        self._db.insert(
            _SQL_INSERT_AUDIT,
            (actor_subject, action, target_kind, target_ref,
             outcome, scrub_detail(detail)),
        )

    @staticmethod
    def _with_atomic_flag(detail: Mapping[str, Any] | None, atomic: bool) -> dict:
        out = dict(detail or {})
        out['atomic'] = atomic
        return out

    # ---- 资源台账 ----

    def add_resource(self, kind: str, external_id: str, owner_subject: str,
                     tenant: str | None = None) -> Resource:
        """单写台账（不写审计）。业务成功路径请用 add_resource_with_audit。"""
        return _row_to_resource(
            self._insert_resource(kind, external_id, owner_subject, tenant))

    def add_resource_with_audit(self, kind: str, external_id: str,
                                owner_subject: str, tenant: str | None,
                                actor_subject: str, *,
                                detail: Mapping[str, Any] | None = None) -> Resource:
        """登记资源并写 ok 审计。支持事务的后端上一次原子提交。

        非原子后端显式降级：台账先落地、审计后写，审计 detail 带
        ``atomic=False``；此时审计失败抛 :class:`AuditCommitError`，
        含义是「台账已落地」，调用方不得据此回滚真实资源。
        """
        args = (actor_subject, 'provision', kind, external_id, 'ok')
        if not self.capabilities()['atomic_audit']:
            logger.warning('Backend %s 不支持事务，台账与审计非原子写入',
                           type(self._db).__name__)
            row = self._insert_resource(kind, external_id, owner_subject, tenant)
            try:
                self._insert_audit(*args,
                                   detail=self._with_atomic_flag(detail, False))
            except Exception as exc:
                raise AuditCommitError(f'审计写入失败（台账已落地）: {exc}') from exc
            return _row_to_resource(row)

        try:
            with self._db.transaction():
                row = self._insert_resource(kind, external_id,
                                            owner_subject, tenant)
                self._insert_audit(*args, detail=detail)
        except (DuplicateResource, StoreError):
            raise
        except Exception as exc:
            raise StoreError(f'台账与审计原子写入失败: {exc}') from exc
        return _row_to_resource(row)

    def get_resource(self, kind: str, external_id: str) -> Resource | None:
        row = self._db.query_one(_SQL_GET_RESOURCE, (kind, external_id))
        return _row_to_resource(row)

    def list_resources(self, *, owner_subject: str | None = None,
                       tenant: str | None = None, kind: str | None = None,
                       limit: int = 100) -> list[Resource]:
        where, params = [], []
        if owner_subject is not None:
            where.append('`owner_subject` = %s')
            params.append(owner_subject)
        if tenant is not None:
            where.append('`tenant` = %s')
            params.append(tenant)
        if kind is not None:
            where.append('`kind` = %s')
            params.append(kind)
        sql = f"SELECT * FROM `{TABLE_RESOURCES}`"
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY `id` DESC LIMIT %s'
        params.append(max(1, min(int(limit), 1000)))
        return [_row_to_resource(r) for r in self._db.query(sql, tuple(params))]

    def delete_resource(self, kind: str, external_id: str) -> bool:
        """真删：物理 DELETE，不设软删标记、不写归档表。"""
        affected = self._db.execute(_SQL_DELETE_RESOURCE, (kind, external_id))
        return affected > 0

    def delete_resource_with_audit(self, kind: str, external_id: str,
                                   actor_subject: str, *,
                                   detail: Mapping[str, Any] | None = None) -> bool:
        """物理删除台账并写 ok 审计。支持事务的后端上一次原子提交。

        注意：事务只覆盖本地台账与审计，**不覆盖外部真实资源**。
        外部删除已成功而本地提交失败时，台账会回滚（记录仍在），
        需由调用方写 failed 审计并提示人工核对，不能盲目重试删除。
        """
        args = (actor_subject, 'deprovision', kind, external_id, 'ok')
        if not self.capabilities()['atomic_audit']:
            logger.warning('Backend %s 不支持事务，台账与审计非原子写入',
                           type(self._db).__name__)
            if not self.delete_resource(kind, external_id):
                raise StoreError('台账物理删除未生效，请人工核对')
            try:
                self._insert_audit(*args,
                                   detail=self._with_atomic_flag(detail, False))
            except Exception as exc:
                raise AuditCommitError(f'审计写入失败（台账已删除）: {exc}') from exc
            return True

        try:
            with self._db.transaction():
                affected = self._db.execute(_SQL_DELETE_RESOURCE,
                                            (kind, external_id))
                if affected <= 0:
                    raise StoreError('台账物理删除未生效，请人工核对')
                self._insert_audit(*args, detail=detail)
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(f'台账与审计原子删除失败: {exc}') from exc
        return True

    def count_resources(self) -> int:
        row = self._db.query_one(f"SELECT COUNT(*) AS n FROM `{TABLE_RESOURCES}`")
        return int(row['n'])

    # ---- 审计 ----

    def append_audit(self, actor_subject: str, action: str, target_kind: str,
                     target_ref: str, outcome: str,
                     detail: Mapping[str, Any] | None = None) -> None:
        """写一条操作事实。detail 经脱敏后才落盘。"""
        try:
            self._insert_audit(actor_subject, action, target_kind, target_ref,
                               outcome, detail)
        except Exception as exc:
            raise StoreError(f'审计写入失败: {exc}') from exc

    def tail_audit(self, *, actor_subject: str | None = None,
                   limit: int = 50) -> list[AuditEvent]:
        if actor_subject is not None:
            rows = self._db.query(
                f"SELECT * FROM `{TABLE_AUDIT}` WHERE `actor_subject` = %s "
                f"ORDER BY `id` DESC LIMIT %s",
                (actor_subject, max(1, min(int(limit), 1000))),
            )
        else:
            rows = self._db.query(
                f"SELECT * FROM `{TABLE_AUDIT}` ORDER BY `id` DESC LIMIT %s",
                (max(1, min(int(limit), 1000)),),
            )
        return [_row_to_audit(r) for r in rows]

    def count_audit(self) -> int:
        row = self._db.query_one(f"SELECT COUNT(*) AS n FROM `{TABLE_AUDIT}`")
        return int(row['n'])


__all__ = [
    'SCHEMA_VERSION', 'TABLE_RESOURCES', 'TABLE_AUDIT', 'ALL_TABLES',
    'DDL_MYSQL', 'DDL_SQLITE', 'StoreError', 'DuplicateResource',
    'AuditCommitError', 'TransactionError',
    'SqliteBackend', 'MysqlBackend', 'ResourceStore', 'scrub_detail',
]
