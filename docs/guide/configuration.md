# 配置系统

Zeronus 有两个配置文件，分别对应「三层」中的层级职责：

| 文件 | 作用 |
| --- | --- |
| `config.yaml` | **内核级与服务级配置**：数据库、内核端口、日志、sys/user 服务端口、看门狗、zkg、插件目录、通讯安全、项目身份；以及软件级扩展的开关与参数 |
| `extensions.yaml` | **软件级官方扩展清单**：每个官方扩展的开关与参数（端口 / token 等） |

两者都在 `.gitignore` 中：`config.yaml` 首次启动会按三层模板自动生成，升级不会覆盖。

## 一、config.yaml

### 数据库（内核级）

支持三种方言，由 `type` 切换：

```yaml
database:
  type: sqlite                 # sqlite（默认，零配置）/ mysql / postgresql
  path: data/zernus.db
  # MySQL 模式：
  # type: mysql
  # host: 127.0.0.1
  # port: 3306
  # user: root
  # password: ""
  # database: zernus
  # PostgreSQL 模式（需安装驱动，见 requirements.txt 注释）：
  # type: postgresql
  # host: 127.0.0.1
  # port: 5432
  # user: postgres
  # password: ""
  # database: zernus
```

MySQL 连接保活 / 自动重连（`type: mysql` 时生效）：`ping_interval` / `connect_timeout` /
`read_timeout` / `write_timeout` / `max_reconnect`。

### 内核端口（内核级）

```yaml
core:
  host: 127.0.0.1
  port: 37001                  # 内核启动后监听的本地端口（状态 / 事件通道）
```

### 服务级（sys / user 服务与看门狗）

```yaml
service:
  sys:
    host: 127.0.0.1
    port: 38001                # sys 服务（初始化）监听端口
  user:
    host: 127.0.0.1
    user_port: 38002           # user 服务监听端口
  watchdog:
    max_memory_mb: 256         # 内存上限（MB）：服务级/软件级看门狗与插件级监控共用这一键
    interval: 30               # 采样间隔（秒）
```

### zkg 包管理（服务级）

```yaml
zkg:
  local_dir: repo              # 本地包仓库目录
  official_source: ""          # 官方源地址（默认留空 = 仅本地，不主动联网）
```

### 官方扩展（软件级）

`config.yaml` 里的一段布尔开关，与 `extensions.yaml` 的详细配置配合：

```yaml
extensions:
  onebot_adapter: true
  webui: true
  session: true
  scheduler: true
  http_api: false
  http_inject: false
  image_renderer: true
```

> 启动时会自动扫描 `software/extensions/`，把「已安装但未列出」的官方扩展补进这一段，
> 并回写 `extensions.yaml`。
> `http_api` / `http_inject` 需要**此处为 `true`** 且 `extensions.yaml` 对应块的 `enabled` 也为 `true`，两者都满足才生效。

### 用户插件（软件级）

```yaml
plugin:
  dir: software/plugins        # 插件代码目录
  dat_dir: data/plugins_dat    # 插件数据/配置目录
  heartbeat_interval: 60       # 插件心跳间隔（秒）
  auto_install_deps_on_startup: true   # 启动自动补依赖（移机自愈）
  max_memory_mb: 64            # 单插件内存上限，连续超限自动卸载
```

### OneBot 接入（软件级扩展）

```yaml
onebot:
  listen_host: 0.0.0.0
  listen_port: 6830
  access_token: ""             # 强烈建议设置：留空则任何客户端都能接入
```

### Web 后台（软件级扩展）

```yaml
web:
  host: 127.0.0.1              # 需局域网/公网访问改为 0.0.0.0（注意安全）
  port: 8080
  session_timeout: 3600        # 登录会话超时（秒）
  official_sidebar: true       # 是否显示官方默认侧边栏
  sidebar:
    order: []                  # 官方菜单顺序
    hidden: []                 # 隐藏的官方菜单项
```

官方菜单键：`dashboard, marketplace, plugins, commands, users, groups, permissions, apikeys, tasks, runtime, connection, filebrowser, logs, database`。

### SSL / TLS（软件级）

```yaml
ssl:
  enabled: false
  cert: ""                     # 证书链（相对项目根或绝对路径）
  key: ""                      # 私钥
```

启用后 WebUI 走 HTTPS、OneBot 接入走 WSS（共用同一证书）。

### 对外接口（软件级，可选）

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

### 日志

```yaml
log:
  level: INFO                  # DEBUG / INFO / WARNING / ERROR
  file: data/logs/zernus.log   # 留空则只输出控制台
  log_raw_message: true        # 记录收到的原始消息
  log_sent_message: true       # 记录发出的消息
```

### 通讯安全

按启动流程决定：是否加密通讯。

```yaml
security:
  encrypted: false             # false → 读 Token 校验；true → RSA 完事 → 回调端
  token: ""                    # 非加密模式下的访问 Token（留空 = 不强制校验）
  rsa_callback: ""             # 加密模式下 RSA 握手完成后的回调端地址
  # cors_allowed_origins: []   # Web 后台跨域白名单（留空 = 仅同源）
  # trusted_proxies: []        # 反向代理可信 IP（用于取真实客户端 IP）
```

### GitHub 加速

```yaml
github_proxy: ""               # 如 https://ghproxy.net；留空走内置镜像回退
```

用于插件市场、插件下载与框架更新。

### 项目身份

```yaml
project:
  name: ZER NUS                # 启动横幅与 Web 后台显示的项目名
```

## 二、extensions.yaml

软件级官方扩展的**开关与详细参数**集中在这里。启动时会自动扫描 `software/extensions/`：
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

相关：[部署上线](../advanced/deployment.md) ／ [架构总览](../advanced/architecture.md)。
