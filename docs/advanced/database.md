# 数据库

Zeronus 内置一套**双方言**数据库抽象：SQLite（默认，零配置）与 MySQL 共用同一套 API，
SQL 由框架按方言自动翻译。

## 一、两种模式

```yaml
# config.yaml
database:
  type: sqlite                 # 默认
  path: data/zernus.db
```

```yaml
database:
  type: mysql
  host: 127.0.0.1
  port: 3306
  user: root
  password: ''
  database: zernus
  ping_interval: 5             # 连接保活（空闲多久后 ping）
  connect_timeout: 10
  read_timeout: 30
  write_timeout: 30
  max_reconnect: 3             # 单次操作最大自动重连次数
```

检测到 MySQL 配置时会**自动安装** `pymysql` / `DBUtils`，无需手动装。

## 二、建表

系统表在启动时自动创建 / 补齐（`sql/init.sql`）。**业务表由插件自管**：

```python
def register(ctx):
    ctx.db_execute("""
        CREATE TABLE IF NOT EXISTS greet_log (
            id   INTEGER PRIMARY KEY,
            uid  INTEGER,
            ts   REAL
        )
    """)
```

> `sql/init.sql` 面向 SQLite，`sql/init_mysql55.sql` 是 MySQL 兼容版本，框架按方言选文件。

## 三、参数占位符统一用 `?`

你只管写 `?`，框架按方言翻译（SQLite `?` ↔ MySQL `%s`）：

```python
ctx.db_execute("INSERT INTO greet_log (uid, ts) VALUES (?, ?)", (event.user_id, time.time()))
rows = ctx.db_query("SELECT * FROM greet_log WHERE uid = ? ORDER BY ts DESC LIMIT 10", (event.user_id,))
```

## 四、插件里的数据库 API

| 方法 | 返回 |
| ---- | ---- |
| `ctx.db_query(sql, params=None)` | `list[dict]`（每行一个字典） |
| `ctx.db_query_one(sql, params=None)` | `dict` 或 `None` |
| `ctx.db_execute(sql, params=None)` | 受影响行数 |
| `ctx.db_execute_many(sql, params_list)` | 受影响行数 |
| `ctx.db_insert(sql, params=None)` | 新插入行的 id |
| `ctx.db_connection()` | 原始连接（高级用法） |

**异步版**（`async def` handler 推荐，走数据库专用线程池）：
`db_query_async` / `db_query_one_async` / `db_execute_async` / `db_execute_many_async` / `db_insert_async`。

```python
async def on_cmd(event, match):
    rows = await ctx.db_query_async("SELECT COUNT(*) AS c FROM greet_log")
    await ctx.asend_msg(group_id=event.group_id, user_id=None, message=str(rows[0]["c"]))
```

## 五、事务

用 `Database.transaction()` 上下文管理器。它是**同连接**事务：块内 `execute/insert` 都固定在一条连接上，
正常退出统一提交，抛异常自动回滚。

```python
db = ctx._framework.db

with db.transaction():
    db.execute("UPDATE accounts SET balance = balance - ? WHERE uid = ?", (10, a))
    db.execute("UPDATE accounts SET balance = balance + ? WHERE uid = ?", (10, b))
```

> 事务内的多条语句共享同一连接；事务外的单条操作请用 `execute()` / `query()` 或 `ctx` 的 db 系列方法。

## 六、数据库层的便捷方法

`Database` 还提供：

| 方法 | 说明 |
| ---- | ---- |
| `scalar(sql, params)` | 取单行单列的值 |
| `exists(sql, params)` | 查询是否有结果 |
| `count(sql, params)` | COUNT 结果转 int |
| `table_exists(name)` / `table_info(name)` / `table_has_column(t, c)` | 结构自省 |
| `pool_status()` | 连接池状态 |
| `close()` | 关闭连接池 |

`ctx.db_pool_status` 可直接读到连接池状态。

## 七、schema 迁移

启动时会自动执行增量迁移（`core/storage/migrations.py`），例如给老表补列。
所以升级框架后一般不需要手动改表。

## 八、扩展点

数据库读写是**可观察**的，四个扩展点可用于审计、慢查询统计或同步：

| 扩展点 | 时机 |
| ---- | ---- |
| `db.query.before` / `db.query.after` | 查询前 / 后（参数 `sql, params`，异常时 `after` 带 `error`） |
| `db.execute.before` / `db.execute.after` | 写操作前 / 后（同上） |

```python
import time

def register(ctx):
    ctx.hook("db.execute.after", on_write)

def on_write(sql=None, params=None, result=None, error=None, **kw):
    if error:
        ctx.log(f"[DB 失败] {sql} -> {error}", level="error")
```

详见[扩展点](../api/advanced/hooks.md)。

## 九、实践建议

- **热路径别查库**：命令 handler 里能缓存的（权限、配置）用 `ctx.cache_set/get`；
- **异步 handler 优先异步 API**：避免阻塞事件循环；
- **批量写用 `db_execute_many`** 或放事务里；
- **表名加插件前缀**（如 `mytool_log`）避免与其他插件撞名；
- **别把业务表塞进系统表**：系统表由框架维护，升级可能迁移。

---

延伸：[ctx 数据库方法](../api/basic/ctx.md#七数据库) · [权限系统](./permission.md) · [架构总览](./architecture.md)
