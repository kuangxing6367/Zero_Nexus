# 配置系统

Zeronus 有两个配置文件：

| 文件 | 作用 |
| --- | --- |
| `config.yaml` | **主配置**：数据库、Web、SSL、日志、插件目录、系统与安全、双进程等 |
| `extensions.yaml` | **官方扩展配置中心**：每个官方扩展的开关与参数 |

两者都在 `.gitignore` 中，首次启动自动生成，升级不会覆盖。

## 一、config.yaml

### 数据库

```yaml
database:
  type: sqlite                 # sqlite（默认，零配置）/ mysql
  path: data/zernus.db
  # MySQL 模式：
  # type: mysql
  # host: 127.0.0.1
  # port: 3306
  # user: root
  # password: ''
  # database: zernus
  # ping_interval: 5         # 连接保活
  # connect_timeout: 10
  # read_timeout: 30
  # write_timeout: 30
  # max_reconnect: 3
```

检测到 MySQL 配置时会自动安装 `pymysql` / `DBUtils` 驱动。

### OneBot 接入

```yaml
onebot:
  listen_port: 6830
  access_token: ""             # 强烈建议设置：留空则任何客户端都能接入
```

### Web 后台

```yaml
web:
  host: 127.0.0.1              # 需局域网/公网访问改为 0.0.0.0（注意安全）
  port: 8080
  secret_key: ""               # 留空则每次重启随机生成
  session_timeout: 3600        # 登录会话超时（秒）
  official_sidebar: true       # 是否显示官方默认侧边栏
  sidebar:
    order: []                  # 官方菜单顺序
    hidden: []                 # 隐藏的官方菜单项
```

官方菜单键：`dashboard, marketplace, plugins, commands, users, groups, permissions, apikeys, tasks, runtime, connection, filebrowser, logs, database`。

### SSL / TLS

```yaml
ssl:
  enabled: false
  cert: ""                     # 证书链（相对项目根或绝对路径）
  key: ""                      # 私钥
```

启用后 WebUI 走 HTTPS、OneBot 接入走 WSS（共用同一证书）。

### 对外接口（可选）

```yaml
http_api:                      # 给外部程序用的 REST API
  host: 127.0.0.1
  port: 1145
  token: ""                    # 留空则每次启动随机生成（打印到日志）
  allow_db: false              # 是否开放 db/query、db/execute（高危，默认关）

http_inject:                   # HTTP 事件注入接入端
  enabled: false
  host: 127.0.0.1
  port: 8901
  path: /hook
  token: ""
```

### 官方扩展开关

`config.yaml` 里还有一段布尔开关，与 `extensions.yaml` 的详细配置配合：

```yaml
extensions:
  onebot_adapter: true
  webui: true
  session: true
  scheduler: true
  http_api: false
  http_inject: false
```

> 启动时会自动扫描 `extensions/`，把「已安装但未列出」的官方扩展补进这一段。
> `http_api` / `http_inject` 需要**此处为 `true`** 且 `extensions.yaml` 对应块的 `enabled` 也为 `true`，两者都满足才生效。

### 日志

```yaml
log:
  level: INFO
  file: data/logs/zernus.log
  log_raw_message: true        # 记录收到的原始消息
  log_sent_message: true       # 记录发出的消息
```

### 插件

```yaml
plugin:
  dir: plugins                 # 插件代码目录
  heartbeat_interval: 60       # 插件心跳间隔（秒）
  auto_install_deps_on_startup: true   # 启动自动补依赖（移机自愈）
  max_memory_mb: 64            # 单插件内存上限，超限自动卸载
```

### 系统与安全

```yaml
system:
  show_cpu: true
  show_disk: true
  status_interval: 30

security:
  # 双请求防破解认证：先发 fake_token_len 位探针拿 nonce，再发 real_token_len 位 Token
  fake_token_len: 8
  real_token_len: 8192
  nonce_len: 16
  nonce_expiry: 60
  blacklist_enabled: true
  whitelist_ips:
    - "127.0.0.1"
```

### GitHub 加速

```yaml
github_proxy: ""               # 如 https://ghproxy.net；留空走内置镜像回退
```

用于插件市场、插件下载与框架更新。

### 双进程（实验）

```yaml
dual_process:
  enabled: false
  extensions:                  # 核心进程加载的官方扩展白名单
    - onebot_adapter
    - http_inject
    - http_api
    - webui
  max_restarts: 5
  restart_interval: 30
```

详见[双核心（实验）](../advanced/dual-core.md)。

## 二、extensions.yaml

官方扩展的**开关与详细参数**集中在这里。启动时会自动扫描 `extensions/` 目录：
为已安装但未列出的扩展补默认块、回写缺失项，再合并进主配置。

```yaml
extensions:
  onebot_adapter:
    enabled: true
    listen_host: 0.0.0.0
    listen_port: 6830
    access_token: ''
  webui:
    enabled: true
    host: 127.0.0.1
    port: 8080
  session:
    enabled: true
  scheduler:
    enabled: true
  image_renderer:
    enabled: true
  http_api:
    enabled: false
    host: 127.0.0.1
    port: 1145
    token: ''
    allow_db: false
  http_inject:
    enabled: false
    host: 127.0.0.1
    port: 8901
    path: /hook
    token: ''
```

> `http_api` / `http_inject` 需要**此处 `enabled: true`** 且**主配置对应段自身也启用**，两者都满足才生效。

## 三、环境变量替换

配置值支持 `${VAR}` 与带默认值的 `${VAR:-default}`：

```yaml
database:
  password: "${DB_PASSWORD:-}"
onebot:
  access_token: "${ONEBOT_TOKEN}"
```

适合把密钥留在环境变量里，而不是写进配置文件。

## 四、改配置后

大部分配置**重启生效**；Web 后台里的「设置」页可直接改 `config.yaml` / `extensions.yaml` 的多数项并保存，
部分项（如侧边栏）即时生效。

---

相关：[部署上线](../advanced/deployment.md) ／ [双核心](../advanced/dual-core.md)。
