# 多轮会话

多轮会话让插件能"问一句、等用户回答、再继续"。由官方扩展 `session` 提供（`extensions.yaml` 里 `session.enabled`，默认开）。

## 一、`await ctx.wait_for(...)`

最常用的一行式写法：发一条提示，等同一会话里的下一条消息，超时返回。

```python
def register(ctx):
    ctx.command("/rename", on_rename)

async def on_rename(event, match):
    ans = await ctx.wait_for(event, prompt="请发送新的群名片（60 秒内）：", timeout=60)
    if not ans:
        await ctx.asend_msg(group_id=event.group_id, user_id=None, message="已超时，取消。")
        return
    card = ans["message"]
    ctx.set_card(event.group_id, event.user_id, card)
    await ctx.asend_msg(group_id=event.group_id, user_id=None, message=f"已改为：{card}")
```

签名：`wait_for(event, prompt=None, timeout=60, handler=None)`

| 参数 | 说明 |
| --- | --- |
| `event` | 触发本轮会话的事件（用来定位是哪个用户/群） |
| `prompt` | 提示文本，给了就自动发出 |
| `timeout` | 等待秒数，超时返回 `None`（或空） |
| `handler` | 可选：一条自定义的「是否属于本会话」判定 |

返回：命中时是一个事件字典（常取 `ans["message"]`），超时返回空。

## 二、`ctx.create_session(...)`：更细的控制

需要多次往返、或中途要复用会话时：

```python
async def on_wizard(event, match):
    sess = ctx.create_session(event, timeout=120)
    await sess.ask("第一步：请输入名称")
    name = (await sess.wait())["message"]

    await sess.ask("第二步：请输入描述")
    desc = (await sess.wait())["message"]

    sess.close()
    await ctx.asend_msg(group_id=event.group_id, user_id=None, message=f"完成：{name} / {desc}")
```

| 方法 | 说明 |
| --- | --- |
| `ctx.create_session(event, timeout=60)` | 建立会话，返回 `Session` |
| `await sess.ask(prompt, timeout=None)` | 发提示并等待下一条（等价于 wait+发送） |
| `await sess.wait(timeout=None)` | 只等待下一条消息 |
| `sess.close()` | 结束会话，释放计数 |

## 三、并发与清理

- 每个会话按「来源（群 + 用户）」隔离，同一来源同时只有一轮会话；
- 后台能看到当前活跃会话数（`session` 扩展提供）；
- 超时或 `close()` 后会话立即失效，用户后续消息按普通消息处理。

## 四、与扩展点联动

会话生命周期的四个扩展点可用来做审计 / 埋点：

| 扩展点 | 时机 |
| --- | --- |
| `session.create.before` / `session.create.after` | 会话建立前 / 后 |
| `session.wait.before` / `session.wait.after` | 等待用户回复前 / 后（含结果） |

```python
def register(ctx):
    ctx.hook("session.wait.after", on_wait_done)

async def on_wait_done(key=None, result=None, **kw):
    ctx.log(f"会话 {key} 结束，结果: {bool(result)}")
```

详细扩展点清单见[扩展点（Hook 系统）](../api/advanced/hooks.md)。

## 五、常见坑

- **别在同步 handler 里 `await`**：`wait_for` 是协程，必须在 `async def` handler 里 `await`；
- **超时要处理**：`wait_for` 返回空值时要主动收尾，否则用户会觉得"机器人卡住"；
- **`wait_for` 会占用该来源的会话槽**：同一个群/用户不要并发开多个，会互相抢占；
- 会话只是内存态，重启即清空；需要持久化请自己写库。

---

延伸：[编写插件](./writing-plugins.md) · [ctx 参考](../api/basic/ctx.md)
