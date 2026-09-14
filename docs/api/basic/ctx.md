# PluginContext (ctx) 完整参考

> **面向**：插件开发者。写插件时最常查的能力清单。

`ctx` 是插件与框架交互的**唯一入口**，由框架在 `register(ctx)` 时注入，并挂到插件主模块上
（handler 里可直接使用全局 `ctx`）。

绝大多数能力都提供**同步**与**异步**两个版本：普通函数 handler 用同步方法；
`async def` handler 优先用带 `a` 前缀的异步方法，避免阻塞事件循环。

## 目录

1. [属性](#一属性)
2. [命令注册](#二命令注册)
3. [消息发送与接入端动作](#三消息发送与接入端动作)
4. [群管快捷方法与身份](#四群管快捷方法与身份)
5. [事件订阅与发布](#五事件订阅与发布)
6. [配置](#六配置)
7. [数据库](#七数据库)
8. [权限](#八权限)
9. [多轮会话](#九多轮会话)
10. [定时任务与任务调度](#十定时任务与任务调度)
11. [WebUI 与仪表盘](#十一webui-与仪表盘)
12. [文件 / HTTP / 序列化](#十二文件--http--序列化)
13. [缓存与依赖注入](#十三缓存与依赖注入)
14. [扩展点](#十四扩展点)
15. [工具类](#十五工具类)

---

## 一、属性

| 属性 | 类型 | 说明 |
| ---- | ---- | ---- |
| `ctx.plugin_name` | `str` | 当前插件名（用户插件即目录名） |
| `ctx.logger` | `logging.Logger` | 带插件名前缀的标准库 logger |
| `ctx.onebot` | 动作封装 | OneBot 11 API 封装（`onebot_api` 服务）；支持任意动态 action |
| `ctx.command_bus` | `CommandBus` | 内核命令原语（`register` / `invoke`） |
| `ctx.db_pool_status` | `dict` | 数据库连接池状态 |

```python
print(ctx.plugin_name)
ctx.logger.info("用标准 logger 也可以")
```

## 二、命令注册

```python
ctx.command(
    pattern,                 # 命令名或正则
    handler,                 # (event, match) -> None
    priority=50,             # 越小越先匹配
    dynamic=False,           # 登记到后台「动态命令」
    alias="/h,/hello2",      # 别名（逗号分隔字符串或列表）
    description="打招呼",     # 不填则取 docstring 首行
    require_admin=False,     # 需要管理员/群主/超管
    require_superuser=False, # 需要超管
    require_perm=None,       # 权限节点，如 'greeter.ban'
)
```

`pattern` 含正则元字符时按**正则**匹配，否则按**字面串**匹配；别名同理。示例：

```python
ctx.command(r"/echo\s+(.+)", on_echo)

def on_echo(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message=match.group(1))
```

## 三、消息发送与接入端动作

| 方法 | 说明 |
| ---- | ---- |
| `ctx.send_msg(user_id=None, group_id=None, message=...)` / `await ctx.asend_msg(...)` | 协议无关发消息 |
| `ctx.api(action, bot=None, **params)` / `await ctx.aapi(...)` | 调任意接入端动作 |
| `ctx.onebot.<action>(...)` | OneBot 11 专用动作（社区扩展、群管、查询等） |

```python
ctx.send_msg(group_id=10001, user_id=None, message="你好")
ctx.api("set_group_ban", group_id=10001, user_id=10002, duration=60)
ctx.onebot.get_group_member_list(group_id=10001)
```

## 四、群管快捷方法与身份

群管快捷方法（内部走协议无关动作）：

| 方法 | 说明 |
| ---- | ---- |
| `ctx.ban(group_id, user_id, duration=600)` / `await ctx.aban(...)` | 禁言 |
| `ctx.kick(group_id, user_id, reject_add_request=False)` / `await ctx.akick(...)` | 踢人 |
| `ctx.mute_all(group_id, enable=True)` / `await ctx.amute_all(...)` | 全员禁言 |
| `ctx.set_card(group_id, user_id, card)` / `await ctx.aset_card(...)` | 改群名片 |
| `ctx.get_member_list(group_id)` / `await ctx.aget_member_list(...)` | 群成员列表 |
| `ctx.get_member_info(group_id, user_id)` / `await ctx.aget_member_info(...)` | 群成员信息 |

身份与群级开关：

| 方法 | 说明 |
| ---- | ---- |
| `ctx.is_group_admin(group_id, user_id)` | 是否群管理员（含群主） |
| `ctx.is_group_owner(group_id, user_id)` | 是否群主 |
| `ctx.is_superuser(user_id)` | 是否框架超管 |
| `ctx.is_blacklisted(user_id)` | 是否黑名单 |
| `ctx.get_user_role(group_id, user_id)` | 取角色字符串 |
| `ctx.enable_plugin_in_group(plugin_name, group_id)` / `disable_...` | 群级插件开关 |
| `ctx.is_plugin_enabled_in_group(plugin_name, group_id)` | 查询群级开关 |
| `ctx.get_plugin_status_list(group_id)` | 该群全部插件开关状态 |

## 五、事件订阅与发布

| 方法 | 说明 |
| ---- | ---- |
| `ctx.on(event_name, handler)` | 订阅业务事件 |
| `ctx.on_raw_message(handler)` | 订阅命令匹配前的原始消息 |
| `ctx.emit(event_name, payload=None)` / `await ctx.aemit(...)` | 发布事件 |
| `ctx.once(event_name, handler)` | 只触发一次，之后自动退订 |
| `ctx.off(event_name, handler=None)` | 退订单个；不给 handler 则退订本插件在该事件的全部订阅 |
| `await ctx.await_event(event_name, timeout=None)` | 异步等待某事件，超时抛 `asyncio.TimeoutError` |

```python
ctx.on("notice.group_recall", on_recall)
ctx.once("plugin.load", on_first_load)
payload = await ctx.await_event("myext.done", timeout=30)
```

## 六、配置

| 方法 | 说明 |
| ---- | ---- |
| `ctx.get_config(key, default=None)` | 读插件配置（`data/plugins_dat/<名>/plugin.yaml`） |
| `ctx.get_all_config()` | 取全部插件配置 |

```python
greet = ctx.get_config("greet_text", "Hello")
```

## 七、数据库

**同步**：

| 方法 | 返回 |
| ---- | ---- |
| `ctx.db_query(sql, params=None)` | `list[dict]` |
| `ctx.db_query_one(sql, params=None)` | `dict` 或 `None` |
| `ctx.db_execute(sql, params=None)` | 受影响行数 |
| `ctx.db_execute_many(sql, params_list)` | 受影响行数 |
| `ctx.db_insert(sql, params=None)` | 新插入行的 id |
| `ctx.db_connection()` | 原生连接（事务/批量） |

**异步**（`async def` handler 里用，走数据库专用线程池）：`db_query_async` / `db_query_one_async` /
`db_execute_async` / `db_execute_many_async` / `db_insert_async`。

占位符统一用 `?`，框架按方言自动翻译（SQLite `?` ↔ MySQL `%s`）：

```python
ctx.db_execute("CREATE TABLE IF NOT EXISTS demo (uid INTEGER, ts REAL)")
ctx.db_execute("INSERT INTO demo (uid, ts) VALUES (?, ?)", (event.user_id, time.time()))
rows = await ctx.db_query_async("SELECT * FROM demo WHERE uid = ?", (event.user_id,))
```

详见[数据库](../../advanced/database.md)。

## 八、权限

| 方法 | 说明 |
| ---- | ---- |
| `ctx.has_perm(user_id, node, context=None, role=None) -> bool` | 是否有权限节点 |
| `ctx.check_perm(user_id, node, context=None, role=None)` | 三态（允许 / 拒绝 / 未设置） |
| `ctx.user_groups(user_id, context=None, role=None) -> list` | 命中的权限组 |

> 事件内更常用 `event.has_perm(node)` / `event.check_perm(node)`（上下文自动从事件构造）。
> 详见[权限系统](../../advanced/permission.md)。

## 九、多轮会话

| 方法 | 说明 |
| ---- | ---- |
| `await ctx.wait_for(event, prompt=None, timeout=60, handler=None)` | 发提示并等下一句 |
| `ctx.create_session(event, timeout=60)` | 建立会话，返回 `Session`（`.ask()` / `.wait()` / `.close()`） |

详见[多轮会话](../../guide/session.md)。

## 十、定时任务与任务调度

| 方法 | 说明 |
| ---- | ---- |
| `ctx.task(cron_expr, executor, description=None)` | 声明式注册定时任务（5 字段 cron） |
| `ctx.add_job(handler, cron_expression, description='', job_id=None) -> str` | 快捷注册，返回任务键 |
| `ctx.remove_job(job_id)` | 移除任务 |

```python
ctx.task("0 8 * * *", daily_report, description="每日报表")

key = ctx.add_job(my_task, "*/5 * * * *", description="每 5 分钟")
ctx.remove_job("my_task")
```

详见[定时任务](../../advanced/scheduler.md)。

## 十一、WebUI 与仪表盘

| 方法 | 说明 |
| ---- | ---- |
| `ctx.dashboard_card(title, handler, icon=None, priority=50)` | 注册仪表盘卡片 |
| `ctx.webui(title, entry='index.html', icon=None, order=50, sidebar=False)` | 注册插件 WebUI 页 |
| `ctx.override_webui()` | 接管整个后台 |
| `ctx.register_group_extension(key, title, handler, ext_type='column')` | 群详情页扩展 |
| `ctx.register_user_extension(key, title, handler, ext_type='column')` | 用户详情页扩展 |
| `ctx.register_api(path, handler, methods=None, auth=True, ...)` | 注册自定义 REST 路由（复用框架鉴权） |

## 十二、文件 / HTTP / 序列化

**文件**（严格限定在插件数据目录内，默认拒绝绝对路径）：

| 方法 | 说明 |
| ---- | ---- |
| `ctx.read_file(name, encoding='utf-8', allow_abs=False)` | 读文本 |
| `ctx.write_file(name, content, encoding='utf-8', allow_abs=False)` | 写文本（自动建父目录） |
| `ctx.list_dir(subdir='', include_dirs=False)` | 列目录（相对名） |

**HTTP**（`urllib`，返回 `(status_code, body_text)`）：

| 方法 | 说明 |
| ---- | ---- |
| `ctx.http_get(url, params=None, headers=None, timeout=10)` | 同步 GET |
| `ctx.http_post(url, data=None, json_body=None, headers=None, timeout=10)` | 同步 POST |
| `await ctx.http_get_async(...)` / `await ctx.http_post_async(...)` | 异步版（线程池，不阻塞事件循环） |

**序列化**：

| 方法 | 说明 |
| ---- | ---- |
| `ctx.load_json(text, default=None)` / `ctx.dump_json(obj)` | JSON（`ensure_ascii=False`、缩进 2） |
| `ctx.load_yaml(text)` / `ctx.dump_yaml(obj)` | YAML（需 `pyyaml`） |

## 十三、缓存与依赖注入

**TTL 缓存**：

```python
ctx.cache_set('demo:k', 42, ttl=60)     # ttl 秒，缺省 300
v = ctx.cache_get('demo:k', default=None)
ctx.cache_delete('demo:k')
```

> 键请加插件名前缀（如 `demo:k`）避免与其他插件碰撞。

**依赖注入**：

```python
ctx.provide('client', obj)                       # 直接注册单例
ctx.provide('lazy', factory=lambda: build())     # 惰性单例（只构造一次）
c = ctx.inject('client')
d = ctx.inject('missing', default=None)          # 未注册且无 default 时抛 KeyError
```

## 十四、扩展点

| 方法 | 说明 |
| ---- | ---- |
| `ctx.hook(point, handler, priority=50)` | 在某个扩展点挂 handler（越小越先执行） |
| `ctx.unhook(point)` | 移除本插件在该扩展点的全部 handler |

```python
ctx.hook('action.after', audit)
ctx.hook('event.before_dispatch', drop_blocked, priority=10)
```

完整点位清单见[扩展点（Hook 系统）](../advanced/hooks.md)。

## 十五、工具类

| 方法 | 说明 |
| ---- | ---- |
| `ctx.log(msg, level='info')` | 写日志（`debug` / `info` / `warning` / `error`） |
| `ctx.run_async(func, *args, **kwargs)` | 把耗时任务丢到内置线程池，返回 `Future` |
| `ctx.audit_log(action, target_type=None, target_name=None, detail=None, result='success', error_message=None)` | 以插件身份写审计日志 |
| `ctx.call_async(coro)` | 在同步上下文里安全调度一个协程 |
| `ctx.get_data_dir() -> str` | 插件私有数据目录（`data/plugins_dat/<名>/`，不存在则创建） |

---

> 想在这些能力之外插入行为？见[扩展点（Hook 系统）](../advanced/hooks.md)。
