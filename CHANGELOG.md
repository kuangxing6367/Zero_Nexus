# 更新日志（CHANGELOG）

记录 ZER NUS 的版本变化。版本号遵循语义化版本 `主版本.次版本.修订号`，`-alpha` / `-beta` 为预发布；
未发布的在研变化放在最顶部「开发中」小节。版本事实以 GitHub Releases 与 Git Tag 为准。

仓库：<https://github.com/kuangxing6367/Zero_Nexus>

---

## 开发中

### 多机管理（L2 控制通道）

- 新增官方扩展 `software/extensions/node_control/`（hub 侧）：监听 TCP 接受节点
  主动外连，per-node 预共享密钥验签（HELLO 帧载荷携带节点名，伪造无法通过
  整帧 HMAC）；心跳/状态聚合进内存快照，命令下发/回执经同一长连接；
  `fw.services.get('node_control')` 提供 `snapshot()` / `send_cmd(node, cmd, args, timeout)`
  （线程安全，内部投递到 asyncio 循环）。默认关闭（仅 hub 开启）。
- 新增官方扩展 `software/extensions/node_agent/`（节点侧）：主动外连 hub
  （节点可在 NAT 后），断线指数退避重连；周期心跳（状态同 status_panel /health
  结构）；命令白名单执行——`ping` / `health` 恒可用，`shell` 需节点侧
  `allow_shell: true` 显式开启（默认禁）。默认关闭（仅被管节点开启）。
- 配置项并入 `extensions.yaml` 与 core/config 内置模板；新增测试
  `tests/test_node_control.py`（9 项：握手验签 / 未知密钥拒绝 / 心跳 /
  命令回执 / 离线节点）与 `tests/test_node_agent.py`（5 项端到端：接入 /
  ping / health / shell 禁用 / 未知命令）。

### 多机管理（L1）

- 新增官方扩展 `software/extensions/node_manager/`：星型拓扑中心侧只读监控聚合，
  主动轮询各节点 `status_panel /health`，写入 `nodes` 表（心跳 `updated_at`），
  状态翻转打日志；`fw.services.get('node_manager')` 提供 `poll_now()` / `snapshot()`。
  默认关闭（仅 hub 开启），零新协议、不反向连接节点。
- 新增测试 `tests/test_node_manager.py`（10 项：表读写 / up/down 判定 /
  心跳落库 / 状态翻转 / enabled=false）。

### zkg 包管理

- 包签名（HMAC-SHA256）：`repo/build.py` 在设置 `ZKG_SIGNING_KEY`（或
  `--key-file`）时对每个包体计算 HMAC 写入索引 `signature` 字段；加载端
  校验通过才加载，带签名而本机无密钥 → 拒绝（fail closed），密钥不匹配 → 拒绝。
  sha256 防传输损坏，HMAC 防索引+包体同被替换的供应链替换。
- 端到端演示包：`repo/cron`（cron 表达式解析/匹配/next_run，纯 stdlib 机制包）
  + 演示插件 `software/plugins/demo_cron/`（声明依赖并经 `ctx.zkg_tool('cron')`
  消费），覆盖「依赖声明 → 源安装 → 校验 → 加载 → 使用」完整链路。
- 新增测试 `tests/test_pkg_signature.py`（7 项：打包签名 / 篡改拒绝 /
  缺钥拒绝 / 无依赖不加载）与 `tests/test_cron_pkg.py`（19 项）。
- 发布链路文档 `docs/zkg-publish.md`：开发 → 打包 → 签名 → 发布（动服务器需
  人工确认）→ 安装 → 升级，含「只增不删 / 先 pool 后 index / 密钥不上服务器」约定。

### 部署

- 官方部署产物：`deploy/Dockerfile`（数据全落 /app/data 卷）、
  `deploy/zernus.service`（systemd 单元，含读写路径加固）与 `deploy/README.md`；
  `docs/deployment.md` 指向官方产物并补多机管理（L1）章节。

### 配置

- 环境变量展开补全：`extensions.yaml` 合并进主 config 时同样执行
  `${VAR}` / `${VAR:-default}` 展开（此前只有 config.yaml 展开，扩展段密钥
  如 `onebot.access_token` 写变量会被未展开的明文覆盖）；回写 yaml 保留
  `${VAR}` 引用，不把展开后的明文落盘。

### zkg 包管理

- 依赖解析接入用户插件：`software/plugins/<pkg>/manifest.toml` 的 `dependencies`
  现在会被 zkg 解析，据此按需加载官方机制包（此前生产路径只扫 `repo/`，接线断裂）。
- 加载器新增 `scan_roots` 参数支持多扫描根，同 id 以主扫描根优先。
- 包完整性校验落地：从源拉取包体后先校验索引声明的 sha256，不一致即拒绝加载。
- 新增端到端测试 `tests/test_pkg_e2e.py`（下载路径加载 / 多扫描根合并 / 哈希篡改拒绝）。
- 机制包暴露给插件：`fw.zkg_tools`（startup 填充）+ `ctx.zkg_tool(name)` 访问器，
  插件消费机制包有了标准路径（此前工具加载后无任何途径触达）。
- 新增 `zkg new` 插件脚手架（`python -m service.zkg new <name> [--deps ...]`），
  生成 manifest.toml / main.py / requirements.txt 骨架。
- 新增演示插件 `software/plugins/demo_kv/`：声明 `store` 依赖并消费 KV 的最小完整样例。
- 稳定 ABI 承诺落地：`core/ctx.PLUGIN_API_VERSION`（当前 1）+ `ctx.api_version`；
  插件 manifest 可声明 `api_version` 兼容区间（如 `"1"` / `">=1,<2"`），
  zkg 加载器启动校验并告警不兼容插件（`api_incompatible`）。
- 机制包工具箱 5→9：新增 `http`（HTTP 客户端，回环自动绕代理、结果对象不抛异常）、
  `retry`（指数退避重试）、`lock`（命名锁 + 跨进程文件锁，同路径可重入）、
  `validate`（声明式参数校验与清洗），全部纯标准库。
- 新增测试 `tests/test_zkg_tools.py`（5 项：本地 HTTP 往返 / 重试语义 /
  锁互斥与重入 / 校验清洗 / 全链路加载）。

### 官方扩展

- 新增 **状态面板**（`software/extensions/status_panel`，默认开启，`127.0.0.1:8090`）：
  第一个与 IM 无关的官方扩展——Web 暗色状态页 `/`（5s 自动刷新）+ 只读 JSON 探活端点
  `/health`（版本 / 运行时长 / 内存 / 任务队列 / 扩展与插件清单 / zkg 工具），纯标准库。

### 工程与文档

- 新增 GitHub Actions CI（`.github/workflows/ci.yml`）：Python 3.11 / 3.13 ×
  Ubuntu / Windows 矩阵，逐个执行脚本式测试（不可 `unittest discover`）。
- `docs/writing-plugins.md` 新增「从 0 到 1」实战教程（基于 demo_kv）。
- 表述修正：service 层明确为「框架自带系统服务库」（同进程，非独立进程）；
  内核 37001 / sys / user 端口如实标注为端口占位 / 探活通道（回 `OK\n`）；
  README 架构段补任务队列 / 数据库限速 / 路由表事件驱动；requirements.txt
  去掉「框架自动安装 DB 驱动」的过时说法。

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
