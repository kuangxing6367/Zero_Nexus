# Zeronus

**一个微内核式的事件驱动服务宿主。**

内核只做三件事：**加载扩展、路由事件、暴露扩展点契约**。
接入哪个平台、开不开网页后台、要不要多轮会话与定时任务——全是挂在扩展点上的可插拔扩展。

> OneBot 11 只是官方自带的一个默认接入端，不是 Zeronus 的身份。
> 换一个 `ProtocolAdapter`，它可以是 Telegram / Discord 机器人、HTTP Webhook 接收器、纯定时任务服务，
> 或任何「事件 → 处理 → 响应」的程序。

| | |
| --- | --- |
| 当前版本 | **v0.0.1-beta.0-alpha.0**（见 [CHANGELOG.md](CHANGELOG.md)） |
| 仓库 | <https://github.com/kuangxing6367/Zero_Nexus> |
| 官方插件仓库 | <https://github.com/kuangxing6367/Zero_Nexus_plugins> |
| 开源协议 | MIT + Apache 2.0 双协议，任选其一 |

---

## 一、它是什么

- **内核极小**：`core/` 只含运行时装配、机制包与包管理器，**不含任何 IM 协议实现**。关掉 `onebot_adapter`，它照样能作为纯定时服务或 Webhook 接收器运行。
- **一切皆扩展**：接入端、Web 后台、会话、定时、权限、数据库……都是依赖内核机制包的可插拔扩展。
- **扩展点是一等公民**：26 个标准扩展点覆盖几乎每一个运行环节，用 `ctx.hook()` 一行就能挂逻辑，不必改框架源码、不必继承基类。
- **自带治理与持久化**：LuckPerms 风格权限引擎、双令牌（会话 token + API Key）、审计日志、多用户 Web 后台、SQLite / MySQL 双方言。
- **零第三方内核依赖**：密码哈希用 `hashlib`、HTTP 用 `urllib`、调度用标准库——换机器部署不必先装一堆包。

### 它不是什么

- **不是** NapCat / Lagrange / go-cqhttp 这类协议端。它需要 OneBot 实现端以「反向 WebSocket」连入。
- **不是**分布式/多节点中台。它是单进程宿主（可选 core/host 双进程），不内置集群与消息队列编排。
- **不提供**跨语言 SDK。业务扩展用 Python 写；跨语言交互走它暴露的 HTTP API / Webhook。

---

## 二、谁适合用

| 你是 | 你能拿到什么 |
| --- | --- |
| 想开箱跑一个机器人 | 默认接入端就是 OneBot：启动 → 连一个 NapCat/Lagrange → 后台点鼠标装插件、改配置、管权限 |
| 写业务的 Python 开发者 | 白拿依赖注入、权限引擎、双方言数据库、多轮会话、定时任务、Web 扩展与扩展点切面，只写 `register(ctx)` 里的业务 |
| 要接非 IM 事件源 | 内置 `http_inject`（Webhook）/ `scheduler`（定时），或自写 `ProtocolAdapter` 接 Telegram / Discord / MQTT |
| 想把一批脚本收成服务 | 内核自带调度、持久化、鉴权、可插拔前端，不必自己搭壳 |

---

## 三、快速开始

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
python main.py
```

- 首次启动会自动建表（SQLite → `data/zernus.db`）并在需要时自愈缺失依赖。
- 启动后打开 **<http://127.0.0.1:8080>** 进入 Web 管理后台。
- **不需要 IM 接入端也能跑**：在 `extensions.yaml` 里开 `http_inject`，一条 `curl` 就能注入事件。

也可以直接用脚本（自动建 venv、装依赖、启动）：

```bash
bash start.sh
```

默认监听端口：

| 服务 | 默认地址 | 配置位置 |
| --- | --- | --- |
| WebUI / HTTP API | `127.0.0.1:8080` | `extensions.yaml → webui` |
| OneBot 反向 WS | `0.0.0.0:6830` | `extensions.yaml → onebot_adapter` |
| HTTP API（独立） | `127.0.0.1:1145`（默认关） | `extensions.yaml → http_api` |
| HTTP 事件注入 | `127.0.0.1:8901`（默认关） | `extensions.yaml → http_inject` |

---

## 四、目录结构

```
.
├── main.py              # 启动入口：python main.py [自定义配置路径]
├── config.yaml          # 主配置（首次启动生成；不含扩展开关）
├── extensions.yaml      # 官方扩展配置中心：开关与参数集中于此
├── requirements.txt
├── core/                # 内核（不含任何 OneBot 实现）
│   ├── engine.py        #   装配层：把内核原语与运行时节点组装成可运行引擎
│   ├── kernel/          #   最小原语（event_bus / banner / logging / data_dirs / stats）
│   ├── runtime/         #   编排节点（dispatch / plugins / watchdogs / lifecycle / reply）
│   ├── hooks/           #   扩展点注册表（HookRegistry）—— 内核契约
│   ├── storage/         #   数据库抽象（SQLite/MySQL 双方言、连接池、自动建表）
│   ├── messaging/       #   Event 事件对象 / 事件总线 / 消息路由
│   ├── ctx/             #   PluginContext（插件可用能力）
│   ├── commands/        #   命令原语 CommandBus
│   ├── adapters/        #   ProtocolAdapter 抽象 + ActionProxy + 服务注册表
│   ├── perm/            #   LuckPerms 风格权限引擎
│   ├── plugin_loader/   #   插件加载器（合成包 / 相对导入 / 热重载）
│   ├── zkg/             #   包管理器（依赖驱动加载 + 多源索引）
│   ├── auth/ cache.py di.py webhook.py
│   ├── scheduler/ terminal/ tls/ log_broker/ config/
│   ├── api/             #   后台 REST 功能域（可插入路由注册表）
│   └── ipc/             #   core/host 双进程 JSON-RPC（dual_process 门控）
├── extensions/          # 官方扩展（在 extensions.yaml 开关）
│   ├── onebot_adapter/  #   OneBot 11 接入端
│   ├── webui/           #   Web 管理后台
│   ├── session/         #   多轮会话
│   ├── scheduler/       #   定时任务
│   ├── image_renderer/  #   图片渲染（含 Rust 原生扩展）
│   ├── http_inject/     #   HTTP 事件注入接入端
│   └── http_api/        #   独立对外 HTTP API
├── adapters/            # 协议适配器
├── plugins/             # 用户插件（每个一个目录，入口 main.py）
├── web/                 # 后台前端构建产物（由 webui/ 构建）
├── webui/               # 后台前端源码（Vue 3 + Vite + Element Plus）
├── sql/                 # 建表 SQL
├── data/                # 运行时数据：zernus.db、logs/、plugins_dat/
└── docs/                # 开发文档（VitePress）
```

---

## 五、写一个插件

用户插件放在 `plugins/<名字>/`，入口是 `main.py`，必须提供 `register(ctx)`：

```python
# plugins/greeter/main.py
def register(ctx):
    ctx.command("/hello", on_hello, description="打个招呼")

def on_hello(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message="Hello, World!")
```

要点：

- `ctx` 是插件与框架交互的**唯一入口**，由框架在 `register(ctx)` 时注入，并挂到插件主模块上（handler 里可直接用全局 `ctx`）。
- 有同步与异步两份 API：普通函数 handler 用同步方法；`async def` handler 用带 `a` 前缀的异步方法（推荐，避免阻塞事件循环）。
- 插件要拆多个文件时先读[插件加载与模块机制](docs/advanced/loader.md)（合成包 / 相对与短名导入 / 热重载）。

---

## 六、插件 API（`ctx`）速览

| 能力 | 主要方法 |
| --- | --- |
| 命令注册 | `ctx.command(pattern, handler, priority=50, alias=None, description=None, require_admin=False, require_superuser=False, require_perm=None)` |
| 消息发送 | `ctx.send_msg(...)` / `ctx.asend_msg(...)` |
| 接入端动作 | `ctx.api(action, params)` / `ctx.aapi(...)`（协议无关）；`ctx.onebot.*`（OneBot 专用） |
| 事件 | `ctx.on(name, handler)` / `ctx.on_raw_message(...)` / `ctx.emit(...)` / `ctx.aemit(...)` / `ctx.once(...)` / `ctx.off(...)` / `await ctx.await_event(name, timeout)` |
| 配置 | `ctx.get_config(key, default=None)` / `ctx.get_all_config()` |
| 数据库 | `ctx.db_query(...)` / `ctx.db_execute(...)` / `ctx.db_query_async(...)` / `ctx.db_execute_async(...)` / `ctx.db_transaction(...)` |
| 权限与身份 | `ctx.has_perm(uid, node, ...)` / `ctx.check_perm(...)` / `ctx.user_groups(...)` |
| 多轮会话 | `await ctx.wait_for(event, prompt=None, timeout=60, handler=None)` / `ctx.create_session(event, timeout=60)` |
| 定时任务 | `ctx.task(cron_expr, executor, description=None)` |
| 任务调度（快捷） | `ctx.add_job(handler, cron_expression, description='', job_id=None)` / `ctx.remove_job(job_id)` |
| 文件（限插件数据目录） | `ctx.read_file(name)` / `ctx.write_file(name, content)` / `ctx.list_dir(subdir='', include_dirs=False)` |
| HTTP 请求 | `ctx.http_get(url, params=None, headers=None, timeout=10)` / `ctx.http_post(...)`（及异步版 `http_get_async` / `http_post_async`） |
| 序列化 | `ctx.load_json(text, default=None)` / `ctx.dump_json(obj)` / `ctx.load_yaml(text)` / `ctx.dump_yaml(obj)` |
| 缓存 | `ctx.cache_set(key, value, ttl=None)` / `ctx.cache_get(key, default=None)` / `ctx.cache_delete(key)` |
| 依赖注入 | `ctx.provide(name, obj=None, factory=None)` / `ctx.inject(name, default=None)` |
| WebUI / 仪表盘 | `ctx.dashboard_card(...)` / `ctx.webui(...)` / `ctx.register_group_extension(...)` |
| 扩展点 | `ctx.hook(point, handler, priority=50)` / `ctx.unhook(point)` |
| 工具 | `ctx.log(...)` / `ctx.run_async(func, *args)` / `ctx.audit_log(...)` / `ctx.get_data_dir()` |

完整说明见 [PluginContext (ctx) 参考](docs/api/basic/ctx.md)。

---

## 七、扩展点（Hook）

扩展点就是内核运行流程上的「插槽」。插件用 `ctx.hook(point, handler)` 插进去，内核跑到那个环节就按优先级依次调用：

```python
def register(ctx):
    ctx.hook('action.after', audit)        # 任意协议动作调用后
    ctx.hook('event.before_dispatch', drop_blocked)   # 事件进入内核前

def audit(action, params, bot, result):
    ctx.log(f"[审计] {action} status={result.get('status')}")

def drop_blocked(event, bot_name):
    if event.get('user_id') in BLOCK:
        return False      # 返回 False 丢弃该事件
```

共 **26 个**标准扩展点，按环节分组：

| 分组 | 扩展点 |
| --- | --- |
| 生命周期 | `lifecycle.startup` · `lifecycle.shutdown` |
| HTTP | `http.before_request` \* · `http.after_request` |
| 事件分发 | `event.before_dispatch` \* · `event.after_dispatch` |
| 命令 | `command.before` \* · `command.after` |
| 消息出站 | `message.before_send` \* · `message.after_send` |
| 协议动作 | `action.before` · `action.after` |
| 服务注册 | `service.register.before` · `service.register.after` |
| 插件装卸 | `plugin.load` · `plugin.unload` |
| 数据库 | `db.query.before` · `db.query.after` · `db.execute.before` · `db.execute.after` |
| 多轮会话 | `session.create.before` · `session.create.after` · `session.wait.before` · `session.wait.after` |
| 定时任务 | `cron.task.trigger.before` · `cron.task.trigger.after` |

> 带 `*` 的 4 个支持短路（返回值可改变流程）；其余均为观察型（通知）。
> 点位只是字符串，你也可以注册自定义点位（如 `'myext.on_tick'`）做插件内部发布/订阅。

详见[扩展点（Hook 系统）](docs/api/advanced/hooks.md)。

---

## 八、官方扩展与包管理

官方扩展在 `extensions.yaml` 里开关：

| 扩展 | 作用 | 默认 |
| --- | --- | --- |
| `onebot_adapter` | OneBot 11 接入端（反向 WS） | 开 |
| `webui` | Web 管理后台 | 开 |
| `session` | 多轮会话 | 开 |
| `scheduler` | 定时任务 | 开 |
| `image_renderer` | 图片渲染（含 Rust 原生扩展） | 开 |
| `http_inject` | HTTP 事件注入接入端 | 关 |
| `http_api` | 独立对外 HTTP API | 关 |

**依赖驱动的包管理器（`zkg`）**：插件在自己的清单里声明 `dependencies`，启动时扫描 → 解析依赖图 →
写入独立 `plugins.db` → 只加载「有依赖方」的机制包。支持多源索引与社区包（与官方源分列，官方优先）。

---

## 九、对外接口（HTTP API）

WebUI 后端由若干功能域组成，可插入路由注册表：`auth` / `admins` / `apikeys` / `dashboard` / `plugins` /
`commands` / `users_groups` / `tasks` / `logs` / `config` / `db_gateway` / `framework_ops` / `webui` /
`files` / `stats` / `perm_api` / `static_routes`。在其之上还有：

| 接口 | 说明 |
| --- | --- |
| `POST /api/batch` | 批量调用：一次请求派发多个内部 `/api/` 子调用 |
| `/api/webhooks` | 出站 Webhook：订阅事件总线，事件触发即向目标 URL 出站 POST（可选 HMAC 签名） |
| WebSocket | 通用事件推送通道 |
| `POST /api/graphql` | GraphQL：`info` / `health` / `plugins` / `services` 查询 + `emitEvent` 变更 |
| gRPC | `Health` / `ListPlugins` / `ListServices` / `EmitEvent`（`config.grpc.enabled` 门控） |

后端鉴权采用双令牌：会话 token（WebUI 登录）与 **API Key**（程序调用）。API Key 可在后台「接口令牌」页管理。

---

## 十、配置

- `config.yaml` —— 主配置：数据库、Web、SSL、日志、插件目录、系统与安全、双进程等。
- `extensions.yaml` —— **官方扩展配置中心**：扩展开关与参数集中于此，启动自动扫描 `extensions/` 同步、回写、合并。

> 两个文件都在 `.gitignore` 中（含敏感信息），不进仓库。首次启动会自动生成。

---

## 十一、文档导航

| 入口 | 内容 |
| --- | --- |
| [开发文档总入口](docs/guide/README.md) | 按角色分流的上手路径 |
| [安装](docs/guide/installation.md) · [开始使用](docs/guide/getting-started.md) | 环境、启动、接入平台 |
| [编写插件](docs/guide/writing-plugins.md) | 从零写一个完整插件 |
| [配置系统](docs/guide/configuration.md) | `config.yaml` 与 `extensions.yaml` |
| [PluginContext (ctx)](docs/api/basic/ctx.md) · [Event](docs/api/basic/event.md) | 写插件最常查的两份 API |
| [扩展点（Hook 系统）](docs/api/advanced/hooks.md) | 26 个标准扩展点契约 |
| [协议适配器](docs/api/advanced/protocol_adapter.md) | 写自己的接入端 |
| [架构详解](docs/advanced/architecture.md) | 分层、启动时序、消息流转 |
| [权限系统](docs/advanced/permission.md) · [数据库](docs/advanced/database.md) · [定时任务](docs/advanced/scheduler.md) | 治理与数据 |
| [部署上线](docs/advanced/deployment.md) | systemd / Docker / 反向代理 / 安全清单 |

---

## 十二、双核心（实验特性，默认关闭）

把一次启动拆成「核心进程」+「宿主进程」，中间用标准库 IPC（回环 TCP + authkey）通信，零第三方依赖。
用于隔离用户扩展故障、压低核心常驻内存。

```yaml
dual_process:
  enabled: true        # 开启双进程
  max_restarts: 5      # 宿主崩溃重启限流（窗口内最大次数）
  restart_interval: 30 # 限流窗口（秒）
```

已知限制：`db.get_connection()` 在双进程下不可用（改用 `db.transaction()` 或 `ctx` 的 db 系列）；
目前为单宿主，多接入端并发与跨机部署尚未实现。详见[双核心开发文档](docs/advanced/dual-core.md)。

---

## 开源协议

MIT + Apache 2.0 双协议，任选其一适用。
