# 配置

Zeronus 有两份配置：

- **`config.yaml`**：内核级 / 服务级 / 软件级的主配置，**首次启动自动生成**，可手工修改后重启。
- **`extensions.yaml`**：软件级**官方扩展**的启用清单与端口等参数（非配置中心，仅记录启用与端口）。

> 实际扩展启用以 `extensions.yaml` 为准；`config.yaml → extensions` 段仅为直观查看，启动时会由 `extensions.yaml` 同步覆盖。

## 托管覆盖层 `config.managed.yaml`

`config.yaml` 同目录下可存在 `config.managed.yaml`：加载时**最后合并**（深度合并
dict，list / 标量整体覆盖），优先级高于 `config.yaml` 与 `extensions.yaml`。
它由编排面下发（节点侧 `config.push`），**不修改用户的 `config.yaml`**；删除该
文件即恢复用户原配置。

```yaml
# config.managed.yaml：只写需要覆盖的片段
node_agent:
  interval: 60
  allow_shell: true
```

热重载：`Framework.reload_config()` 会重读 `config.yaml` + 托管覆盖层，就地更新
`fw.config` 并触发 `config.reload` 扩展点，不重启进程。**端口、数据库连接、
SSL 上下文等启动期绑定项仍必须重启**；扩展若实现 `config.reload` 处理器，
可自行即时应用自己的配置。

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
  update_expected_sha256: ""   # 框架更新 ZIP 的期望 sha256（留空且未 allow_unsigned 时拒绝更新）
  update_allow_unsigned: false # 显式允许未校验的框架更新（不推荐）
```

> 框架更新（WebUI / 节点 `framework.update`）先校验 sha256，再暂存、编译检查、
> 备份旧文件后替换；失败自动回滚，也可用 `/api/framework/rollback` 或
> `rollback_framework()` 显式回滚。升级/回滚后需重启进程。

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

修改后重启生效；需要不重启更新时，改用上文的托管覆盖层并调用
`Framework.reload_config()`（启动期绑定项仍需重启）。
