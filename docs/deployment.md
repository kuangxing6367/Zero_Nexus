# 部署上线

仓库 `deploy/` 目录提供官方部署产物：`deploy/Dockerfile`、`deploy/zernus.service`，
用法与多机管理见 [deploy/README.md](../deploy/README.md)。

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

- 所有监听默认 `127.0.0.1`；公网暴露前务必设置 `onebot.access_token`。Web 后台种子账号 `admin / admin123` 带强制改密标记，上线前必须完成改密。
- 框架自更新、插件包上传等外部归档入口统一经 `core/security/archive_guard.py` 校验，见 [archive-guard.md](archive-guard.md)；`security.update_expected_sha256` 未配置时自更新默认拒绝（fail-closed）。
- 优先用反向代理 + TLS 终止，框架不直连公网。
- `http_api` / `http_inject` 调试用扩展默认关闭，生产环境保持关闭或限制来源。
- 数据库按需选 MySQL / PostgreSQL，并定期备份 `data/zernus.db` 或对应库。

## 多机管理（L1 监控聚合）

中心机（hub）在 `extensions.yaml` 开启 `node_manager`，轮询各节点的
`status_panel /health`（节点默认开启、绑定内网地址即可），写入 `nodes` 表：

```yaml
node_manager:
  enabled: true
  interval: 30
  nodes:
    - name: node-1
      url: http://192.168.1.10:8090
```

星型拓扑、节点无需公网（hub 不反向连接）。

## 多机管理（L2 控制通道）

L1 只读监控之上，可加控制通道：hub 开 `node_control`（监听 TCP），节点开
`node_agent`（主动外连 hub，节点可在 NAT 后）。传输复用 framed 协议
（整帧 HMAC、防重放），鉴权为 per-node 预共享密钥：

```yaml
# hub 侧
node_control:
  enabled: true
  host: 0.0.0.0
  port: 37010
  nodes:
    - name: node-1
      secret: change-me-node-1     # 务必换成强随机值

# 节点侧
node_agent:
  enabled: true
  hub_host: 10.0.0.1
  hub_port: 37010
  name: node-1
  secret: change-me-node-1
  allow_shell: false               # shell 命令安全开关，默认禁
  # allow_docker / allow_packages / allow_config 均默认 false，按需逐项开启
```

命令白名单：`ping` / `health` / `pkg.list` 恒可用；`shell` 需节点侧显式
`allow_shell: true`；`docker.*` 需 `allow_docker: true`；`pkg.sync` 需
`allow_packages: true`。进程内调用（hub 上任意线程）：

```python
ctrl = fw.services.get('node_control')
ctrl.snapshot()                                   # 在线节点快照
ctrl.send_cmd('node-1', 'health', {}, timeout=10) # {'ok': True, 'data': ...}
```

## 多机管理（L3 编排）

L2 之上提供**分组 + 并发/滚动下发 + 能力上报**。分组只是成员解析，
不绕过 `nodes` 预共享密钥清单；命令仍由节点侧白名单执行：

```yaml
# hub 侧
node_control:
  enabled: true
  nodes:
    - {name: node-1, secret: change-me-node-1}
    - {name: node-2, secret: change-me-node-2}
  groups:
    edge: [node-1]       # 灰度组：先发这台
    core: [node-2]       # 稳定组：验证后再发
```

```python
ctrl = fw.services.get('node_control')

ctrl.list_groups()      # {'edge': {'members': [...], 'online': [...], 'offline': [...]}}

# 灰度：先 edge，一次一台，间隔 30s；检查回执后再放量到 core
ctrl.send_group('edge', 'health', {}, timeout=10, concurrency=1, stagger=30)
ctrl.send_group('core', 'health', {}, timeout=10, concurrency=1, stagger=30)

# 全量并发（默认 4 路）：send_group(None) 或 broadcast
ctrl.broadcast('ping', {}, concurrency=4)
```

返回摘要：`{'group','cmd','total','ok','failed','offline','elapsed','results'}`；
单台失败不会中断整组，`results` 里逐台给出 `ok` / `data`。未知分组直接返回
`error`，**不会**静默退化成全量下发。

### 包同步（节点侧，需显式开闸）

节点开启 `allow_packages: true` 后，hub 可下发 `pkg.sync`：节点按自身 zkg 源
执行一次 `scan → resolve → rebuild → 按需加载`，与 sys 服务启动路径共用
`service/startup.run_zkg`，sha256 / HMAC 签名校验照旧强制执行。只读的
`pkg.list` 无需开闸，返回已加载工具与本地 `repo/` 包目录。

```yaml
# 节点侧
node_agent:
  enabled: true
  allow_packages: true   # 会拉取并加载远端机制包，按需开启
```

```python
ctrl.send_group('edge', 'pkg.sync', {}, timeout=120)
```

### 配置下发（节点侧，需显式开闸）

节点开启 `allow_config: true` 后，hub 可下发配置。写入目标是 `config.yaml`
**同目录的托管覆盖层** `config.managed.yaml`：加载时最后合并、优先级最高
（压过 `config.yaml` 与 `extensions.yaml`），不改动用户原配置文件。

```yaml
# 节点侧
node_agent:
  enabled: true
  allow_config: true     # 默认 false；开启后才能 config.push / config.reload
```

```python
# hub 侧：只写差异片段即可（深度合并，不是整份配置替换）
ctrl.push_config('edge', {'node_agent': {'interval': 60, 'allow_shell': True}},
                 concurrency=1, stagger=5, dry_run=False)

ctrl.config_status('edge')   # 只读：allow_config / 覆盖层大小 / sha256 / 顶层键
ctrl.reload_config('edge')   # 仅重读配置（含覆盖层），不改文件
ctrl.push_config('edge', {'clear': True})   # 删除覆盖层，恢复用户原配置
```

安全与一致性：

- **强校验**：`push_config` 在 hub 侧把配置规范化为 YAML 并计算 sha256，
  节点侧重算比对后才落盘；sha256 必须是 64 位小写十六进制，空配置 /
  非映射 / 超过 1MiB 一律拒绝。
- **原子写**：同目录临时文件 + `fsync` + `os.replace`，不会出现半截文件。
- **失败回滚**：写盘或热重载失败时自动恢复上一份覆盖层，并重载旧配置。
- **dry_run**：只校验不落盘，可先灰度验证再真正下发。
- 只读的 `config.status` 无需开闸；实际执行仍由节点侧白名单兜底。

> 热重载只即时生效**可安全变更**的项（如 `node_agent.allow_*`、`interval`
> 与扩展自行实现的 `config.reload` 处理器）。端口、数据库连接、SSL 等
> 启动期绑定项仍需重启进程。

### 框架自身升级（节点侧，需显式开闸）

统一机制在 `service/updater.py`，WebUI 与节点代理共用：

1. sha256 强校验（默认 fail closed，未提供哈希直接拒绝）；
2. `core/security/archive_guard.py` 归档安全校验；
3. 解压到暂存区并对所有 `.py` 做编译检查；
4. 旧文件移入 `<root>/.update_backup/<时间戳>-<sha前12位>/`；
5. 新文件移入线上；任一步失败自动回滚。

只替换 `core` / `service` / `software/extensions` / `sql` / `deploy` / `docs`
等框架代码与文档，**不动** `data/`、`repo/`、`config.yaml`、`extensions.yaml`
与 `software/plugins/`（`extensions.yaml` 由用户维护端口/开关，新扩展默认值
由代码内默认值兜底，升级不会覆盖用户改动）。

```yaml
# 节点侧
node_agent:
  allow_upgrade: true    # 默认 false；开启后才能 framework.update / rollback
```

```python
# hub 侧：先 dry-run 验证包，再逐台滚动（concurrency=1 + stagger 即灰度）
ctrl.push_framework('edge', 'Zero_Nexus-1.2.0.zip', dry_run=True)
ctrl.push_framework('edge', 'Zero_Nexus-1.2.0.zip', concurrency=1, stagger=30)

ctrl.framework_status('edge')     # 版本 + 可回滚备份
ctrl.rollback_framework('edge')   # 回到最新备份（可传 backup 名）
```

WebUI 侧对应 `POST /api/framework/update`（支持 `dry_run`）、
`GET /api/framework/backups`、`POST /api/framework/rollback`。

> **边界**：升级/回滚都**不会自动重启进程**，也不会在重启后做健康检查与
> 自动回滚；灰度是否继续由调用方查看逐台回执后决定。升级包上限 8MiB
> （framed 单帧上限 16MiB，base64 后约为原始大小的 4/3）。
