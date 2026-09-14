"""引擎生命周期节点：启动 / 停止 / 启动前安全提示 / 依赖自愈 / 运行时长格式化。

以 ``fw`` 为上下文编排各部件；不含状态。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from core.hooks import HookPoints
from core.kernel.banner import emit_banner
from .plugins import load_extensions
from .watchdogs import heartbeat_loop, memory_watchdog_loop

logger = logging.getLogger("zernus")


def format_uptime(fw) -> str:
    """格式化运行时间"""
    seconds = time.time() - fw._start_time if hasattr(fw, "_start_time") else 0
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if mins > 0:
        parts.append(f"{mins}分钟")
    if secs > 0 or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def warn_insecure_config(fw):
    """启动安全提示（协议中立；各接入端自身的令牌提示由适配器注册时给出）"""
    web_cfg = fw.config.get("web", {})
    web_host = web_cfg.get("host", "0.0.0.0")
    if web_host in ("0.0.0.0", "::"):
        logger.warning(
            "⚠ 安全提示: Web 面板监听 0.0.0.0，公网部署请确认已设置访问凭据，"
            "并按需将 web.host 改为 127.0.0.1。"
        )


def auto_heal_plugin_deps(fw):
    """插件依赖自愈：启动时为缺失依赖的插件尝试自动安装。"""
    cfg = fw.config.get("plugin", {})
    auto_install = cfg.get("auto_install_deps_on_startup", True)
    if not auto_install:
        logger.info("插件依赖自愈已关闭 (plugin.auto_install_deps_on_startup: false)")
        return

    with fw.plugin_loader._lock:
        missing_snapshot = {
            name: list(deps)
            for name, deps in fw.plugin_loader._missing_deps.items()
            if deps
        }
    if not missing_snapshot:
        return

    logger.info(
        f"检测到 {len(missing_snapshot)} 个插件依赖缺失，"
        f"启动自愈流程: {list(missing_snapshot.keys())}"
    )
    for plugin_name, deps in missing_snapshot.items():
        try:
            logger.info(f"[{plugin_name}] 自愈：尝试自动安装缺失依赖: {deps}")
            result = fw.plugin_loader.install_missing_deps(plugin_name)
            if result["success"]:
                if result.get("installed"):
                    logger.info(f"[{plugin_name}] 自愈完成，已安装: {', '.join(result['installed'])}")
                else:
                    logger.info(f"[{plugin_name}] 自愈完成，依赖已满足")
            else:
                failed = result.get("failed", [])
                conflicts = result.get("conflicts", [])
                if failed:
                    logger.warning(
                        f"[{plugin_name}] 自愈部分失败，未能安装: "
                        f"{', '.join(failed)}。请在 Web UI 手动处理。"
                    )
                if conflicts:
                    logger.warning(
                        f"[{plugin_name}] 存在版本冲突（不会自动覆盖全局包），"
                        f"请在 Web UI 创建隔离虚拟环境: "
                        f"{', '.join(c['name'] + ' ' + c['required'] + ' (已安装 ' + c['installed'] + ')' for c in conflicts)}"
                    )
        except Exception as e:
            logger.error(f"[{plugin_name}] 依赖自愈异常: {e}")


async def start(fw):
    """启动引擎（异步）"""
    from core.terminal import register_builtins

    fw.loop = asyncio.get_running_loop()
    fw._running = True

    logger.info("=" * 50)
    logger.info("Zeronus 框架 启动中...")
    logger.info("=" * 50)

    warn_insecure_config(fw)

    # 1. 加载官方插件（extensions/）— 必须最先加载，提供基础服务
    load_extensions(fw)

    # 2. 确保 plugins_dat 目录存在
    os.makedirs(fw.plugin_loader.plugins_dat_dir, exist_ok=True)
    fw.plugin_loader.migrate_legacy_configs()

    # 3. 加载用户插件（plugins/）
    loaded = fw.plugin_loader.load_all()
    fw._loaded_user_plugins = loaded
    logger.info(f"已加载 {len(loaded)} 个用户插件: {loaded}")

    # 3.5 插件依赖自愈
    auto_heal_plugin_deps(fw)
    if hasattr(fw.plugin_loader, "_missing_deps"):
        with fw.plugin_loader._lock:
            healed_candidates = list(fw.plugin_loader._missing_deps.keys())
        for plugin_name in healed_candidates:
            if plugin_name not in loaded and fw.plugin_loader.is_plugin_active_in_db(plugin_name):
                if fw.plugin_loader.load_plugin(plugin_name):
                    loaded.append(plugin_name)
        if loaded:
            logger.info(f"自愈后共加载 {len(loaded)} 个插件: {loaded}")

    # 4. 对每个已加载的插件执行 register
    for plugin_name in loaded:
        fw.plugin_loader.register_commands(plugin_name)

    # 5. 启动路由表后台刷新
    fw.router.start(fw.loop)
    try:
        await asyncio.to_thread(fw.router._rebuild_routes)
    except Exception as e:
        logger.error(f"路由表预热失败: {e}")

    # 6. 启动统计批量写库器
    fw.stats_writer.start()

    # 7. 启动心跳
    fw._heartbeat_task = asyncio.create_task(heartbeat_loop(fw), name="heartbeat")

    # 8. 启动内存看门狗
    fw._memory_watchdog_task = asyncio.create_task(memory_watchdog_loop(fw), name="memory-watchdog")

    # 9. 触发系统事件
    await fw.event_bus.aemit("system.plugin.loaded", {"plugins": loaded})

    # 9.5 触发启动扩展点
    try:
        await fw.hooks.trigger_async(HookPoints.LIFECYCLE_STARTUP)
    except Exception as e:
        logger.error(f"启动扩展点异常: {e}", exc_info=True)

    # 10. 终端命令注册（核心/宿主两个进程都注册）；交互输入只在非宿主进程启动
    register_builtins(fw)
    if getattr(fw, "_role", "standard") != "host":
        fw.terminal.start()

    # 10.5 启动 WebSocket 事件推送服务（若 config.ws.enabled）
    _ws_cfg = fw.config.get('ws') or {}
    if _ws_cfg.get('enabled'):
        try:
            from core.api.ws_events import start_ws_server
            ws_port = int(_ws_cfg.get('port', 6840))
            ws_host = _ws_cfg.get('host', '0.0.0.0')
            ws_events = _ws_cfg.get('events')
            asyncio.ensure_future(
                start_ws_server(fw, ws_host, ws_port, ws_events, fw.loop)
            )
            logger.info(f"WebSocket 事件推送已请求启动: ws://{ws_host}:{ws_port}/ws")
        except Exception as e:
            logger.error(f"WebSocket 事件推送启动失败: {e}")

    # 10.6 启动 gRPC 服务（若 config.grpc.enabled）
    _grpc_cfg = fw.config.get('grpc') or {}
    if _grpc_cfg.get('enabled'):
        try:
            from core.api.grpc import start_grpc_server
            grpc_port = int(_grpc_cfg.get('port', 50051))
            grpc_host = _grpc_cfg.get('host', '0.0.0.0')
            srv = start_grpc_server(fw, grpc_host, grpc_port)
            if srv is not None:
                fw._grpc_server = srv
                logger.info(f"gRPC 服务已请求启动: {grpc_host}:{grpc_port}")
        except Exception as e:
            logger.error(f"gRPC 服务启动失败: {e}")

    emit_banner(
        version=fw._read_version(),
        role=getattr(fw, "_role", "standard"),
        dual=fw.config.get("dual_process", {}) or {},
        project_root=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        core_loaded=getattr(fw, "_loaded_extensions", []),
        user_loaded=getattr(fw, "_loaded_user_plugins", []),
        config=fw.config,
        logger=logger,
    )


async def stop(fw):
    """停止引擎（异步）"""
    logger.info("正在停止框架...")
    fw._running = False

    try:
        await fw.hooks.trigger_async(HookPoints.LIFECYCLE_SHUTDOWN)
    except Exception as e:
        logger.warning(f"关闭扩展点异常: {e}")

    fw.terminal.stop()

    try:
        await fw.stats_writer.stop()
    except Exception as e:
        logger.warning(f"统计写库器停止异常: {e}")

    try:
        await fw.router.stop()
    except Exception as e:
        logger.warning(f"路由表刷新任务停止异常: {e}")

    if fw._heartbeat_task:
        fw._heartbeat_task.cancel()
        try:
            await fw._heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        fw._heartbeat_task = None

    if fw._memory_watchdog_task:
        fw._memory_watchdog_task.cancel()
        try:
            await fw._memory_watchdog_task
        except (asyncio.CancelledError, Exception):
            pass
        fw._memory_watchdog_task = None

    for name in ("ws_server", "web_server", "scheduler"):
        svc = fw.services.get(name)
        if svc is not None:
            try:
                if asyncio.iscoroutinefunction(svc.stop):
                    await svc.stop()
                else:
                    svc.stop()
            except Exception as e:
                logger.warning(f"服务 [{name}] 停止异常: {e}")

    # gRPC 服务优雅关闭（grpc.server.stop 需要 grace 参数）
    grpc_srv = getattr(fw, "_grpc_server", None)
    if grpc_srv is not None:
        try:
            grpc_srv.stop(0)
        except Exception as e:
            logger.warning(f"gRPC 服务关闭异常: {e}")
        fw._grpc_server = None

    try:
        fw._db_executor.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"数据库线程池关闭异常: {e}")

    await fw.event_bus.aemit("system.plugin.unloaded", {})
    logger.info("框架已停止")
