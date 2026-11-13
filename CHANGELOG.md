# 更新日志（CHANGELOG）

记录 ZER NUS 的版本变化。版本号遵循语义化版本 `主版本.次版本.修订号`，`-alpha` / `-beta` 为预发布；
未发布的在研变化放在最顶部「开发中」小节。版本事实以 GitHub Releases 与 Git Tag 为准。

仓库：<https://github.com/kuangxing6367/Zero_Nexus>

---

## 开发中

### 统一资源控制面 · 第二阶段 A（工程加固）

> 只修工程正确性与可验证性，**没有新增任何业务逻辑**。
> 真实网关 claims、凭据保险库、资源适配器、套餐计费、前端仍按既定约束
> 由部署方决定（第二阶段 B）。

- **修复一个会误删已有资源的缺陷**：第一阶段 `provision` 把台账写入失败
  一律当成「刚创建的资源没登记上」，无条件调用
  `adapter.deprovision(external_id)` 回滚。若失败原因是
  `UNIQUE(kind, external_id)` 冲突（适配器 ID 复用 / 请求重放 / 状态漂移），
  该回滚会**删掉台账里已有资源对应的真实实例**，而原记录仍在。
  现在 `store.py` 把唯一键冲突单独识别为 `DuplicateResource`
  （异常特征匹配后再查一次台账确认），编排层抛 `DuplicateExternalId`、
  写 `failed` 审计，**不碰真实资源**。真 SQLite 与 DB-API 后端均有回归。
- **台账与终态审计同事务**：`SqliteBackend`（`BEGIN IMMEDIATE`）与
  `MysqlBackend`（显式 `BEGIN`，不依赖连接 autocommit）提供
  `transaction()`；`add_resource_with_audit` / `delete_resource_with_audit`
  在成功路径一次原子提交，任一步失败两者一起回滚。嵌套事务抛
  `TransactionError`，不做 savepoint 伪装。第一阶段「审计与业务写入
  非同一事务」的限制在支持事务的后端上已消除。
- **后端能力显式声明，不假装原子**：`capabilities()` 与
  `health()['capabilities']` 暴露 `atomic_audit`。`FrameworkBackend`
  明确 `supports_transactions = False`（`ctx.db_*` 每次可能取不同池连接，
  跨调用拼事务无效）；降级时台账先落地、审计后写，审计 detail 带
  `atomic=False` 并输出 warning，审计失败抛 `AuditCommitError`
  （含义是「台账已落地」，不得据此回滚真实资源）。
- **失败语义重写**：授权拒绝永远抛 `PolicyDenied`，补偿审计故障只记日志、
  不覆盖原始失败原因；原子后端审计写失败 → 台账回滚 + 清理外部资源 +
  `failed` 审计；非原子后端审计写失败 → `AuditWriteError` 且保留已落地台账；
  删除路径本地提交失败 → 明确提示「真实资源已删、台账已回滚，人工核对，
  不要直接重试」。事务只覆盖本地库，这条边界写进所有失败文案。
- **MySQL 代码路径真验证（非真服务端）**：新增
  `tests/test_control_plane_tx.py`，用 DB-API 兼容引擎（底层标准库
  sqlite3，`%s` → `?`）真执行 `MysqlBackend` 发出的每条 SQL，验证参数绑定、
  SQL 文本无拼接值字面量、BEGIN/COMMIT/ROLLBACK 次数与时机、游标全部回收、
  事务内故障整体回滚、`ControlPlane` 经该后端跑完整生命周期闭环。
  MySQL 服务端的 DDL / 隔离级别 / 锁行为仍需上线前真库实测，未粉饰。
- **壳层只读可观测**：`expose_readonly_api` 默认关闭，开启后额外注册
  `GET control-plane/resources` 与 `GET control-plane/audit`（复用框架鉴权、
  limit 夹 1..1000、审计已脱敏）。「谁能看」属于授权策略，壳不写死。
  `FrameworkBackend` 的降级声明与只读路由一同纳入框架壳回归。
- 表结构未改动，`SCHEMA_VERSION` 仍为 2；DDL 与列名保持兼容。
- 重写 `docs/control-plane.md`（阶段划分、事务矩阵、失败语义、
  缺陷修复说明、已知限制）、两个 README；更新文档导航与 `extensions.yaml`。
- 验证（本地真跑）：业务内核自检 **116 项**、事务/MySQL 路径 **45 项**、
  框架壳 **29 项**，全部 0 失败；全量 py_compile 0 错误；YAML 通过；
  CI 全量 **30** 个测试脚本 0 失败；跑完全量测试后 pool 字节未变化；
  敏感信息与硬编码生产 IP 扫描无命中。
- 仍存限制：外部真实资源不在事务内；没有真实网关 / 加密 / 适配器实现；
  CLI 不提供写操作；MySQL 真服务端未验证；非原子后端没有自动审计补偿。

### 统一资源控制面 · 第一阶段（取代上一版骨架抽象层）

> 方向纠正：**框架只是个壳子**。上一版把主线做成了 Zeronus 扩展里的
> 契约层 / 槽位注册表 / fail-closed 拒绝实现，那还是在堆框架抽象。
> 本阶段把业务内核整体移出框架，壳层收敛到只剩两个文件。
> 骨架的 `contracts.py` / `slots.py` / `schema.py` / `selftest.py`、
> `docs/resource-control.md` 与 `tests/test_resource_control.py` 已删除，
> 避免两套数据结构与两套契约并存（git 历史可回溯）。

- 新增仓库顶层 **`control_plane/`**：零框架依赖、只用标准库，
  不 import `core` / `service` / `software`，可单独运行、单独测试、
  单独开源。业务内核与框架承载彻底分离。
- **流程做实、策略留空**：`ControlPlane` 提供
  `provision` / `deprovision` / `status` / `list_resources` / `tail_audit`
  真实编排；`policy` 与 `adapters` 是构造参数传入的普通对象，
  缺一个即抛 `DependencyMissing`，内核一行实现都不提供。
  没有插件注册中心、没有槽位字典、没有事件总线、没有自动发现。
- **身份由网关注入**：内核只接收 `actor` / `tenant` / `scopes` 普通参数，
  不定义 Principal、不查库验身份、不写死任务授权；
  `Policy.allowed()` 只接受布尔 `True`，`None` / `0` / `'yes'` / `1`
  一律视为拒绝，防 truthy 值误放行。
- **数据库薄且自治**：仍只有 `rc_resources` / `rc_audit` 两张表，
  列名与上一版保持一致避免冲突；不跨库、不 JOIN 框架身份表、
  不接受第二个连接；不建身份 / 角色 / 权限 / 套餐 / 计费表。
  DDL 按方言显式分两份（MySQL 5.7 / SQLite），不走魔法翻译。
- **真删与失败语义**：物理 `DELETE`，无软删列、无归档表；
  台账写失败尽力回滚真实资源；外部删除失败时台账保留可重试；
  审计写失败抛 `AuditWriteError` 并明确提示勿盲目重试。
- **审计脱敏**：`scrub_detail()` 按键名递归脱敏，命中
  password / secret / token / credential / api_key / private /
  authorization / cookie 一律替换；detail 限 4000 字符；
  内核不接触 spec 里的凭据，审计只写元数据。
- 三种 Backend：`SqliteBackend`（标准库，真跑）、`MysqlBackend`
  （DB-API 映射，不 import 驱动）、`FrameworkBackend`
  （`ctx.db_*` → Backend 的薄适配）。CLI 四个子命令
  init / health / list / audit，刻意不提供 provision / delete。
- 框架壳 `software/extensions/resource_control/` 只剩
  `main.py`（承载 store + 只读健康路由）与 `bridge.py`（DB 适配），
  `register(ctx)` 不建表、不联网、不构造 `ControlPlane`、不注册事件；
  `GET /api/control-plane/health` 复用框架鉴权。
- 新增 `docs/control-plane.md`（阶段划分、分层、失败语义、已知限制）、
  `control_plane/README.md`、`tests/test_control_plane.py`（CI 入口）；
  更新文档导航、`extensions.yaml` 注释、`NOTICE`。
- 自检 `python -m control_plane.selftest` **76 项**：真 SQLite 端到端闭环
  （provision → status → list → deprovision）、SQL 参数化注入串原样存取、
  真删无残留、审计脱敏与截断、依赖缺失拒绝、非布尔授权拒绝、
  适配器失败与回滚、外部删除失败可重试、审计写失败语义、
  CLI 四子命令真跑、源码卫生（零框架 import / 无硬编码 IP 与密钥）。
  自检夹具 `_TestingOnly*` 只在 selftest.py 内，不注入生产路径。
- 已知限制（未粉饰）：审计与业务写入非同一事务；无真实网关 / 加密 /
  适配器实现；MySQL 未连真库验证；CLI 不提供写操作。

### 可复现构建（更正前一条说明）

> 上一条归档加固里把 pool 抖动描述为「包池与索引未同步提交」，
> 该表述不准确，以此条为准：`repo/dist/` 被 `.gitignore` 忽略，
> **索引本来就不入库**，不存在「索引滞后」。

- 根因：`repo/build.py` 用 `tarfile.open(path, "w:gz")`，gzip header 写入
  当前时间，TarInfo 还携带源文件 mtime 与运行账号 uid/gid。同一份源码
  每次 build 都得到不同字节的 tar.gz 与 sha256，导致 `pool/*.tar.gz`
  入库后，任何一次构建（含 CI 与 `tests/test_pkg_signature.py`）都会把
  工作区 15 个包弄脏。
- 修复：改用内存 USTAR + `_normalize_tarinfo`（mtime=0 / uid=gid=0 /
  空 uname、gname / 文件 0644、目录 0755）+ `GzipFile(mtime=0)` 且
  不写原始文件名。**同一份源码重复构建字节完全一致**。
- 新增 `tests/test_repro_build.py`（9 项）：间隔 1.1 秒两次构建字节一致、
  索引 15/15 相符、tar 元数据全固定、gzip 不写 FNAME。
- 文档更正：`docs/zkg-publish.md`、`docs/best-practices.md`、CI 注释改为
  「索引不入库、构建可复现、发布时索引与包池同批上传」。
- 验收：跑完 CI 全量 28 个测试脚本后 pool 字节未变化，工作区不再被污染。

### 归档解包统一加固（P0）

- 新增 `core/security/archive_guard.py`：全站唯一的归档解包安全层，纯标准库、
  自包含。在标准库 `zipfile` / `tarfile` 之上补齐四类独立校验——路径穿越与
  `commonpath` containment 复核、文件名可控性（控制字符 / NTFS ADS 冒号 /
  Windows 保留设备名 / 尾随点空格 / 深度与长度）、成员类型白名单（symlink /
  device / FIFO 一律拒绝）、资源配额（条目数 / 总展开体积 / 单文件体积 /
  压缩比 / Zip Bomb），以及 NFC + casefold 归一化碰撞检测（防 Windows / macOS
  默认文件系统静默覆盖）。
- **校验必须先于不可逆动作**：`validate_zip` / `validate_tar` 只校验不落盘，
  调用方捕获 `SafeArchiveError` 并拒绝请求后，才允许移走旧插件目录、覆盖框架
  代码文件，最后用 `prevalidated=True` 的 `safe_extract_*` 落盘（仍做 containment
  复核）。此前插件上传是「先移走旧目录再 extractall」，校验失败会把旧版插件
  一起弄丢。
- 三档配额 `DEFAULT_LIMITS` / `FRAMEWORK_UPDATE_LIMITS` /
  `SOURCE_TARBALL_LIMITS`：GitHub archive 与源码 tarball 单独放宽条目数和体积，
  避免误伤正常发布包；单条压缩比只对 > 1 MiB 的成员判定，不误伤小文本文件。
- 四个外部归档入口全部接入：插件包上传（`webui/plugins.py`）、框架自更新
  （`webui/framework_ops.py`）、终端更新命令（`core/terminal/builtins.py`）、
  zkg 机制包安装（`service/zkg/loader.py`）。zkg 加载器异常收口从只捕获
  `TarError` 扩展到 `OSError` / `ValueError` / `EOFError`，Windows 特有问题
  不再穿透。
- 包池重打包并逐包校验 15/15 与索引一致。**注：本条对根因的描述已被
  上方「可复现构建」更正** —— 索引 `repo/dist/index.json` 不入库，
  真正原因是打包不可复现；此处仅记录当次重新生成的结果。
- 新增 `tests/test_archive_guard.py`（43 项）与 `docs/archive-guard.md`；
  CI 中逐文件执行。`docs/index.md`、README 文档导航、`docs/best-practices.md`
  同步补充安全章节，CI 中「repo/dist、repo/pool 不入库」的过时注释已修正。

### 安全加固（P0/P1 冲刺）

- **OneBot 接入端默认值收紧（P0）**：`onebot.listen_host` 默认 `0.0.0.0` →
  `127.0.0.1`（extensions.yaml 与 config 内置模板同步）；非回环监听且
  `access_token` 为空时接入端**拒绝启动**（空 token 即不校验，任意主机可注入
  伪造消息事件）；token 比对全部改为常数时间（`hmac.compare_digest`）。
- **admin 默认密码治理**：启动自愈不再把「从未登录的遗留 bcrypt 账号」重置回
  已知的 admin123，改为**随机一次性密码**（仅日志打印一次）；`admin_users` 新增
  `must_change_password` 列——种子账号、存量仍在用默认密码的账号、被随机重置的
  账号自动打标；打标账号登录后除改密/登出/`/api/me` 外的所有管理接口返回 403
  （响应携带 `must_change_password: true`），改密成功自动清除标记。
- **zkg 加载器完整性强制（fail closed）**：源索引未声明 sha256 的包拒绝加载
  （杜绝「下载即执行」的无校验供应链）；tar 解包统一走
  `core/security/archive_guard.py`（逐成员类型白名单 + canonical containment +
  资源配额预检，再以 PEP 706 的 `filter='data'` 解包），详见
  [archive-guard.md](docs/archive-guard.md)。
- **框架自更新 fail-closed**：`/api/framework/update` 改为 require_super，且
  更新 ZIP 必须通过 sha256 校验——配置 `security.update_expected_sha256` 强制比对，
  未配置时默认拒绝更新（显式 `security.update_allow_unsigned: true` 可跳过，
  不推荐）；`/api/restart`、`/api/terminal/exec` 同步提升为 require_super。
- **admin/super 角色边界**：加载即执行代码或可读取敏感信息的端点提升为
  require_super——插件上传、市场安装、插件更新、依赖安装（pip）、隔离环境管理、
  文件读取/下载、config.yaml 全量读取（含数据库口令与各扩展密钥）。
- **node_agent 安全门禁**：新增 `node_agent.allow_docker`（默认 false）——
  `docker.ws`（含容器 exec）与 `docker.pty` / PTY 流此前不受 `allow_shell`
  约束，拿到 hub 控制权即默认获得节点容器 shell；现在与 shell 同级，须显式开闸。
- **node_agent 入站帧验签**：agent 对 hub 下发帧执行与 hub 对称的校验
  （时间戳窗口 + 整帧 HMAC + 单调序号），校验失败断开重连；hub 侧出站帧序号
  从「毫秒时间戳截断」改为全帧型统一单调计数器（`_out_seq`），与 agent 端
  防重放窗口正确配对。**注意：hub 与 agent 需同版本部署。**
- **WebUI CORS 默认拒绝**：未配置 `cors_allowed_origins` 时不再反射任意 Origin
  （此前反射 + `Allow-Credentials: true` 等于放行任意站点携带凭据跨源调用）。
- **API Key 哈希落库**：`api_tokens.token` 改存 sha256（明文仅创建响应返回一次）；
  升级前入库的遗留明文 Key 验证通过后就地升级为哈希。
- **node_agent 停止修复**：`stop()` 此前取消一个从未赋值的 `_task`（恒为 None），
  热卸载/停机后连接与线程全部残留；现改为停止位 + 关连接 + 取消循环任务。
- **node_agent Windows 兼容**：顶层 `import pty` 加平台防护（pty 仅 POSIX，
  此前 Windows 启用该扩展即加载失败）。
- 新增回归测试 `tests/test_security_hardening.py`（37 项：接入端门禁 / token
  语义 / zkg 完整性与穿越 / 强制改密 / CORS / API Key 哈希 / 更新 fail-closed /
  账号自愈迁移）。

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
- 机制包工具箱 9→14：新增 `cache`（TTL + LRU 内存缓存，装饰器版 `cached`）、
  `ratelimit`（令牌桶限速，`try_acquire` / `acquire` 阻塞等待）、
  `text`（显示宽度 / 按宽度截断 / 脱敏掩码 / slugify）、
  `pathsafe`（防目录穿越的安全路径拼接）、
  `hashcode`（哈希摘要 + base64/hex/url 编解码 + 短 ID / UUID），全部纯标准库。
  新增测试 `tests/test_new_tools.py`（6 项：LRU/TTL / 令牌桶 / 文本 / 路径 / 哈希 / 全链路加载）。
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
