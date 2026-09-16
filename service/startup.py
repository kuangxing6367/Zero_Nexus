"""服务级（Service Layer）：sys 服务与 user 服务的拉起与生命周期。

对应手写笔记：
- 服务级职责：zkg 包管理、框架服务基础（mg/db 等）、软件启动与注销核心服务、看门狗。
- 启动流程：拉起 sys 服务（初始化）→ 拉起 user 服务；两者均监听本地端口。

本模块是「服务级」的程序化入口，main.py 在拉起内核（core）后调用这里。
"""
import asyncio
import logging
import os

from core.kernel.paths import project_root
from service.watchdog import Watchdog

logger = logging.getLogger('zernus')


def _watchdog_limit_mb(config: dict, default: int = 256) -> int:
    """内存上限统一取自 service.watchdog.max_memory_mb（与服务级看门狗同源）。"""
    wd = ((config.get('service') or {}).get('watchdog') or {})
    try:
        return int(wd.get('max_memory_mb', default))
    except (TypeError, ValueError):
        return default


def _watchdog_interval_s(config: dict, default: float = 30.0) -> float:
    """采样间隔取自 service.watchdog.interval（与服务级看门狗同源）。"""
    wd = ((config.get('service') or {}).get('watchdog') or {})
    try:
        return float(wd.get('interval', default))
    except (TypeError, ValueError):
        return default


def open_local_port(host: str, port: int, label: str):
    """在内核事件循环里监听一个本地端口（服务级控制/状态通道），返回 Task。"""
    async def _handler(reader, writer):
        try:
            await asyncio.wait_for(reader.read(256), timeout=2)
            writer.write(b'OK\n')
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _serve():
        server = await asyncio.start_server(_handler, host, port)
        logger.info(f"[{label}] 监听本地端口 {host}:{port}")
        async with server:
            await server.serve_forever()

    return asyncio.ensure_future(_serve())


def _zkg_sources(config: dict) -> list:
    """构造 zkg 源列表：默认仅本地；配置 zkg.official_source 后启用远程官方源。

    注意：内置默认源里 http 源是 enabled=True，但按笔记「默认不主动连接远程」，
    未显式配置 official_source 时这里把它关掉，避免启动即联网。
    """
    from service.zkg import defaults
    srcs = defaults.get_default_sources()
    official = ((config.get('zkg') or {}).get('official_source') or '').strip()
    for s in srcs:
        if s.get('type') == 'http':
            if official:
                s['url'] = official
            else:
                s['enabled'] = False
    return srcs


async def start_sys_service(fw, config: dict):
    """拉起 sys 服务（初始化）：zkg 包管理、框架服务（mg/db）、看门狗。"""
    svc = (config.get('service', {}) or {}).get('sys', {}) or {}
    host = svc.get('host', '127.0.0.1')
    port = int(svc.get('port', 38001))

    # zkg 包管理：scan(本地仓库 + 用户插件) → resolve(依赖图) → 重建 data/plugins.db
    #            → 加载被依赖工具。用户插件在 software/plugins/<pkg>/manifest.toml
    #            的 dependencies 里声明所需官方工具，zkg 据此决定加载哪些。
    try:
        from service.zkg.loader import Loader
        from core.ctx import PLUGIN_API_VERSION
        root = (fw.config.get('zkg') or {}).get('local_dir', 'repo')
        if not os.path.isabs(root):
            root = os.path.join(project_root(), root)
        plugin_root = os.path.join(project_root(), 'software', 'plugins')
        data_dir = os.path.join(project_root(), 'data')
        loader = Loader(root, data_dir,
                        sources_cfg=_zkg_sources(fw.config),
                        scan_roots=[plugin_root],
                        plugin_api_version=PLUGIN_API_VERSION)
        result = loader.run()
        # 机制包统一暴露：插件经 ctx.zkg_tool(name) 取用
        fw.zkg_tools = loader.loaded_tools()
        tools = result.get('loaded_tools') or []
        logger.info(
            f"[sys] zkg 包管理就绪：仓库 {result.get('plugin_count', 0)} 个包，"
            f"按依赖加载工具 {len(tools)} 个"
            + (f" {tools}" if tools else "")  + f"（仓库目录 {root}）"
        )
    except Exception as e:
        logger.warning(f"[sys] zkg 初始化跳过: {e}")

    # 框架服务基础（mg=消息网关 / db=数据库）已在内核初始化阶段就绪
    logger.info("[sys] 框架服务（mg/db）已就绪")

    wd = Watchdog(limit_mb=_watchdog_limit_mb(config),
                  interval=_watchdog_interval_s(config))
    wd.start()
    task = open_local_port(host, port, 'sys')
    logger.info("[sys] sys 服务已拉起（初始化完成）")
    return {'name': 'sys', 'task': task, 'watchdog': wd, 'port': port}


async def start_user_service(fw, config: dict):
    """拉起 user 服务：加载软件级（extensions/plugins）并启动；看门狗。"""
    svc = (config.get('service', {}) or {}).get('user', {}) or {}
    host = svc.get('host', '127.0.0.1')
    port = int(svc.get('user_port', 38002))

    # 软件级：经内核加载用户插件与官方扩展（WebUI / OneBot 等在此启动）
    await fw.start()

    wd = Watchdog(limit_mb=_watchdog_limit_mb(config),
                  interval=_watchdog_interval_s(config))
    wd.start()
    task = open_local_port(host, port, 'user')
    logger.info("[user] user 服务已拉起（软件级已启动）")
    return {'name': 'user', 'task': task, 'watchdog': wd, 'port': port}
