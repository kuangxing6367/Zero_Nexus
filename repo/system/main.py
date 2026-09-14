"""system —— 系统自述接口（机制包）。

框架亲口告诉下游「我跑在什么环境、有哪些能力」，插件不再自己探测
（你点名的第 4 个缺失接口：插件自己去查系统而不是框架用函数告诉你）。

稳定 API 表面：
    get_system_info() -> dict
    capabilities() -> list[str]
"""
from __future__ import annotations

import os
import platform
import socket
import sys


def get_system_info() -> dict:
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python_version": sys.version.split()[0],
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "cpu_count": os.cpu_count(),
        "executable": sys.executable,
    }


def capabilities() -> list:
    """本内核实例当前提供的机制能力清单。"""
    return ["exec", "ws", "udp", "store", "system"]
