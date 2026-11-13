# 统一资源控制面（骨架设计）

本文描述 `software/extensions/resource_control/` 的**边界与约束**，
不是功能清单。实现细节（加密、授权、适配器、网关）由部署方决定。

## 定位

Zeronus 三层架构中位于**软件级**：作为官方扩展装入，不修改内核。
目标是把分散的账号体系、套餐计费、凭据保管、审计能力收敛到同一套边界，
让多种资源类型（主机站点、AI 沙箱实例、MC 实例）共用，而不是各造一套。

刻意避免过度抽象：

- 槽位是**一个普通字典 + 显式 `bind()`**，没有插件注册中心；
- 适配器是普通类，没有自动发现、没有 entry_points 扫描；
- 不新增事件总线、不引入新的泛型层；
- 五个槽位穷举全部可替换点，不再加第六个。

## 分层

```
接入层        框架 Web 鉴权（复用，不自行判定）
                ↓
契约层        contracts.py：数据结构 + 5 个 Protocol，零实现
                ↓
装配层        slots.py：显式绑定 / fail-closed 拒绝 / 只读状态
                ↓
持久化层      schema.py：2 张表，走 ctx.create_table 方言适配
                ↓
实现层        由部署方注入（本仓库不提供）
```

`main.py` 的 `register(ctx)` 只做装配：建注册表 → 挂 `fw.services` →
注册只读状态路由。不建表、不联网、不启动监听、不注册事件。

## 数据库为什么这么薄

只保留两张表：

| 表 | 作用 | 不存什么 |
| --- | --- | --- |
| `rc_resources` | 资源索引：kind / external_id / owner_subject / tenant | 连接串、凭据明文、套餐价格 |
| `rc_audit` | 审计元数据：谁 / 动作 / 目标 / 结果 | 凭据明文、请求体全文 |

明确不做的：

- **不建身份、角色、权限、套餐、计费表**。身份由统一网关注入 claims；
  将来网关换成 SSO 或另一套授权中心，表结构不需要改动。
- **不跨库**。模块只接受框架注入的 `ctx`，不接受连接串、库名或第二个
  数据库句柄；DDL 不带库名前缀、无 `USE`、不 JOIN `users` / `admin_users` /
  `perm_*`。
- **不写死任务授权**。`AuthorizationPolicy` 完全由实现方决定，内核只消费
  `Decision`，不内置角色模型或权限矩阵。
- **删除即真删**。物理 `DELETE`，无 `deleted_at` / `is_deleted`，不归档。
- 所有业务 SQL 参数化，占位符由 `core/storage/dialect.py` 统一翻译。

DDL 为 MySQL 5.7 风格、`CREATE TABLE IF NOT EXISTS`，经
`ctx.create_table` 翻译后也适用于 SQLite；`install(ctx)` 幂等，重复调用安全。

## 身份与授权

```
外部请求 → 框架鉴权 → identity 槽位解析 claims → Principal
                                                    ↓
                              policy 槽位 decide() → Decision
```

`Principal` 是 frozen dataclass，只有 `subject` / `tenant` / `scopes` /
`claims`，**没有查库能力**。本层不验证身份真伪、不反查用户表，
只消费网关注入的结果。这样"统一网关授权"接入时只需换 `identity` 实现。

## 凭据保险库

`CredentialVault` 只有两个方法：`seal(plaintext, context)` / `open(token, context)`。

**算法、密钥派生、信封结构、轮换周期、服务密钥与人工解锁的取舍，
全部不在本仓库决定。** 契约里只钉死一条失败语义：
`open` 失败必须抛异常，不允许静默返回密文或空串——上层把密文当明文
继续用，比直接失败危险得多。

同理，未配置 `vault` 槽位时调用一律抛 `SlotUnavailable`，不降级到任何
内置算法。

## Fail-closed 装配

| 状态 | 行为 |
| --- | --- |
| 扩展未启用 | `register()` 直接返回，不装配、不注册路由 |
| 启用但槽位未配置 | 业务调用抛 `SlotUnavailable`；状态接口仍可读 |
| 槽位已绑定 | 由注入实现决定行为；绑定时校验接口形状 |
| `unbind()` 后 | 立即回到拒绝态，不保留缓存实现 |

只读状态接口 `GET /api/resource-control/status` 复用框架自身鉴权
（`auth=True`），只返回槽位是否配置，不返回实现类名、配置值或任何凭据。

## 开源与许可

- 沿用仓库根 `LICENSE`：**MIT 与 Apache-2.0 任选其一**；
  源码带 `SPDX-License-Identifier: MIT OR Apache-2.0`。
- 本扩展仅依赖 Python 标准库与 Zeronus 内核，无第三方组件，
  详见仓库根 `NOTICE`。
- 源码不含硬编码生产 IP、密钥、明文口令或授权码校验；
  自检第 6 组会扫描这些模式，防止后续提交把敏感信息带进公开仓库。
- 允许修改、商用与二次开发，保留原始版权声明即可。

## 自检

```bash
python software/extensions/resource_control/selftest.py   # 46 项
python tests/test_resource_control.py                     # CI 入口
```

不启动框架、不连数据库、不联网。覆盖：契约数据结构、槽位默认拒绝、
绑定/解除/错配、schema 幂等与不跨库、真删、装配无副作用、源码卫生。

**未覆盖**：真实网关验签、真实加解密、真实资源开通、端到端 HTTP。
这些依赖你后续选定的实现，本骨架不代做验证。

## 接入清单（给后续实现者）

1. 实现 `IdentityGateway`，明确 claims 来源与验签方式；
2. 实现 `AuthorizationPolicy`，定义 scope 与动作语义；
3. 实现 `CredentialVault`，确定加密方案与密钥生命周期；
4. 实现 `AdapterResolver` + 各 `ResourceAdapter`（真删）；
5. 实现 `AuditSink`，确认审计保留期与不外发明文；
6. `extensions.yaml` 打开 `resource_control.enabled`；
7. 显式调用 `install(ctx)` 幂等建表；
8. 用自己的端到端测试验证，不依赖本骨架的边界自检。
