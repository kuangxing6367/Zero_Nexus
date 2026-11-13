# control_plane · 统一资源控制面业务内核

> 零框架依赖，只用 Python 标准库。Zeronus 只是可选的运行壳
> （[`software/extensions/resource_control/`](../software/extensions/resource_control/)），
> 不提供业务能力。完整设计见 [`docs/control-plane.md`](../docs/control-plane.md)。

许可：沿用仓库根 `LICENSE`（MIT / Apache-2.0 任选其一），
源码带 `SPDX-License-Identifier: MIT OR Apache-2.0`。
本包无第三方依赖、无密钥、无硬编码生产 IP、无授权码校验，可直接开源。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `Resource` / `AuditEvent` 数据结构；三个 Protocol 仅作类型提示 |
| `store.py` | 双库 DDL、参数化 SQL、真删、审计脱敏、三种 Backend |
| `control.py` | `ControlPlane` 生命周期编排；依赖构造参数注入，缺失即拒绝 |
| `cli.py` | 命令行入口（init / health / list / audit） |
| `selftest.py` | 真 SQLite 端到端自检（76 项） |

## 最小用法

```python
from control_plane import ControlPlane, ResourceStore, SqliteBackend

store = ResourceStore(SqliteBackend('data/controlplane.db'))
store.install()

cp = ControlPlane(store, policy=my_policy, adapters=my_adapters)
res = cp.provision('hosting', {'domain': 'a.example'},
                   actor='sub-123', tenant='t1', scopes=('host.create',))
cp.deprovision('hosting', res.external_id, actor='sub-123')
```

`policy.allowed(actor, action, target, scopes) -> bool`、
`adapters.resolve(kind) -> adapter`（`provision` / `deprovision` / `status`）
都是普通对象，**由你实现并显式传入**。本包不提供默认实现，
不注册、不扫描、不自动发现；缺依赖抛 `DependencyMissing`。

## 三条硬约束

1. **数据库薄且自治**：只有 `rc_resources` / `rc_audit`，不跨库、
   不 JOIN 框架身份表、不接受第二个连接；不建身份 / 角色 / 权限 / 套餐 / 计费表。
2. **身份由网关注入**：内核只接收 `actor` / `tenant` / `scopes`，
   不查库验身份、不写死任务授权；删除即真删，无归档。
3. **具体逻辑不由本包决定**：加密、验签、授权策略、适配器全部外置；
   内核不接触凭据明文，审计只记脱敏后的元数据。

## 验证

```bash
python -m control_plane.selftest
python -m control_plane.cli --db data/controlplane.db init
python tests/test_control_plane.py
```

不启动框架、不连 MySQL、不联网。已知限制（审计与业务写入非同一事务、
MySQL 未连真库验证等）见设计文档，未做粉饰。
