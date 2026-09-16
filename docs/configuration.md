# 配置

Zeronus 有两份配置：

- **`config.yaml`**：内核级 / 服务级 / 软件级的主配置，**首次启动自动生成**，可手工修改后重启。
- **`extensions.yaml`**：软件级**官方扩展**的启用清单与端口等参数（非配置中心，仅记录启用与端口）。

> 实际扩展启用以 `extensions.yaml` 为准；`config.yaml → extensions` 段仅为直观查看，启动时会由 `extensions.yaml` 同步覆盖。

## config.yaml 字段

### 数据库 `database`
```yaml
database:
  type: sqlite                 # sqlite | mysql | postgresql
  path: data/zernus.db         # SQLite 文件路径（MySQL/PG 下忽略）
  # host / port / user / password / database  # 仅 mysql / postgresql 模式使用
```

### 内核端口 `core`
```yaml
core:
  host: 127.0.0.1
  port: 37001
```

### 日志 `log`
```yaml
log:
  level: INFO                  # DEBUG / INFO / WARNING / ERROR
  file: data/logs/zernus.log   # 留空则只输出控制台
  log_raw_message: true        # 是否记录收到的原始消息
  log_sent_message: true       # 是否记录发出的消息
```

### 服务端口与看门狗 `service`
```yaml
service:
  sys:
    host: 127.0.0.1
    port: 38001
  user:
    host: 127.0.0.1
    user_port: 38002
  watchdog:
    max_memory_mb: 256         # 内存上限（MB），超限告警 / 回收
    interval: 30               # 采样间隔（秒）
```

### zkg 包管理 `zkg`
```yaml
zkg:
  local_dir: repo              # 本地包仓库目录
  official_source: ""          # 官方源地址（默认不主动连接；留空则仅本地）
```

### 插件 `plugin`
```yaml
plugin:
  dir: software/plugins        # 用户插件根目录
  dat_dir: data/plugins_dat    # 插件数据 / 配置目录
  auto_install_deps_on_startup: true   # 启动时自动安装缺失依赖（移机自愈）
  max_memory_mb: 64            # 单插件内存上限（MB），连续超限自动卸载
```

### 官方扩展开关 `extensions`
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

### OneBot `onebot`
```yaml
onebot:
  listen_host: 0.0.0.0
  listen_port: 6830
  access_token: ""             # 必须设置！留空则不校验 token
```

### Web 后台 `web`
```yaml
web:
  host: 127.0.0.1
  port: 8080
  session_timeout: 3600        # 登录会话超时（秒）
  official_sidebar: true       # 是否显示官方默认侧边栏
  sidebar:
    order: []                  # 官方菜单顺序覆盖
    hidden: []                 # 隐藏项
```

### 可选通道
```yaml
ws:        # WebUI 事件推送 WebSocket，默认关
  enabled: false
  host: 0.0.0.0
  port: 6840
  events: []                   # 留空 = 推送全部事件
grpc:      # gRPC 接口，默认关（需 grpcio + grpcio-tools）
  enabled: false
  host: 0.0.0.0
  port: 50051
ssl:       # HTTPS / WSS，默认关
  enabled: false
  cert: ""
  key: ""
github_proxy: ""               # GitHub 加速镜像（国内慢/失败时填写）
```

### 通讯安全 `security`
```yaml
security:
  encrypted: false             # 是否启用加密通讯（RSA）；false 走 Token 校验
  token: ""                    # 非加密模式访问 Token（留空 = 不强制校验）
  rsa_callback: ""             # 加密模式 RSA 握手完成后的回调端地址
```

### 项目身份 `project`
```yaml
project:
  name: ZER NUS
```

## extensions.yaml 字段

每个扩展可独立开关并配置参数，例如：

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

修改后重启生效。
