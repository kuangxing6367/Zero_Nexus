# API 参考（ctx）

扩展 / 插件通过 `register(ctx)` 拿到 `ctx` 对象，使用其 Mixin 能力。以下方法均已在源码中核实。

## 入口

```python
def register(ctx):
    # 所有注册在此完成
    ...
```

## 事件与消息

| 方法 | 说明 |
| --- | --- |
| `ctx.on(event_name, handler)` | 订阅框架内部事件（如 `"message"`） |
| `ctx.once(event_name, handler)` | 只触发一次 |
| `ctx.emit(event_name, payload=None)` | 发出事件（延迟投递到主事件循环） |
| `ctx.command(pattern, handler, priority=50, require_level="")` | 注册正则命令；`require_level` 可为 `admin`/`super` |
| `ctx.task(cron_expr, executor, description=None)` | 注册 cron 定时任务 |
| `ctx.send_msg(user_id=None, group_id=None, message=None)` | 发送消息 |

## 配置与状态

| 方法 | 说明 |
| --- | --- |
| `ctx.get_config(key, default=None)` | 读取插件配置（来自 `plugin_configs` 表） |
| `ctx.get_user_role(group_id, user_id)` | 返回用户在群内的角色字符串 |

## HTTP 与 WebUI

| 方法 | 说明 |
| --- | --- |
| `ctx.register_api(path, handler, methods=None, auth=True)` | 扩展暴露 HTTP 接口 |
| `ctx.register_group_extension(key, title, handler, ...)` | 在 Web 后台挂载群面板 |
| `ctx.register_user_extension(key, title, handler, ...)` | 在 Web 后台挂载用户面板 |

## 扩展点（HookPoints）

标准扩展点常量位于 `core/hooks/registry.py` 的 `HookPoints`，常见如：

- `HTTP_BEFORE_REQUEST`：返回 Flask `Response` 即短路；
- `EVENT_BEFORE_DISPATCH`：返回 `False` 丢弃事件；
- `COMMAND_BEFORE`：返回 `False` 跳过该命令；
- `MESSAGE_BEFORE_SEND`：返回 `False` 取消发送。

通过 `HookRegistry` 注册。

## 事件总线（EventBus）

- 唯一实现在 `core/kernel/event_bus.py`，`core/messaging/event_bus.py` 透明重导出。
- 线程安全，支持 async / sync handler。
- 在「非运行循环的线程」中 `emit` 会安全调度回框架主事件循环，**不会每次新建临时事件循环**。

## 注意

- `ctx.emit` 为**延迟投递**：在 `register()` 内 `emit`，handler 在 `register()` 返回后才触发（设计行为）。
- 命令匹配为注册式，多命令冲突时按 `priority`（越小越优先）与注册顺序裁决。
- 更完整的 Mixin 列表（files / http / serialize / jobs / cache / di 等）见源码 `core/ctx/`。
