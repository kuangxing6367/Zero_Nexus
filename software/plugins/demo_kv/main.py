"""demo_kv —— 演示插件：消费 zkg 机制包的最小完整样例

由 `zkg new demo_kv --deps store` 生成。register(ctx) 是框架加载插件的
唯一入口，所有注册都写在这个函数里；不要在 register 之外执行副作用。
"""


def register(ctx):
    log = ctx.logger

    # 示例：正则命令（收到匹配消息时回复）。pattern / 优先级 / 权限见 docs/writing-plugins.md
    @ctx.command(r"^demo_kv$", handler=_hello)
    def _hello(event):
        kv = ctx.zkg_tool("store")
        if kv is None:
            return "demo_kv 就绪（机制包 store 未加载）。"
        kv.set("last_seen", "demo_kv")
        return f"demo_kv 就绪（KV 写入成功: {kv.get('last_seen')}）。"

    log.info("demo_kv 已注册")
