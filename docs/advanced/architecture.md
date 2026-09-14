# 架构总览

Zeronus 是一个**微内核**：内核极小且不含任何业务/协议实现，能力全部挂在内核的扩展点上。

## 一、分层

```
┌────────────────────────── 用户层 ──────────────────────────┐
│  plugins/<名>/main.py     用户插件（业务逻辑）              │
└───────────────────────────┬────────────────────────────────┘
                            │ ctx（唯一入口）
┌────────────────────────── 官方扩展层 ──────────────────────┐
│  extensions/                                                │
│  onebot_adapter  webui  session  scheduler  image_renderer  │
│  http_inject     http_api                                   │
└───────────────────────────┬────────────────────────────────┘
                            │ 扩展点 / 服务注册 / 事件总线
┌────────────────────────── 内核 core/ ──────────────────────┐
│  engine 装配层                                              │
│  kernel/runtime   最小原语 + 编排节点                        │
│  hooks 扩展点契约 · storage 数据库 · messaging 事件/路由      │
│  ctx 插件上下文 · commands 命令原语 · adapters 协议抽象       │
│  perm 权限 · plugin_loader 加载器 · zkg 包管理器             │
│  auth/config/scheduler/terminal/tls/log_broker/api/ipc       │
└───────────────────────────┬────────────────────────────────┘
                            │
┌────────────────────────── 数据层 ──────────────────────────┐
│  SQLite / MySQL（sql/init.sql 建表）                        │
└────────────────────────────────────────────────────────────┘
```

**依赖方向永远是"外层依赖内层"**：`core/` 内不出现任何 `import extensions` / `import plugins`。
内核与扩展之间只有三条边界：

| 边界 | 说明 |
| ---- | ---- |
| **服务注册表** | 扩展注册能力，别人按名取用（不互相 import） |
| **扩展点** | 内核在每个运行环节留插槽，扩展挂行为 |
| **事件总线** | 业务解耦的发布/订阅 |

## 二、`core/` 模块地图

| 模块 | 职责 |
| ---- | ---- |
| `engine.py` | 装配层：把下面这些东西组装成可运行的 `Framework` |
| `kernel/` | 最小原语：事件总线、横幅、日志初始化、数据目录、统计写入 |
| `runtime/` | 编排节点：分发、扩展/插件编排、看门狗、生命周期、回复 |
| `hooks/` | 扩展点注册表（`HookRegistry` / `HookPoints`）——内核契约 |
| `storage/` | 数据库抽象：双方言翻译、连接池、自动建表、迁移 |
| `messaging/` | `Event` 事件对象、`EventBus`、`MessageRouter` 消息路由 |
| `ctx/` | `PluginContext`（按职责拆成 17 个 Mixin 装配） |
| `commands/` | 命令原语 `CommandBus`（register / invoke） |
| `adapters/` | `ProtocolAdapter` 抽象 + `ActionProxy` + `ServiceRegistry` |
| `perm/` | LuckPerms 风格权限引擎 |
| `plugin_loader/` | 插件扫描/加载/卸载/热重载、合成包机制、依赖安装 |
| `zkg/` | 包管理器：清单、多源索引、依赖解析、按需加载机制包 |
| `auth/` | 双令牌认证（会话 token + API Key） |
| `config/` | 配置加载、默认模板、`extensions.yaml` 同步合并 |
| `scheduler/` | 定时任务调度（cron） |
| `terminal/` | 终端交互与内置命令 |
| `tls/` `log_broker/` | SSL 上下文 / 日志代理（控制台 + 轮转文件 + 内存流） |
| `api/` | WebUI 后端 REST 功能域（可插入路由注册表） |
| `ipc/` | 双进程（核心进程 / 宿主进程）IPC（`dual_process` 门控） |

## 三、启动时序

```
main.py → Framework(config_path)
   │  装配：config → logging → database → services/hooks/router/loader
   └─ await start()
        ① 建表 / schema 迁移
        ② 扫描 extensions/，按 extensions.yaml + 依赖加载官方扩展
        ③ 加载 plugins/ 用户插件 → 执行 register(ctx)，收集命令 / 任务 / 扩展点
        ④ 启动服务：接入端 → scheduler → webui →（可选）WS 推送 / gRPC
        ⑤ 触发 lifecycle.startup
        ⑥ 打印横幅，进入运行
```

停止时逆序：触发 `lifecycle.shutdown` → 停服务 → 落日志。

## 四、一条消息的旅程

```
接入端收到原始报文
  └─ ProtocolAdapter.handle_event()  → 统一事件 dict
        └─ framework.dispatch_event(event)
              ├─ 扩展点 event.before_dispatch   （可返回 False 丢弃）
              ├─ MessageRouter.route()
              │     ├─ 遍历内存路由表（命令按 priority 匹配）
              │     ├─ 扩展点 command.before    （可返回 False 跳过）
              │     ├─ 调用插件 handler(event, match)
              │     └─ 扩展点 command.after
              ├─ 未命中 → 关键词回复 / 未匹配日志
              └─ 扩展点 event.after_dispatch
```

要点：

- 路由表在**内存**里，热路径零 DB 查询；改命令后自动重建；
- 命令匹配支持字面串与正则，别名一并参与；
- 框架自身要发文本（权限提示、关键词回复）统一走 `message.before_send` 扩展点，可被拦截/改写。

## 五、三条边界的用法

| 你想 | 用 |
| ---- | ---- |
| 复用别人的能力 | 服务注册表（`ctx._framework.services.get(name)`） |
| 在某个环节插行为 | 扩展点（`ctx.hook(point, handler)`） |
| 业务模块间解耦 | 事件总线（`ctx.on` / `ctx.emit`） |
| 声明一条命令 | `ctx.command(pattern, handler)`（内核命令原语 + 路由表） |

## 六、机制 ≠ 策略

内核**只提供机制**，不写死策略：

- 内核给 `CommandBus.register/invoke`，**不**规定命令怎么解析、别名怎么配、优先级怎么裁决——那是 `MessageRouter`（策略）的事；
- 内核给扩展点，**不**规定谁挂、挂几个——那是扩展的事；
- 内核给服务注册表，**不**规定注册什么服务。

这条原则决定了：换一个 `MessageRouter` 策略、换一套权限规则、换一个接入端，都不用动内核。

---

延伸：[插件加载与模块机制](./loader.md) · [扩展点](../api/advanced/hooks.md) ·
[协议适配器](../api/advanced/protocol_adapter.md) · [双核心](./dual-core.md)
