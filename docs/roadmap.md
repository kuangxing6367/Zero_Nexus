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
- [x] 端到端演示包：`repo/cron` 机制包 + `demo_cron` 插件（依赖声明 → 源安装 → 校验 → 加载 → ctx.zkg_tool 消费，2026-09-17）。
- [x] 发布链路：`docs/zkg-publish.md`「开发 → 打包 → 签名 → 发布 → 安装 → 升级」（上传服务器一步需人工确认，2026-09-17）。
- [x] 包签名：HMAC-SHA256（`ZKG_SIGNING_KEY` / `--key-file` 签名入索引，loader 缺钥或不匹配拒绝加载，2026-09-17）。

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
- [x] Dockerfile + systemd unit 示例，「自托管」定位闭环：
      `deploy/Dockerfile`、`deploy/zernus.service`、`deploy/README.md`，
      docs/deployment.md 已指向官方产物（2026-09-17）。
- [x] 只读 status 端点：`/health` 已随状态面板扩展落地（版本 / 内存 / 任务队列 / 插件清单，2026-09-17）。

---

## P2 — 表述与边界修正

- [x] service 层定位表述：明确为「框架自带的系统服务库」，与内核同进程，非独立进程（README / architecture.md，2026-09-17）。
- [x] 内核端口 37001 协议说明：如实标注为端口占位 / 探活通道（回 `OK\n`），「状态/事件」协议待定义（README / architecture.md / getting-started.md，2026-09-17）。
- [x] README 架构段补：数据库令牌桶限速、内核任务队列、路由表事件驱动、PostgreSQL 现状（2026-09-17）。
- [x] PostgreSQL：README / database.md / requirements.txt 均如实标注「仅方言翻译、连接未实现、回退 SQLite」（2026-09-17）。

---

## 多机管理（设计定稿 2026-09-17，分三级）

核心决策：**星型拓扑，节点主动外连中心（hub）**——节点可在 NAT 后，无需公网；
复用 `service/transport/framed.py`（整帧 HMAC、防重放、超时都已加固）做控制面传输。

- [x] **L1 监控聚合（只读，先行）**：`node_manager` 扩展已落地——轮询各节点
      `status_panel /health`，写 `nodes` 表（心跳 updated_at），默认关闭（2026-09-17）。
- [x] **L2 控制通道**：hub 侧 `node_control`（监听 TCP）+ 节点侧 `node_agent`
      （FramedClient 主动连 hub），帧类型：HELLO 握手 / HEARTBEAT 心跳状态 /
      CMD 命令下发 / RESULT 回执；鉴权用预共享 per-node secret（整帧 HMAC 验签），
      命令白名单 ping/health/shell（shell 需节点侧 allow_shell 显式开启）（2026-09-17）。
- [x] **L3 编排（第一切片，2026-11-14）**：
  - 节点分组：hub `node_control.groups`，成员须在 `nodes` 密钥清单内（分组不绕过鉴权）。
  - 灰度/批量下发：`send_group(group, cmd, args, timeout, concurrency, stagger)` /
    `broadcast(...)`，单台失败不中断整组，聚合 `total/ok/failed/offline/results`。
  - 能力上报：HELLO 携带 `capabilities`（命令 → 是否启用），hub `snapshot()` 可见。
  - 包分发：节点侧 `pkg.list`（只读）/ `pkg.sync`（按 zkg 源重跑 scan→resolve→
    rebuild→加载，sha256/签名校验复用启动路径），`pkg.sync` 需 `allow_packages: true`。
- [x] **配置热重载 / 配置下发（2026-11-14）**：
  - `Framework.reload_config()` 就地更新 `self.config`，刷新 heartbeat/watchdog 派生值，
    触发 `config.reload` 扩展点（`handler(old, new)`）。
  - `core.config` 支持托管覆盖层 `config.managed.yaml`：加载时最后合并，优先级高于
    `config.yaml` / `extensions.yaml`，不改用户原配置文件。
  - 节点侧 `config.status`（只读）/ `config.reload` / `config.push`，后两者需
    `allow_config: true`；下发带 sha256 强校验、1MiB 上限、dry_run、原子写、
    失败自动回滚，`clear` 可删除覆盖层。
  - 局限：端口 / 数据库连接 / SSL 等启动期绑定项仍需重启。
- [x] **框架自身升级（2026-11-14）**：
  - `service/updater.py` 统一「校验 → 暂存 → 编译检查 → 备份 → 替换 → 回滚」：
    sha256 fail-closed（默认必须显式提供）、归档安全校验、旧文件备份到
    `.update_backup/`、替换失败自动回滚、可显式回滚到任意备份。
  - 白名单只覆盖框架代码/文档：`core` / `service` / `software/extensions` /
    `sql` / `deploy` / `docs` 等；保护 `data/`、`repo/`、`config.yaml`、
    `extensions.yaml` 与 `software/plugins/`。
  - WebUI：`POST /api/framework/update` 复用该机制并支持 `dry_run`；
    新增 `GET /api/framework/backups` 与 `POST /api/framework/rollback`。
  - 多机：节点侧 `framework.status`（只读）/ `framework.update` /
    `framework.rollback`，后两者需 `allow_upgrade: true`；hub 侧
    `push_framework()` / `rollback_framework()` / `framework_status()`，
    默认逐台（`concurrency=1`）滚动。
  - 局限：**不自动重启、不做重启后健康检查与自动回滚**；灰度是否继续
    由调用方看回执决定。升级/回滚后需重启进程才加载新代码。
- 原则：L1 没跑稳不碰 L2；hub 不主动反向连接节点（保持出站单向，网络要求最低）。

---

## 原则

- 机制≠策略；一类能力一个通用接口；内核极简（仅硬依赖 PyYAML）；稳定 ABI 边界；合理默认+可覆盖。
- 文档所有 API 名 / 签名 / 路径 / 配置键以源码为准，写前核验。
- 生态优先于机制：每加一个内核能力前，先问「这个能力帮谁装上了什么包」。
