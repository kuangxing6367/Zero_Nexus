# 编写插件

插件放在 `software/plugins/<插件名>/`，每个插件一个子目录，入口文件 `main.py`。

## 最小插件

```python
# software/plugins/hello/main.py
def register(ctx):
    @ctx.command(r"^你好$", handler=_hello)
    def _hello(event):
        # event 为框架内部事件对象
        return "你好，我是 Zeronus 插件。"
```

- `register(ctx)` 是框架加载插件时调用的**唯一入口**；所有注册都在此函数内完成。
- 不要在 `register()` 之外执行副作用；`register()` 返回后 `ctx.emit` 才会被投递（延迟投递是设计行为）。

## 注册能力

| 能力 | 接口 | 说明 |
| --- | --- | --- |
| 消息监听 | `ctx.on("message", handler)` | 订阅框架内部事件 |
| 一次性事件 | `ctx.once(event, handler)` | 只触发一次 |
| 发出事件 | `ctx.emit(event, payload)` | 延迟投递到主事件循环 |
| 命令 | `ctx.command(pattern, handler, priority=50, require_level="")` | 正则匹配命令；`require_level` 可为 `admin` / `super` |
| 定时任务 | `ctx.task(cron_expr, executor, description="")` | 注册 cron 任务 |
| 读取配置 | `ctx.get_config(key, default=None)` | 读取插件配置（来自 `plugin_configs` 表） |
| 发消息 | `ctx.send_msg(user_id=..., group_id=..., message=...)` | 发送消息 |
| 权限查询 | `ctx.get_user_role(group_id, user_id)` | 返回角色字符串 |
| HTTP 接口 | `ctx.register_api(path, handler, methods=None, auth=True)` | 扩展暴露 HTTP 接口 |
| WebUI 面板 | `ctx.register_group_extension(key, title, handler, ...)` / `ctx.register_user_extension(...)` | 在 Web 后台挂载自定义面板 |

## 命令与权限

- 命令通过正则 `pattern` 匹配，可带 `priority`（越小越优先）与 `require_level`。
- 权限模型为 LuckPerms 风格（见 [permission.md](permission.md)）：内置角色组 `super / owner / admin / member` 由框架虚拟注入。

## 插件配置

- 配置 Schema 通过 `_conf_schema.json` 定义，用户可在 Web 后台修改；
- 插件用 `ctx.get_config(key, default)` 读取。

## 内存与生命周期

- 单插件内存上限由 `config.yaml → plugin.max_memory_mb`（默认 64MB）控制；
- 连续超限会被看门狗**自动卸载**（见 [loader.md](loader.md)）。

## 调试技巧

- 不接 IM 也能调试：开启 `http_inject` 扩展，用 `curl` 向 `http://127.0.0.1:8901/hook` 注入事件（见 [getting-started.md](getting-started.md)）。
- 日志写入 `data/logs/zernus.log`，可按 `config.yaml → log.level` 调整级别。
