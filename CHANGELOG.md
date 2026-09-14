# 更新日志（CHANGELOG）

记录 Zeronus 的版本变化。版本号遵循语义化版本 `主版本.次版本.修订号`，`-alpha` / `-beta` 为预发布；
未发布的在研变化放在最顶部「开发中」小节。版本事实以 GitHub Releases 与 Git Tag 为准。

仓库：<https://github.com/kuangxing6367/Zero_Nexus>

---

## 开发中

_暂无。_

---

## v0.0.1-beta.0-alpha.0

**首个版本**：微内核骨架落定。

### 架构分层

- `core/` —— 最小内核：运行时装配（`engine` / `runtime` / `kernel`）+ 机制包 + 依赖驱动的包管理器 `zkg`。
- `extensions/` —— 官方能力扩展，依赖内核机制包：`onebot_adapter` / `webui` / `session` / `scheduler` /
  `image_renderer` / `http_inject` / `http_api`。
- `adapters/` —— 协议适配器目录（换一个 `ProtocolAdapter` 即可接入新协议）。
- `plugins/` —— 用户插件。
- `data/` —— 运行时数据（`zernus.db` / `plugins.db` / 日志 / 插件数据目录）。

### 内核

- **扩展点（Hook）契约**：26 个标准扩展点，覆盖生命周期、HTTP 请求、事件分发、命令执行、消息收发、
  协议动作、服务注册、插件装卸、数据库读写、多轮会话与定时任务触发；一律观察型、不短路。
- **包管理器 `zkg`（依赖驱动）**：插件 manifest 声明 `dependencies` → 启动扫描 / 解析 / 写独立
  `plugins.db` → 只加载「有依赖方」的官方机制包；支持多源索引与社区包（与官方源分列，官方优先）。
- **命令原语 `CommandBus`**：`register(name, callable)` + `invoke / ainvoke`，内核只给 name→callable 薄分发，
  路由 / 别名 / 权限等策略留给下游。
- **事件与服务**：事件总线（含 `unsubscribe`）、服务注册表、依赖注入容器、TTL 缓存。
- **存储**：SQLite / MySQL 双方言抽象，启动自动建表（`sql/init.sql`）。
- **内建子系统**：权限引擎（`perm`）、配置（`config`）、认证（`auth`）、定时任务（`scheduler`）、
  终端（`terminal`）、TLS（`tls`）、日志代理（`log_broker`）、双进程 IPC（`ipc`）。

### 插件 API（`ctx`）

在命令 / 权限 / 配置 / API / 消息 / 身份 / 群级开关 / 数据库 / 日志 / 异步 / WebUI / 会话之外，新增：

- 文件读写（限定插件数据目录）、HTTP 请求（同步 + 异步）、JSON / YAML 序列化；
- 任务调度、事件总线 `once` / `off` / `await_event`、缓存、依赖注入。

### 对外接口（HTTP API）

- WebUI 后端功能域：`auth` / `admins` / `apikeys` / `dashboard` / `plugins` / `commands` / `users_groups` /
  `tasks` / `logs` / `config` / `db_gateway` / `framework_ops` / `webui` / `files` / `stats` / `perm_api` /
  `static_routes`。
- 新增：批量调用 `POST /api/batch`；出站 Webhook（订阅事件总线 → 出站 POST，可选 HMAC 签名）；
  WebSocket 事件推送；GraphQL `POST /api/graphql`；gRPC（`Health` / `ListPlugins` / `ListServices` / `EmitEvent`）。

### 文档

- VitePress 文档站（`docs/`）：指南 / API / 进阶三大块。

---

## 维护约定

- 每个版本一个 `##` 小节，子节按需使用「新增 / 修复 / 变更 / 移除」。
- 版本事实（Tag / Release 日期）以 GitHub 为准；文档与代码如有出入，以代码为准。
