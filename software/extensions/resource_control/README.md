# 统一资源控制面 · 框架壳

> **框架只是个壳子。** 业务内核在仓库顶层 [`control_plane/`](../../../control_plane/)，
> 零框架依赖、只用标准库，可以单独运行、单独测试、单独开源。
> 本目录只负责把内核**承载**到 Zeronus 进程里，不提供任何业务能力。

## 壳做什么

| 文件 | 职责 |
| --- | --- |
| `bridge.py` | `ctx.db_*` → Backend 的薄适配，只做方法名映射；明确声明不支持事务 |
| `main.py` | 构造 `ResourceStore` 挂到 `fw.services`；注册只读路由 |

明确**不做**：

- 不构造 `ControlPlane`（需要注入真实策略与适配器，框架不该猜）；
- 不实现身份、授权、加密、适配器；
- 不定义契约层、槽位注册表、插件发现、事件总线；
- `register(ctx)` 不建表、不联网、不写数据、不启动监听。

## 启用

```yaml
# extensions.yaml
resource_control:
  enabled: false             # 默认关闭；改 true 才承载
  expose_readonly_api: false # 默认关闭；true 才额外注册两条只读路由
```

启用后：

- `fw.services['control_plane_store']` 可拿到 `ResourceStore`；
- `GET /api/control-plane/health` 复用框架鉴权，只读探测表、记录数与后端能力；
- `expose_readonly_api: true` 时额外注册
  `GET /api/control-plane/resources`（支持 `tenant` / `kind` / `owner` / `limit`）
  与 `GET /api/control-plane/audit`（支持 `limit`），均为只读、复用框架鉴权，
  `limit` 夹在 1..1000。审计 detail 已由 store 脱敏后才落库；
- 运维显式调用 `install(ctx)` 幂等建表（`register` 不会自动建）。

「谁能看台账与审计」属于授权策略，本壳**不写死**，所以这两条路由默认不注册。
部署方在框架权限层决定后再打开开关。

## 关于事务

`FrameworkBackend` 明确 `supports_transactions = False`：`ctx.db_*` 每次调用
可能从连接池取到不同连接，跨调用拼事务并不能保证落在同一事务里，
假装原子比不原子更危险。`store.capabilities()['atomic_audit']` 与
`health()['capabilities']` 都会显示 `False`；降级写入的审计 detail 带
`atomic=False` 并输出 warning。需要原子审计请直接用
`SqliteBackend` / `MysqlBackend`。

## 部署方怎么接业务

```python
from control_plane import ControlPlane

store = fw.services.get('control_plane_store')
cp = ControlPlane(store, policy=my_policy, adapters=my_adapters)

# 网关在壳层把 claims 摊平成 actor / tenant / scopes 后传入
cp.provision('hosting', spec, actor='sub-123', tenant='t1', scopes=('host.create',))
```

`policy`、`adapters` 是普通对象，由你实现并**显式传入**；缺一个就抛
`DependencyMissing`，没有任何默认实现或内置算法。

## 自检

```bash
python -m control_plane.selftest                # 业务内核自检，116 项
python tests/test_control_plane.py              # CI 入口
python tests/test_control_plane_tx.py           # 事务与 MysqlBackend 路径，45 项
python tests/test_control_plane_bridge.py       # 框架壳，29 项
python -m control_plane.cli --db data/controlplane.db init
```

设计文档见 [`docs/control-plane.md`](../../../docs/control-plane.md)。
