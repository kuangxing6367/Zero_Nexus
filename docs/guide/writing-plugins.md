# 编写插件

本篇从零写一个完整插件，覆盖命令、事件、数据库、配置、权限、定时任务与多文件组织。

## 一、最小插件

一个插件就是一个目录，放在 `software/plugins/` 下，入口固定为 `main.py`，必须提供 `register(ctx)`：

```
plugins/
└── greeter/
    └── main.py
```

```python
# software/plugins/greeter/main.py
def register(ctx):
    ctx.command("/hello", on_hello, description="打个招呼")

def on_hello(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message="Hello, World!")
```

- `ctx` 由框架在 `register(ctx)` 时注入，并挂到插件主模块上，handler 里可直接用全局 `ctx`；
- handler 签名固定为 `(event, match)`：`event` 是事件对象，`match` 是正则匹配结果（无捕获组时为 `None`）。

放好后在后台「插件」页启用，或重启框架。

## 二、命令注册

```python
ctx.command(
    pattern,                 # 主匹配串：命令名或正则
    handler,                 # (event, match) -> None
    priority=50,             # 越小越先匹配
    dynamic=False,           # 是否登记到后台「动态命令」
    alias="/h,/hello2",      # 别名，逗号分隔字符串或列表
    description="打招呼",     # 不填则取 handler 的 docstring 首行
    require_admin=False,     # 需要管理员/群主/超管
    require_superuser=False, # 需要超管（优先于 require_admin）
    require_perm=None,       # 权限节点，如 'greeter.hello'
)
```

**匹配规则**：`pattern` 若形如正则（含正则元字符）则按**正则**匹配，否则按**字面串**匹配；
`alias` 里的每个别名都按同样方式参与匹配。命令在内存路由表里按 `priority` 从小到大依次尝试。

带捕获组的正则示例：

```python
ctx.command(r"/echo\s+(.+)", on_echo)

def on_echo(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message=match.group(1))
```

## 三、同步与异步

```python
def register(ctx):
    ctx.command("/slow", on_slow)          # 普通函数 → 同步方法

async def on_slow(event, match):           # async handler → 用异步方法
    rows = await ctx.db_query_async("SELECT 1")
    await ctx.asend_msg(group_id=event.group_id, user_id=None, message="done")
```

> **推荐异步**：同步方法内部桥接到线程/事件循环，异步方法（带 `a` 前缀）零线程切换、不阻塞事件循环。

## 四、事件订阅

```python
def register(ctx):
    ctx.on("notice.group_recall", on_recall)          # 业务事件
    ctx.on_raw_message(on_any_message)               # 命令匹配前的原始消息
    ctx.once("plugin.load", on_first_load)           # 只触发一次
    ctx.emit("greeter.hello", {"user": 1})           # 发布自定义事件

def on_any_message(event):
    ctx.log(f"原始消息: {event.message}")
```

`ctx.on / once / off / emit / aemit` 与 `await ctx.await_event(name, timeout)` 详见 [ctx 参考](../api/basic/ctx.md)。

## 五、数据库

```python
def register(ctx):
    ctx.db_execute("CREATE TABLE IF NOT EXISTS greet_log (uid INTEGER, ts REAL)")

def on_hello(event, match):
    ctx.db_execute("INSERT INTO greet_log (uid, ts) VALUES (?, ?)", (event.user_id, time.time()))
    n = ctx.db_query_one("SELECT COUNT(*) AS c FROM greet_log")["c"]
    ctx.send_msg(group_id=event.group_id, user_id=None, message=f"第 {n} 次打招呼")
```

- 建表：插件在 `register` 里 `CREATE TABLE IF NOT EXISTS`（框架不代管你的业务表）；
- 占位符用 `?`：框架会按数据库方言自动翻译（SQLite `?` ↔ MySQL `%s`）；
- 复杂写入用 `ctx._framework.db.transaction()` 事务；异步场景用 `db_query_async` 等。

详见[数据库](../advanced/database.md)。

## 六、配置

插件配置写在插件自己的 `plugin.yaml`（存放在 `data/plugins_dat/<插件名>/`），在代码里读：

```python
def register(ctx):
    greet = ctx.get_config("greet_text", "Hello")

def on_hello(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message=ctx.get_config("greet_text"))
```

`ctx.get_all_config()` 取全部；后台「插件」页可在线改。

## 七、权限

```python
ctx.command("/ban", on_ban, require_perm="greeter.ban")          # 权限节点
ctx.command("/admin_only", on_admin, require_admin=True)          # 管理员/群主/超管
ctx.command("/super_only", on_super, require_superuser=True)      # 超管

def on_ban(event, match):
    if not event.has_perm("greeter.ban.others"):
        return
```

- 命令级：`require_perm` / `require_admin` / `require_superuser` 任一满足即放行；
- 事件内：`event.has_perm(node)` / `event.check_perm(node)`（三态）；
- 非事件场景：`ctx.has_perm(uid, node, context=..., role=...)`。

详见[权限系统](../advanced/permission.md)。

## 八、定时任务

```python
def register(ctx):
    ctx.task("0 8 * * *", daily_report, description="每日报表")   # 5 字段 cron

def daily_report():
    ctx.log("跑每日报表")
```

也可用 `ctx.add_job(handler, cron_expression, ...)` / `ctx.remove_job(job_id)` 动态增删。

详见[定时任务](../advanced/scheduler.md)。

## 九、插件私有数据

```python
d = ctx.get_data_dir()                       # data/plugins_dat/greeter/
ctx.write_file("cache.json", ctx.dump_json(data))
```

`ctx.get_data_dir()` 会自动创建目录；`ctx.read_file / write_file / list_dir` 默认只允许写在这个目录内
（绝对路径需显式 `allow_abs=True`）。

## 十、插件要拆多个文件

多文件插件需要用**相对导入**，框架已用「合成包」机制支持：

```
plugins/
└── mytool/
    ├── main.py
    └── util.py
```

```python
# main.py
from .util import helper          # 相对导入

def register(ctx):
    ctx.command("/x", lambda e, m: helper(ctx, e))
```

> 多文件、嵌套包、短名导入的细节与踩坑见[插件加载与模块机制](../advanced/loader.md)（**多文件插件必读**）。

## 十一、热重载

后台「插件」页点重载，或在终端执行重载命令，框架会：

1. 卸载插件（调用可能的清理、注销命令/事件/扩展点）；
2. 从磁盘重新加载 `main.py` 与依赖子模块（走源码现场编译，避免旧字节码残留）；
3. 重新执行 `register(ctx)`。

对应的扩展点：`plugin.unload` / `plugin.load`。

## 十二、调试建议

- 用 `ctx.log("...")` 写日志，后台「日志」页实时可见；
- 用 `ctx.log(..., level="debug")` + `config.yaml` 里 `log.level: DEBUG` 打开详细日志；
- 插件内存有上限（`plugin.max_memory_mb`），超限会被自动卸载——注意别在内存里堆积数据；
- 启动自检：`python main.py` 的横幅会列出已加载的官方扩展与用户插件。

---

延伸阅读：[ctx 完整参考](../api/basic/ctx.md) · [Event 事件对象](../api/basic/event.md) ·
[扩展点](../api/advanced/hooks.md) · [多轮会话](./session.md) · [最佳实践](./best-practices.md)
