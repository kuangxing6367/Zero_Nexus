# 权限系统

Zeronus 内置一套 **LuckPerms 风格**的权限引擎（`core/perm/`），无第三方依赖。

## 一、核心概念

| 概念 | 说明 |
| ---- | ---- |
| **节点（node）** | 权限点，如 `mytool.ban`。**三态**：允许 / 拒绝 / 未设置 |
| **权限组（group）** | 一组节点的集合，有权重、可继承、可按上下文生效 |
| **用户直节点** | 直接授给某个用户的节点，优先于组 |
| **上下文（context）** | 节点生效的条件，键为 `group`（群）/ `bot`（实例）/ `msgtype`（消息类型） |
| **轨道（track）** | 组的晋升顺序（如 新人群 → 活跃群 → 管理群），支持按轨道升降级 |
| **时效（expire_at）** | 节点/组可带过期时间 |
| **审计（audit）** | 每次变更都会写审计日志 |

## 二、内置角色组

框架虚拟注入四个内置组（**不入库**，防止误删），按角色自动映射：

| 内置组 | 权重 | 继承自 | 对应角色节点 |
| ---- | ---- | ---- | ---- |
| `__member` | 0 | — | `zernus.role.member` |
| `__admin` | 20 | `__member` | `zernus.role.admin` |
| `__owner` | 30 | `__admin` | `zernus.role.owner` |
| `__super` | 100 | `__owner` | `zernus.role.super` |

因为继承是链式的，**超管自动拥有群主/管理/成员的一切权限**。

角色 → 组的映射：`member → __member`、`admin → __admin`、`owner → __owner`、`super → __super`。

## 三、解析顺序

用户在某个上下文下的权限这样算出来：

```
用户直节点
  → 默认组 + 内置角色组
  → 继承展开（沿链拿到全部祖先组）
  → 按 weight 降序
  → 收集各来源的节点（用户来源优先）
  → 构造 PermissionSet
```

结果带 **TTL 进进程缓存**（默认 60s）；后台改权限会自动失效对应缓存。

## 四、插件里怎么用

### 命令级声明（最常用）

```python
ctx.command("/ban", on_ban, require_perm="mytool.ban")     # 需要权限节点
ctx.command("/admin", on_admin, require_admin=True)         # 需要管理员/群主/超管
ctx.command("/super", on_super, require_superuser=True)     # 需要超管
```

任一满足即放行；`require_superuser` 优先于 `require_admin`。

### 事件内查询

```python
def on_cmd(event, match):
    if not event.has_perm("mytool.ban.others"):
        return
    mode = event.check_perm("mytool.ban")     # 三态：True / False / None
    groups = event.perm_groups                # 命中的组
```

| 成员 | 说明 |
| ---- | ---- |
| `event.has_perm(node)` | 是否「允许」 |
| `event.check_perm(node)` | 三态：允许 / 拒绝 / 未设置 |
| `event.perms` | 权限快照（`PermissionSet`） |
| `event.perm_groups` / `event.primary_group` | 命中的组 / 主组 |

### 非事件场景（如定时任务）

```python
ctx.has_perm(user_id, "mytool.ban", context={"group": gid})
ctx.check_perm(user_id, "mytool.report")
ctx.user_groups(user_id)
```

## 五、组与节点的管理

后台「权限」页可视化操作；也可以用 `core/perm` 的函数（`ctx._framework.db` 作 db）：

```python
from core.perm.groups import create_group, update_group, delete_group, list_groups
from core.perm.nodes import set_group_node, unset_group_node, set_user_node, list_user_nodes
from core.perm.tracks import save_track, list_tracks

create_group(db, "vip", display_name="VIP", weight=50)
set_group_node(db, "vip", "mytool.vip_only", value=True)
set_group_node(db, "vip", "mytool.beta", value=False, ctx_key="group", ctx_val="10086")  # 上下文
set_user_node(db, "123456", "mytool.debug")                                             # 用户直授

rows = list_user_nodes(db, 123456)
```

> 组名不能以 `__` 开头（`__` 前缀为内置组保留）。

## 六、三态语义

| 值 | 含义 |
| ---- | ---- |
| `True` | 允许 |
| `False` | **拒绝**（可用于在更大范围内屏蔽某个节点） |
| 未设置 | 不表态，继续向上/向外查找 |

同一节点在多处出现时，按「用户来源优先 → weight 降序」取第一个表态的结果。

## 七、轨道（track）

轨道把多个组排成一条晋升线，支持一键沿轨道升级/降级用户：

```python
save_track(db, "staff", ["vip", "moderator", "admin"], display_name="管理晋升线")
```

后台「权限 → 轨道」页可直接操作。

## 八、审计

所有权限变更都会写审计日志（`audit_logs` 表），可在后台「日志」页按类型筛选查看。
插件自己写审计用 `ctx.audit_log(action, target_type=..., detail=...)`。

```python
ctx.audit_log("reset_data", target_type="user", target_name=str(uid), detail={"by": "plugin"})
```

## 九、缓存与刷新

```python
from core.perm.cache import invalidate_user, invalidate_groups, invalidate_all

invalidate_user(user_id)     # 用户相关缓存失效
invalidate_groups()          # 组结构变化后失效
invalidate_all()             # 全清
```

框架在后台改动后会自动调用，通常无需手动干预。事件对象上的角色缓存同理，
必要时可用 `core.messaging.event.invalidate_user_role_cache(uid)`。

## 十、数据表

| 表 | 存什么 |
| ---- | ---- |
| `perm_groups` | 权限组（weight / prefix / suffix / is_default） |
| `perm_group_nodes` / `perm_user_nodes` | 组 / 用户节点（含上下文与时效） |
| `perm_tracks` | 晋升轨道 |
| `audit_logs` | 审计日志 |
| `admin_users` | 后台管理员（含角色 super / admin） |

---

延伸：[Event 权限查询](../api/basic/event.md#四权限查询) · [数据库](./database.md) · [扩展点](../api/advanced/hooks.md)
