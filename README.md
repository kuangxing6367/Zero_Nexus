# ZER NUS

**一个基于 Python 的软件层内核，采用内核级 / 服务级 / 软件级 三层架构。**

| 项 | 值 |
| --- | --- |
| 当前版本 | **v0.0.1-alpha.0**（见 [CHANGELOG.md](CHANGELOG.md)） |
| 仓库 | <https://github.com/kuangxing6367/Zero_Nexus> |
| 开源协议 | MIT + Apache 2.0 双协议，任选其一 |

---

## 一、三层架构

```
             ┌─────────────────────────────────────────────┐
             │                main.py                       │
             │  启动 core → 监听本地端口                     │
             │     ↓ 拉起 sys 服务（初始化）                 │
             │     ↓ 拉起 user 服务（均监听本地端口）         │
             │     ↓ 是否加密通讯？否→读 Token / 是→RSA+回调  │
             └─────────────────────────────────────────────┘
                 │                  │                    │
        ┌────────┴───┐     ┌────────┴──────┐     ┌────────┴────────┐
        │  core/     │     │  service/     │     │  software/      │
        │  内核级    │     │  服务级       │     │  软件级         │
        └────────────┘     └───────────────┘     └─────────────────┘
```

### 内核级（core/）
- 维护服务级与软件级的活动（生命周期、注册、注销）。
- 与数据库交互：**SQLite / MySQL / PostgreSQL**。
- 提供底层 Hook，负责检索三级状态（内存占用、CPU 占用等）。
- 负责 Log 输出。

### 服务级（service/）
- **zkg 包管理**：提供统一内容来源（官方包仓库 + 本地），减少造轮子。
- 框架服务基础（如 `mg`、`db` 等）。
- 软件启动与注销核心服务（sys 服务 / user 服务）。
- **看门狗**：自身服务内存处理、zkg 拉起的服务。

### 软件级（software/）
- 与上层服务级通讯，并在用户操作之间启动服务。
- **看门狗**负责内存。
- 具体实现见 `software/extensions/`（官方扩展）与 `software/plugins/`（用户插件）。

---

## 二、目录结构

```
.
├── main.py                 # 启动入口：三层架构拉起（见上图流程）
├── config.yaml             # 主配置（首次启动生成）：数据库 / 内核端口 / 服务端口 / 安全 / 扩展
├── extensions.yaml         # 软件级官方扩展启用清单（非配置中心，仅记录启用与端口）
├── requirements.txt
├── core/                   # 内核级：数据库 / 状态 Hook / 日志 / 活动维护
├── service/                # 服务级：zkg / 框架服务 / 启停 / 看门狗
│   ├── zkg/                #   包管理（本地 + 官方源）
│   ├── startup.py          #   sys / user 服务拉起
│   └── watchdog.py         #   内存看门狗
├── software/               # 软件级
│   ├── extensions/         #   官方扩展（OneBot 接入 / Web 后台 / 会话 / 定时 …）
│   └── plugins/            #   用户插件
├── repo/                   # 本地包仓库（zkg）
├── sql/                    # 建表 SQL
├── data/                   # 运行时数据：zernus.db / logs / plugins_dat
└── docs/                   # 开发文档（VitePress）
```

---

## 三、快速开始

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
python main.py
```

- 首次启动自动建表（`data/zernus.db`，SQLite）并在需要时自愈缺失依赖。
- 启动后：
  - 内核监听 `127.0.0.1:37001`（状态 / 事件通道）；
  - sys 服务监听 `127.0.0.1:38001`、user 服务监听 `127.0.0.1:38002`；
  - Web 管理后台默认 `127.0.0.1:8080`（由 `software/extensions/webui` 提供）。
- 不需要 IM 接入端也能跑：在 `extensions.yaml` 开 `http_inject` 即可用一条 `curl` 注入事件。

也可以直接用脚本（自动建 venv、装依赖、启动）：

```bash
bash start.sh
```

---

## 四、启动流程（手写笔记第二页）

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

---

## 五、通讯安全

对应启动流程最后一环「是否加密通讯」：

- **非加密**（`config.yaml → security.encrypted: false`）：校验请求携带的 Token 是否与 `security.token` 一致；`token` 留空即不强制校验。
- **加密**（`security.encrypted: true`）：由 RSA 握手完成后的回调端（`security.rsa_callback`）负责最终校验。

所有监听地址默认 `127.0.0.1`；需要局域网 / 公网访问时再按需放开。

---

## 六、数据库

`core/` 提供 SQLite / MySQL / PostgreSQL 三方言抽象，由 `config.yaml → database.type` 切换：

| type | 说明 |
| --- | --- |
| `sqlite` | 默认，零配置开箱即用（`data/zernus.db`） |
| `mysql` | 需 `pymysql`（框架按需自动安装） |
| `postgresql` | 需 `psycopg2`（框架按需自动安装） |

---

## 七、包管理（zkg，服务级）

`service/zkg/` 提供统一内容来源：

- 官方包仓库 + 本地仓库（`repo/`）双源；
- 启动扫描并解析依赖，只加载有依赖方的机制包；
- 减少造轮子，官方优先、社区可扩展。

---

## 八、官方扩展（软件级）

| 扩展 | 作用 | 默认 |
| --- | --- | --- |
| `onebot_adapter` | OneBot 11 WebSocket 协议接入 | 开 |
| `webui` | Web 管理后台 | 开 |
| `session` | 多轮会话 | 开 |
| `scheduler` | 定时任务 | 开 |
| `http_api` | 独立对外 HTTP API | 关 |
| `http_inject` | HTTP 事件注入接入端 | 关 |

---

## 九、文档导航

| 入口 | 内容 |
| --- | --- |
| [开发文档总入口](docs/guide/README.md) | 按角色分流的上手路径 |
| [安装](docs/guide/installation.md) · [开始使用](docs/guide/getting-started.md) | 环境、启动、接入平台 |
| [编写插件](docs/guide/writing-plugins.md) | 从零写一个完整插件 |
| [配置系统](docs/guide/configuration.md) | `config.yaml` 与 `extensions.yaml` |
| [架构详解](docs/advanced/architecture.md) | 三层、启动时序 |
| [数据库](docs/advanced/database.md) · [定时任务](docs/advanced/scheduler.md) | 治理与数据 |
| [部署上线](docs/advanced/deployment.md) | systemd / Docker / 反向代理 / 安全清单 |

---

## 开源协议

MIT + Apache 2.0 双协议，任选其一适用。
