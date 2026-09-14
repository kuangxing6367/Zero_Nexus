"""
数据库连接引擎（core/storage 节点 2）

Database 类：SQLite（默认）/ MySQL 双后端，连接池、事务、自动重连、
方言翻译（委托 core.storage.dialect）。对外暴露 query/query_one/execute/
execute_many/insert/get_connection/transaction/table_* 等稳定 API。
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from threading import local

from .dialect import (
    _translate_sql_for_sqlite, _translate_sql_for_mysql, _replace_now,
)
from core.hooks import HookPoints

logger = logging.getLogger('zernus')

# MySQL 连接断开类错误码（触发自动重连）
_MYSQL_RECONNECT_ERRORS = {2006, 2013, 2055, 1927, 1040}
# 连接断开类错误关键字（用于兜底判断）
_MYSQL_RECONNECT_KEYWORDS = (
    'server has gone away',
    'lost connection',
    'connection is closed',
    'broken pipe',
    'connection reset by peer',
)


class Database:
    """
    数据库连接管理器
    支持 SQLite（默认）和 MySQL（可选）
    """

    def __init__(self, config: dict):
        self.config = config
        self.db_type = config.get('type', 'sqlite').lower()
        self._local = local()
        self._lock = threading.Lock()
        # 扩展点：由引擎注入 HookRegistry；nil 时不触发 db.* hook
        self._hooks = None
        self._hook_local = local()  # 线程本地重入防护，避免 hook 处理器内再次查询导致递归
        # MySQL 连接保活/重连配置
        self._ping_interval = float(config.get('ping_interval', 5.0))
        self._connect_timeout = float(config.get('connect_timeout', 10))
        self._read_timeout = float(config.get('read_timeout', 30))
        self._write_timeout = float(config.get('write_timeout', 30))
        self._max_reconnect = int(config.get('max_reconnect', 3))
        # MySQL 连接池参数（DBUtils PooledDB，替代每线程一连接方案）
        self._pool_max = int(config.get('pool_size', 10) or 10)
        self._pool_min_cached = int(config.get('min_cached', 0) or 0)
        self._pool_max_cached = int(config.get('max_cached', 0) or 0)
        self._pool_wait_timeout = float(config.get('pool_wait_timeout', 30) or 30)
        if self._pool_max_cached <= 0:
            self._pool_max_cached = self._pool_max
        self._pool = None

        if self.db_type == 'mysql':
            self._init_mysql()
        else:
            mysql_only = ['host', 'port', 'user', 'password']
            configured = [k for k in mysql_only if config.get(k) not in (None, '')]
            if configured:
                logger.warning(
                    f"检测到 database.type = sqlite，但配置了 MySQL 字段（{', '.join(configured)}），"
                    f"这些字段将被忽略。如果要用 MySQL，请将 type 改为 mysql。"
                )
            self._init_sqlite()

    def _init_sqlite(self):
        """初始化 SQLite"""
        db_path = self.config.get('path', 'data/zernus.db')
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._db_path = db_path
        logger.info(f"SQLite 数据库已初始化: {db_path}")

    def _init_mysql(self):
        """初始化 MySQL 连接池（检测到 MySQL 配置时，自动安装 pymysql/DBUtils）"""
        try:
            import pymysql
            from pymysql.cursors import DictCursor
            self._pymysql = pymysql
            self._DictCursor = DictCursor
            logger.info("MySQL 模式已启用")
        except ImportError:
            logger.warning("MySQL 模式需要 pymysql，正在自动安装...")
            import subprocess
            import sys
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', 'pymysql', 'DBUtils'],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    logger.info("pymysql 安装成功，重新导入...")
                    import pymysql
                    from pymysql.cursors import DictCursor
                    self._pymysql = pymysql
                    self._DictCursor = DictCursor
                else:
                    logger.error(f"pymysql 自动安装失败: {result.stderr}")
                    raise ImportError("pymysql 安装失败，请手动执行: pip install pymysql DBUtils")
            except Exception as e:
                logger.error(f"pymysql 自动安装异常: {e}")
                raise ImportError(f"无法自动安装 pymysql: {e}")

        try:
            from dbutils.pooled_db import PooledDB
        except ImportError:
            logger.error("缺少 DBUtils，请手动执行: pip install DBUtils")
            raise ImportError("缺少 DBUtils，请手动执行: pip install DBUtils")
        self._pool = PooledDB(
            creator=self._pymysql,
            maxconnections=self._pool_max,
            mincached=self._pool_min_cached,
            maxcached=self._pool_max_cached,
            maxshared=0,
            blocking=False,
            setsession=[],
            reset=True,
            ping=1,
            host=self.config.get('host', '127.0.0.1'),
            port=int(self.config.get('port', 3306)),
            user=self.config.get('user', 'root'),
            password=self.config.get('password', ''),
            database=self.config.get('database', 'zernus'),
            charset=self.config.get('charset', 'utf8mb4'),
            cursorclass=self._DictCursor,
            autocommit=True,
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            write_timeout=self._write_timeout,
        )
        logger.info(
            f"MySQL 连接池已初始化: max={self._pool_max}, "
            f"min_cached={self._pool_min_cached}, max_cached={self._pool_max_cached}"
        )

    def _get_conn_sqlite(self):
        """获取 SQLite 连接（线程本地）"""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            import sqlite3
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _get_conn_mysql(self):
        """从连接池借出连接（PooledDB 自动处理 ping/重建/回收，池满有界等待）"""
        if self._pool is None:
            raise RuntimeError("MySQL 连接池未初始化")
        deadline = time.time() + self._pool_wait_timeout
        last_err = None
        while True:
            try:
                return self._pool.connection()
            except Exception as e:
                last_err = e
                if time.time() >= deadline:
                    logger.error(
                        f"MySQL 连接池繁忙: {self._pool_max} 个连接全被占用超 "
                        f"{self._pool_wait_timeout}s（{last_err}）。"
                        f"请检查是否存在连接未归还"
                    )
                    raise RuntimeError(
                        f"MySQL 连接池繁忙（{self._pool_max} 个连接全被占用超 "
                        f"{self._pool_wait_timeout}s），请检查连接泄漏"
                    ) from None
                time.sleep(0.05)

    def _close_thread_conn(self):
        """关闭当前线程的 SQLite 连接并释放线程本地状态。"""
        if self.db_type != 'mysql':
            conn = getattr(self._local, 'conn', None)
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"关闭 SQLite 线程连接失败: {e}")
                try:
                    del self._local.conn
                except Exception:
                    pass

    def _mark_conn_used(self):
        """记录连接最近使用时间（避免频繁 ping）"""
        self._local.last_use = time.time()

    @staticmethod
    def _is_reconnect_error(exc: Exception) -> bool:
        """判断异常是否为 MySQL 连接断开类错误（需要自动重连）"""
        if exc is None:
            return False
        code = None
        if isinstance(getattr(exc, 'args', None), (tuple, list)) and exc.args:
            code = exc.args[0]
        if isinstance(code, int) and code in _MYSQL_RECONNECT_ERRORS:
            return True
        msg = str(exc).lower()
        return any(kw in msg for kw in _MYSQL_RECONNECT_KEYWORDS)

    def _get_conn(self):
        """获取连接。处于事务中时返回被固定（pin）的事务连接。"""
        txn_conn = getattr(self._local, 'txn_conn', None)
        if txn_conn is not None:
            return txn_conn
        if self.db_type == 'mysql':
            return self._get_conn_mysql()
        return self._get_conn_sqlite()

    def _should_commit(self) -> bool:
        """事务内不自动提交（交由外层 transaction() 统一提交/回滚）"""
        return not getattr(self._local, 'in_txn', False)

    def _get_cursor(self):
        """获取游标"""
        return self._get_conn().cursor()

    def _run_with_reconnect(self, func, *args, **kwargs):
        """执行数据库操作，MySQL 连接断开时自动重连并重试（最多 _max_reconnect 次）。"""
        if self.db_type != 'mysql':
            return func(*args, **kwargs)
        for attempt in range(self._max_reconnect + 1):
            try:
                result = func(*args, **kwargs)
                self._mark_conn_used()
                return result
            except Exception as e:
                if not self._is_reconnect_error(e):
                    raise
                if attempt >= self._max_reconnect:
                    logger.error(f"MySQL 连接断开且重连 {self._max_reconnect} 次后仍失败: {e}")
                    raise
                logger.warning(f"MySQL 连接断开（{e}），正在进行第 {attempt + 1} 次自动重连...")
                self._close_thread_conn()
                time.sleep(min(0.5 * (attempt + 1), 3))

    # ── 扩展点（db.* hook，观察型、不短路）──

    def _fire_db_hook(self, point, sql, params, result=None, error=None):
        """线程安全触发 db.* 扩展点；未注入 hooks 或重入时静默跳过。"""
        hooks = self._hooks
        if hooks is None:
            return
        depth = getattr(self._hook_local, 'depth', 0)
        if depth > 0:
            return  # 重入防护：hook 处理器内再次查询不二次触发
        self._hook_local.depth = depth + 1
        try:
            hooks.trigger_sync(point, sql=sql, params=params, result=result, error=error)
        finally:
            self._hook_local.depth = depth

    # ── 公开 API ──

    def query(self, sql: str, params: tuple = None) -> list:
        """查询多条记录，返回 list[dict]"""
        def _do(sql, params):
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                if self.db_type == 'sqlite':
                    sql = _translate_sql_for_sqlite(sql)
                else:
                    sql = _translate_sql_for_mysql(sql)
                self._exec(cursor, sql, params)
                rows = cursor.fetchall()
                if self.db_type == 'sqlite':
                    return [dict(r) for r in rows]
                return rows
            finally:
                cursor.close()
                if self.db_type == 'mysql':
                    conn.close()
        self._fire_db_hook(HookPoints.DB_QUERY_BEFORE, sql, params)
        try:
            result = self._run_with_reconnect(_do, sql, params)
        except Exception as e:
            self._fire_db_hook(HookPoints.DB_QUERY_AFTER, sql, params, error=e)
            raise
        self._fire_db_hook(HookPoints.DB_QUERY_AFTER, sql, params, result=result)
        return result

    def query_one(self, sql: str, params: tuple = None) -> dict:
        """查询单条记录，返回 dict 或 None"""
        def _do(sql, params):
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                if self.db_type == 'sqlite':
                    sql = _translate_sql_for_sqlite(sql)
                else:
                    sql = _translate_sql_for_mysql(sql)
                self._exec(cursor, sql, params)
                row = cursor.fetchone()
                if row is None:
                    return None
                if self.db_type == 'sqlite':
                    return dict(row)
                return row
            finally:
                cursor.close()
                if self.db_type == 'mysql':
                    conn.close()
        self._fire_db_hook(HookPoints.DB_QUERY_BEFORE, sql, params)
        try:
            result = self._run_with_reconnect(_do, sql, params)
        except Exception as e:
            self._fire_db_hook(HookPoints.DB_QUERY_AFTER, sql, params, error=e)
            raise
        self._fire_db_hook(HookPoints.DB_QUERY_AFTER, sql, params, result=result)
        return result

    def _exec(self, cursor, sql: str, params=None):
        """执行 sql，自动处理 params 为 None 的情况"""
        if params is not None:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

    def execute(self, sql: str, params: tuple = None) -> int:
        """执行插入/更新/删除，返回受影响行数"""
        def _do(sql, params):
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                if self.db_type == 'sqlite':
                    if 'NOW()' in sql.upper():
                        sql, params = _replace_now(sql, params)
                    sql = _translate_sql_for_sqlite(sql)
                else:
                    sql = _translate_sql_for_mysql(sql)
                self._exec(cursor, sql, params)
                if self._should_commit():
                    conn.commit()
                return cursor.rowcount
            except Exception:
                if not getattr(self._local, 'in_txn', False):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                raise
            finally:
                cursor.close()
                if self.db_type == 'mysql':
                    conn.close()
        self._fire_db_hook(HookPoints.DB_EXECUTE_BEFORE, sql, params)
        try:
            result = self._run_with_reconnect(_do, sql, params)
        except Exception as e:
            self._fire_db_hook(HookPoints.DB_EXECUTE_AFTER, sql, params, error=e)
            raise
        self._fire_db_hook(HookPoints.DB_EXECUTE_AFTER, sql, params, result=result)
        return result

    def execute_many(self, sql: str, params_list: list) -> int:
        """批量执行，返回受影响行数"""
        def _do(sql, params_list):
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                if self.db_type == 'sqlite':
                    if 'NOW()' in sql.upper():
                        new_sql, _ = _replace_now(sql, params_list[0] if params_list else None)
                        new_params_list = []
                        for p in params_list:
                            _, now_p = _replace_now(sql, p)
                            new_params_list.append(now_p)
                        sql = new_sql
                        params_list = new_params_list
                    sql = _translate_sql_for_sqlite(sql)
                else:
                    sql = _translate_sql_for_mysql(sql)
                cursor.executemany(sql, params_list)
                if self._should_commit():
                    conn.commit()
                return cursor.rowcount
            except Exception:
                if not getattr(self._local, 'in_txn', False):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                raise
            finally:
                cursor.close()
                if self.db_type == 'mysql':
                    conn.close()
        self._fire_db_hook(HookPoints.DB_EXECUTE_BEFORE, sql, params_list)
        try:
            result = self._run_with_reconnect(_do, sql, params_list)
        except Exception as e:
            self._fire_db_hook(HookPoints.DB_EXECUTE_AFTER, sql, params_list, error=e)
            raise
        self._fire_db_hook(HookPoints.DB_EXECUTE_AFTER, sql, params_list, result=result)
        return result

    def insert(self, sql: str, params: tuple = None) -> int:
        """插入并返回自增 ID"""
        def _do(sql, params):
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                if self.db_type == 'sqlite':
                    if 'NOW()' in sql.upper():
                        sql, params = _replace_now(sql, params)
                    sql = _translate_sql_for_sqlite(sql)
                else:
                    sql = _translate_sql_for_mysql(sql)
                self._exec(cursor, sql, params)
                if self._should_commit():
                    conn.commit()
                return cursor.lastrowid
            except Exception:
                if not getattr(self._local, 'in_txn', False):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                raise
            finally:
                cursor.close()
                if self.db_type == 'mysql':
                    conn.close()
        return self._run_with_reconnect(_do, sql, params)

    def get_connection(self):
        """获取原始连接（高级用法）"""
        return self._get_conn()

    def scalar(self, sql: str, params: tuple = None):
        """取单行单列的值。无结果返回 None。"""
        row = self.query_one(sql, params)
        if not row:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()), None)
        return row[0]

    def exists(self, sql: str, params: tuple = None) -> bool:
        """判断查询是否有结果（EXISTS 快捷方式）"""
        return self.query_one(sql, params) is not None

    def count(self, sql: str, params: tuple = None) -> int:
        """执行 COUNT 查询并返回整数结果（无结果返回 0）"""
        v = self.scalar(sql, params)
        return int(v) if v is not None else 0

    @contextmanager
    def transaction(self, conn=None):
        """
        事务上下文管理器：块内 db.execute/insert/execute_many 固定在同一连接上执行，
        正常退出统一提交，块内抛异常自动回滚，保证原子性。
        """
        if conn is None:
            conn = self._get_conn()
            borrowed = self.db_type == 'mysql'
        else:
            borrowed = False
        self._local.txn_conn = conn
        self._local.in_txn = True
        if self.db_type == 'mysql':
            conn.autocommit(False)
        else:
            conn.execute('BEGIN')
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._local.in_txn = False
            self._local.txn_conn = None
            if self.db_type == 'mysql':
                conn.autocommit(True)
                if borrowed:
                    conn.close()

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        if self.db_type == 'sqlite':
            row = self.query_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            return row is not None
        else:
            row = self.query_one("SHOW TABLES LIKE %s", (table_name,))
            return row is not None

    def table_info(self, table_name: str) -> list:
        """获取表结构信息"""
        if self.db_type == 'sqlite':
            return self.query(f"PRAGMA table_info({table_name})")
        else:
            return self.query(f"SHOW COLUMNS FROM {table_name}")

    def table_has_column(self, table_name: str, column_name: str) -> bool:
        """检查表是否有指定列"""
        cols = self.table_info(table_name)
        if self.db_type == 'sqlite':
            return any(r['name'] == column_name for r in cols)
        else:
            return any(r['Field'] == column_name for r in cols)

    @property
    def pool_status(self) -> dict:
        """获取连接池状态"""
        if self.db_type == 'mysql' and self._pool is not None:
            try:
                checked_out = len(getattr(self._pool, '_usage', {}))
                idle = len(getattr(self._pool, '_idle_cache', []))
                return {
                    'type': self.db_type,
                    'max': self._pool_max,
                    'min_cached': self._pool_min_cached,
                    'max_cached': self._pool_max_cached,
                    'checked_out': checked_out,
                    'idle': idle,
                    'total': checked_out + idle,
                }
            except Exception:
                pass
        return {
            'type': self.db_type,
            'path': getattr(self, '_db_path', None),
        }

    def close(self):
        """关闭连接：MySQL 关闭整个连接池，SQLite 关闭当前线程连接"""
        if self.db_type == 'mysql':
            if self._pool is not None:
                try:
                    self._pool.close()
                except Exception as e:
                    logger.warning(f"关闭 MySQL 连接池失败: {e}")
        else:
            self._close_thread_conn()
