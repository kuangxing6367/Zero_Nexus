# 对接 IM 平台（OneBot 11）

Zeronus **不直接连平台**，而是作为「反向 WebSocket 服务端」等 OneBot 实现端连入。
官方接入端是 `software/extensions/onebot_adapter/`（默认开启）。

## 一、拓扑

```
 聊天平台 ── NapCat / Lagrange / LLOneBot ──反向 WS──▶ Zeronus :6830
                （协议端 / OneBot 实现）             （本框架 + onebot_adapter）
```

Zeronus 只认 OneBot 11 协议，具体连哪个平台由协议端决定。

## 二、启用与配置

`extensions.yaml`：

```yaml
extensions:
  onebot_adapter:
    enabled: true
    listen_host: 0.0.0.0     # 监听地址
    listen_port: 6830        # 监听端口
    access_token: ''         # 可选：连接鉴权 token
```

## 三、在协议端配置反向 WS

| 协议端 | 操作 |
| --- | --- |
| NapCat | 网络配置 → 添加「WebSocket 客户端」→ URL 填 `ws://<服务器IP>:6830/onebot/v11/ws` |
| Lagrange | 配置文件里加 `websocket-client` 项，URL 同上 |
| LLOneBot | 设置 → 反向 WS 地址，URL 同上 |

> 路径不强制：接入端从请求里取 token，URL 路径可任意（约定用 `/onebot/v11/ws`）。
> 若配了 `access_token`，协议端需要以 `Authorization: Bearer <token>` 或 `?access_token=<token>` 带上。

启动横幅里出现 `OneBot WS : 0.0.0.0:6830` 即为监听成功；协议端连上后日志会打印连接事件。

## 四、多实例

一个 Zeronus 可以同时接多个协议端（多 bot）。事件对象上的 `event.bot_name` 标识来源实例，
发消息时可用 `ctx.api(action, bot=..., **params)` 指定目标实例。

## 五、发消息与群管

业务里优先用**协议无关**接口：

```python
ctx.send_msg(group_id=event.group_id, user_id=None, message="你好")
ctx.api("set_group_ban", group_id=gid, user_id=uid, duration=60)
```

需要 OneBot 专用能力时走 `ctx.onebot`（`onebot_api` 服务），覆盖 OneBot 11 的常用动作，例如：

- 消息：`send_private_msg` / `send_group_msg` / `send_msg` / `delete_msg` / `get_msg` / `get_forward_msg` / `send_like`
- 群管：`set_group_kick` / `set_group_ban` / `set_group_whole_ban` / `set_group_admin` /
  `set_group_card` / `set_group_name` / `set_group_special_title` / `set_group_leave` / `set_group_anonymous`
- 请求处理：`set_friend_add_request` / `set_group_add_request`
- 查询：`get_login_info` / `get_stranger_info` / `get_friend_list` / `get_group_info` / `get_group_list` /
  `get_group_member_info` / `get_group_member_list`
- 资源：`get_record` / `get_image` / `can_send_image` / `can_send_record` / `get_status`

`ctx.onebot` 支持任意动态 action（`__getattr__` 透传），未封装的动作也能直接调用。

## 六、富媒体

事件对象保留了原始消息段 `event.segments`（`[{type, data}, ...]`），可用 `event.has_image()` /
`has_voice()` / `has_video()` / `has_reply()` 快速判断，再按需解析 `segments`。

```python
def on_msg(event, match):
    if event.has_image():
        for seg in event.segments:
            if seg["type"] == "image":
                url = seg["data"]["url"]
```

## 七、HTTPS / WSS

`config.yaml` 的 `ssl` 段启用证书后，WebUI 走 HTTPS、OneBot 接入走 WSS：

```yaml
ssl:
  enabled: true
  cert: certs/fullchain.pem   # 相对项目根或绝对路径
  key:  certs/privkey.pem
```

协议端的反向 WS URL 相应改成 `wss://`。

---

不想接 IM？看[最佳实践](./best-practices.md)里的「纯定时服务」与「HTTP Webhook 事件源」。
