# 架构详解

Zeronus 采用**三层架构**：内核级（`core/`）、服务级（`service/`）、软件级（`software/`）。

## 设计总纲（类 Linux 内核，五条原则）

1. **机制 ≠ 策略**：内核只提供通用机制，具体策略由上层决定。
2. **一类能力一个通用接口**：避免重复、碎片化的同类入口。
3. **极简**：内核仅硬依赖 `PyYAML`，其余按需。
4. **稳定 ABI 边界**：层级之间以稳定接口通信。
5. **合理默认 + 可覆盖**：给出安全的默认行为，允许按需覆盖。

## 启动时序

```
main.py
  → load_config                        读取 config.yaml / extensions.yaml
  → Framework(core)                   构造内核，监听 core.host:core.port（默认 37001）
  → start_sys_service                 zkg init + mg/db 就绪 + Watchdog（38001）
  → start_user_service                fw.start + Watchdog（38002）
  → 加密决策                          非加密=Token 校验；加密=RSA + rsa_callback
```

- 内核端口、sys 服务、user 服务均为本地端口（默认 `127.0.0.1`）。**三者目前都是端口占位 / 探活通道**：
  TCP 连接后发送任意 ≤256 字节，服务端回 `OK\n` 即断开；尚未承载「状态 / 事件」协议（见 [roadmap.md](roadmap.md)）。
- `sys` 服务负责初始化（zkg 初始化、数据库就绪、看门狗拉起）；`user` 服务承载用户侧能力。
- **服务级与内核同进程运行**——「sys / user 服务」是同一进程内的两个启动阶段，不是独立进程或跨机拆分；
  框架自带能力（zkg / 看门狗 / 传输原语）都由它拉起。

## 事件总线（EventBus）

- **唯一实现**位于 `core/kernel/event_bus.py`，`core/messaging/event_bus.py` 仅作透明重导出。
- 线程安全，支持 async / sync handler。
- 在非运行循环的线程中 `emit` 时，会安全地调度回框架主事件循环（`run_coroutine_threadsafe`），**不会每次新建临时事件循环**。
- API：`on(event, handler)` / `once(event, handler)` / `off(...)` / `emit(event, payload)` / `await_event(...)`。

## Hook 扩展点

- 标准扩展点常量在 `core/hooks/registry.py` 的 `HookPoints`（如 `HTTP_BEFORE_REQUEST`、`EVENT_BEFORE_DISPATCH`、`COMMAND_BEFORE`、`MESSAGE_BEFORE_SEND`）。
- 通过 `HookRegistry` 注册；部分 Hook 返回特定值可短路 / 丢弃（详见源码 `core/hooks/registry.py`）。

## 协议抽象

- `core/adapters/protocol.py` 提供 `ProtocolAdapter` 与 `ServiceRegistry`，用于统一接入不同协议（如 OneBot 11）。
- 适配器负责把外部协议事件翻译为框架内部事件，并通过 `ctx.emit` 分发。

## 包 / 插件加载

- **包（zkg）**：由 `service/zkg` 按 `manifest.toml` 依赖驱动加载，仅加载被依赖引用的机制包。
- **插件**：`software/plugins/` 下每个子目录一个插件，入口 `main.py`，实现 `register(ctx)`。

详见 [zkg.md](zkg.md)、[loader.md](loader.md)、[writing-plugins.md](writing-plugins.md)。
