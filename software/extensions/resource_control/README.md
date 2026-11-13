# 统一资源控制面 · 框架壳

> **框架只是个壳子。** 业务内核在仓库顶层 [`control_plane/`](../../../control_plane/)，
> 零框架依赖、只用标准库，可以单独运行、单独测试、单独开源。
> 本目录只负责把内核**承载**到 Zeronus 进程里，不提供任何业务能力。

## 壳做什么

| 文件 | 职责 |
| --- | --- |
| `bridge.py` | `ctx.db_*` → Backend 的薄适配，只做方法名映射 |
| `main.py` | 构造 `ResourceStore` 挂到 `fw.services`；注册只读健康路由 |

明确**不做**：

- 不构造 `ControlPlane`（需要注入真实策略与适配器，框架不该猜）；
- 不实现身份、授权、加密、适配器；
- 不定义契约层、槽位注册表、插件发现、事件总线；
- `register(ctx)` 不建表、不联网、不写数据、不启动监听。

## 启用

```yaml
# extensions.yaml
resource_control:
  enabled: false   # 默认关闭；改 true 才承载
```

启用后：

- `fw.services['control_plane_store']` 可拿到 `ResourceStore`；
- `GET /api/control-plane/health` 复用框架鉴权，只读探测表与记录数；
- 运维显式调用 `install(ctx)` 幂等建表（`register` 不会自动建）。

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
python -m control_plane.selftest       # 业务内核自检（真 SQLite 闭环）
python -m control_plane.cli --db data/controlplane.db init
python tests/test_control_plane.py     # CI 入口
```

设计文档见 [`docs/control-plane.md`](../../../docs/control-plane.md)。
