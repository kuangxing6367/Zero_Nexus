# CUCKOO.md — Zeronus 项目说明

> 面向 AI 编程助手（Cuckoo Code）的项目上下文说明。修改代码前请先读本文与 `docs/` 中相关章节。
> 文档中所有路径 / 配置键 / API 名以**源码为准**，写文档或改代码前先核验源码。

## 项目是什么

**Zeronus / Zero Nexus** —— 一个基于 Python 的**软件宿主框架**，采用**内核级 / 服务级 / 软件级**三层架构，事件驱动，程序化扩展（非传统插件化）。

- 当前版本：`v0.0.1-alpha.0`（见 `VERSION` / `CHANGELOG.md`）
- 仓库：https://github.com/kuangxing6367/Zero_Nexus
- 协议：MIT + Apache 2.0 双协议，任选其一
- 运行环境：Python 3.11 / 3.13（CI 矩阵），Windows / Ubuntu 均可

## 三层架构

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 内核级 | `core/` | 服务/软件生命周期与状态、数据库（SQLite/MySQL/PostgreSQL 方言）、Hook（28 扩展点）、日志、事件总线、权限、认证、协议抽象、任务队列 |
| 服务级 | `service/` | zkg 包管理、sys/user 服务启停、看门狗、安全传输原语（与内核**同进程**） |
| 软件级 | `software/` | `extensions/`（官方扩展）+ `plugins/`（用户插件） |

### 启动时序（`main.py`）

```
main.py
  → _check_and_install_deps()        自检并安装 requirements.txt 缺失依赖
  → load_config                      读 config.yaml / extensions.yaml
  → Framework(core)                  构造内核，监听 core.host:core.port（默认 127.0.0.1:37001）
  → start_sys_service                zkg init + db 就绪 + Watchdog（默认 38001）
  → start_user_service               fw.start + Watchdog（默认 38002）
  → 加密决策                         非加密=Token 校验；加密=RSA + rsa_callback
```

> 内核 37001 / sys 38001 / user 38002 目前均为**端口占位 / 探活通道**（TCP 连上发 ≤256 字节，回 `OK\n` 断开），尚未承载状态/事件协议。

## 目录结构

```
.
├── main.py                 # 启动入口：三层架构拉起
├── config.yaml             # 主配置（首次启动生成）
├── extensions.yaml         # 官方扩展启用清单（实际启用以本文件为准）
├── requirements.txt        # 依赖（内核仅硬依赖 PyYAML）
├── VERSION / CHANGELOG.md  # 版本与更新日志
├── core/                   # 内核级
├── service/                # 服务级（zkg / transport / startup / watchdog）
├── software/
│   ├── extensions/         # 官方扩展
│   └── plugins/            # 用户插件（每个插件一个子目录，入口 main.py）
├── repo/                   # 本地 zkg 包仓库（机制包）
├── sql/                    # 建表 SQL（init.sql / init_mysql55.sql）
├── data/                   # 运行时数据：zernus.db / logs / plugins_dat
├── deploy/                 # Dockerfile + systemd unit + README
├── docs/                   # 扁平 Markdown 开发文档
└── tests/                  # 脚本式测试（不可 unittest discover，逐个执行）
```

## 关键模块速查

### core/
- `kernel/event_bus.py` —— **事件总线唯一实现**（`core/messaging/event_bus.py` 仅透明重导出）。线程安全，支持 async/sync handler；非主循环 emit 会安全调度回主循环。
- `kernel/task_queue.py` —— 内核任务队列，N 个 worker 线程执行 sync/async 任务，FIFO + 有界历史。
- `hooks/registry.py` —— `HookPoints` 扩展点常量与 `HookRegistry`（部分 Hook 返回值可短路/丢弃）。
- `adapters/protocol.py` —— `ProtocolAdapter` / `ServiceRegistry`，统一接入不同协议。
- `perm/` —— LuckPerms 风格权限，内置角色 `super / owner / admin / member`。
- `plugin_loader` / `config` / `log_broker` / `messaging` / `storage` / `scheduler` / `di.py` 等。

### service/
- `zkg/` —— 包管理：`cli / credentials / defaults / depdb / loader / manifest / resolver / scanner / sources`。
- `transport/framed.py` —— `FramedServer` / `FramedClient`，协议无关的安全二进制帧（HEADER_LEN=29，HMAC 令牌 + 时间戳窗口 + 单调序号防重放，抗粘包/拆包，16MB 载荷防护，纯标准库）。
- `startup.py` —— sys/user 服务与本地端口探活。
- `watchdog.py` —— 内存看门狗。

### software/extensions/（官方扩展）

| 扩展 | 作用 | 默认 |
| --- | --- | --- |
| `onebot_adapter` | OneBot 11 WebSocket 接入（6830） | 开 |
| `webui` | Web 管理后台（8080，前端在 `frontend/`，后端 Flask + waitress） | 开 |
| `status_panel` | 状态面板（8090）：`/` 状态页 + `/health` 只读 JSON | 开 |
| `session` | 多轮会话 | 开 |
| `scheduler` | 定时任务 | 开 |
| `image_renderer` | 图像渲染 | 开 |
| `http_api` | 独立对外 HTTP REST API（1145） | 关 |
| `http_inject` | HTTP 事件注入接入端（8901，调试用） | 关 |
| `node_manager` | 多机 L1 监控聚合（轮询各节点 /health） | 关 |
| `node_control` | 多机 L2/L3 hub 侧（37010）：分组 + 批量/滚动命令 + PTY 流 | 关 |
| `node_agent` | 多机 L2/L3 节点侧（主动连 hub，心跳/命令/包同步） | 关 |

## 开发约定

### 编写插件（`software/plugins/<name>/`）
- 每个插件一个子目录，入口 `main.py`，唯一入口 `register(ctx)`；所有注册写在 `register` 内，**函数外不放副作用**。
- 用 `ctx.command(pattern, handler, priority=50, require_level="")` 注册命令（正则匹配）。
- 依赖机制包：在 `manifest.toml` 的 `dependencies` 声明，再用 `ctx.zkg_tool("<id>")` 取用（未声明/未加载返回 `None`，需防御）。
- 生成骨架：`python -m service.zkg new <name> [--deps store,...]`。
- 插件配置 Schema：`_conf_schema.json`；读取用 `ctx.get_config(key, default)`。
- 单插件内存上限 `plugin.max_memory_mb`（默认 64MB），连续超限被看门狗自动卸载。

### zkg 机制包（`repo/<id>/`）
- 来源：本地 `repo/` + 官方源 `https://zkg.zgric.top/zeronus`（默认不主动连接）。
- 依赖驱动：只加载被依赖引用的机制包；拉包校验索引声明的 `sha256`，不一致拒绝。
- 包签名：HMAC-SHA256，`ZKG_SIGNING_KEY` / `--key-file` 签名入索引；带签名而本机无密钥 → fail closed。
- 现有机制包 id（如 `store` / `http` / `retry` / `lock` / `validate` / `cron` 等，以 `repo/` 为准）。
- 发布链路见 `docs/zkg-publish.md`（上传服务器需人工确认）。

### 测试
- `tests/` 为**脚本式测试**，逐个执行，**不可** `python -m unittest discover`。
- 参考现有：`tests/test_pkg_e2e.py` / `test_zkg_tools.py` / `test_cron_pkg.py` / `test_node_*.py` 等。

### 配置
- 主配置 `config.yaml`；扩展启用以 `extensions.yaml` 为准（启动同步覆盖 `config.yaml → extensions`）。
- 托管覆盖层 `config.managed.yaml`（与 `config.yaml` 同目录）：加载时**最后合并**，
  优先级最高，由编排面下发；不改用户 `config.yaml`。`Framework.reload_config()`
  就地更新配置并触发 `config.reload` 扩展点（端口/DB/SSL 等启动期项仍需重启）。
- 支持 `${VAR}` / `${VAR:-default}` 环境变量展开；回写 yaml 保留 `${VAR}` 引用，不落盘明文。
- 监听地址默认 `127.0.0.1`；局域网/公网需显式放开并配合 `ssl` + 反向代理。
- 默认管理员 `admin / admin123`（首次登录改密；密码用 `pbkdf2_sha256`，不依赖 bcrypt）。
- 多机 L3：hub `node_control.groups` 只做分组成员解析，不绕过 `nodes` 密钥清单；
  `node_agent` 的 `pkg.sync` 会拉取并加载机制包，须显式 `allow_packages: true`（默认禁）；
  配置下发（`config.push` / `config.reload`）须显式 `allow_config: true`（默认禁），
  带 sha256 强校验、原子写与失败回滚；框架升级（`framework.update` / `rollback`）须
  显式 `allow_upgrade: true`（默认禁），走 `service/updater.py`：sha256 + 归档校验 +
  暂存编译 + 备份替换 + 失败/显式回滚，升级后需重启。
  `pkg.list` / `config.status` / `framework.status` 只读，无需开闸。

### 设计总纲（类 Linux 内核，五条原则）
1. **机制 ≠ 策略**：内核只提供通用机制，策略由上层决定。
2. **一类能力一个通用接口**：避免重复碎片入口。
3. **极简**：内核仅硬依赖 `PyYAML`。
4. **稳定 ABI 边界**：层级间以稳定接口通信（插件 API 版本 `core/ctx.PLUGIN_API_VERSION`，当前 1）。
5. **合理默认 + 可覆盖**。

## 文档导航（`docs/`，扁平 Markdown）

| 主题 | 文件 |
| --- | --- |
| 总览与导航 | docs/index.md |
| 路线图 | docs/roadmap.md |
| 安装 | docs/installation.md |
| 开始使用 | docs/getting-started.md |
| 配置 | docs/configuration.md |
| 架构详解 | docs/architecture.md |
| 数据库 | docs/database.md |
| 包管理 zkg | docs/zkg.md / docs/zkg-publish.md |
| 编写插件 | docs/writing-plugins.md |
| 加载器 | docs/loader.md |
| 定时任务 | docs/scheduler.md |
| 权限系统 | docs/permission.md |
| 会话 | docs/session.md |
| API 参考 | docs/api.md |
| 安全传输 | docs/transport.md |
| 接入 IM（OneBot） | docs/connect-im.md |
| 部署上线 | docs/deployment.md |
| 最佳实践 | docs/best-practices.md |

## 注意事项

- **改代码前先核验源码**，文档与代码不一致时以代码为准。
- 修改文档中的 API 名 / 签名 / 路径 / 配置键前，先确认源码实现。
- 不要顺手重构无关代码；每次改动聚焦单一目标。
- 新增内核能力前先问：「这个能力帮谁装上了什么包」（生态优先于机制）。
- `.workbuddy/` 为工作过程日志（测试日志、memory 等），非项目源码。
- 内核层面避免在请求路径静默安装依赖（依赖自愈只走 `main.py` 启动自检）。
