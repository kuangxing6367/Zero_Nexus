# 扩展点（Hook 系统）

> **面向**：所有想"往框架里插入自己逻辑"的开发者。
> 这是**内核级（`core/`）对外开放的核心契约**——内核只做机制，其余策略行为都通过扩展点开放。

## 一句话理解

内核（`Framework`）只负责：**加载扩展、路由事件、提供公共服务**（数据库 / 权限 / 服务注册 / 事件总线）。
它不规定"消息来了该干什么、Web 请求该怎么拦、动作发出前要不要审计"——这些全部留给**扩展点（hook point）**。

内核级 / 服务级 / 软件级三层中，扩展点属于**内核级机制**；服务级（`zkg` / 看门狗）与软件级（官方扩展 / 用户插件）
都通过它接入内核运行流程。

一个扩展点就是内核运行流程上的一个"插槽"。扩展用 `ctx.hook(point, handler)` 往里插函数，
内核跑到那个环节就按优先级依次调用。于是你可以在**几乎每一个运行环节**挂载行为，
而无需修改框架源码、无需继承任何基类。

```
        ┌────────────── 内核（Framework） ──────────────┐
        │ 生命周期 · HTTP · 事件分发 · 命令 · 消息收发    │
        │ 协议动作 · 服务注册 · 插件装卸 · 数据库读写     │
        │ 多轮会话 · 定时任务触发                        │
        └───────────────────────┬───────────────────────┘
                                │ ctx.hook(point, handler)
                        你的审计 / 限流 / 过滤 / 中间件
```

## 一、26 个标准扩展点

| 分组 | 扩展点 | 触发时机 | handler 参数 | 可短路？ | 运行上下文 |
| ---- | ------ | -------- | ------------ | -------- | ---------- |
| 生命周期 | `lifecycle.startup` | 全部扩展/插件加载注册完成后、终端启动前 | — | 否 | 事件循环（async） |
| 生命周期 | `lifecycle.shutdown` | 框架停止最开始 | — | 否 | 事件循环（async） |
| HTTP | `http.before_request` | 每个 HTTP 请求处理前 | `request` | **是**（返回 Flask Response 即短路） | Web 线程（sync） |
| HTTP | `http.after_request` | 每个 HTTP 请求返回前 | `request, response` | 否（须返回 response） | Web 线程（sync） |
| 事件分发 | `event.before_dispatch` | 事件进入内核、分发前 | `event(dict), bot_name` | **是**（返回 `False` 丢弃事件） | 事件循环（async） |
| 事件分发 | `event.after_dispatch` | 事件分发处理完毕后（含提前返回） | `event(dict), bot_name` | 否 | 事件循环（async） |
| 命令 | `command.before` | 命令命中、执行 handler 前 | `{'plugin','handler','command_id','pattern','event','match'}` | **是**（返回 `False` 跳过该命令） | 事件循环（async） |
| 命令 | `command.after` | 命令 handler 执行后 | 同上 + `result` | 否（通知） | 事件循环（async） |
| 消息出站 | `message.before_send` | 框架主动发文本前（权限提示、关键词回复等） | `{'text','group_id','user_id','source'}` | **是**（返回 `False` 取消发送） | 事件循环（async） |
| 消息出站 | `message.after_send` | 框架主动发文本后 | 同上 + `result` | 否 | 事件循环（async） |
| 协议动作 | `action.before` | 任意协议动作调用前（send_msg / 禁言 / 查询…） | `action(str), params(dict), bot` | 否（通知） | 视调用方（async/sync） |
| 协议动作 | `action.after` | 任意协议动作调用后 | `action, params, bot, result` | 否（通知） | 视调用方（async/sync） |
| 服务注册 | `service.register.before` | 服务注册进注册表前 | `name, service`（kwargs） | 否 | 视调用方 |
| 服务注册 | `service.register.after` | 服务注册完成后 | `name, service`（kwargs） | 否 | 视调用方 |
| 插件装卸 | `plugin.load` | 插件命令注册完成、加载就绪后 | `plugin_name` | 否 | 加载线程 |
| 插件装卸 | `plugin.unload` | 插件卸载时 | `plugin_name` | 否 | 加载线程 |
| 数据库 | `db.query.before` | 每次查询（`query` / `query_one`）前 | `sql, params` | 否 | 调用线程 |
| 数据库 | `db.query.after` | 查询返回或异常后 | `sql, params, result`（异常时传 `error`） | 否 | 调用线程 |
| 数据库 | `db.execute.before` | 每次写操作（`execute` / `execute_many`）前 | `sql, params` | 否 | 调用线程 |
| 数据库 | `db.execute.after` | 写操作返回或异常后 | `sql, params, result`（异常时传 `error`） | 否 | 调用线程 |
| 多轮会话 | `session.create.before` | 创建会话前 | `key, ctx, event, timeout`（kwargs） | 否 | 事件循环 |
| 多轮会话 | `session.create.after` | 会话创建后 | `key, session`（kwargs） | 否 | 事件循环 |
| 多轮会话 | `session.wait.before` | 等待用户回复前 | `key, timeout`（`wait_for` 场景附 `ctx, event`） | 否 | 事件循环（async） |
| 多轮会话 | `session.wait.after` | 等待结束 / 超时后 | 同上 + `result` | 否 | 事件循环（async） |
| 定时任务 | `cron.task.trigger.before` | 定时任务触发、执行前 | `plugin_name, handler_name`（kwargs） | 否 | 事件循环（async） |
| 定时任务 | `cron.task.trigger.after` | 定时任务执行后 | `plugin_name, handler_name, status`（kwargs） | 否 | 事件循环（async） |

> 除标注**可短路**的 4 个外，其余都是**观察型（通知）**：handler 的返回值不影响主流程。
> `action.before/after` 覆盖面最广——只要通过 `ctx.api()` / `ctx.aapi()` / `ctx.onebot.*` 发出的动作都会经过它。

## 二、注册与注销

```python
def register(ctx):
    ctx.hook('action.after', on_action, priority=10)          # 普通函数
    ctx.hook('event.before_dispatch', on_event, priority=50)
    ctx.hook('http.after_request', add_header)

async def on_action(action, params, bot, result):
    ctx.log(f"动作 {action} -> {result.get('status')}")

def on_event(event, bot_name):
    if event.get('user_id') in BLOCKLIST:
        return False        # 丢弃该事件

def add_header(request, response):
    response.headers['X-Powered-By'] = 'Zeronus'
    return response
```

要点：

- **优先级**：`priority` 越小越先执行（默认 50）；多个插件挂同一点按优先级串行调用。
- **同名去重**：内核以 `插件名:扩展点` 作为 handler 唯一名，热重载时重复 `ctx.hook` 同一点会自动覆盖，不会越挂越多。
- **注销**：`ctx.unhook(point)` 移除本插件在该点的全部 handler；插件卸载时框架也会自动清理（`clear_plugin`）。
- **自定义点位**：点位只是字符串，你可以注册任意自定义点位（如 `'myext.on_tick'`），
  并在自己代码里用 `ctx._framework.hooks.trigger_async('myext.on_tick', ...)` 触发，实现插件内部发布/订阅。

## 三、同步 vs 异步 handler

| 运行上下文 | 推荐写法 | 说明 |
| ---------- | -------- | ---- |
| 事件循环内（lifecycle / event / command / message / session / cron / action 经 `aapi`） | `async def` 直接 `await` | 与内核同循环，零线程切换 |
| Web 线程内（`http.*`） | 普通函数 | 写成 `async def` 会被交给事件循环 **fire-and-forget**（不阻塞请求线程） |
| 数据库（`db.*`） | 普通函数 | 在发起查询的线程里同步执行 |

> 在 `http.*` 里要做异步 DB？用 `ctx.call_async(coro)` 或 `ctx.db_query_async(...)`，不要自己 `await` 阻塞请求线程。

## 四、短路语义

只有这 4 个扩展点能改变流程：

- `http.before_request`：返回带 `status_code` 的响应对象（如 `jsonify(...)` / `redirect(...)`）即短路，内核直接返回它，不再走后续路由。
- `event.before_dispatch`：任一 handler 返回 `False` → 事件被丢弃，不再路由 / 广播。
- `command.before`：任一 handler 返回 `False` → 跳过当前命令执行。
- `message.before_send`：任一 handler 返回 `False` → 取消本次框架文本发送。

其余全部不短路（包括 `action.before`：动作已不可避免，仅作通知 / 审计）。

## 五、示例

### 审计每一次出站动作

```python
def register(ctx):
    ctx.hook('action.after', audit)

def audit(action, params, bot, result):
    ctx.log(f"[审计] {action} bot={bot} status={result.get('status')}")
```

### 丢弃黑名单来源的事件

```python
BLOCK = {123456}

def register(ctx):
    ctx.hook('event.before_dispatch', drop_blocked)

def drop_blocked(event, bot_name):
    if event.get('user_id') in BLOCK:
        return False
```

### 命令执行切面（计时）

```python
import time

def register(ctx):
    ctx.hook('command.before', cmd_enter)
    ctx.hook('command.after', cmd_exit)

_t = {}

def cmd_enter(info):
    _t[info['command_id']] = time.time()

def cmd_exit(info):
    cost = time.time() - _t.pop(info['command_id'], time.time())
    ctx.log(f"[命令耗时] {info['handler']} 用时 {cost:.3f}s")
```

### 启动预热

```python
def register(ctx):
    ctx.hook('lifecycle.startup', warm_up)

async def warm_up():
    rows = await ctx.db_query_async("SELECT word FROM sensitive_words")
    ctx._cache = {r['word'] for r in rows}
    ctx.log(f"敏感词缓存已预热：{len(ctx._cache)} 条")
```

## 六、性能与注意

- **空载零成本**：某点没有 handler 时，触发只是遍历空列表；
- **保持幂等 / 轻量**：`command.before` / `event.before_dispatch` 在高频路径上，重活交给 `*.after` 或 `ctx.run_async`；
- **异常隔离**：任一 handler 抛异常，内核记日志并跳过它，不连累其它 handler 与主流程；
- **别在 sync hook 里阻塞 IO**：`http.*` 跑在 Web 请求线程，阻塞会拖慢所有请求。

## 七、与其它扩展机制的关系

| 需求 | 用什么 |
| ---- | ------ |
| 插入 HTTP REST 路由（复用框架鉴权） | `ctx.register_api(path, handler, ...)` |
| 订阅 / 发布业务事件 | `ctx.on / emit / aemit`（事件总线） |
| 接管原始消息（命令匹配前） | `ctx.on_raw_message(handler)` |
| 在运行环节插行为（本文） | `ctx.hook(point, handler)` |

扩展点是其中最"底层、最广"的一层：事件总线偏业务解耦，`register_api` 偏对外 HTTP，
而 `hook` 直接挂在内核的每个运行环节上，适合做横切关注点（审计、限流、过滤、中间件、生命周期管理）。

---

延伸：[协议适配器](./protocol_adapter.md) · [ctx 参考](../basic/ctx.md) · [架构总览](../../advanced/architecture.md)
