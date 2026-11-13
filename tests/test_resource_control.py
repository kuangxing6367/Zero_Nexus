# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""统一资源控制面骨架回归测试（CI 入口）。

CI 的测试是脚本式（顶层 sys.exit），逐个执行 tests/test_*.py。
这里直接调用扩展自带的 selftest，保证本地与 CI 跑的是同一套断言。

    python tests/test_resource_control.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELFTEST = os.path.join(
    ROOT, 'software', 'extensions', 'resource_control', 'selftest.py')

result = subprocess.run([sys.executable, SELFTEST], cwd=ROOT)
sys.exit(result.returncode)
