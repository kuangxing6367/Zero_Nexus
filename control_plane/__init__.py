# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面 · 业务内核（独立于 Zeronus 框架）。

这个包只依赖 Python 标准库，可以单独运行、单独测试、单独开源。
Zeronus 只是可选的进程 / HTTP 承载壳，见
``software/extensions/resource_control/``；壳不提供任何业务能力。
"""
from __future__ import annotations

from control_plane.control import ControlPlane
from control_plane.models import AuditEvent, Resource
from control_plane.store import ResourceStore, SqliteBackend

__version__ = '0.2.0'

__all__ = [
    '__version__', 'ControlPlane', 'Resource', 'AuditEvent',
    'ResourceStore', 'SqliteBackend',
]
