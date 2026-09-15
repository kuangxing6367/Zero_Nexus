# 架构总览

Zeronus 是**分三层的事件驱动服务宿主**：内核级（`core/`）、服务级（`service/`）、软件级（`software/`）。
每一层职责单一，依赖方向始终是「外层依赖内层」。

## 一、三层

```
┌──────────────────────── 软件级 software/ ────────────────────────┐
│  extensions/  官方扩展：onebot_adapter webui session scheduler    │
│               http_api http_inject image_renderer                 │
│  plugins/     用户插件（业务逻辑，register(ctx)）                  │
└───────────────────────────────┬──────────────────────────────────┘
                                │ 与上层服务级通讯 / 在用户操作之间启动服务
┌──────────────────────── 服务级 service/ ─────────────────────────┐
│  zkg/         包管理器：清单 → 多源索引 → 依赖解析 → 按需加载       │
│  startup.py   sys 服务与 user 服务的拉起与生命周期                 │
│  watchdog.py  看门狗：内存占用监控                                 │
└───────────────────────────────┬──────────────────────────────────┘
                                │ 框架服务基础（mg / db 等）
┌──────────────────────── 内核级 core/ ────────────────────────────┐
│  engine.py    装配层：把下列部件组装成可运行的 Framework           │
│  kernel/      最小原语：事件总线、横幅、日志初始化、数据目录、统计   │
│  runtime/     编排节点：分发、扩展装载、看门狗、生命周期、回复       │
│  hooks/       Hook 契约（HookRegistry / HookPoints）              │
│  storage/     数据库抽象：方言翻译、连接池、自动建表、迁移          │
│  messaging/   Event 事件对象、EventBus、MessageRouter 消息路由     │
│  ctx/         PluginContext（按职责拆成多个 Mixin 装配）           │
│  commands/    命令原语 CommandBus（register / invoke）            │
│  adapters/    ProtocolAdapter 抽象 + ActionProxy + ServiceRegistry │
│  perm/        权限引擎 · plugin_loader/ 插件加载卸载 · scheduler/   │
│  auth/ 认证 · config/ 配置 · terminal/ 终端 · tls/ SSL            │
│  log_broker/ 日志代理 · webhook.py 外发 · cache.py / di.py        │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌──────────────────────── 数据层 ──────────────────────────────────┐
│  SQLite / MySQL / PostgreSQL（自动建表、方言翻译）                 │
└──────────────────────────────────────────────────────────────────┘
```

**依赖方向**：`core/` 内不出现任何 `import software.*`（软件级依赖内核级，反向不允许）。
层与层之间只有三条边界：

| 边界 | 说明 |
| ---- | ---- |
| **服务注册表** | 组件注册能力，别人按名取用（不互相 import） |
| **Hook** | 内核在每个运行环节留插槽，扩展挂行为 |
| **事件总线** | 业务解耦的发布/订阅 |

## 二、`core/` 模块地图

| 模块 | 职责 |
| ---- | ---- |
| `engine.py` | 装配层：把下面这些东西组装成可运行的 `Framework` |
| `kernel/` | 最小原语：事件总线、横幅、日志初始化、数据目录、统计写入、路径 |
| `runtime/` | 编排节点：分发、扩展/插件编排、看门狗、生命周期、回复 |
| `hooks/` | Hook 注册表（`HookRegistry` / `HookPoints`）——内核契约 |
| `storage/` | 数据库抽象：SQLite / MySQL / PostgreSQL 方言翻译、连接池、自动建表、迁移 |
| `messaging/` | `Event` 事件对象、`EventBus`、`MessageRouter` 消息路由 |
| `ctx/` | `PluginContext`（按职责拆成多个 Mixin 装配） |
| `commands/` | 命令原语 `CommandBus`（register / invoke） |
| `adapters/` | `ProtocolAdapter` 抽象 + `ActionProxy` + `ServiceRegistry` |
| `perm/` | 权限引擎 |
| `plugin_loader/` | 插件扫描/加载/卸载/热重载、合成包机制、依赖安装 |
| `auth/` | 通讯认证（未加密走 Token 校验；加密走 RSA + 回调端） |
| `config/` | 配置加载、默认模板、`extensions.yaml` 同步合并 |
| `scheduler/` | 定时任务调度（cron） |
| `terminal/` | 终端交互与内置命令 |
| `tls/` `log_broker/` | SSL 上下文 / 日志代理（控制台 + 轮转文件 + 内存流） |
| `webhook.py` | 外发 Webhook |
| `cache.py` `di.py` | 缓存原语 / 依赖注入原语 |

## 三、`service/` 模块地图

| 模块 | 职责 |
| ---- | ---- |
| `zkg/` | 包管理器：`manifest` 清单、`sources` 多源索引、`resolver` 依赖解析、`depdb` 依赖库、`loader` 按需加载 |
| `startup.py` | `start_sys_service` / `start_user_service` / `open_local_port`：服务级入口 |
| `watchdog.py` | 服务级 / 软件级共用的内存看门狗 |

## 四、启动时序

```
main.py → 启动 core（内核级）
   │  load_config → Framework 装配 → 开内核本地端口（默认 127.0.0.1:37001）
   └─ 拉起 sys 服务 ← 初始化
        ① zkg：扫描本地仓库 → 解析依赖图 → 重建依赖库 → 加载被依赖工具
        ② 框架服务基础（mg / db）就绪
        ③ 启动看门狗 → 开 sys 本地端口（默认 38001）
   └─ 拉起 user 服务
        ④ 加载软件级：官方扩展（software/extensions）+ 用户插件（software/plugins）
        ⑤ 启动看门狗 → 开 user 本地端口（默认 38002）
   └─ 是否加密通讯？
         ├─ 否 → 读 Token
         └─ 是 → RSA 完事 → 回调端
```

停止时逆序：取消端口任务 → 停看门狗 → `fw.stop()`。

## 五、一条消息的旅程

```
接入端收到原始报文
  └─ ProtocolAdapter.handle_event()  → 统一事件 dict
        └─ framework.dispatch_event(event)
              ├─ Hook event.before_dispatch   （可返回 False 丢弃）
              ├─ MessageRouter.route()
              │     ├─ 遍历内存路由表（命令按 priority 匹配）
              │     ├─ Hook command.before    （可返回 False 跳过）
              │     ├─ 调用插件 handler(event, match)
              │     └─ Hook command.after
              ├─ 未命中 → 关键词回复 / 未匹配日志
              └─ Hook event.after_dispatch
```

要点：

- 路由表在**内存**里，热路径零 DB 查询；改命令后自动重建；
- 命令匹配支持字面串与正则，别名一并参与；
- 框架自身要发文本（权限提示、关键词回复）统一走 `message.before_send` Hook，可被拦截/改写。

## 六、三条边界的用法

| 你想 | 用 |
| ---- | ---- |
| 复用别人的能力 | 服务注册表（`ctx._framework.services.get(name)`） |
| 在某个环节插行为 | Hook（`ctx.hook(point, handler)`） |
| 业务模块间解耦 | 事件总线（`ctx.on` / `ctx.emit`） |
| 声明一条命令 | `ctx.command(pattern, handler)`（内核命令原语 + 路由表） |

## 七、机制 ≠ 策略

内核**只提供机制**，不写死策略：

- 内核给 `CommandBus.register/invoke`，**不**规定命令怎么解析、别名怎么配、优先级怎么裁决——那是 `MessageRouter`（策略）的事；
- 内核给 Hook，**不**规定谁挂、挂几个——那是扩展的事；
- 内核给服务注册表，**不**规定注册什么服务。

这条原则决定了：换一个 `MessageRouter` 策略、换一套权限规则、换一个接入端，都不用动内核。

---

延伸：[插件加载与模块机制](./loader.md) · [扩展点（Hook 系统）](../api/advanced/hooks.md) ·
[协议适配器](../api/advanced/protocol_adapter.md) · [数据库](./database.md)
