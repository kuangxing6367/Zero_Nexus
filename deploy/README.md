# 部署（自托管）

「自托管」定位的两种标准姿势。前置：Python 3.11+（镜像内 3.12），内核仅硬依赖 PyYAML。

## Docker

```bash
docker build -t zeronus:latest -f deploy/Dockerfile .
docker run -d --name zeronus -p 8080:8080 -v zeronus-data:/app/data zeronus:latest
```

- 数据库 / 日志 / 包缓存 / 插件数据全部在卷 `/app/data`，升级镜像不丢数据。
- 容器内访问 Web 后台需把 `config.yaml` 中 `web.host` 改为 `0.0.0.0`（容器场景属合理暴露面；宿主机上仍建议加防火墙/反代）。
- 需要状态面板对外（供 hub 纳管）时，把 `status_panel.host` 改 `0.0.0.0` 并 `-p 8090:8090`。
- 首次登录 Web 后台：`admin / admin123`，**登录后立即改密**。

## systemd

```bash
# 仓库放 /opt/zernus，建议建专用用户
useradd -r -s /usr/sbin/nologin zernus && chown -R zernus /opt/zernus
cp deploy/zernus.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zernus
journalctl -u zernus -f        # 看日志
```

## 多机管理（L1）中心机

中心机（hub）在 `extensions.yaml` 开启：

```yaml
node_manager:
  enabled: true
  interval: 30
  nodes:
    - name: node-1
      url: http://192.168.1.10:8090   # 各节点 status_panel 地址
```

各被纳管节点只需开 `status_panel`（默认开，建议绑定内网地址）。hub 每 30s
轮询一次 `/health`，写入 `nodes` 表（心跳 `updated_at`），状态翻转时打日志。
进程内：`fw.services.get('node_manager').snapshot()` 读聚合快照。
