# 开始使用

## 启动

```bash
python main.py
```

日志写入 `data/logs/zernus.log`（可在 `config.yaml → log.file` 关闭，仅输出控制台）。

## 默认端口

| 服务 | 地址（默认） | 说明 |
| --- | --- | --- |
| 内核 core | `127.0.0.1:37001` | 端口占位 / 探活通道（回 `OK\n`；状态/事件协议未定义） |
| sys 服务 | `127.0.0.1:38001` | 初始化服务（同为探活通道） |
| user 服务 | `127.0.0.1:38002` | 用户服务（`service.user.user_port`） |
| Web 后台 | `127.0.0.1:8080` | `software/extensions/webui` |
| 状态面板 | `127.0.0.1:8090` | `status_panel`：Web 状态页 `/` + JSON `/health`（只读，默认开启） |
| OneBot WS | `0.0.0.0:6830` | `onebot_adapter`（需配置 `access_token`） |
| http_api | `127.0.0.1:1145` | 默认关闭 |
| http_inject | `127.0.0.1:8901` | 默认关闭（调试用） |
| ws（事件推送） | `0.0.0.0:6840` | 默认关闭 |
| grpc | `0.0.0.0:50051` | 默认关闭（需 `grpcio`） |

## 登录 Web 后台

1. 打开 <http://127.0.0.1:8080>；
2. 使用默认账号 `admin / admin123` 登录；
3. **首次登录请立即修改密码**（账号体系使用 `pbkdf2_sha256`，不依赖可选 bcrypt）。

## 不接 IM 也能跑

没有 OneBot 客户端时，可开启 `http_inject` 扩展，用一条 `curl` 注入事件来调试业务逻辑：

```bash
# 在 extensions.yaml 中 http_inject.enabled: true 后重启
curl -X POST http://127.0.0.1:8901/hook \
  -H 'Content-Type: application/json' \
  -d '{"event":"message","payload":{"user_id":123,"group_id":0,"message":"你好"}}'
```

## 关闭与重启

- `Ctrl+C` 发送 `SIGINT`，框架按 sys / user / core 顺序优雅关停；
- 看门狗会在单服务 / 单插件连续超限时自动回收，无需手动干预。
