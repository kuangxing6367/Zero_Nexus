# 路线图（Roadmap）

> 从定位出发推导的缺口清单，不是 bug 列表。核心判断：README 定位是「通用软件宿主」，
> 但生态证据仍是「OneBot 机器人框架」——本路线图的所有条目都服务于弥合这个差距。
> 完成情况以本文件勾选与 CHANGELOG 为准。

---

## 核心判断

- **定位**：Zero Nexus = 三层架构（内核级 / 服务级 / 软件级）的软件宿主框架，程序化扩展，类 Linux 内核设计总纲。
- **现状差距**：官方扩展 7 个全是 IM 机器人生态；`software/plugins/` 用户插件为空；transport 的
  「远程 agent / 节点控制面」用例无实际消费者；zkg 官方源无真实可装内容。
- **结论**：机制层（core）密度已经足够，瓶颈在**生态与开发者路径**。未来一个版本周期内优先补
  「能装、能写、能跑非机器人软件」的证据，而不是继续加内核机制。

---

## P0 — 生态命脉

### 1. 跑通 zkg 全链路
- [x] 完整性校验：加载器拉包后校验索引 sha256，不一致拒绝加载（2026-09-16）。
- [x] 生产接线：`software/plugins/` 用户插件的 `dependencies` 纳入 zkg 依赖解析，
      插件声明即可触发机制包按需加载（2026-09-16）。
- [ ] 端到端演示包：把现有官方扩展之一（候选：`scheduler`，纯逻辑、零前端）改造为 zkg 包，从官方源安装后可正常工作。
- [ ] 发布链路：「开发 → 打包 → 发布（zkg.zgric.top）→ 安装 → 升级」写成文档（发布动服务器前先确认）。
- [ ] 包签名：密钥级签名校验（当前为 SHA-256 完整性校验）。

### 2. 插件开发者路径
- [x] `zkg new <name>` 脚手架：`python -m service.zkg new <name> [--deps store,ws]`，
      生成 manifest.toml + main.py + requirements.txt（2026-09-16）。
- [x] 机制包消费路径：`ctx.zkg_tool(name)` 访问器 + `demo_kv` 演示插件（2026-09-16）。
- [x] ctx 插件 API 版本承诺：`core/ctx.PLUGIN_API_VERSION` + `ctx.api_version` +
      manifest `api_version` 兼容区间声明，zkg 加载器启动校验（2026-09-16）。
- [x] 从 0 到 1 完整教程：`docs/writing-plugins.md` 实战章节（基于 demo_kv，含运行验证与常见坑，2026-09-17）。

---

## P1 — 证明「宿主」定位

### 3. 非 IM 官方扩展示例
- [x] 轻量状态面板（`software/extensions/status_panel`）：只读 `/health` JSON + Web 状态页，
      纯标准库、默认开启、绑定 127.0.0.1（2026-09-17）。

### 4. 工程信任基础
- [x] GitHub Actions CI：Python 3.11/3.13 × Ubuntu/Windows，逐个执行脚本式测试（2026-09-17）。
- [ ] Dockerfile + systemd unit 示例，「自托管」定位闭环。
- [x] 只读 status 端点：`/health` 已随状态面板扩展落地（版本 / 内存 / 任务队列 / 插件清单，2026-09-17）。

---

## P2 — 表述与边界修正

- [x] service 层定位表述：明确为「框架自带的系统服务库」，与内核同进程，非独立进程（README / architecture.md，2026-09-17）。
- [x] 内核端口 37001 协议说明：如实标注为端口占位 / 探活通道（回 `OK\n`），「状态/事件」协议待定义（README / architecture.md / getting-started.md，2026-09-17）。
- [x] README 架构段补：数据库令牌桶限速、内核任务队列、路由表事件驱动、PostgreSQL 现状（2026-09-17）。
- [x] PostgreSQL：README / database.md / requirements.txt 均如实标注「仅方言翻译、连接未实现、回退 SQLite」（2026-09-17）。

---

## 原则

- 机制≠策略；一类能力一个通用接口；内核极简（仅硬依赖 PyYAML）；稳定 ABI 边界；合理默认+可覆盖。
- 文档所有 API 名 / 签名 / 路径 / 配置键以源码为准，写前核验。
- 生态优先于机制：每加一个内核能力前，先问「这个能力帮谁装上了什么包」。
