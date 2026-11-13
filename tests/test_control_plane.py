# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面业务内核回归测试（CI 入口）。

CI 的测试是脚本式（文件顶层 sys.exit），逐个执行 tests/test_*.py。
这里调用业务内核自带的 selftest，保证本地与 CI 跑的是同一套断言：
真 SQLite 端到端闭环 + CLI 真跑 + 源码卫生扫描。

    python tests/test_control_plane.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get('PYTHONPATH', ''))

result = subprocess.run(
    [sys.executable, '-m', 'control_plane.selftest'], cwd=ROOT, env=env)
sys.exit(result.returncode)
