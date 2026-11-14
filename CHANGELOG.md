# 更新日志（CHANGELOG）

记录 Zeronus 的版本变化。版本号遵循语义化版本 `主版本.次版本.修订号`，`-alpha` / `-beta` 为预发布；
未发布的在研变化放在最顶部「开发中」小节。版本事实以 GitHub Releases 与 Git Tag 为准。

仓库：<https://github.com/kuangxing6367/Zero_Nexus>

---

## 开发中

### 框架自身升级 / 回滚（L3 收尾完成）

- 新增 `service/updater.py`，统一「校验 → 暂存 → 编译检查 → 备份 → 替换 → 回滚」：
  - sha256 **fail closed**：默认必须提供目标 ZIP 哈希；显式
    `allow_unsigned=True` 才允许跳过（WebUI 仍由 `security.update_allow_unsigned` 控制）。
  - 复用 `core/security/archive_guard.py` 做归档安全校验；解压到暂存区后
    对全部 `.py` 执行编译检查，任何失败都不触碰线上文件。
  - 替换前把旧条目移入 `<root>/.update_backup/<stamp>-<sha12>/`，中途失败自动
    恢复；`rollback_source_update()` 可回滚到任意备份（幂等），
    `list_backups()` 只读列出备份。
  - 白名单只覆盖框架代码/文档（`core` / `service` / `software/extensions` /
    `sql` / `deploy` / `docs` 等），保护 `data/`、`repo/`、`config.yaml`、
    `extensions.yaml` 与 `software/plugins/`。
- 修复 WebUI 框架更新白名单仍是仓库重组前的 `framework/` / `extensions/`
  （实际上只更新了 `sql`、`main.py` 等少数文件），改用 `FRAMEWORK_INCLUDE`。
- WebUI：`POST /api/framework/update` 复用更新机制并支持 `dry_run`；新增
  `GET /api/framework/backups`、`POST /api/framework/rollback`。
- 多机 L3：节点侧新增 `framework.status`（只读）/ `framework.update` /
  `framework.rollback`，后两者需 `allow_upgrade: true`（默认 false）；
  hub 侧新增 `push_framework()` / `rollback_framework()` / `framework_status()`，
  默认 `concurrency=1` 逐台滚动，支持 `stagger`。
- 新增 `tests/test_framework_update.py`（43 项：fail closed / sha256 / 路径穿越 /
  编译失败 / 白名单 / 用户数据保护 / dry_run / 备份 / 幂等回滚 / 节点闸门 /
  hub 载荷构造）。
- `.gitignore` 增加 `config.managed.yaml` 与 `.update_backup/`。
- 局限：升级/回滚不自动重启，也不做重启后健康检查与自动回滚；灰度节奏由
  调用方按逐台回执控制。

### 配置热重载 / 配置下发（L3 收尾之一）

- 新增 `Framework.reload_config()`：重新读取配置文件并**就地更新** `self.config`
  （对象身份不变，已持有引用的模块能读到新值），刷新 heartbeat / watchdog
  派生值，触发 `config.reload` 扩展点（`handler(old_config, new_config)`）。
  不重启服务；端口 / 数据库连接 / SSL 等启动期绑定项仍需重启。
- `core.config` 新增托管覆盖层：`config.yaml` 同目录的 `config.managed.yaml`
  在加载时**最后合并**（`managed_config_path()` / `_deep_merge()` /
  `_apply_managed_overlay()`），优先级高于 `config.yaml` 与 `extensions.yaml`，
  不修改用户原配置文件与 `extensions.yaml`。根节点非映射的配置直接告警返回，
  由调用方以 `ValueError` 拒绝，避免在合并处抛 `AttributeError`。
- 节点侧 `node_agent` 新增：
  - `config.status`（只读）：覆盖层是否存在 / 大小 / sha256 / 顶层键与 `allow_config`。
  - `config.reload`：仅重读配置（含覆盖层）并触发扩展点。
  - `config.push`：把 YAML 原文 + 64 位 sha256 下发到覆盖层；节点重算比对后
    原子写入（临时文件 + fsync + `os.replace`），可选立即重载；写盘或重载失败
    自动回滚到上一份覆盖层。支持 `dry_run`、`reload=false`、`clear`；
    拒绝空 / 非映射 / 超过 1MiB / 校验不匹配的内容。
  - `config.reload` / `config.push` 由 `node_agent.allow_config`（默认 false）开闸；
    热重载只即时应用 `allow_*` / `interval`，`name` / `secret` / `hub_host` /
    `hub_port` 绑定在当前连接上，改为提示需重启。
- hub 侧 `node_control` 新增 `push_config()`（规范化 YAML + 计算 sha256 后走
  `send_group`）、`config_status()`、`reload_config()`，默认 `concurrency=1`
  逐台下发。
- 新增 `tests/test_config_reload.py`（53 项：覆盖层优先级、`reload_config`
  就地更新与扩展点、闸门、sha256/YAML/大小校验、dry_run、原子写、失败回滚、
  clear、热更新字段与连接类字段提示、hub 载荷构造）。
- 更新 `extensions.yaml` 与 `core/config` 的扩展默认值，新增
  `node_agent.allow_config: false`。

### 多机管理（L3 编排第一切片）

- hub 侧 `node_control` 新增逻辑分组：`node_control.groups`（成员须在
  `nodes` 预共享密钥清单内，分组**不绕过鉴权**；未声明的成员启动时告警且不会接入）。
- 新增批量/灰度下发：`send_group(group, cmd, args, timeout, concurrency, stagger)`
  与 `broadcast(...)`。并发上限默认 4，`stagger` 控制相邻下发间隔
  （`concurrency=1` + `stagger=N` 即逐台灰度）；单台失败不中断整组，
  返回 `total / ok / failed / offline / elapsed / results` 聚合摘要；
  未知分组直接返回 `error`，不会静默退化成全量下发。`snapshot()` 增加
  节点所属分组与能力声明。
- 节点侧 `node_agent` 的 HELLO 上报 `capabilities`（命令 → 是否启用），
  新增只读 `pkg.list`（已加载 zkg 工具 + 本地 `repo/` 包目录）与
  `pkg.sync`（按 zkg 源重跑 scan → resolve → rebuild → 按需加载）。
  `pkg.sync` 会拉取并执行远端机制包，须显式 `allow_packages: true`
  （默认 false），完整性校验（sha256 / HMAC 签名）仍在 loader 内强制执行。
- 抽出 `service/startup.run_zkg(fw)`：sys 服务启动与节点 `pkg.sync` 共用同一
  扫描根、源配置与校验路径，避免两套接线漂移。
- 新增 `tests/test_node_orchestration.py`（29 项：能力/闸门 / 只读包清单 /
  分组解析 / 在线离线判定 / 分组与全量下发 / 回执聚合 / 并发与滚动语义）。
- 修订：`docs/deployment.md` 补 L3 配置与调用示例，`docs/roadmap.md` 勾选
  第一切片；配置下发与框架自身升级已在下方 / 上方单独记录。
- 顺带修复 `tests/test_perm.py` 在 Windows GBK 控制台下打印 `⊃` 触发
  `UnicodeEncodeError`（测试因此假红，不是权限断言失败）；删除控制面移除后
  残留的未跟踪 `control_plane/__pycache__` 与 `software/extensions/resource_control/__pycache__`
  （仅 .pyc，无源码，已确认不在 Git 索引内）。

### 边界纠正：统一资源控制面移出框架仓库

> 此前 `cc36856` / `fc34776` 两个提交把上层业务的资源台账、生命周期编排
> 与承载壳误放进本框架仓库，并写进文档主线。Zeronus 是软件宿主框架，
> 该提供的是 DB / Hook / 事件总线 / 扩展承载这类可复用机制，不是某个
> 具体业务的台账与开通流程。本条记录移除，不粉饰、不改写历史。

- 删除仓库顶层 `control_plane/`、`software/extensions/resource_control/`、`docs/control-plane.md`
  与 `tests/test_control_plane*.py`（共 15 个文件）。
- 清理 `README.md`、`docs/index.md`、`extensions.yaml` 与 `NOTICE` 中的相关叙事与启用项；
  「开发中」里原两段控制面记录一并移除。
- 代码整体移出为独立项目（零框架依赖，可单独运行与开源）；
  **已推送的 Git 历史保留**，未 rewrite、未 force push。
- 框架自身功能不受影响：归档解包安全层、可复现构建、安全加固、多机管理、zkg
  等条目均在下方保留。
- 验证：移除后 CI 逐个测试脚本 0 失败、全量 `py_compile` 0 错误、
  `extensions.yaml` 解析通过、pool 字节未变化、全仓无控制面残留引用。

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
