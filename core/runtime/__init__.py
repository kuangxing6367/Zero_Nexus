"""core.runtime —— 内核运行时（在 core.kernel 原语之上的装配/编排层）。

与 ``core.kernel`` 的分工：
- ``core.kernel``：最小原语（event_bus / banner / logging / data_dirs / stats_writer）。
- ``core.runtime``：把这些原语与框架其它部件组装成可运行引擎的编排节点，
  每个节点单一职责、各自暴露清晰 API，接受引擎实例 ``fw`` 作为上下文。

过渡期说明：编排节点会引用尚未迁入 core/ 的 framework 部件（log_broker / hooks /
ctx / messaging 等），待这些部件也逐模块迁入 core/ 后，引用方向将统一为 core→core。
"""
from .context import current_source_var, current_bot_var
from .dispatch import (
    dispatch_event, register_raw_message_handler, unregister_raw_message_handlers,
    dispatch_raw_message_handlers, handle_notice, handle_request,
    sync_group_member_join, sync_group_member_leave,
)
from .plugins import load_extensions, read_plugin_process_tag, core_plugin_is_core_side
from .watchdogs import heartbeat_loop, memory_watchdog_loop
from .lifecycle import start, stop, warn_insecure_config, auto_heal_plugin_deps, format_uptime
from .reply import reply_text, on_message_sent

__all__ = [
    "dispatch_event", "register_raw_message_handler", "unregister_raw_message_handlers",
    "dispatch_raw_message_handlers", "handle_notice", "handle_request",
    "sync_group_member_join", "sync_group_member_leave",
    "load_extensions", "read_plugin_process_tag", "core_plugin_is_core_side",
    "heartbeat_loop", "memory_watchdog_loop",
    "start", "stop", "warn_insecure_config", "auto_heal_plugin_deps", "format_uptime",
    "reply_text", "on_message_sent",
    "current_source_var", "current_bot_var",
]
