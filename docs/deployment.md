# 部署上线

## 进程管理（systemd）

```ini
# /etc/systemd/system/zernus.service
[Unit]
Description=Zeronus
After=network.target

[Service]
WorkingDirectory=/opt/zernus
ExecStart=/opt/zernus/.venv/bin/python main.py
User=zernus
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now zernus
```

## 容器（Docker）

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080 6830
CMD ["python", "main.py"]
```

构建运行：

```bash
docker build -t zernus .
docker run -d --name zernus -p 8080:8080 -p 6830:6830 -v zernus-data:/app/data zernus
```

## 反向代理（Nginx）

```nginx
server {
    listen 443 ssl;
    server_name zernus.example.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- 若使用 `ws`（WebUI 事件推送）或 `grpc`，按需额外 `proxy_pass` 对应端口（WebSocket 需 `proxy_set_header Upgrade $http_upgrade;` 等）。
- 反向代理的客户端真实 IP 可用 `config.yaml → security.trusted_proxies` 配置。

## TLS

- 框架内置 `ssl` 段（`cert` / `key`），启用后 Web 走 HTTPS、OneBot WS 走 WSS（共用同一份证书）。
- 也可仅由反向代理终止 TLS，框架保持 HTTP（监听 `127.0.0.1`，不直连公网）。

## 安全清单

- 所有监听默认 `127.0.0.1`；公网暴露前务必设置 `onebot.access_token` 与 Web 后台强密码（默认 `admin / admin123`）。
- 优先用反向代理 + TLS 终止，框架不直连公网。
- `http_api` / `http_inject` 调试用扩展默认关闭，生产环境保持关闭或限制来源。
- 数据库按需选 MySQL / PostgreSQL，并定期备份 `data/zernus.db` 或对应库。
