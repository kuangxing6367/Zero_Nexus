# Zeronus / Zero Nexus

> 一个基于 Python 的**软件宿主框架**，采用**内核级 / 服务级 / 软件级**三层架构，事件驱动，程序化扩展。

| 项 | 值 |
| --- | --- |
| 当前版本 | **v0.0.1-alpha.0**（见 `CHANGELOG.md`） |
| 仓库 | <https://github.com/kuangxing6367/Zero_Nexus> |
| 开源协议 | MIT + Apache 2.0 双协议，任选其一 |

---

## 一、三层架构

```
                ┌──────────────────────────────────────────────┐
                │                  main.py                      │
                │  启动 core → 监听本地端口                       │
                │     ↓ 拉起 sys 服务（初始化）                  │
                │     ↓ 拉起 user 服务（均监听本地端口）          │
                │     ↓ 是否加密通讯？否→Token / 是→RSA+回调      │
                └──────────────────────────────────────────────┘
                    │                    │                     │
           ┌────────┴───┐       ┌────────┴──────┐      ┌────────┴────────┐
           │  core/     │       │  service/     │      │  software/      │
           │  内核级    │       │  服务级       │      │  软件级         │
           └────────────┘       └───────────────┘      └─────────────────┘
```

### 内核级（`core/`）
- 维护服务级与软件级的活动（生命周期、注册、注销、状态检索：内存 / CPU 占用）。
- 与数据库交互：**SQLite / MySQL / PostgreSQL** 三方言抽象。
- 提供底层 Hook（`core/hooks`）、日志（`core/log_broker`）、事件总线（`core/kernel/event_bus.py`，唯一实现，`core/messaging/event_bus.py` 透明重导出）。
- 权限系统（`core/perm`，LuckPerms 风格）、认证（`core/auth`）、协议抽象（`core/adapters/protocol.py`）。

### 服务级（`service/`）
- **zkg 包管理**（`service/zkg`）：官方包仓库 + 本地 `repo/`，扫描依赖、只加载被依赖的机制包。
- 框架基础服务：`sys` 服务 / `user` 服务拉起（`service/startup.py`）、内存看门狗（`service/watchdog.py`）。
- **通用安全传输原语**（`service/transport`）：认证 TCP 帧，见下文「安全传输」。

### 软件级（`software/`）
- 官方扩展 `software/extensions/`、用户插件 `software/plugins/`。
- 看门狗负责单插件内存（连续超限自动卸载）。

---

## 二、目录结构

```
.
├── main.py                 # 启动入口：三层架构拉起（见上图流程）
├── config.yaml             # 主配置（首次启动生成）：数据库 / 端口 / 安全 / 扩展
├── extensions.yaml         # 软件级官方扩展启用清单（端口等参数）
├── requirements.txt
├── core/                   # 内核级：数据库 / Hook / 日志 / 事件 / 权限 / 认证 / 协议
├── service/                # 服务级：zkg / transport / 启停 / 看门狗
├── software/               # 软件级：extensions（官方）/ plugins（用户）
├── repo/                   # 本地包仓库（zkg）
├── sql/                    # 建表 SQL（init.sql / init_mysql55.sql）
├── data/                   # 运行时数据：zernus.db / logs / plugins_dat
└── docs/                   # 开发文档（扁平 Markdown，见「文档导航」）
```

> 说明：`docs/` 已全部扁平化——所有 `.md` 平铺在 `docs/` 根目录，无子目录、无构建步骤、无 `node_modules`。直接读源码或任意 Markdown 预览器即可。

---

## 三、快速开始

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
python main.py
```

- 首次启动自动建表（`data/zernus.db`，SQLite）并自愈缺失依赖。
- 启动后端口（均默认 `127.0.0.1`）：
  - 内核 `37001`（状态 / 事件通道）
  - `sys` 服务 `38001`、`user` 服务 `38002`
  - Web 管理后台 `8080`（由 `software/extensions/webui` 提供）
- Web 后台默认账号 **`admin` / `admin123`**（首次登录请修改密码）。
- 不需要 IM 接入端也能跑：在 `extensions.yaml` 开启 `http_inject`，用一条 `curl` 注入事件即可调试。

也可以直接用脚本（自动建 venv、装依赖、启动）：

```bash
bash start.sh
```

---

## 四、启动流程

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
├─ 否 → 校验 Token（security.token）
└─ 是 → RSA 握手完成 → 回调端（security.rsa_callback）
```

---

## 五、通讯安全

对应启动流程最后一环「是否加密通讯」：

- **非加密**（`config.yaml → security.encrypted: false`）：校验请求携带的 Token 是否与 `security.token` 一致；`token` 留空即不强制校验。
- **加密**（`security.encrypted: true`）：由 RSA 握手完成后的回调端（`security.rsa_callback`）负责最终校验。

所有监听地址默认 `127.0.0.1`；需要局域网 / 公网访问时再按需放开，并配合 `ssl`（HTTPS/WSS）与反向代理。

---

## 六、数据库

`core/` 提供 SQLite / MySQL / PostgreSQL 三方言抽象，由 `config.yaml → database.type` 切换：

| type | 说明 |
| --- | --- |
| `sqlite` | 默认，零配置开箱即用（`data/zernus.db`） |
| `mysql` | 需先 `pip install pymysql DBUtils`（缺失时启动报错并提示，内核不在请求路径静默装包） |

> PostgreSQL：仅保留 SQL 方言翻译层，数据库连接与驱动接入尚未实现（`type: postgresql` 会回退 SQLite 行为），请勿在生产使用。

SQLite 与 MySQL 的查询 API 一致（`query` / `query_one` / `execute` / `insert` / `scalar` / `count` / `exists` 等），返回均为 `list[dict]`。

---

## 七、包管理（zkg，服务级）

`service/zkg/` 提供统一内容来源：

- 官方包仓库 + 本地仓库（`repo/`）双源；默认不主动连接官方源（留空则仅本地）。
- 启动扫描并解析依赖，只加载被依赖方引用的机制包，减少造轮子。
- 官方优先、社区可扩展。

---

## 八、官方扩展（软件级）

| 扩展 | 作用 | 默认 |
| --- | --- | --- |
| `onebot_adapter` | OneBot 11 WebSocket 协议接入 | 开 |
| `webui` | Web 管理后台（`software/extensions/webui`，前端在 `frontend/`） | 开 |
| `session` | 多轮会话 | 开 |
| `scheduler` | 定时任务 | 开 |
| `http_api` | 独立对外 HTTP REST API | 关 |
| `http_inject` | HTTP 事件注入接入端（调试用） | 关 |
| `image_renderer` | 图像渲染 | 开 |

启用 / 端口由 `extensions.yaml` 控制，并在 `config.yaml → extensions` 可见（实际启用以 `extensions.yaml` 为准）。

---

## 九、安全传输（service/transport）

`service/transport` 提供**协议无关的安全二进制帧传输原语**，可复用于任意「裸 TCP 上的认证通道」场景（远程 agent、节点控制面、内网穿透控制通道等）：

- 帧格式（`HEADER_LEN = 29`，大端序）：`type(1) | ts(4) | token(16=HMAC-SHA256(secret,ts)[:16]) | plen(4) | seq(4) | payload`
- 安全模型：固定对称密钥 + HMAC 令牌 + 时间戳窗口 + 单调序号防重放，恒定时间比对（`hmac.compare_digest`）。
- `FramedServer` / `FramedClient`：基于 `asyncio` 的认证 TCP 通道，自动处理**粘包 / 拆包 / 失步重同步 / 16MB 载荷防护**。
- 纯标准库实现，零第三方依赖。

详见 [安全传输](docs/transport.md)。

---

## 十、文档导航

> 全部为扁平 Markdown，直接打开阅读。

| 主题 | 文件 |
| --- | --- |
| 路线图 | [roadmap](docs/roadmap.md) |
| 总览与导航 | [docs/index.md](docs/index.md) |
| 安装 | [docs/installation.md](docs/installation.md) |
| 开始使用 | [docs/getting-started.md](docs/getting-started.md) |
| 配置 | [docs/configuration.md](docs/configuration.md) |
| 架构详解 | [docs/architecture.md](docs/architecture.md) |
| 数据库 | [docs/database.md](docs/database.md) |
| 包管理 zkg | [docs/zkg.md](docs/zkg.md) |
| 编写插件 | [docs/writing-plugins.md](docs/writing-plugins.md) |
| 定时任务 | [docs/scheduler.md](docs/scheduler.md) |
| 权限系统 | [docs/permission.md](docs/permission.md) |
| 会话 | [docs/session.md](docs/session.md) |
| 加载器 | [docs/loader.md](docs/loader.md) |
| API 参考 | [docs/api.md](docs/api.md) |
| 安全传输 | [docs/transport.md](docs/transport.md) |
| 接入 IM（OneBot） | [docs/connect-im.md](docs/connect-im.md) |
| 部署上线 | [docs/deployment.md](docs/deployment.md) |
| 最佳实践 | [docs/best-practices.md](docs/best-practices.md) |

---

## 开源协议

MIT + Apache 2.0 双协议，任选其一适用。
