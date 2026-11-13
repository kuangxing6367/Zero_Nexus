# 统一资源控制面（骨架）

> 状态：**只有边界，没有实现**。默认关闭，启用后未注入实现时所有业务调用
> fail-closed 拒绝。
> 许可：沿用仓库根 `LICENSE`（MIT 与 Apache-2.0 任选其一），
> 源码文件带 `SPDX-License-Identifier: MIT OR Apache-2.0`。

## 这个目录是什么

Zeronus 的一个官方扩展，提供"统一资源控制面"的**契约层与装配骨架**：
把身份、授权、凭据、资源适配器、审计五类能力定义为可替换槽位，
让不同资源（主机站点、沙箱实例、MC 实例等）共用同一套接入边界。

它**不是**：一个能开通主机的面板、一套 SSO 实现、一个加密库、一个插件框架。
具体逻辑由部署方注入，本目录不代做决定。

## 三条硬约束

1. **数据库薄且自治**。只有 `rc_resources`、`rc_audit` 两张表；不建身份/角色/
   权限/套餐/计费表，不跨库、不 JOIN 框架 `users` / `admin_users` / `perm_*`，
   不接受第二个数据库连接或连接串。将来接入统一网关授权时，改的是
   `identity` 槽位，表结构不用动。
2. **身份由网关注入 claims**。`Principal` 只携带 subject / tenant / scopes /
   原始 claims，没有查库能力。授权判定完全交给 `policy` 槽位，本层不写死
   任何角色、任务授权或权限矩阵。
3. **删除即真删**。`deprovision` 必须物理移除，DDL 无 `deleted_at` /
   `is_deleted`，不归档。审计只记元数据，不记凭据明文。

## 槽位

| 槽位 | 协议 | 由谁决定 |
| --- | --- | --- |
| `identity` | `IdentityGateway` | 统一网关 / SSO / 本地会话如何解析 claims |
| `policy` | `AuthorizationPolicy` | 授权策略、角色模型、scope 语义 |
| `vault` | `CredentialVault` | **加密算法、密钥派生、信封结构、轮换与解锁策略** |
| `adapters` | `AdapterResolver` | 每种资源类型对应的普通类，如何注册 |
| `audit` | `AuditSink` | 审计落库 / 落文件 / 转发外部系统 |

槽位**显式绑定**，不自动发现、不扫描目录、不读 entry_points：

```python
from software.extensions.resource_control.slots import SlotRegistry
from software.extensions.resource_control import contracts as c

reg = SlotRegistry()
reg.bind(c.SLOT_IDENTITY, MyGateway())
reg.bind(c.SLOT_POLICY, MyPolicy())
reg.bind(c.SLOT_VAULT, MyVault())       # 加密实现由你注入
reg.bind(c.SLOT_ADAPTERS, MyAdapters())
reg.bind(c.SLOT_AUDIT, MyAudit())
```

绑定时做接口形状校验（`runtime_checkable` Protocol），错配直接抛
`SlotContractError`。`unbind()` 后立即回到拒绝态。

## Fail-closed 语义

未配置的槽位返回**拒绝型实现**：任何方法调用都抛 `SlotUnavailable`，
不会静默返回 `None` / `False` / 空串，也不会降级到某个内置默认算法。
`require(name)` 更严格，未绑定直接抛错。

这条是刻意的：凭据加密一旦静默降级为明文，上层会拿密文当明文继续跑，
比直接报错危险得多。

## 启用与安装

`extensions.yaml`：

```yaml
resource_control:
  enabled: false     # 默认关闭；改为 true 才装配
```

启用后 `register(ctx)` 只做装配：把注册表挂到 `fw.services['resource_control']`，
注册一个只读状态路由 `GET /api/resource-control/status`（复用框架鉴权）。
**不自动建表、不联网、不启动监听、不注册事件总线**。

需要建表时由运维显式调用（幂等，重复执行安全）：

```python
from software.extensions.resource_control.main import install
status = install(ctx)     # 走 ctx.create_table，自动适配 MySQL / SQLite 方言
```

## 自检

```bash
python software/extensions/resource_control/selftest.py
python tests/test_resource_control.py       # CI 入口，同一套断言
```

自检不启动框架、不连数据库、不联网。验证的是**边界与失败语义**：
槽位默认拒绝、显式绑定与错配拒绝、schema 极薄/幂等/不跨库/真删、
装配入口无副作用、源码无硬编码 IP 与密钥、许可头齐备。
共 46 项断言。

## 下一步（不在本骨架内）

按你的计划由你决定，本目录只留出边界：

- 统一网关授权的 claims 契约与验签方式
- 凭据保险库的具体加密方案与密钥生命周期
- 授权策略与套餐/配额模型
- 各资源适配器（主机站点、AI 沙箱、MC 实例）的真实实现
- 前端控制台

设计文档见 [docs/resource-control.md](../../../docs/resource-control.md)。
