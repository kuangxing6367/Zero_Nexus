# 架构详解

> **本篇面向**：角色 C，以及想理解内核运行机制的 B。讲清分层、启动时序与消息流转。

## 分层总览

```
┌─────────────────────────────────────────────────┐
│            Core（core/ 内核）     │
│  插件加载器 loader · 事件总线 event_bus          │
│  消息路由 router · 上下文 ctx · 数据库 db         │
└──────────────────────┬──────────────────────────┘
                       │ 先加载，提供基础服务
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│onebot_adapter│ │  scheduler   │ │ session/webui│  extensions/（可开关）
└──────────────┘ └──────────────┘ └──────────────┘
                       │ 后加载
                       ▼
                ┌──────────────┐
                │  plugins/    │ 用户插件（每个一个目录，含 main.py）
                └──────────────┘
```

> 上图以默认接入端 `onebot_adapter` 为例；`http_inject`、自写 `ProtocolAdapter` 都在同一位置把事件归一化后送入内核，后续流程完全一致——内核不区分事件来自哪个接入端。

## core/ 内核模块一览

全部真实代码位于 `core/`（原 `framework/` 已整体迁入并删除）。按职责拆分为 19 个模块目录 + `core/engine.py` 装配层，按「最小原语 → 编排 → 机制 → 功能」分层：

| 层 | 模块 | 职责 |
|----|------|------|
| 最小原语 | `core/kernel` | event_bus / banner / logging / data_dirs / stats_writer |
| 编排 | `core/runtime` | 把原语装配成可运行引擎（dispatch / lifecycle / watchdogs / reply / context） |
| 引擎 | `core/engine.py` | `Framework` 装配层（原 `framework/core.py`），`main.py` 直接实例化 |
| 机制层 | `core/commands` | 命令总线（薄分发原语，与事件总线同形状） |
| | `core/messaging` | event / event_bus / router / protocol（消息事件通讯） |
| | `core/adapters` | ProtocolAdapter / ActionProxy / ServiceRegistry（协议接入契约） |
| | `core/storage` | dialect / engine / migrations / init_db（存储层，原 database） |
| | `core/perm` | LuckPerms 风格权限引擎 |
| | `core/ctx` | PluginContext（向插件暴露的统一上下文） |
| | `core/plugin_loader` | 插件加载器（pip / source_loader / venv / module 等） |
| | `core/zkg` | 包管理器（apt 式依赖解析，见 [包管理器 zkg](./zkg.md)） |
| 功能/扩展点 | `core/hooks` | 扩展点系统（HookPoints / HookRegistry） |
| | `core/config` | 配置加载（环境变量替换、默认生成） |
| | `core/auth` | 双请求防破解认证系统（原 dual_auth） |
| | `core/scheduler` | APScheduler 定时任务调度器 |
| | `core/terminal` | 终端交互（status / plugins / reload 等） |
| | `core/log_broker` | 日志代理（跨进程日志聚合） |
| | `core/tls` | TLS 证书解析 |
| | `core/ipc` | 双进程 IPC（JsonRpc / IpcServer / IpcClient / CoreRuntime / Remote*） |
| | `core/api` (+ `core/apis.py`) | Web 管理后台 / REST API 后端 |

内核纪律（最高准绳）：**机制 ≠ 策略**——内核只给 `exec/ws/udp/store/system` 等机制原语与 `register/invoke` 薄分发；命令路由、正则匹配、优先级裁决、权限门控、聊天消息→命令翻译等**策略留在扩展层**（`core/messaging/router.py` 与协议适配器），不属于内核。

## 启动时序

`main.py → Framework.start()`（`core/engine.py`）：

1. 打印安全提示（监听 `0.0.0.0` 且无 token 时告警）；
2. `_load_extensions()`：按 `extensions.yaml`（启动时合并进主配置 `extensions` 段）的开关加载官方插件，
   它们向服务注册表注册基础能力；
3. 创建 `data/plugins_dat/`，把旧版散落在代码目录的配置迁移过去；
4. `plugin_loader.load_all()`：发现并加载全部未被禁用的用户插件
   （依赖检查/自动安装 → 建合成包 → 预载子模块 → 执行 main.py）；
5. 依赖自愈：首轮缺依赖失败的插件，补装后再尝试一次；
6. 逐个 `register_commands()`：执行 `register(ctx)`，落库命令/任务/卡片，触发 `on_loaded`；
7. 启动路由表刷新、统计批量写库器、心跳、内存看门狗；
8. 广播 `system.plugin.loaded`，启动内置终端。

关闭 `Framework.stop()` 按相反顺序停止 WebSocket、调度器、Web 服务并关闭数据库。

## 消息处理流程

```
OneBot 客户端
    │  WebSocket 反向连接
    ▼
WebSocket 服务端 (extensions/onebot_adapter)
    │
    ▼
事件标准化为 Event (core/messaging/event.py)
    │
    ▼
框架核心 (core/engine.py)
    │
    ├─→ 原始消息处理器 (ctx.on_raw_message)
    │       │  返回 True 则被接管，流程终止
    │       ▼  未接管继续
    ├─→ 消息路由器 (router.py)
    │       ├─→ 插件命令匹配（按 priority 升序）
    │       │       │  命中且未 continue_route → 不再走关键词
    │       │       ▼  未命中
    │       ├─→ 关键词自动回复（dynamic_commands）
    │       │       ▼  未命中
    │       └─→ message 事件广播（ctx.on("message")）
    │
    ├─→ 通知事件 notice
    └─→ 请求事件 request
```

命令/事件处理器支持 `def`（转线程）与 `async def`（事件循环内）两种写法；
`event.stop_event()` 可截断后续传播。

## 服务注册表

核心框架通过 `services` 注册表解耦官方插件与用户插件：

```python
# 官方插件侧：注册能力
fw.services.register('api_caller', api_caller)

# 用户插件侧：按需取用（可能尚未就绪，必要时监听 system.plugin.loaded）
caller = ctx._core.services.get('api_caller')
```

| 服务名 | 提供者 | 说明 |
|--------|--------|------|
| `protocol_adapter` | onebot_adapter | 协议适配器抽象 |
| `api_caller` | onebot_adapter | OneBot API 调用器 |
| `onebot_api` | onebot_adapter | OneBot API 面向对象封装 |
| `ws_server` | onebot_adapter | WebSocket 服务端 |
| `scheduler` | scheduler | 定时任务调度器（APScheduler） |
| `session_manager` | session | 多轮会话管理器 |
| `web_server` | webui | Web 管理后台服务 |
| `http_api` | http_api | 独立对外 HTTP API（默认关闭） |

> `protocol_adapter` / `api_caller` 是**协议无关的通用槽位**：默认由 onebot_adapter 填充；换成其它接入端后由新接入端填充，业务插件的取用方式不变。

详见 [ServiceRegistry](../api/basic/services.md)。

## 插件加载机制（要点）

- 每个用户插件的主模块注册为 `plugin_<插件名>`，它同时是一个带 `__path__`
  的“合成包”，因此插件内部可以用 `from .xxx import Y` 做相对导入；
- 子模块同时拥有 `plugin_<名>.<模块>`（相对导入）、`plugin_<名>_<模块>`（旧唯一名）、
  `<模块>`（短名绝对导入）三个名字，指向同一对象；
- 卸载按模块 `__file__` 前缀一次性扫净 `sys.modules`。

完整原理、导入规则、热重载行为、排错见
[插件加载与模块机制](./loader.md)。

## 插件优先级

数字越小越先加载、越先收到原始消息、命令匹配越优先。

| 优先级 | 典型用途 |
|--------|----------|
| 0 | 官方插件（onebot_adapter、session 等） |
| 1–20 | 基础设施类用户插件（session_waiter、message_guard、依赖图等） |
| 50 | 默认（大多数用户插件） |
| 100+ | 低优先级/展示类（如 help=100） |

## 心跳、增量注册与热重载

- 每 `plugin.heartbeat_interval`（默认 60s）执行一次 `heartbeat_register()`：
  扫描插件目录 `.py` 文件的最大 mtime，**仅对发生变化的插件重新执行
  `register(ctx)`**，不重新 import；
- 因此“改了注册结构（新增命令/任务）”靠心跳即可刷新，
  而“改了函数体逻辑”需要 Web 面板的**完全重载**（unload + load，重新读盘）；
- 心跳后路由缓存失效并兜底重建，保证命令表与内存快照一致。

## 自检与自愈

- **依赖自愈**：启动时对缺依赖导致加载失败的插件，在补装依赖后自动再试；
- **孤儿自检 `self_check_orphans`**：周期性清理代码目录已不存在的命令/任务，
  以及调度器里属于未加载插件的“幽灵任务”；
- **内存看门狗**：每 3s 采样，单插件模块估算内存连续超过
  `plugin.max_memory_mb`（默认 64MB）两次即自动卸载并记录日志。

## 事件总线

```python
ctx.emit("user_sign_in", {"user_id": 123456})      # 同步
await ctx.aemit("user_sign_in", {...})             # 异步
ctx.on("notice.group_increase", on_member_join)    # 订阅（同步/异步 handler 均可）
```

### 内置事件

| 事件名 | 触发时机 |
|--------|----------|
| `message` | 收到文本消息且命令/关键词均未命中 |
| `notice.group_increase` | 新成员入群 |
| `notice.group_decrease` | 成员退群 |
| `request.friend` | 好友请求 |
| `request.group` | 加群请求 |
| `meta.heartbeat` | OneBot 心跳包 |
| `bot.connected` / `bot.disconnected` | OneBot 客户端连接/断开 |
| `system.plugin.loaded` | 本轮插件全部加载注册完成（payload 含插件列表） |
| `after_message_sent` | 消息发送完成后 |

插件可自定义任意事件名，通过 `emit/aemit` 在插件间解耦通信。
