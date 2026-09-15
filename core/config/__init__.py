"""
配置加载模块
支持环境变量替换：${VAR_NAME} 或 ${VAR_NAME:-default_value}
首次启动时 config.yaml 不存在则自动生成默认配置
"""
import os
import re
import yaml

logger = None  # 延迟初始化，避免循环导入


def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger('zernus')
    return logger


# 默认配置模板（首次启动自动生成；与项目根 config.yaml 三层结构保持一致）
_DEFAULT_CONFIG = """\
# ============================================================
# ZER NUS 配置文件（三层架构：内核级 / 服务级 / 软件级）
# 首次启动自动生成，可按需修改后重启。
# 监听地址默认均为 127.0.0.1（安全默认值）。
# ============================================================

# ── 内核级（core）：数据库 ───────────────────────────────────
# 支持 SQLite / MySQL / PostgreSQL 三种；由 type 切换。
database:
  type: sqlite                       # sqlite | mysql | postgresql
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

# ── 内核级：本地端口 ────────────────────────────────────────
core:
  host: 127.0.0.1
  port: 37001

# ── 内核级：日志 ────────────────────────────────────────────
log:
  level: INFO                            # DEBUG / INFO / WARNING / ERROR
  file: data/logs/zernus.log             # 留空则只输出控制台
  log_raw_message: true                  # 是否记录收到的原始消息内容
  log_sent_message: true                 # 是否记录发出的消息内容

# ── 服务级（service）：sys / user 服务端口与看门狗 ──────────
service:
  sys:
    host: 127.0.0.1
    port: 38001                          # sys 服务监听端口
  user:
    host: 127.0.0.1
    user_port: 38002                     # user 服务监听端口
  watchdog:
    max_memory_mb: 256                   # 内存上限（MB），超限告警/回收
    interval: 30                         # 采样间隔（秒）

# ── 服务级：zkg 包管理 ─────────────────────────────────────
zkg:
  local_dir: repo                        # 本地包仓库目录
  official_source: ""                    # 官方源地址（留空则仅本地）

# ── 软件级（software）：用户插件 ────────────────────────────
plugin:
  dir: software/plugins                  # 用户插件根目录（每个插件一个子目录，入口 main.py）
  dat_dir: data/plugins_dat              # 插件数据/配置目录
  heartbeat_interval: 60                 # 插件注册心跳间隔（秒）
  auto_install_deps_on_startup: true     # 启动时自动安装缺失依赖（移机自愈）
  max_memory_mb: 64                      # 单插件内存上限（MB），连续超限自动卸载

# ── 软件级：官方扩展（software/extensions） ─────────────────
# 每个扩展可独立开关；本段由启动扫描 software/extensions/ 自动同步 extensions.yaml，
# 无需手动维护（此处列出仅为直观）。
extensions:
  onebot_adapter: true                   # OneBot 11 WebSocket 协议接入
  webui: true                            # Web 管理后台
  session: true                          # 会话管理器
  scheduler: true                        # 定时任务调度器
  http_api: false                        # HTTP REST API（给外部程序用）
  http_inject: false                     # HTTP 事件注入接入端
  image_renderer: true                   # 图像渲染

# ── 软件级：OneBot WebSocket 服务端（扩展 onebot_adapter） ──
onebot:
  listen_host: 0.0.0.0
  listen_port: 6830
  access_token: ""                       # 必须设置！留空则不校验 token

# ── 软件级：Web 管理后台（扩展 webui） ─────────────────────
web:
  host: 127.0.0.1                        # 仅本机访问；局域网/公网请改 0.0.0.0（注意安全）
  port: 8080
  session_timeout: 3600                  # 登录会话超时（秒）
  official_sidebar: true                 # 是否显示官方默认侧边栏
  sidebar:                               # 侧边栏自定义：order=官方菜单顺序，hidden=隐藏项
    order: []
    hidden: []

# ── 软件级：WebUI 事件推送 WebSocket（可选，默认关） ────────
ws:
  enabled: false                         # 开启后 webui 另起 WS 服务推送事件
  host: 0.0.0.0
  port: 6840
  events: []                             # 留空 = 推送全部事件；可填事件名白名单

# ── 软件级：gRPC 接口（可选，默认关；需 grpcio + grpcio-tools） ──
grpc:
  enabled: false
  host: 0.0.0.0
  port: 50051

# ── 软件级：SSL / TLS（HTTPS / WSS，可选） ──────────────────
# 启用后：Web 管理后台走 https、OneBot WS 走 wss（两者共用同一份证书）。
# cert / key 支持绝对路径，或相对项目根目录的路径（如 certs/fullchain.pem）。
ssl:
  enabled: false
  cert: ""                               # 证书链文件（fullchain.pem / .crt）
  key: ""                                # 私钥文件（privkey.pem / .key）

# ── 软件级：GitHub 加速（插件市场 / 下载 / 更新） ───────────
# 留空则用内置 ghproxy 镜像回退；国内直连慢/失败时填写，如 https://ghproxy.net
github_proxy: ""

# ── 通讯安全（对应启动流程：是否加密通讯） ─────────────────
security:
  encrypted: false                       # 是否启用加密通讯（RSA）；false 走 Token 校验
  token: ""                              # 非加密模式下的访问 Token（留空 = 不强制校验）
  rsa_callback: ""                       # 加密模式下 RSA 握手完成后的回调端地址
  # cors_allowed_origins: []             # Web 后台跨域白名单（留空 = 仅同源）
  # trusted_proxies: []                  # 反向代理可信 IP（用于取真实客户端 IP）

# ── 项目身份（可自定义名称，不硬编码） ─────────────────────
project:
  name: ZER NUS
"""


def _env_replace(value):
    """
    递归遍历配置值，将 ${VAR_NAME} 替换为环境变量
    支持默认值语法：${VAR_NAME:-default}
    """
    if isinstance(value, str):
        def replacer(m):
            expr = m.group(1)
            if ':-' in expr:
                var, default = expr.split(':-', 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, m.group(0))  # 未找到则不替换
        return re.sub(r'\$\{([^}]+)\}', replacer, value)
    elif isinstance(value, dict):
        return {k: _env_replace(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_env_replace(v) for v in value]
    return value


def _generate_default_config(config_path: str):
    """生成默认配置文件"""
    config_dir = os.path.dirname(config_path)
    if config_dir and not os.path.isdir(config_dir):
        os.makedirs(config_dir, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(_DEFAULT_CONFIG)
    _get_logger().info(f"已生成默认配置文件: {config_path}，请按需修改后重启")


def load_config(config_path: str = None) -> dict:
    """
    加载 YAML 配置文件，支持环境变量替换
    如果配置文件不存在，自动生成默认配置并加载
    """
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.yaml')

    if not os.path.isfile(config_path):
        _get_logger().warning(f"配置文件不存在，正在生成默认配置: {config_path}")
        _generate_default_config(config_path)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 环境变量替换
    config = _env_replace(config)

    # 官方插件清单：自动扫描 software/extensions/ 同步 extensions.yaml，并合并进 config
    _autoload_extensions(config)

    return config


def get_config() -> dict:
    """获取全局配置"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


# 默认禁用（需显式开启）的官方插件——安全/端口相关，避免误开
_EXTENSION_DEFAULT_DISABLED = ('http_api', 'http_inject')


# 官方插件清单：独立 yaml，由启动时自动扫描 software/extensions/ 目录同步
EXTENSIONS_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'extensions.yaml')


# 官方插件 → 主 config 段名映射（仅列段名与插件名不同的）
_EXTENSION_SECTION = {'onebot_adapter': 'onebot', 'webui': 'web'}


# 官方插件默认配置（含 enabled 与主要配置项），自动写入 extensions.yaml。
# 未列出的已安装插件也会被扫描加入（enabled 按 _default_core_plugin_enabled）。
_EXTENSION_SCHEMA = {
    'onebot_adapter': {'enabled': True, 'listen_host': '0.0.0.0',
                       'listen_port': 6830, 'access_token': ''},
    'webui': {'enabled': True, 'host': '127.0.0.1', 'port': 8080},
    'session': {'enabled': True},
    'scheduler': {'enabled': True},
    'http_api': {'enabled': False, 'host': '127.0.0.1', 'port': 1145,
                 'token': '', 'allow_db': False},
    'http_inject': {'enabled': False, 'host': '127.0.0.1', 'port': 8901,
                    'path': '/hook', 'token': ''},
    'image_renderer': {'enabled': True},
}


def _scan_extensions() -> list:
    """扫描 extensions/ 目录，返回已安装（含 main.py）的官方插件名列表"""
    plugins_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'software', 'extensions')
    names = []
    if os.path.isdir(plugins_dir):
        for name in os.listdir(plugins_dir):
            if name.startswith('_'):
                continue
            if os.path.isfile(os.path.join(plugins_dir, name, 'main.py')):
                names.append(name)
    return sorted(names)


def _default_core_plugin_enabled(name: str) -> bool:
    """官方插件缺省开关：http_api/http_inject 默认 false，其余默认 true"""
    return name not in _EXTENSION_DEFAULT_DISABLED


def _autoload_extensions(config: dict) -> dict:
    """官方插件清单（extensions.yaml）自动同步 + 合并进主 config。

    流程：
      1. 扫描 extensions/ 目录得到已安装官方插件；
      2. 读 extensions.yaml，为「已安装但缺失」的插件补默认配置块；
      3. 移除 yaml 中已不再安装的插件块（卸载自动删除）；
      4. 有变化则回写 yaml（自动更新，用户无需手动维护）；
      5. 把每个插件的 enabled 合并进 config['extensions']，把插件配置块
         合并进对应 section（onebot/web/http_api/...），插件现有
         `fw.config.get('onebot')` 等读取方式无需改动。
    返回合并后的 {plugin: cfg}。
    """
    installed = _scan_extensions()
    if not installed:
        return {}
    yaml_path = EXTENSIONS_YAML

    # 读现有 yaml
    data = {}
    if os.path.isfile(yaml_path):
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            _get_logger().warning(f"读取 extensions.yaml 失败: {e}")
    # 向后兼容：旧配置/旧 yaml 键 core_plugins 仍可被识别（新键 extensions 优先）
    cps = data.get('extensions') if isinstance(data, dict) else None
    if not isinstance(cps, dict) and isinstance(data, dict):
        cps = data.get('core_plugins')
    cps = cps if isinstance(cps, dict) else {}

    # 主 config 中已有的官方插件开关（用于首次生成时迁移，向后兼容不丢设置）
    main_cp = config.get('extensions') or config.get('core_plugins')
    if not isinstance(main_cp, dict):
        main_cp = {}

    changed = False
    # 2. 已安装缺失插件 → 补默认配置块；优先迁移主 config 已有值
    for name in installed:
        section = _EXTENSION_SECTION.get(name, name)
        main_sec = config.get(section)
        if not isinstance(main_sec, dict):
            main_sec = {}
        blk = cps.get(name)
        if not isinstance(blk, dict):
            blk = dict(_EXTENSION_SCHEMA.get(name, {}))
            cps[name] = blk
            changed = True
        if 'enabled' not in blk:
            blk['enabled'] = bool(main_cp[name]) if name in main_cp \
                else _default_core_plugin_enabled(name)
            changed = True
        # 补默认配置键（不覆盖 yaml 已设的值；优先迁移主 config 对应段的值）
        for k, v in _EXTENSION_SCHEMA.get(name, {}).items():
            if k not in blk:
                blk[k] = main_sec[k] if k in main_sec else v
                changed = True
    # 3. 移除已卸载插件
    for name in list(cps.keys()):
        if name not in installed:
            del cps[name]
            changed = True

    # 4. 回写 yaml（自动更新）
    if changed:
        try:
            with open(yaml_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump({'extensions': cps}, f,
                               allow_unicode=True, sort_keys=False)
            _get_logger().info(f"已自动同步官方插件配置: {yaml_path}")
        except Exception as e:
            _get_logger().warning(f"同步 extensions.yaml 失败: {e}")

    # 5. 合并进主 config：enabled → extensions 段；配置块 → 对应 section
    core_cfg = {}
    for name, blk in cps.items():
        if not isinstance(blk, dict):
            continue
        section = _EXTENSION_SECTION.get(name, name)
        cur = config.get(section)
        if isinstance(cur, dict):
            merged = dict(cur)
            merged.update(blk)
        else:
            merged = dict(blk)
        config[section] = merged
        core_cfg[name] = bool(blk.get('enabled', False))
    config['extensions'] = core_cfg
    return cps


_config = None
