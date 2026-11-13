# 统一资源控制面 · 第一阶段

> **框架只是个壳子。** Zeronus 提供进程、数据库连接与 HTTP 承载，
> 不提供业务能力。业务内核是仓库顶层 [`control_plane/`](../control_plane/)，
> 零框架依赖、只用标准库，可单独运行、单独测试、将来整个拆出去开源。

## 阶段划分

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 第一阶段 | 独立业务内核：资源台账 + 审计落库 + 生命周期编排，真库跑通闭环 | 已交付 |
| 第二阶段 | 真实网关 claims 接入、真实凭据保险库、真实资源适配器、套餐/配额、前端控制台 | **由部署方决定，本仓库不代做实现** |

第一阶段的取舍：**把「流程」做实，把「策略」留空**。
授权怎么判、claims 怎么验、凭据怎么加密、外部系统怎么开通，
一行实现都不写；只把依赖作为构造参数收进来，缺一个就明确拒绝。

## 分层

```
统一网关 / 调用方   把 claims 摊平成 actor / tenant / scopes
        ↓  普通参数，不是对象注册
control_plane/control.py     ControlPlane：生命周期编排（流程）
        ↓  构造参数注入
policy / adapters            普通对象，由部署方实现
        ↓
control_plane/store.py       两张表，参数化 SQL，真删，审计脱敏
        ↓
Backend                      SqliteBackend / MysqlBackend / FrameworkBackend
        ↓
Zeronus（可选壳）            software/extensions/resource_control/，仅承载
```

刻意避免过度抽象：没有插件注册中心、没有槽位字典、没有事件总线、
没有自动发现、没有 `runtime_checkable` 校验。`models.py` 里三个 Protocol
**只用于类型提示**，运行时不检查、不注册。

## 数据库为什么这么薄

只有两张表，列名与上一版骨架保持一致，避免同库并存时冲突：

| 表 | 作用 | 不存什么 |
| --- | --- | --- |
| `rc_resources` | 资源索引：kind / external_id / owner_subject / tenant | 连接串、凭据明文、套餐价格 |
| `rc_audit` | 操作事实：谁 / 动作 / 目标 / 结果 / 脱敏 detail | 凭据明文、spec 全文、请求体 |

明确不做：

- **不建身份、角色、权限、套餐、计费表**。身份由统一网关注入，
  将来网关换成 SSO 或另一套授权中心，表结构不需要改动。
- **不跨库**。SQL 不带库名前缀、无 `USE`、不 JOIN `users` /
  `admin_users` / `perm_*`；只接受调用方注入的一个 Backend。
- **不写死任务授权**。`Policy.allowed()` 完全由注入方决定；
  内核只做一件事——**只接受布尔 `True`**，`None` / `0` / `'yes'` / `1`
  一律视为未放行，避免 truthy 值意外放行。
- **删除即真删**。物理 `DELETE`，无 `deleted_at` / `is_deleted`，不归档、不写备份表。
- 所有业务 SQL 参数化（`%s`），`SqliteBackend` 内部翻译为 `?`；
  `limit` 强制转 int 并夹在 1..1000。

DDL 由 Backend 按方言提供：MySQL 5.7 风格给 `MysqlBackend` /
`FrameworkBackend`（走 `ctx.create_table` 自动翻译），SQLite 风格给
`SqliteBackend`。没有魔法翻译函数，两份 DDL 显式摆着。

## 生命周期与失败语义

```
provision    授权 → 适配器开通 → 台账登记 → 审计 ok
                 │        │            │
                 │        │            └ 失败：尽力 deprovision 回滚真实资源
                 │        └ 失败：不写台账，审计 failed
                 └ 拒绝：不动任何东西，审计 denied

deprovision  授权 → 外部物理删除 → 台账物理 DELETE → 审计 ok
                          │                │
                          │                └ 失败：审计 failed，需人工核对
                          └ 失败：台账保留，可重试（不产生「账上有、实际没了」）
```

顺序是刻意的：外部删除失败时台账仍在，运维可直接重试；
反过来若先删台账，真实资源就成了看不见的孤儿。

`spec` 原样交给适配器，内核不读、不改、不存凭据；审计 detail 只写
`kind` / `external_id` / 失败原因这类元数据。

## 审计脱敏

`scrub_detail()` 在写库前按键名递归脱敏（限深 3）：命中
`password` / `secret` / `token` / `credential` / `api_key` / `private` /
`authorization` / `cookie` 及其变体，值一律替换为 `<redacted>`；
字符串截到 256 字符，整体 JSON 截到 4000 字符。

**审计写入失败抛 `AuditWriteError`**，不静默吞掉。异常信息明确提示
「业务动作可能已落地，请按外部标识核对台账后再决定重试，不要盲目重建」。

## 已知限制（不粉饰）

1. **审计与资源写入不是同一事务**。进程在两次写入之间崩溃，
   可能出现资源已建但缺对应终态审计。第二阶段接真实事务或外部审计系统时消除。
2. **没有真实网关 / 加密 / 适配器实现**。这是刻意留空，按既定约束
   具体逻辑由部署方决定；自检里的 `_TestingOnly*` 夹具只存在于
   `selftest.py`，不导出、不注入生产路径。
3. **CLI 只做 init / health / list / audit**，刻意不提供 provision / delete
   子命令——那需要注入真实实现，塞演示实现等于给生产留后门。
4. **MySQL 路径未连真库验证**。`MysqlBackend` 只做 DB-API 映射，
   本机没有 MySQL；DDL 与占位符按框架既有约定落地，上线前需实测。
5. `Policy.allowed()` 由内核在业务线程同步调用；若策略要查外部系统，
   超时与熔断由实现方自己负责。

## 运行与验证

```bash
# 业务内核自检（真 SQLite 端到端闭环，不启动框架、不联网）
python -m control_plane.selftest                 # 76 项

# 命令行：不启动框架、不开 HTTP
python -m control_plane.cli --db data/controlplane.db init
python -m control_plane.cli --db data/controlplane.db health
python -m control_plane.cli --db data/controlplane.db list --tenant t1
python -m control_plane.cli --db data/controlplane.db audit --limit 20

# CI 入口
python tests/test_control_plane.py
```

自检覆盖：源码独立性与卫生（零框架 import、无硬编码 IP / 密钥 /
授权码）、建表幂等、SQL 参数化（注入串原样存取）、真删无残留、
审计脱敏与截断、依赖缺失拒绝、非布尔授权返回值一律拒绝、
适配器缺失与失败、台账失败回滚、外部删除失败台账保留可重试、
审计写失败语义、完整 provision → status → list → deprovision 闭环、
CLI 四个子命令真跑。

**未覆盖**：真实网关验签、真实加解密、真实资源开通、端到端 HTTP、
MySQL 真库。这些依赖第二阶段选定的实现。

## 接入清单（给第二阶段实现者）

1. 网关侧把 claims 摊平成 `actor` / `tenant` / `scopes`，本层不解释格式；
2. 实现 `Policy.allowed(actor, action, target, scopes)`；
3. 实现 `AdapterResolver` + 各 `ResourceAdapter`（`deprovision` 必须真删）；
4. 凭据如需加密，由调用方在**把 spec 交给内核之前**自行处理——
   内核不接触凭据，也不存 `seal` / `open` 的结果；
5. `ControlPlane(store, policy=..., adapters=...)` 显式构造，缺依赖即拒绝；
6. 用自己的端到端测试验证，不依赖本层的边界自检。
