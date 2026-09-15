"""
定时任务调度器（官方插件）

薄壳：实例化内核 core.scheduler.TaskScheduler（纯 stdlib cron 实现），
注册为 framework.scheduler 服务。内核不绑定任何第三方调度栈，
本扩展只负责把它挂到事件循环上。
"""
import logging

from core.scheduler import TaskScheduler

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "定时任务调度器",
    "version": "1.0.0",
    "author": "Zeronus",
    "desc": "基于 stdlib cron 的定时任务调度",
    "priority": 0,
    "official": True,
}

_scheduler = None


def register(ctx):
    """注册调度器为官方插件"""
    global _scheduler
    fw = ctx._framework

    sched_cfg = fw.config.get('scheduler', {})
    if sched_cfg.get('enabled') is False:
        ctx.log("调度器已禁用 (scheduler.enabled: false)")
        fw.services.register('scheduler', None)
        return

    _scheduler = TaskScheduler(fw)
    fw.services.register('scheduler', _scheduler)
    _scheduler.start(fw.loop)

    ctx.log("调度器已启动")


def unregister():
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
