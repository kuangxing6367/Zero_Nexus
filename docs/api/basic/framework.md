# Framework 内核对象

> **面向**：需要访问内核的高级扩展/插件开发者。绝大多数插件**只需要 `ctx`**。

`Framework` 是内核的装配体（`core/engine.py`），`main.py` 直接实例化它。
插件里可以通过 `ctx._framework` 拿到引用，但它属于**内部接口**，随版本可能调整。

## 一、生命周期

```python
framework = Framework(config_path)     # 装配：配置 / 数据库 / 服务 / 钩子 / 路由 / 加载器
await framework.start()                # 启动：加载扩展与插件 → 触发 lifecycle.startup
...
await framework.stop()                 # 停止：触发 lifecycle.shutdown，依次停服务
```

`main.py` 负责信号处理与 `asyncio.run`，通常不需要你手动调用。

## 二、核心属性

| 属性 | 类型 | 说明 |
| ---- | ---- | ---- |
| `fw.config` | `dict` | 合并后的配置（`config.yaml` + `extensions.yaml`） |
| `fw.config_path` | `str` | 配置文件的绝对路径 |
| `fw.db` | `Database` | 数据库抽象（双进程下为远程代理） |
| `fw.services` | `ServiceRegistry` | 服务注册表（见[服务注册表](./services.md)） |
| `fw.hooks` | `HookRegistry` | 扩展点注册表（见[扩展点](../advanced/hooks.md)） |
| `fw.event_bus` | `EventBus` | 事件总线（`on` / `once` / `off` / `emit` / `aemit` / `await_event`） |
| `fw.command_bus` | `CommandBus` | 命令原语（`register` / `invoke` / `ainvoke`） |
| `fw.router` | `MessageRouter` | 消息路由（命令匹配、关键词回复） |
| `fw.plugin_loader` | `PluginLoader` | 插件加载器（扫描 / 加载 / 卸载 / 热重载） |
| `fw.terminal` | `TerminalInput` | 终端交互 |
| `fw.loop` | `asyncio.AbstractEventLoop` | 主事件循环（启动后可用） |

## 三、便捷属性（服务快捷方式）

| 属性 | 等价于 |
| ---- | ---- |
| `fw.api_caller` | `fw.services.get('api_caller')` |
| `fw.ws_server` | `fw.services.get('ws_server')` |
| `fw.web_server` | `fw.services.get('web_server')` |
| `fw.scheduler` | `fw.services.get('scheduler')` |

## 四、公开方法

| 方法 | 说明 |
| ---- | ---- |
| `await fw.start()` / `await fw.stop()` | 启动 / 停止 |
| `await fw.dispatch_event(event: dict)` | 把一条事件字典投进内核（接入端用它） |
| `fw.register_raw_message_handler(plugin_name, handler, priority=50)` | 注册原始消息 handler |
| `fw.unregister_raw_message_handlers(plugin_name)` | 注销某插件的原始消息 handler |
| `await fw.reply_text(target, text)` | 由内核统一发一条文本（会走 `message.before_send` 扩展点） |
| `await fw.terminal_exec(name, args='')` | 执行一个终端命令，返回输出文本 |
| `fw.build_ssl_context()` | 按 `config.ssl` 构造 SSLContext |

## 五、启动时序（简化）

```
Framework()                     装配：配置 → 日志 → 数据库 → 服务/钩子/路由/加载器
  └─ await start()
       1. 建表 / 迁移
       2. 扫描并加载官方扩展（按 extensions.yaml 与依赖）
       3. 加载用户插件（plugins/）→ 执行 register(ctx)，收集命令/任务/扩展点
       4. 启动服务：接入端 → 调度器 → Web → （可选）WS 事件推送 / gRPC
       5. 触发 lifecycle.startup
       6. 打印启动横幅，进入运行
  └─ await stop()
       触发 lifecycle.shutdown → 停服务 → 落日志
```

## 六、`platform` 等兼容层

为兼容旧调用点，`Framework` 保留了一些薄委托方法与属性（如把请求转发给 `router` / `plugin_loader`）。
新代码请直接用上表里的属性。

## 七、什么时候不该用它

- 想发消息 → 用 `ctx.send_msg` / `ctx.api`；
- 想读配置 → 用 `ctx.get_config`；
- 想挂行为 → 用 `ctx.hook`；
- 想注册路由 → 用 `ctx.register_api`。

只有当你做的事情**确实不属于任何一个插件能力**时，才去碰 `ctx._framework`。

---

延伸：[服务注册表](./services.md) · [扩展点](../advanced/hooks.md) · [架构总览](../../advanced/architecture.md)
