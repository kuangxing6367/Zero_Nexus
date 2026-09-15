# API 参考总览

Zeronus 的 API 分三部分：**基础参考**（写插件每天用到的对象）、**进阶扩展**（改造内核行为的契约）、
**对外 HTTP API**（把 Zeronus 当服务调用）。

## 基础参考

| 文档 | 内容 |
| ---- | ---- |
| [PluginContext (ctx)](./basic/ctx.md) | 插件在 `register(ctx)` 里拿到的全部能力 |
| [Event 事件对象](./basic/event.md) | handler 收到的事件：字段、富媒体段、传播控制、权限查询 |
| [Framework 内核](./basic/framework.md) | 底层容器 `fw`：生命周期、服务注册、数据库、运行时上下文 |
| [服务注册表（DI）](./basic/services.md) | 内核与扩展如何通过 `services` 解耦、内置服务名清单 |

## 进阶扩展

| 文档 | 内容 |
| ---- | ---- |
| [扩展点（Hook 系统）](./advanced/hooks.md) | 26 个标准扩展点，覆盖几乎每个运行环节 |
| [协议适配器 ProtocolAdapter](./advanced/protocol_adapter.md) | 写一个接入端（Telegram / Discord / MQTT / 自定义） |

## 对外 HTTP API

WebUI 后端由若干功能域组成，可插入路由注册表：`auth` / `admins` / `apikeys` / `dashboard` / `plugins` /
`commands` / `users_groups` / `tasks` / `logs` / `config` / `db_gateway` / `framework_ops` / `webui` /
`files` / `stats` / `perm_api` / `static_routes`。在这之上另有：

| 接口 | 说明 |
| ---- | ---- |
| `POST /api/batch` | 批量调用：一次请求派发多个内部 `/api/` 子调用 |
| `/api/webhooks` | 出站 Webhook：订阅事件总线，事件触发即向目标 URL 出站 POST（可选 HMAC 签名） |
| WebSocket | 通用事件推送通道（插件装卸、定时任务触发、生命周期事件） |
| `POST /api/graphql` | GraphQL：`info` / `health` / `plugins` / `services` 查询 + `emitEvent` 变更（需安装 `ariadne`，缺失时本域自动跳过，不影响其余路由） |
| gRPC | `Health` / `ListPlugins` / `ListServices` / `EmitEvent`（由 `config.grpc.enabled` 门控，且需安装 `grpcio` + `grpcio-tools`） |

鉴权采用双令牌：会话 token（WebUI 登录）与 **API Key**（程序调用，后台「接口令牌」页管理）。

## 三层视角

Zeronus 按**内核级 / 服务级 / 软件级**三层组织，`Framework` 属于内核级：

- **内核级（`core/`）**：机制。`Framework` 只负责加载扩展、路由事件、提供公共服务（数据库 / 权限 / 服务注册 / 事件总线），以及 26 个扩展点。
- **服务级（`service/`）**：常驻服务。包管理器 `zkg`（依赖驱动的插件解析与加载）、启动编排（`startup.py`）、看门狗（`watchdog.py`）。
- **软件级（`software/`）**：策略与业务。`software/extensions/`（官方扩展：Web 后台 / 接入端 / 调度器 …）与 `software/plugins/`（用户插件）。

你在[编写插件](../guide/writing-plugins.md)里写的命令、在 `software/extensions/` 里看到的 Web / 接入端 / 调度器，
全都是**挂在内核扩展点上的软件级实现**。

```text
┌──────────── 内核级（core/） ────────────┐
│  Framework · HookRegistry · ServiceRegistry │
│  EventBus   · MessageRouter · Database · Perm │
└───────────────────────┬───────────────────────┘
                        │ 扩展点 / 服务注册 / 事件总线
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
 软件级·官方扩展        软件级·用户插件    自定义接入端
 software/extensions/  software/plugins/  ProtocolAdapter
 webui/session/        业务命令/定时任务    扩展点切面
 scheduler/http_api/
 http_inject/image_renderer/onebot_adapter
```

> 服务级（`service/`）横跨其上：`zkg` 负责把软件级插件按依赖解析并加载，`startup.py` 编排内核服务与用户服务的拉起顺序。

想"插到几乎每个地方"？从[扩展点（Hook 系统）](./advanced/hooks.md)开始。
