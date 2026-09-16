# 权限系统

Zeronus 的权限系统采用 **LuckPerms 风格**的节点模型，由 `core/perm` 实现。

## 核心模型

- **组（group）**：`perm_groups` 表，含 `weight`（权重，越大越优先，决定 primary group）、`prefix` / `suffix`、`is_default`（全员自动拥有）。
- **节点（node）**：`perm_group_nodes` / `perm_user_nodes` 表。
  - 权限节点：`chat.*`、`admin.plugin` 等；
  - 继承节点：`group.xxx` 表示该组 / 用户继承 `xxx` 组（与 LuckPerms v5 一致）；
  - `value = 0` 表示显式否决。
- **上下文（context）**：`context_key` / `context_val`（如 `group=12345`、`msgtype=group`），为 `NULL` 时全局生效。
- **临时权限**：`expire_at`（unix 时间戳），`NULL` 为永久。
- **升降级轨道（track）**：`perm_tracks` 表，`groups_order` 左→右为晋升方向（`promote` / `demote`）。
- **审计**：`perm_audit` 记录每次授权变更。

## 内置角色组

- `super / owner / admin / member` 由框架代码**虚拟注入，不入库**，防止误删。
- 默认组 `default`（weight 0，全员自动拥有）。

## 查询接口

- `ctx.get_user_role(group_id, user_id)`：返回用户在指定群内的角色字符串。
- 命令级权限通过 `ctx.command(..., require_level="admin" | "super")` 声明。

## 设计要点

- 时间字段统一存 **unix 时间戳字符串**，保证 SQLite / MySQL 行为一致；
- 节点名定长 `191`，对应 utf8mb4 下 767 字节索引上限（191×4=764）的安全值；
- 用户直接节点优先级高于所属组的节点。
