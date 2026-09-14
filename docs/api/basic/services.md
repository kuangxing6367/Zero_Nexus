# 服务注册表（DI）

> **面向**：想复用官方扩展能力、或想写一个可被他人复用的服务的开发者。

内核**不直接 import 官方扩展**。扩展启动时把自己的能力注册进**服务注册表**，其余代码通过
`framework.services.get(name)` 取用——这就是 Zeronus 的解耦方式。

## 一、为什么需要它

```
        ┌─────────────── core/ 内核 ───────────────┐
        │  PluginContext ──▶ services.get('...')   │
        └───────────────────────┬──────────────────┘
                                │ 只认「名字 + 契约」
        ┌───────────────────────┼───────────────────┐
        ▼                       ▼                   ▼
  onebot_adapter 注册         scheduler 注册       webui 注册
  'api_caller'/'onebot_api'  'scheduler'         'web_server'
```

好处：关掉某个扩展，取用方只是拿不到服务（可以降级），不会因为 import 失败而崩。
内核与扩展之间只有一条稳定边界。

## 二、内置服务名

| 服务名 | 提供方 | 说明 |
| ---- | ---- | ---- |
| `api_caller` | 接入端（onebot_adapter / http_inject …） | 协议无关的「动作调用」契约：`call` / `acall` |
| `onebot_api` | onebot_adapter | OneBot 11 动作封装（`ctx.onebot` 底层） |
| `protocol_adapter` | 接入端 | 协议适配器实例 |
| `ws_server` | onebot_adapter | 反向 WS 服务端（连接管理、已连实例列表） |
| `web_server` | webui | Web 后台服务 |
| `scheduler` | scheduler | 定时任务调度器 |
| `session_manager` | session | 会话管理器 |
| `http_api` | http_api | 独立对外 REST API |

## 三、取用服务

```python
def register(ctx):
    sm = ctx._framework.services.get('session_manager')
    if sm is None:
        ctx.log("未启用 session 扩展，功能降级", level="warning")
        return
    ...
```

日常里**大部分服务都有更友好的封装**，优先用封装：

| 想做的事 | 用 |
| ---- | ---- |
| 发消息 / 调动作 | `ctx.send_msg` / `ctx.api` |
| 定时任务 | `ctx.task` / `ctx.add_job` |
| 多轮会话 | `await ctx.wait_for` |
| OneBot 专用动作 | `ctx.onebot.*` |

## 四、注册自己的服务

官方扩展在 `register(ctx)` 里注册：

```python
def register(ctx):
    fw = ctx._framework
    fw.services.register('my_service', MyService())
    ctx.log("已注册服务 my_service")
```

注册会触发扩展点 `service.register.before` / `service.register.after`（可用于审计 / 观察）。

## 五、注册表 API

| 方法 | 说明 |
| ---- | ---- |
| `register(name, service)` | 注册（同名会覆盖并告警） |
| `get(name, default=None)` | 取用 |
| `has(name) -> bool` | 是否存在 |
| `remove(name)` | 移除 |
| `all() -> dict` | 全部服务快照 |

## 六、`api_caller` 契约（写接入端必读）

接入端只要把实例注册为 `api_caller`，就自动满足 `ctx.api()` / `ctx.aapi()`：

```python
class MyAdapter:
    async def call_api(self, action: str, bot: str = None, **params) -> dict:
        """真正执行动作，返回 OneBot 风格的结果字典 {status, retcode, data?}"""
        ...
```

基类 `ProtocolAdapter` 已提供 `call`（同步，自动桥接到主事件循环）与 `acall`（异步）两个包装，
并在前后触发 `action.before` / `action.after` 扩展点——你只需实现 `call_api`。
详见[协议适配器](../advanced/protocol_adapter.md)。

---

延伸：[Framework 内核](./framework.md) · [协议适配器](../advanced/protocol_adapter.md) · [扩展点](../advanced/hooks.md)
