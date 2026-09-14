---
layout: home

hero:
  name: Zeronus
  text: 微内核式事件驱动服务宿主
  tagline: 内核只做三件事——加载扩展、路由事件、暴露扩展点契约；事件从哪来、业务做什么，全部由你插拔
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
  - title: 微内核，不是又一个「框架」
    details: 内核只负责加载扩展、路由事件、暴露扩展点（hook）契约；IM 接入、Web 后台、会话、定时、权限……全是挂在内核上的扩展，可增可减。
    link: /advanced/architecture
  - title: 不限 IM，任意事件源
    details: 内置 OneBot 11 与 HTTP Webhook（http_inject）接入，纯定时任务也能跑；换一个 ProtocolAdapter 即可接 Telegram / Discord / MQTT 或任意系统，业务扩展零改动。
    link: /api/advanced/protocol_adapter
  - title: 扩展点，处处可插
    details: 26 个标准扩展点覆盖生命周期、HTTP 请求、事件分发、命令执行、消息收发、协议动作、服务注册、插件装卸、数据库读写、多轮会话与定时任务；ctx.hook() 一行挂上去。
    link: /api/advanced/hooks
  - title: 自带治理与持久化
    details: LuckPerms 风格权限、双令牌（会话 token + API Key）、审计日志、多用户 Web 后台，SQLite / MySQL 双方言持久化，多用户与多场景开箱可用。
    link: /advanced/permission
---

## 它是什么

Zeronus 是一个**微内核式的通用事件服务宿主**：内核极小（加载、路由、扩展点契约），
其余能力——接入平台、Web 后台、会话、定时、权限、数据库——全部是**可插拔的扩展**。

它不绑任何平台：内核**不含任何 IM 协议实现**，OneBot 11 只是 `onebot_adapter` 这个官方扩展。
关掉它，Zeronus 照样能作为**纯定时服务**或 **HTTP Webhook 接收器**运行。

## 谁适合用

| 你是 | Zeronus 给你什么 |
| --- | --- |
| 做 IM 机器人 / 群管工具 | 白拿接入、权限、多轮会话、定时、Web 后台，只写 `register(ctx)` 里的业务 |
| 要接非 IM 事件源（Webhook / 定时 / MQTT / 任意系统） | 用内置 `http_inject` / `scheduler`，或自写 `ProtocolAdapter`；复用同一套扩展、权限与后台 |
| 想把一批脚本 / 运维任务收成「事件 → 处理 → 响应」服务 | 内核自带调度、持久化、鉴权、可插拔前端，不必自己搭壳 |
| 想给现成系统加后台 / 权限 / 审计 | 微内核 + 扩展点切面，以最小侵入挂载 |

## 能用来做什么

- **接入层**：OneBot 11（默认）/ HTTP Webhook 注入 / 纯定时 / 自写 `ProtocolAdapter`（Telegram、Discord、MQTT…）
- **业务层**：命令、事件订阅、多轮会话、定时任务、任意 Python 逻辑
- **治理层**：LuckPerms 风格权限引擎、接口令牌、审计日志、多用户 Web 管理后台
- **数据层**：SQLite / MySQL 双方言持久化，自动建表
- **表现层**：可被扩展接管 / 扩展的 Web 后台、仪表盘卡片、CLI 终端、扩展点切面

## 三分钟跑起来

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
python main.py
```

启动后打开 `http://127.0.0.1:8080` 进入 Web 管理后台。**不需要 IM 接入端也能跑**：
在 `extensions.yaml` 里开启 `http_inject`，用一条 `curl` 就能注入事件。

详见[安装](./guide/installation.md)与[开始使用](./guide/getting-started.md)；
想理解「内核 + 扩展点」的设计，看[架构总览](./advanced/architecture.md)与[扩展点（Hook 系统）](./api/advanced/hooks.md)。
