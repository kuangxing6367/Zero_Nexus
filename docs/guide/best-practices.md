# 最佳实践

本篇讲怎么把 Zeronus 当**通用宿主**用：不接 IM、只跑定时、只收 Webhook，或搭一套带权限的业务后台。

## 一、四个范式（同一套插件模型）

同一个 `register(ctx)`，换接入端不改业务代码。

### 1) IM 机器人（OneBot 11，默认）

```python
def register(ctx):
    ctx.command("/hello", on_hello, description="打个招呼")

def on_hello(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message="Hello, World!")
```

### 2) 纯定时任务服务（连 IM 都不开）

```python
def register(ctx):
    ctx.task("0 8 * * *", daily_report, description="每日报表")

def daily_report():
    n = ctx.db_query_one("SELECT COUNT(*) AS c FROM users")["c"]
    ctx.log(f"当前用户数: {n}")
```

把 `extensions.yaml` 里的 `onebot_adapter` 关掉，就是一个纯定时服务。

### 3) HTTP Webhook 事件源（外部系统推事件进来）

`extensions.yaml` 开启 `http_inject`：

```yaml
extensions:
  http_inject:
    enabled: true
    host: 127.0.0.1
    port: 8901
    path: /hook
    token: ''
```

```bash
curl -X POST http://127.0.0.1:8901/hook \
     -H 'Content-Type: application/json' \
     -d '{"type":"message","user_id":10001,"message":"/hello"}'
```

事件被归一化后进入同一内核，**第 1 个例子的命令照常触发**——完全不依赖 IM。

### 4) 挂一个切面（审计 / 限流 / 中间件）

```python
def register(ctx):
    ctx.hook("action.after", audit)

def audit(action, params, bot, result):
    ctx.log(f"[审计] {action} -> {result.get('status')}")
```

## 二、写业务扩展的规范

1. **只依赖 `ctx`**：业务里优先用 `ctx.send_msg / ctx.api` 这类协议无关接口，别直接依赖 OneBot 字段；
2. **异步优先**：`async def` handler + `a` 前缀方法，避免阻塞事件循环；
3. **数据与代码分离**：运行期数据一律写 `ctx.get_data_dir()`（`data/plugins_dat/<名>/`），不要写进代码目录；
4. **配置外置**：可变参数用 `ctx.get_config(...)`，别写死在代码里；
5. **建表自管**：业务表在 `register` 里 `CREATE TABLE IF NOT EXISTS`，占位符统一用 `?`；
6. **权限显式声明**：命令上加 `require_perm` / `require_admin`，敏感操作在 handler 里再 `event.has_perm(...)` 复核；
7. **重活丢后台**：高频路径（`command.before` / `event.before_dispatch`）保持轻量，重活走 `ctx.run_async(...)` 或 `*.after` 扩展点；
8. **异常隔离**：任一扩展点 handler 抛异常只会被框架记日志并跳过，但你的命令 handler 要自己 try，避免影响用户体验。

## 三、把 Zeronus 当"通用事件服务"的清单

| 需求 | 做法 |
| --- | --- |
| 事件从哪来 | `protocol_adapter` 服务 + 自写 `ProtocolAdapter`，或内置 `onebot_adapter` / `http_inject` |
| 事件怎么处理 | 命令（`ctx.command`）、事件订阅（`ctx.on`）、扩展点切面（`ctx.hook`） |
| 结果发到哪 | `ctx.send_msg` / `ctx.api`（协议无关）；没有接入端时可只落库 / 只写日志 |
| 定时触发 | `ctx.task(...)` / `ctx.add_job(...)`（`scheduler` 扩展） |
| 多轮交互 | `await ctx.wait_for(...)`（`session` 扩展） |
| 权限与审计 | `perm` 引擎 + `ctx.audit_log(...)` + 后台用户/组管理 |
| 对外接口 | WebUI REST（默认 8080）；需要给外部程序用就开 `http_api`（1145） |
| 实时推送 | WebSocket 事件通道 / `/api/webhooks` 出站 webhook / GraphQL / gRPC |

## 四、性能与稳定性

- **命令匹配在内存路由表**：热路径零 DB 查询；改命令后框架会自动重建路由表；
- **扩展点空载零成本**：没挂 handler 时触发只是遍历空列表；
- **内存看门狗**：单插件超过 `plugin.max_memory_mb`（默认 64MB）会被自动卸载；
- **数据库保活**：MySQL 支持 `ping_interval` 自动重连，避免 wait_timeout 后卡死；
- **依赖自愈**：`plugin.auto_install_deps_on_startup` 打开时，启动会补齐缺失依赖（版本冲突自动跳过）。

## 五、安全建议

- OneBot 的 `access_token` **一定要设**，否则任何客户端都能接入你的机器人；
- Web 后台默认只绑 `127.0.0.1`；要对外必须配反代 + HTTPS（见[部署上线](../advanced/deployment.md)）；
- 默认管理员 `admin / admin123` 首次登录后立刻改；
- `http_api` 的 `allow_db` 默认关闭——它允许执行任意 SQL（含写库/删表），非必要不要开；
- 密钥走环境变量：配置支持 `${VAR}` / `${VAR:-default}`；
- 反向代理后记得把 `security.whitelist_ips` 调整为你的内网段。

## 六、上线前自查

- [ ] OneBot `access_token` 已设置
- [ ] 默认管理员密码已改
- [ ] `web.host` / 反代与证书按需配置（`ssl.enabled`）
- [ ] 用户插件的敏感命令都带了 `require_perm` / `require_admin`
- [ ] 插件数据都写在 `ctx.get_data_dir()`
- [ ] 日志级别与保留策略确认（`log.*`）
- [ ] 需要外部调用才开 `http_api` / `http_inject`，并设 token

---

延伸：[编写插件](./writing-plugins.md) · [架构总览](../advanced/architecture.md) ·
[扩展点](../api/advanced/hooks.md) · [协议适配器](../api/advanced/protocol_adapter.md)
