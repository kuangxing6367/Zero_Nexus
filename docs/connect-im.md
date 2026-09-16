# 接入 IM（OneBot 11）

Zeronus 通过 `onebot_adapter` 扩展接入 **OneBot 11** 协议（WebSocket 反向 / 正向连接），从而对接 QQ 等 IM 平台。

## 启用

`extensions.yaml`：

```yaml
extensions:
  onebot_adapter:
    enabled: true
    listen_host: 0.0.0.0
    listen_port: 6830
    access_token: '你的token'     # 必须设置！留空则不校验
```

并确认 `config.yaml → extensions.onebot_adapter: true`。

## 配置项

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| `listen_host` | `0.0.0.0` | WS 监听地址 |
| `listen_port` | `6830` | WS 监听端口 |
| `access_token` | `""` | **必须设置**，留空则不校验 token |

> 公网 / 局域网暴露 `0.0.0.0` 时务必设置 `access_token`，并尽量结合反向代理 + `ssl`。

## 连接一个 OneBot 客户端

需要一个 OneBot 11 实现作为客户端（如 go-cqhttp、OneBot 标准实现），将其 **WebSocket 上报地址**指向：

```
ws://127.0.0.1:6830/?access_token=你的token
```

连接成功后，IM 侧的消息会作为框架内部 `message` 事件分发，插件用 `ctx.on("message", handler)` 或 `ctx.command(...)` 处理并回复。

## 系统配置中的 OneBot 参数

`system_config` 表内置：

- `onebot.ws_url`：框架主动连接的 WS 地址（正向连接场景）；
- `onebot.access_token`：访问令牌。

## 不接 IM 也能调试

没有 IM 客户端时，开启 `http_inject` 扩展，用 `curl` 向 `http://127.0.0.1:8901/hook` 注入事件即可（见 [getting-started.md](getting-started.md)）。
