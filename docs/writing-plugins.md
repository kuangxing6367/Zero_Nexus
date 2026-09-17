# 编写插件

插件放在 `software/plugins/<插件名>/`，每个插件一个子目录，入口文件 `main.py`。

## 最小插件

```python
# software/plugins/hello/main.py
def register(ctx):
    @ctx.command(r"^你好$", handler=_hello)
    def _hello(event):
        # event 为框架内部事件对象
        return "你好，我是 Zeronus 插件。"
```

- `register(ctx)` 是框架加载插件时调用的**唯一入口**；所有注册都在此函数内完成。
- 不要在 `register()` 之外执行副作用；`register()` 返回后 `ctx.emit` 才会被投递（延迟投递是设计行为）。

## 注册能力

| 能力 | 接口 | 说明 |
| --- | --- | --- |
| 消息监听 | `ctx.on("message", handler)` | 订阅框架内部事件 |
| 一次性事件 | `ctx.once(event, handler)` | 只触发一次 |
| 发出事件 | `ctx.emit(event, payload)` | 延迟投递到主事件循环 |
| 命令 | `ctx.command(pattern, handler, priority=50, require_level="")` | 正则匹配命令；`require_level` 可为 `admin` / `super` |
| 机制包 | `ctx.zkg_tool("<id>")` | 取用 zkg 按需加载的官方机制包（如 `store`）；需在插件 `manifest.toml` 的 `dependencies` 里声明 |
| 定时任务 | `ctx.task(cron_expr, executor, description="")` | 注册 cron 任务 |
| 读取配置 | `ctx.get_config(key, default=None)` | 读取插件配置（来自 `plugin_configs` 表） |
| 发消息 | `ctx.send_msg(user_id=..., group_id=..., message=...)` | 发送消息 |
| 权限查询 | `ctx.get_user_role(group_id, user_id)` | 返回角色字符串 |
| HTTP 接口 | `ctx.register_api(path, handler, methods=None, auth=True)` | 扩展暴露 HTTP 接口 |
| WebUI 面板 | `ctx.register_group_extension(key, title, handler, ...)` / `ctx.register_user_extension(...)` | 在 Web 后台挂载自定义面板 |

## 命令与权限

- 命令通过正则 `pattern` 匹配，可带 `priority`（越小越优先）与 `require_level`。
- 权限模型为 LuckPerms 风格（见 [permission.md](permission.md)）：内置角色组 `super / owner / admin / member` 由框架虚拟注入。

## 插件配置

- 配置 Schema 通过 `_conf_schema.json` 定义，用户可在 Web 后台修改；
- 插件用 `ctx.get_config(key, default)` 读取。

## 内存与生命周期

- 单插件内存上限由 `config.yaml → plugin.max_memory_mb`（默认 64MB）控制；
- 连续超限会被看门狗**自动卸载**（见 [loader.md](loader.md)）。

## 调试技巧

- 不接 IM 也能调试：开启 `http_inject` 扩展，用 `curl` 向 `http://127.0.0.1:8901/hook` 注入事件（见 [getting-started.md](getting-started.md)）。
- 日志写入 `data/logs/zernus.log`，可按 `config.yaml → log.level` 调整级别。

---

## 实战：从 0 到 1 写一个插件（demo_kv 全过程）

下面以仓库里现成的 [demo_kv](https://github.com/kuangxing6367/Zero_Nexus/tree/main/software/plugins/demo_kv)
为例，走完一个插件从空目录到运行验证的完整路径。demo_kv 的功能：声明 `store`
机制包依赖，在收到 `demo_kv` 命令时向 KV 写入并读回一条记录。

### 第 1 步：生成骨架

```bash
python -m service.zkg new demo_kv --deps store --desc "演示插件：消费 KV 机制包"
```

会在 `software/plugins/demo_kv/` 生成三件东西：

| 文件 | 作用 |
| --- | --- |
| `manifest.toml` | 包清单：id、类型、依赖声明、API 版本区间 |
| `main.py` | 入口：`register(ctx)` + 命令/机制包用法示例 |
| `requirements.txt` | 第三方依赖（为空则删除即可） |

### 第 2 步：声明依赖（manifest.toml）

```toml
[package]
id = "demo_kv"
name = "demo_kv"
type = "plugin"
version = "0.1.0"
description = "演示插件：声明 store 机制包依赖并消费 KV 的最小完整样例"
dependencies = ["store"]   # 声明要用的 zkg 机制包 id
api_version = "1"          # 兼容的插件 API 主版本（当前 1）
```

- `dependencies` 里的 id 必须是 zkg 官方源/本地仓库真实存在的机制包（如 `store` / `http` / `retry` / `lock` / `validate`）；
- zkg 启动时会解析这张依赖表，按需加载对应机制包——**没声明就取不到**；
- `api_version` 不写默认全兼容；写了则与框架 `PLUGIN_API_VERSION` 校验，不兼容会告警且不加载。

### 第 3 步：写逻辑（main.py）

```python
def register(ctx):
    log = ctx.logger

    @ctx.command(r"^demo_kv$", handler=_hello)
    def _hello(event):
        kv = ctx.zkg_tool("store")       # 取用机制包；未声明/未加载时返回 None
        if kv is None:
            return "demo_kv 就绪（机制包 store 未加载）。"
        kv.set("last_seen", "demo_kv")
        return f"demo_kv 就绪（KV 写入成功: {kv.get('last_seen')}）。"

    log.info("demo_kv 已注册")
```

要点：

- `register(ctx)` 是唯一入口，所有注册写在这里，函数外不放副作用；
- `ctx.zkg_tool("store")` 拿到的是机制包模块，直接用其稳定 API 表面（`kv.set` / `kv.get`，见 `repo/store/main.py` 的文档串）；
- 防御性判 `None`：机制包被禁用或加载失败时插件应优雅降级而不是崩掉。

### 第 4 步：运行验证

1. 启动框架 `python main.py`，启动日志里 zkg 段会显示解析与加载结果，并出现 `demo_kv 已注册`；
2. 最快的验证方式是不接 IM：开启 `http_inject` 后注入一条消息：

```bash
curl -X POST http://127.0.0.1:8901/hook \
  -H "Content-Type: application/json" \
  -d '{"type": "message", "message_type": "private", "message": "demo_kv"}'
```

`{"ok": true}` 只表示事件已投递；命令的**回复文本与执行效果**看日志——
调试期可在 handler 里加 `log.info(...)`，或像 demo_kv 那样用 KV 写入一条记录再读出，
返回串里带上读回值即可确认「依赖解析 → 机制包加载 → 插件消费」全链路打通；
3. 也可以在 WebUI 后台查看插件状态与日志。

### 常见坑

| 现象 | 原因 |
| --- | --- |
| `zkg_tool()` 恒为 `None` | manifest 里没声明 `dependencies`，或机制包 id 写错 |
| 插件没被加载 | 目录名与 `id` 不一致（以 `manifest.toml` 的 `id` 为准）|
| 启动告警 `api_incompatible` | `api_version` 声明的区间与当前框架主版本不符 |

