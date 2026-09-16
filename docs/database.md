# 数据库

Zeronus 在 `core/` 提供**三方言**数据库抽象，对上层屏蔽差异。

## 方言切换

由 `config.yaml → database.type` 控制：

| type | 说明 | 额外依赖 |
| --- | --- | --- |
| `sqlite` | 默认，零配置，文件 `data/zernus.db` | 无 |
| `mysql` | 需手动建库 | `pymysql`（框架按需自动安装） |
| `postgresql` | 需手动建库 | `psycopg2`（框架按需自动安装） |

## 方言翻译层（`core/storage/dialect.py`）

为兼容三种方言，SQL 在执行前会被翻译：

- 占位符：`%s` → `?`（SQLite）；
- 函数：`NOW()` → 本地时间；
- `DEFAULT CURRENT_TIMESTAMP` → `DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))`（SQLite，幂等写法，避免返回 UTC 与本地时间不一致）；
- 类型转换：`DATETIME` → `TEXT`（大小写不敏感）。

> 翻译对注入的 SQL 必须**幂等**：`init_db.py` 与 `engine.execute` 都会调用，非幂等会导致二次翻译出错。

## 查询 API（一致）

SQLite 与 MySQL 返回均为 `list[dict]`。常用接口：

| 方法 | 说明 |
| --- | --- |
| `query(sql, params)` | 批量查询（**没有** `query_all`） |
| `query_one(sql, params)` | 取单行 |
| `execute(sql, params)` | 执行写操作 |
| `execute_many(sql, seq)` | 批量写 |
| `insert(table, row)` | 插入一行并返回 |
| `scalar(sql, params)` | 取单值 |
| `count(table, where)` | 计数 |
| `exists(table, where)` | 是否存在 |
| `table_exists(name)` / `table_has_column(t, c)` | 结构探测 |

> 注意：批量查询的入口是 `query()`，不要使用不存在的 `query_all()`。

## 建表与种子

- 建表脚本：`sql/init.sql`（MySQL 5.7 风格，经 dialect 翻译后也适用于 SQLite）、`sql/init_mysql55.sql`。
- 默认管理员账号：种子数据写入 `admin / admin123`，**哈希使用 `pbkdf2_sha256`**（标准库 `hashlib`，不依赖可选的 `bcrypt`）。
- 启动自愈：若库为空，`core/storage/migrations.py::_ensure_admin_account` 会自动种入管理员账号；若遗留 bcrypt 哈希且未登录，会按默认密码重置并告警。

## 时区

SQLite 的 `DEFAULT CURRENT_TIMESTAMP` 返回 UTC，而框架其余写入为本地时间。dialect 已用本地 `strftime` 改写，确保一致。权限系统等时间字段统一存 **unix 时间戳字符串**，保证 SQLite / MySQL 行为一致。
