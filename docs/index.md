---
layout: home

hero:
  name: Zeronus
  text: 分层（内核 / 服务 / 软件）事件驱动服务宿主
  tagline: 内核维护服务与软件级活动、与数据库交互、检索三级状态并负责日志；服务级管包与启停；软件级承接上层通讯与具体业务
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/installation
    - theme: alt
      text: 它是什么
      link: /guide/
    - theme: alt
      text: GitHub
      link: https://github.com/kuangxing6367/Zero_Nexus

features:
  - title: 三层分工，边界清晰
    details: 内核级（core/）维护服务与软件级活动、与数据库交互、检索三级状态、负责日志；服务级（service/）管 zkg 包管理与启停；软件级（software/）与上层通讯并承载具体业务。
    link: /advanced/architecture
  - title: 不限事件来源
    details: 内置 OneBot 11 与 HTTP Webhook（http_inject）接入，纯定时任务也能跑；接入平台只是软件级的一个扩展，业务侧零改动即可替换。
    link: /api/advanced/protocol_adapter
  - title: Hook 切面可插
    details: 内核提供 Hook 机制，覆盖生命周期、事件分发、命令执行、消息收发、服务注册、插件装卸、数据库读写、多轮会话与定时任务；ctx.hook() 一行挂上去。
    link: /api/advanced/hooks
  - title: 自带治理与持久化
    details: 权限引擎、令牌校验、审计日志、多用户 Web 后台；SQLite / MySQL / PostgreSQL 三方言持久化，自动建表。
    link: /advanced/permission
---

## 它是什么

Zeronus 是一个**分三层的事件驱动服务宿主**：

- **内核级（core/）**：维护服务与软件级活动；与数据库（SQLite / MySQL / PostgreSQL）交互；
  提供底层 Hook 检索三级状态（内存 / CPU 占用等）；负责日志输出。
- **服务级（service/）**：zkg 包管理；框架服务基础（mg / db 等）；软件启动与注销核心服务；看门狗。
- **软件级（software/）**：与上层服务级通讯，在用户操作之间启动服务；看门狗负责内存。

它不绑任何平台：内核**不含任何 IM 协议实现**，OneBot 11 只是 `onebot_adapter` 这个软件级扩展。
关掉它，Zeronus 照样能作为**纯定时服务**或 **HTTP Webhook 接收器**运行。

## 启动流程

```
main.py → 启动 core → 监听一个本地端口
   ↓
拉起 sys 服务 ← 初始化
   ↓
拉起 user 服务
   ↓
均监听一个本地端口
   ↓
是否加密通讯？
├─ 否 → 读 Token
└─ 是 → RSA 完事 → 回调端
```

## 谁适合用

| 你是 | Zeronus 给你什么 |
| --- | --- |
| 做 IM 机器人 / 群管工具 | 白拿接入、权限、多轮会话、定时、Web 后台，只写 `register(ctx)` 里的业务 |
| 要接非 IM 事件源（Webhook / 定时 / 任意系统） | 用内置 `http_inject` / `scheduler`，或自写接入扩展；复用同一套 Hook、权限与后台 |
| 想把一批脚本 / 运维任务收成「事件 → 处理 → 响应」服务 | 内核自带调度、持久化、鉴权、可插拔前端，不必自己搭壳 |
| 想给现成系统加后台 / 权限 / 审计 | Hook 切面 + 三层边界，以最小侵入挂载 |

## 能用来做什么

- **接入层**：OneBot 11（默认）/ HTTP Webhook 注入 / 纯定时 / 自写接入扩展
- **业务层**：命令、事件订阅、多轮会话、定时任务、任意 Python 逻辑
- **治理层**：权限引擎、接口令牌、审计日志、多用户 Web 管理后台
- **数据层**：SQLite / MySQL / PostgreSQL 三方言持久化，自动建表
- **表现层**：可被扩展接管 / 扩展的 Web 后台、仪表盘卡片、CLI 终端、Hook 切面

## 三分钟跑起来

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
python main.py
```

启动后打开 `http://127.0.0.1:8080` 进入 Web 管理后台。**不需要 IM 接入端也能跑**：
在 `extensions.yaml` 里开启 `http_inject`，用一条 `curl` 就能注入事件。

详见[安装](./guide/installation.md)与[开始使用](./guide/getting-started.md)；
想理解三层设计与边界，看[架构总览](./advanced/architecture.md)与[扩展点（Hook 系统）](./api/advanced/hooks.md)。
