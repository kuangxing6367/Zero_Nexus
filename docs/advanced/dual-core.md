# 双核心（实验特性）

> 一条长期实验线：把一次启动拆成两个进程，隔离用户插件故障、降低核心常驻内存。
> **默认关闭，行为完全不变**；开启前请先读完本篇。

## 一、概念

单进程宿主被拆成：

| 进程 | 角色 | 装什么 |
| ---- | ---- | ---- |
| **核心进程（core）** | `CoreRuntime` | 真实数据库、协议接入端（onebot_adapter / http_inject）、Web/WebUI、IPC 服务端；**不加载用户插件** |
| **宿主进程（host）** | `host_entry` | 加载并执行**全部用户插件**，经 IPC 访问数据库 / 发消息 / 注册路由 / 推送日志 |

进程间用**标准库 IPC**（回环 TCP + `authkey` 握手）通信，**零第三方依赖**。

```
┌───────────── 进程1 核心 ─────────────┐        ┌───────────── 进程2 宿主 ─────────────┐
│  Framework(role='core')              │        │  用户插件 plugins/*                   │
│  真实 Database · 协议接入端 · WebUI   │◀──IPC──▶│  RemoteDatabase / RemoteApiCaller     │
│  IpcServer（AF_INET + authkey）       │  RPC   │  RemoteRoute / host_log               │
│  调度器 · 定时任务                    │  +事件 │  （经代理访问核心能力）               │
└──────────────────────────────────────┘        └───────────────────────────────────────┘
```

## 二、开启

`config.yaml`：

```yaml
dual_process:
  enabled: true
  extensions:              # 核心进程加载的官方扩展白名单
    - onebot_adapter
    - http_inject
    - http_api
    - webui
  max_restarts: 5          # 宿主崩溃重启限流：窗口内最多次数
  restart_interval: 30     # 限流窗口（秒）
```

重启框架即可。`main.py` 检测到 `dual_process.enabled` 时会走 `CoreRuntime(config_path).run()`。

> `session` / `scheduler` 这类需要与用户插件同侧协作的扩展，默认仍在宿主侧参与用户插件链路。

## 三、事件与调用怎么走

| 方向 | 机制 |
| ---- | ---- |
| 接入端收到事件 | 核心把 `framework.dispatch_event` 替换为「IPC 推送到宿主」，由宿主里加载的用户插件处理 |
| 插件发消息 / 调动作 | 宿主经 `RemoteApiCaller` 走 IPC，由核心侧转发给真实接入端 |
| 插件读写数据库 | 宿主经 `RemoteDatabase` 走 IPC，由核心侧的真实 `Database` 执行 |
| 插件注册 REST 路由 | 宿主经 `RemoteRoute` 走 IPC，在核心的 Web 上注册 |
| 插件写日志 | 经 `host_log` 合并进核心日志流 |

核心侧暴露的 RPC：`db.*`（真实数据库执行）、`api.call`（转发发送）、`bots.list`（连接列表）。

## 四、看得见的好处

- **故障隔离**：用户插件崩溃不会拖垮接入端与 Web；
- **内存下压**：核心进程只装必要模块，常驻更小；
- **崩溃自愈**：宿主崩溃后按 `max_restarts` / `restart_interval` 限流重启。

## 五、已知限制

| 限制 | 说明 / 替代 |
| ---- | ---- |
| `db.get_connection()` 不可用 | 双进程下拿不到裸连接。用 `db.transaction()` 或 `ctx` 的 db 系列方法 |
| 单宿主 | 目前只支持一个宿主进程；多接入端并发、跨机部署尚未实现 |
| 跨进程事务 | 一次事务必须落在同一侧；跨侧无法保证原子性 |
| 调试更绕 | 日志虽已合并，但堆栈与生命周期跨进程，排查成本更高 |
| 略高的 IPC 开销 | 热路径上的每次 db / 发消息都要过一次 IPC |

## 六、什么时候值得开

- 用户插件较多、质量参差，希望接入端与 Web 始终在线；
- 想压低核心常驻内存；
- 能接受 IPC 带来的少量延迟与排查复杂度。

反之，**多数场景建议保持单进程**（默认），最简单也最快。

## 七、代码位置

| 文件 | 作用 |
| ---- | ---- |
| `core/ipc/core_runtime.py` | 核心进程运行时（装配 + 起 IpcServer + spawn 宿主） |
| `core/ipc/host_entry.py` | 宿主进程入口 |
| `core/ipc/ipc_server.py` / `ipc_client.py` | IPC 服务端 / 客户端（stdlib，authkey 握手 + PING 保活） |
| `core/ipc/remote_db.py` / `remote_api_caller.py` / `remote_route.py` / `remote_tx.py` | 宿主侧的远程代理 |
| `core/ipc/host_log.py` | 宿主日志合并 |
| `core/ipc/protocol.py` | IPC 消息协议与异常类型 |

## 八、验证

仓库里有针对双核心的回归测试：

```bash
python tests/test_dual_core.py
```

覆盖：插件进程归属解析、IPC 往返（RPC / 事件 notify）、双进程下 `get_connection()` 必须抛 `NotImplementedError`。

---

延伸：[架构总览](./architecture.md) · [服务注册表](../api/basic/services.md) · [配置系统](../guide/configuration.md)
