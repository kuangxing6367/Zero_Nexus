# 会话（session）

`session` 扩展提供**多轮会话**能力，让插件可以维护与用户 / 群之间的连续上下文。

## 启用

`extensions.yaml` 中 `session.enabled: true`（默认开）。

## 能力

- 维护每个用户、每个群的会话状态（上下文窗口、最近交互、临时变量）。
- 插件可通过 `ctx` 读写会话上下文，实现多轮追问、状态机式交互。
- 会话与权限上下文（group / msgtype）联动，支持按群隔离。

## 典型用法

```python
def register(ctx):
    state = {}

    @ctx.command(r"^记一下 (.+)$", handler=_remember)
    def _remember(event):
        key = event["message"].group(1)
        # 把状态存进当前会话上下文
        sid = (event.get("group_id"), event.get("user_id"))
        state[sid] = key
        return f"已记录：{key}"

    @ctx.command(r"^刚才记了啥$", handler=_recall)
    def _recall(event):
        sid = (event.get("group_id"), event.get("user_id"))
        return f"刚才记录的是：{state.get(sid, '（空）')}"
```

> 实际会话状态建议交由 `session` 扩展管理（而非进程内字典），以获得持久化与跨重启恢复。具体读写 API 以源码 `software/extensions/session/` 为准。

## 与权限 / 命令的关系

- 会话上下文可与权限上下文（group / msgtype）叠加，实现「某群里某用户」的隔离会话。
- 配合 `require_level` 可做受限会话指令。
