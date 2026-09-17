# -*- coding: utf-8 -*-
"""cron 机制包：表达式解析/匹配/next_run 单元测试 + zkg 端到端消费链路。"""
import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL") + f" {name}" + (f" | {extra}" if extra and not cond else ""))


# ── 直接 import 机制包源码 ─────────────────────────────
import importlib.util
spec = importlib.util.spec_from_file_location(
    "cron_tool", os.path.join(ROOT, "repo", "cron", "main.py"))
cron = importlib.util.module_from_spec(spec)
sys.modules["cron_tool"] = spec.loader.exec_module(cron) or cron

# parse / 合法性
f = cron.parse("*/5 0-6 1,15 * 0")
check("parse 通配/区间/步长", f["minute"] == set(range(0, 60, 5))
      and f["hour"] == set(range(0, 7))
      and f["day"] == {1, 15} and f["week"] == {0})
for bad in ["* * * *", "61 * * * *", "* 25 * * *", "*/0 * * * *", "5-1 * * * *", "a * * * *"]:
    try:
        cron.parse(bad)
        check(f"非法表达式拒绝 {bad!r}", False)
    except cron.CronError:
        check(f"非法表达式拒绝 {bad!r}", True)

# match
check("match 整点命中", cron.match("0 * * * *", dt.datetime(2026, 9, 17, 8, 0)))
check("match 非整点不命中", not cron.match("0 * * * *", dt.datetime(2026, 9, 17, 8, 1)))
# 日+周同时受限 = POSIX「或」语义：9/17/2026 是周四（cron week=4）
check("match 日周受限取或(日命中)", cron.match("0 0 17 * 0", dt.datetime(2026, 9, 17)))
check("match 日周受限取或(都不命中不触发)",
      not cron.match("0 0 16 * 0", dt.datetime(2026, 9, 17)))
# Python weekday: 2026-09-17 周四 → cron 4
check("match 周字段转换", cron.match("* * * * 4", dt.datetime(2026, 9, 17)))

# next_run / next_runs
t0 = dt.datetime(2026, 9, 17, 22, 50)
check("next_run 每小时整点", cron.next_run("0 * * * *", t0) == dt.datetime(2026, 9, 17, 23, 0))
check("next_run 每分钟", cron.next_run("* * * * *", t0) == dt.datetime(2026, 9, 17, 22, 51))
runs = cron.next_runs("*/15 * * * *", 3, t0)
check("next_runs 序列", runs == [dt.datetime(2026, 9, 17, 23, 0),
                                 dt.datetime(2026, 9, 17, 23, 15),
                                 dt.datetime(2026, 9, 17, 23, 30)])
try:
    cron.next_run("0 0 30 2 *", t0)   # 2 月没有 30 日
    check("无触发时间抛错", False)
except cron.CronError:
    check("无触发时间抛错", True)

check("describe 可读", "08:30" in cron.describe("30 8 * * *"))

# ── zkg 端到端：demo_cron 声明依赖 cron → loader 安装加载 ──
from service.zkg.loader import Loader

tmp = tempfile.mkdtemp()
ld = Loader(os.path.join(ROOT, "repo"), tmp,
            sources_cfg=[{"id": "local", "type": "local",
                          "path": os.path.join(ROOT, "repo"), "enabled": True}],
            scan_roots=[os.path.join(ROOT, "software", "plugins")])
out = ld.run()
check("demo_cron 依赖触发 cron 安装", "cron" in out["loaded_tools"])
check("cron API 表面可用", callable(ld.loaded_tools()["cron"].next_run)
      and callable(ld.loaded_tools()["cron"].match))

print(f"\n结果: {sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
