# 统一资源控制面 · 第一阶段 + 第二阶段工程加固

> **框架只是个壳子。** Zeronus 提供进程、数据库连接与 HTTP 承载，
> 不提供业务能力。业务内核是仓库顶层 [`control_plane/`](../control_plane/)，
> 零框架依赖、只用标准库，可单独运行、单独测试、将来整个拆出去开源。

## 阶段划分

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 第一阶段 | 独立业务内核：资源台账 + 审计落库 + 生命周期编排，真库跑通闭环 | 已交付 |
| 第二阶段 · A（工程加固） | 台账与终态审计同事务、重复标识不误删、后端能力显式声明、MySQL 代码路径真验证、壳层只读可观测 | **本轮已交付** |
| 第二阶段 · B（业务实现） | 真实网关 claims 接入、真实凭据保险库、真实资源适配器、套餐/配额、前端控制台 | **由部署方决定，本仓库不代做实现** |

第一阶段把「流程」做实、「策略」留空；第二阶段 A 只修工程正确性与可验证性，
**没有往里加任何业务逻辑**。授权怎么判、claims 怎么验、凭据怎么加密、
外部系统怎么开通，仍然一行实现都不写。

## 分层

```
统一网关 / 调用方   把 claims 摊平成 actor / tenant / scopes
        ↓  普通参数，不是对象注册
control_plane/control.py     ControlPlane：生命周期编排（流程）
        ↓  构造参数注入
policy / adapters            普通对象，由部署方实现
        ↓
control_plane/store.py       两张表，参数化 SQL，真删，审计脱敏，原子提交
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
- **删除即真删**。物理 `DELETE`，无软删标记，不归档、不写备份表。
- 所有业务 SQL 参数化（`%s`），`SqliteBackend` 内部翻译为 `?`；
  `limit` 强制转 int 并夹在 1..1000。

DDL 由 Backend 按方言提供：MySQL 5.7 风格给 `MysqlBackend` /
`FrameworkBackend`（走 `ctx.create_table` 自动翻译），SQLite 风格给
`SqliteBackend`。没有魔法翻译函数，两份 DDL 显式摆着。
第二阶段 A **没有改表结构**，`SCHEMA_VERSION` 仍是 2。

## 台账与审计同事务

第一阶段的已知限制「审计与业务写入不是同一事务」已消除（限支持事务的后端）。

```
provision    授权 → 适配器开通 → ┌ 台账 INSERT ┐ → 提交
                                 └ 审计 ok     ┘   ← 一次 BEGIN / COMMIT
deprovision  授权 → 外部物理删除 → ┌ 台账 DELETE ┐ → 提交
                                   └ 审计 ok   ┘   ← 一次 BEGIN / COMMIT
```

事务内任一步失败，**台账与审计一起回滚**。失败路径的补偿审计
（`denied` / `failed`）在事务外独立写入，写不进去只记日志，
绝不覆盖原始失败原因——特别是**授权拒绝永远抛 `PolicyDenied`**，
不会被审计故障变成别的异常。

### 后端能力矩阵（不假装原子）

| Backend | 事务 | `atomic_audit` | 降级行为 |
| --- | --- | --- | --- |
| `SqliteBackend` | `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` | `True` | 无降级 |
| `MysqlBackend` | 显式 `BEGIN` / `COMMIT` / `ROLLBACK`，不依赖连接 autocommit | `True` | 无降级 |
| `FrameworkBackend` | **不支持** | `False` | 台账先落地、审计后写，审计 detail 带 `atomic=False`，并输出 warning |

`FrameworkBackend` 明确不提供事务：`ctx.db_*` 每次调用可能从连接池取到
不同连接，跨调用拼 `BEGIN`/`COMMIT` 并不能保证落在同一事务里，
**假装原子比不原子更危险**。能力通过 `ResourceStore.capabilities()`
与 `health()['capabilities']` 对外可见，CLI 与只读路由都能看到。
需要原子审计的部署方请直接用 `SqliteBackend` / `MysqlBackend`。

嵌套事务一律抛 `TransactionError`，不做 savepoint 伪装。

### 失败语义（按后端区分）

| 场景 | 原子后端 | 非原子后端 |
| --- | --- | --- |
| 审计写失败 | 台账回滚，外部真实资源需清理 → `ControlPlaneError` + `failed` 补偿审计 | 台账已落地 → `AuditWriteError` + `AuditCommitError` 语义，**不回滚真实资源** |
| 台账唯一键冲突 | `DuplicateExternalId`，**绝不调用 `adapter.deprovision`** | 同左 |
| 外部删除失败 | 台账保留，可直接重试 | 同左 |
| 本地提交失败（删除路径） | 真实资源已删、台账回滚 → 抛错并提示人工核对，**不要盲目重试** | 台账已删、审计缺失 → `AuditWriteError` |

**事务只覆盖本地库，不覆盖外部真实资源。** 这是分布式动作固有的边界，
所有失败文案都明确写出「请按外部标识核对台账与真实资源状态后再决定重试」。

## 一个必须写清楚的缺陷修复

第一阶段 `provision` 把台账写入失败一律当成「我刚创建的资源没登记上」，
于是调用 `adapter.deprovision(external_id)` 回滚。但失败原因可能是
`UNIQUE(kind, external_id)` 冲突——即适配器返回了**台账里已存在**的外部标识
（ID 复用、请求重放、适配器状态漂移）。此时那个回滚会
**删掉已有资源对应的真实实例**，且原台账记录还在。

现在 `store.py` 把唯一键冲突单独识别为 `DuplicateResource`（异常特征匹配后
再查一次台账确认），编排层收到后抛 `DuplicateExternalId`、写 `failed` 审计，
**不碰真实资源**。两条路径都在真 SQLite 与 DB-API 后端上做了回归测试。

## 审计脱敏

`scrub_detail()` 在写库前按键名递归脱敏（限深 3）：命中
`password` / `secret` / `token` / `credential` / `api_key` / `private` /
`authorization` / `cookie` 及其变体，值一律替换为 `<redacted>`；
字符串截到 256 字符，整体 JSON 截到 4000 字符。
内核不接触 spec 全文，审计只写元数据与失败原因。

## 框架壳（可选，仍然很薄）

`software/extensions/resource_control/` 只有 `main.py` + `bridge.py`：

- `register(ctx)` 构造 `ResourceStore` 挂到 `fw.services['control_plane_store']`，
  默认只注册 `GET /api/control-plane/health`（复用框架鉴权）；
- `expose_readonly_api: true` 时额外注册 `resources` / `audit` 两条只读路由，
  **默认关闭**——「谁能看台账与审计」属于授权策略，本壳不写死；
  路由只做参数夹取与序列化，不返回配置值或实现细节；
- **不构造 `ControlPlane`**、不建表（`install(ctx)` 需显式调用）、
  不联网、不注册事件、不注入实现。

## 已知限制（不粉饰）

1. **外部真实资源不在事务内**。事务只能保证本地台账与审计一致；
   `deprovision` 本地提交失败时会出现「真实资源已删、台账回滚保留」，
   必须人工核对，不能盲目重试删除。
2. **没有真实网关 / 加密 / 适配器实现**。这是刻意留空，按既定约束
   具体逻辑由部署方决定；自检里的 `_TestingOnly*` 夹具只存在于
   `selftest.py` / 测试脚本，不导出、不注入生产路径。
3. **CLI 只做 init / health / list / audit**，刻意不提供 provision / delete
   子命令——那需要注入真实实现，塞演示实现等于给生产留后门。
4. **MySQL 服务端仍未连真库验证**。`tests/test_control_plane_tx.py` 用
   DB-API 兼容引擎（底层标准库 sqlite3，`%s` → `?`）真执行
   `MysqlBackend` 发出的每条 SQL，验证的是**它的代码路径**：参数绑定、
   事务边界、游标回收、故障回滚、完整生命周期闭环。MySQL 服务端的
   DDL / 隔离级别 / 锁行为 / 驱动差异仍需在上线前用真库实测。
5. `Policy.allowed()` 由内核在业务线程同步调用；若策略要查外部系统，
   超时与熔断由实现方自己负责。
6. 不支持事务的 Backend 上，审计缺失只能靠 `atomic=False` 标记事后发现，
   没有自动补偿任务（补偿策略属于业务逻辑，不由本层决定）。

## 运行与验证

```bash
# 业务内核自检（真 SQLite 端到端闭环，不启动框架、不联网）
python -m control_plane.selftest              # 116 项
python tests/test_control_plane.py            # CI 入口（同一套断言）
python tests/test_control_plane_tx.py         # 事务 + MysqlBackend 代码路径，45 项
python tests/test_control_plane_bridge.py     # 框架壳，29 项

# 命令行：不启动框架、不开 HTTP
python -m control_plane.cli --db data/controlplane.db init
python -m control_plane.cli --db data/controlplane.db health
python -m control_plane.cli --db data/controlplane.db list --tenant t1
python -m control_plane.cli --db data/controlplane.db audit --limit 20
```

自检覆盖：源码独立性与卫生（零框架 import、无硬编码 IP / 密钥 /
授权码）、建表幂等、SQL 参数化（注入串原样存取且不出现在 SQL 文本）、
真删无残留、审计脱敏与截断、依赖缺失拒绝、非布尔授权返回值一律拒绝、
授权拒绝不被审计故障掩盖、适配器缺失与失败、**事务提交 / 异常回滚 /
嵌套拒绝**、**原子写入失败后台账与审计一起消失**、
**重复 external_id 不误删已有真实资源**、非原子后端显式降级标记、
审计写失败语义、完整 provision → status → list → deprovision 闭环、
`MysqlBackend` 参数绑定与事务边界与游标回收、CLI 四个子命令真跑。

**未覆盖**：真实网关验签、真实加解密、真实资源开通、端到端 HTTP 框架、
MySQL 真服务端。这些依赖第二阶段 B 选定的实现。

## 接入清单（给第二阶段 B 实现者）

1. 网关侧把 claims 摊平成 `actor` / `tenant` / `scopes`，本层不解释格式；
2. 实现 `Policy.allowed(actor, action, target, scopes)`；
3. 实现 `AdapterResolver` + 各 `ResourceAdapter`（`deprovision` 必须真删），
   并保证 `provision` 返回的 `external_id` 全局唯一且稳定；
4. 凭据如需加密，由调用方在**把 spec 交给内核之前**自行处理——
   内核不接触凭据，也不存 `seal` / `open` 的结果；
5. `ControlPlane(store, policy=..., adapters=...)` 显式构造，缺依赖即拒绝；
   优先选支持事务的 Backend，并通过 `store.capabilities()` 确认
   `atomic_audit=True`；
6. 用自己的端到端测试（含真 MySQL 服务端）验证，不依赖本层的边界自检。
