# 部署上线

## 一、最小部署

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
pip install -r requirements.txt
python main.py
```

首次启动生成 `config.yaml`、建表、打印横幅。生产环境建议用 systemd / Docker 托管。

## 二、systemd（Linux）

`/etc/systemd/system/zeronus.service`：

```ini
[Unit]
Description=Zeronus
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/Zero_Nexus
ExecStart=/opt/Zero_Nexus/venv/bin/python /opt/Zero_Nexus/main.py
Restart=on-failure
RestartSec=5
# 时区（影响定时任务）
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zeronus
sudo systemctl status zeronus
```

## 三、Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# WebUI / OneBot 默认端口
EXPOSE 8080 6830

CMD ["python", "main.py"]
```

```bash
docker build -t zeronus .
docker run -d --name zeronus \
  -p 8080:8080 -p 6830:6830 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -e TZ=Asia/Shanghai \
  zeronus
```

> 记得挂载 `data/`（数据库与插件数据）与 `config.yaml`，否则容器重建会丢。

## 四、反向代理 + HTTPS

推荐用 Nginx 反代 WebUI，证书交给 Nginx：

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;

    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 需要 WebSocket（如未来启用 WS 事件通道）时加上
    location /ws {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

配置要点：

- `config.yaml` 里 `web.host` 保持 `127.0.0.1`（只让反代访问）；
- `security.whitelist_ips` 加上反代所在网段，避免蜜罐误伤；
- 若不用反代、要框架自己上 HTTPS，则配 `ssl.enabled: true` + 证书路径。

## 五、端口清单

| 端口 | 服务 | 建议暴露 |
| ---- | ---- | ---- |
| 8080 | WebUI / REST API | 仅反代可达 |
| 6830 | OneBot 反向 WS | 对协议端可达 |
| 1145 | 独立 HTTP API（默认关） | 仅内网 |
| 8901 | HTTP 事件注入（默认关） | 仅内网 |

## 六、安全清单

- [ ] `onebot.access_token` **必须设置**，否则任何客户端都能接入机器人；
- [ ] 默认管理员 `admin / admin123` **首次登录后立刻改**；
- [ ] WebUI 只绑 `127.0.0.1`，对外走反代 + HTTPS；
- [ ] `http_api` / `http_inject` 非必要不开；开了必须设 token；
- [ ] `http_api.allow_db` 保持 `false`（它允许任意 SQL）；
- [ ] 密钥用环境变量：`${VAR}` / `${VAR:-default}`；
- [ ] `security.whitelist_ips` 按实际网络调整；
- [ ] 定期看后台「日志」页的异常与审计记录。

## 七、升级

```bash
cd /opt/Zero_Nexus
git pull
pip install -r requirements.txt
sudo systemctl restart zeronus
```

- 系统表在启动时自动迁移，无需手动改表；
- `plugins/`、`data/`、`config.yaml`、`extensions.yaml` 都是本地内容，升级不覆盖；
- 关注 [CHANGELOG](https://github.com/kuangxing6367/Zero_Nexus/blob/main/CHANGELOG.md) 的破坏性变更说明。

## 八、常见问题

| 现象 | 排查 |
| ---- | ---- |
| 后台打不开 | 端口占用 / 防火墙 / `web.host` 绑定地址 |
| 协议端连不上 | `onebot.listen_port` 与协议端 URL、`access_token`、防火墙 |
| 定时任务不跑 | 容器 `TZ` 不对；任务被暂停；插件被禁用 |
| 数据库报错 | MySQL 连接参数 / 权限；SQLite 文件所在目录不可写 |
| 插件装了不生效 | 后台「插件」页是否启用；看日志里的加载失败原因 |

---

延伸：[配置系统](../guide/configuration.md) · [双核心（实验）](./dual-core.md)
