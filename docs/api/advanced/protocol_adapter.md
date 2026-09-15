# 协议适配器 ProtocolAdapter

> **面向**：要把 Zeronus 接到"非 OneBot"的来源（Telegram / Discord / MQTT / 自研系统）的开发者。

内核**不认识任何具体协议**。它只面向两个契约编程：

1. **`ProtocolAdapter`** —— 把某个协议的事件翻译成框架统一事件，并能反向调用该协议的动作；
2. **`ServiceRegistry`** —— 接入端把自己注册为 `api_caller` / `protocol_adapter`，其余代码通过服务名取用。

写好一个接入端 = 实现这个抽象类 + 注册服务。

## 一、契约

```python
from core.adapters.protocol import ProtocolAdapter

class MyAdapter(ProtocolAdapter):

    async def handle_event(self, raw_event: dict, bot_name: str):
        """把原始协议事件 → 框架内部事件 dict；返回 None 表示丢弃。"""

    async def call_api(self, action: str, bot: str = None, **params) -> dict:
        """执行一个动作，返回 OneBot 风格结果 {status, retcode, data?}。"""

    def get_connected_bots(self) -> list:
        """返回已连接来源/实例列表（仪表盘、后台会展示）。"""

    def start(self):
        """启动（建监听 / 建连 / 起线程或 task）。"""

    async def stop(self):
        """停止并清理资源。"""
```

可选覆写：

```python
    async def send_text(self, text, *, user_id=None, group_id=None, source=None) -> dict:
        """框架自身要发纯文本时（权限提示、关键词回复）会调用它。
        没有主动发送能力的接入端可沿用默认实现（返回 unsupported，不会抛异常）。"""
```

> 基类已经提供 `call`（同步，自动桥接到主事件循环）与 `acall`（异步）两个包装，
> 并在前后触发 `action.before` / `action.after` 扩展点——**你只需实现 `call_api`**。

## 二、注册服务

```python
def register(ctx):
    fw = ctx._framework
    adapter = MyAdapter(fw.config.get('my_adapter', {}))
    fw.services.register('protocol_adapter', adapter)
    fw.services.register('api_caller', adapter)     # 让 ctx.api()/aapi() 走这里
    adapter.start()
    ctx.log("my_adapter 已启动")
```

- 注册 `api_caller` 后，`ctx.api(action, **params)` / `ctx.aapi(...)` 就会调用你的 `call_api`；
- 注册 `protocol_adapter` 后，内核与后台能拿到"当前接入端"实例（连接列表等）；
- 要在卸载时清理，可实现 `unregister()` 或挂在 `lifecycle.shutdown` 扩展点。

## 三、内部事件格式（`handle_event` 返回什么）

返回一个 dict，至少要能被 `Event` 理解。最小可用结构：

```python
{
    "post_type": "message",        # message / notice / request / meta_event
    "message_type": "group",       # group / private
    "sub_type": "normal",
    "self_id": 0,                  # 机器人自身 ID
    "user_id": 10001,              # 发送者
    "group_id": 10086,             # 群（私聊为 0）
    "message": [                   # 消息段数组
        {"type": "text", "data": {"text": "/hello"}}
    ],
    "raw_message": "/hello",
    "sender": {"nickname": "小明", "role": "member"},
}
```

- `message` 既可以是**消息段数组**（推荐，保留富媒体），也可以是纯字符串；
- 框架会用 `_extract_text` 从段里提取纯文本供命令匹配；
- `post_type != "message"` 的事件同样会被分发（notice / request 等）。

投递方式：

```python
await fw.dispatch_event(event_dict)      # 直接交给内核
```

内核会依次走 `event.before_dispatch` 扩展点 → 命令/关键词路由 → `event.after_dispatch`。

## 四、完整示例：轮询型接入端

下面这个接入端不监听端口，而是每 2 秒去某个 HTTP 接口拉一批消息，翻译后投递：

```python
import asyncio
from core.adapters.protocol import ProtocolAdapter

class PollAdapter(ProtocolAdapter):
    def __init__(self, framework, cfg):
        self.framework = framework
        self.url = cfg.get("url", "http://127.0.0.1:9000/pull")
        self.interval = cfg.get("interval", 2)
        self._task = None

    async def handle_event(self, raw_event, bot_name):
        return {
            "post_type": "message",
            "message_type": "group" if raw_event.get("group") else "private",
            "self_id": 0,
            "user_id": raw_event.get("from", 0),
            "group_id": raw_event.get("group", 0),
            "message": [{"type": "text", "data": {"text": raw_event.get("text", "")}}],
            "raw_message": raw_event.get("text", ""),
            "sender": {"nickname": raw_event.get("name", "")},
        }

    async def call_api(self, action, bot=None, **params):
        # 把"动作"翻译成对上游系统的 HTTP 调用
        return {"status": "ok", "retcode": 0, "data": params}

    def get_connected_bots(self):
        return ["poll-default"]

    def start(self):
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        import urllib.request, json
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=5) as r:
                    items = json.loads(r.read())
                for it in items:
                    ev = await self.handle_event(it, "poll-default")
                    if ev:
                        await self.framework.dispatch_event(ev)
            except Exception as e:
                self.framework.logger.warning(f"[poll] 拉取失败: {e}")
            await asyncio.sleep(self.interval)


def register(ctx):
    fw = ctx._framework
    adapter = PollAdapter(fw, {"url": ctx.get_config("poll_url", "http://127.0.0.1:9000/pull")})
    fw.services.register("protocol_adapter", adapter)
    fw.services.register("api_caller", adapter)
    adapter.start()
    ctx.hook("lifecycle.shutdown", adapter.stop)     # 停机时清理
```

配置（`extensions.yaml` 对应块）：

```yaml
extensions:
  poll_adapter:
    enabled: true
    url: http://127.0.0.1:9000/pull
    interval: 2
```

## 五、纯事件注入 vs 双向接入端

| 类型 | 要不要实现 `call_api` | 例子 |
| ---- | ---- | ---- |
| **双向接入端**（能收也能发） | 要 | `onebot_adapter`（反向 WS，收发俱全） |
| **单向事件源**（只收不发） | 可只做兜底；`send_text` 用默认实现返回 unsupported | `http_inject`（外部 POST 推事件，框架不主动发） |

参考实现：

- `software/extensions/onebot_adapter/main.py` —— 完整双向接入端（WS 服务端 + 动作封装 + 连接管理）
- `software/extensions/http_inject/main.py` —— 极简单向事件源（HTTP → `dispatch_event`）

## 六、多实例与 bot 参数

一个接入端可以连多个"来源/实例"。动作调用里的 `bot` 参数用于指定目标实例；
约定用 `bot_name` 作为实例标识，`ctx.api(action, bot="xxx", **params)` 即可指定。
不确定时传 `None`，由接入端用默认实例兜底。

---

延伸：[服务注册表](../basic/services.md) · [扩展点](./hooks.md) · [架构总览](../../advanced/architecture.md)
