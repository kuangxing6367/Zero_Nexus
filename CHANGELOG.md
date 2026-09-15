# 更新日志（CHANGELOG）

记录 ZER NUS 的版本变化。版本号遵循语义化版本 `主版本.次版本.修订号`，`-alpha` / `-beta` 为预发布；
未发布的在研变化放在最顶部「开发中」小节。版本事实以 GitHub Releases 与 Git Tag 为准。

仓库：<https://github.com/kuangxing6367/Zero_Nexus>

---

## 开发中

_暂无。_

---

## v0.0.1-alpha.0

**首个版本**：三层架构落定（内核级 / 服务级 / 软件级）。

### 架构分层

- `core/`（内核级）：维护服务与软件级活动；与数据库（SQLite / MySQL / PostgreSQL）交互；
  提供底层 Hook 检索三级状态（内存 / CPU 占用等）；负责 Log 输出。
- `service/`（服务级）：zkg 包管理（统一内容来源，官方包仓库 + 本地）；框架服务基础（mg / db 等）；
  软件启动与注销核心服务；看门狗（自身服务内存处理、zkg 拉起的服务）。
- `software/`（软件级）：与上层服务级通讯，在用户操作之间启动服务；看门狗负责内存。
  - `software/extensions/`：官方扩展（OneBot 接入、Web 管理后台、会话、定时任务等）。
  - `software/plugins/`：用户插件。

### 内核级（core）

- 数据库抽象：SQLite / MySQL / PostgreSQL 三方言，启动自动建表。
- 底层 Hook：检索三级状态（内存 / CPU 占用），供内核与看门狗使用。
- Log 输出：统一日志（控制台 + 可选文件）。

### 服务级（service）

- zkg 包管理：扫描本地仓库 + 官方源，统一内容、减少造轮子。
- 框架服务基础：mg（消息网关）/ db（数据库）等内核能力以服务形式暴露。
- 软件启动与注销核心服务：sys 服务（初始化）与 user 服务（加载软件级）。
- 看门狗：监控内存占用，超限告警。

### 软件级（software）

- 官方扩展经内核加载并启动（WebUI / OneBot 等）。
- 用户插件在用户操作之间由服务级拉起。

### 启动流程

`main.py → 启动 core（监听本地端口）→ 拉起 sys 服务（初始化）→ 拉起 user 服务（均监听本地端口）→ 是否加密通讯（否→读 Token / 是→RSA + 回调端）`。

### 通讯安全

- 非加密：`security.encrypted: false`，校验请求 Token。
- 加密：`security.encrypted: true`，由 RSA 握手完成后的回调端（rsa_callback）负责最终校验。
- 监听地址默认均为 `127.0.0.1`。

### 文档

- 文档站（docs/，VitePress）：指南 / API / 进阶三大块。

---

## 维护约定

- 每个版本一个 `##` 小节，子节按需使用「新增 / 修复 / 变更 / 移除」。
- 版本事实（Tag / Release 日期）以 GitHub 为准；文档与代码如有出入，以代码为准。
