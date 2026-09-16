# 加载器（Loader）

Zeronus 有两套加载路径：**包加载（zkg）** 与 **插件加载（plugin_loader）**，分别对应机制复用与业务扩展。

## 包加载（zkg）

见 [zkg.md](zkg.md)：按 `manifest.toml` 依赖驱动，`scan → resolve → 写 data/plugins.db → 只加载解析集合`。

## 插件加载（plugin_loader）

- 目录：`software/plugins/`，每个插件一个子目录，入口 `main.py` 实现 `register(ctx)`。
- 加载时把插件注册到框架，纳入事件 / 命令 / 定时任务分发。

## 内存监控

内存上限由两个独立键控制（两者都生效）：

| 键 | 作用域 | 默认 | 监控位置 |
| --- | --- | --- | --- |
| `service.watchdog.max_memory_mb` | 进程级（engine + watchdog 同源） | 256 | `service/watchdog.py` |
| `plugin.max_memory_mb` | 单插件级 | 64 | `core/plugin_loader/loading.py` 监控线程 |

- 单插件连续超限会被**自动卸载**，防止拖垮宿主。
- 看门狗按 `service.watchdog.interval`（默认 30s）采样。

## 扩展注册

- 官方扩展位于 `software/extensions/`，由 `extensions.yaml` 控制启用与端口。
- 扩展与插件共享 `ctx` 扩展接口（`on` / `emit` / `task` 等），区别在于来源与生命周期管理。

## 常见坑

- 同步编辑同一文件会互相覆盖（后写覆盖前写）——属工作流问题，非框架缺陷。
- `ctx.emit` 为**延迟投递**：在 `register()` 内 `emit`，handler 在 `register()` 返回后才触发，这是设计行为，不是 bug。
