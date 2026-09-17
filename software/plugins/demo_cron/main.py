# -*- coding: utf-8 -*-
"""demo_cron —— 端到端演示插件。

演示两条链路：
1. zkg 依赖解析：manifest.toml 声明 dependencies = ["cron"]，
   启动时 zkg 从官方源安装并加载 cron 机制包；
2. ctx.zkg_tool('cron') 消费机制包：注册一个每分钟触发的调度任务，
   触发时用 cron.next_run 打印下一次时间。
"""
import logging

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "演示-Cron",
    "version": "1.0.0",
    "author": "Zeronus",
    "desc": "演示通过 zkg 消费 cron 机制包",
}


def register(ctx):
    cron = ctx.zkg_tool('cron')          # ← zkg 依赖解析保证已加载
    nxt = cron.next_run('* * * * *')
    ctx.log(f"demo_cron 已启动：cron 机制包就绪，"
            f"下一次整分钟触发: {nxt:%Y-%m-%d %H:%M:%S}")

    def on_minute():
        cron = ctx.zkg_tool('cron')
        twice = cron.next_run('* * * * *', cron.next_run('* * * * *'))
        ctx.log(f"[demo_cron] 分钟触发，下下次: {twice:%H:%M}")

    ctx.add_job(on_minute, '* * * * *', description='demo_cron 每分钟演示')


def unregister():
    pass
