"""cron —— cron 表达式解析/匹配/下次触发时间（机制包）。

纯逻辑、零依赖、自包含：zkg 包不 import 框架内核，任何插件通过
``ctx.zkg_tool('cron')`` 拿到本模块后即可使用。

稳定 API 表面：
    parse(expr) -> dict                 # 解析为各字段集合（校验合法性）
    match(expr, dt=None) -> bool        # 某时刻是否命中
    next_run(expr, after=None) -> datetime   # 下一次触发时间
    next_runs(expr, count, after=None) -> list  # 往后 count 次
    describe(expr) -> str               # 人类可读描述

表达式：5 段「分 时 日 月 周」，支持 * , - / 与数字；
星号为 *；周字段 0-6（0=周日）。不支持 @daily 等别名与秒级精度（保持极简）。
"""
from __future__ import annotations

import datetime as _dt

_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sunday)
]
_FIELD_NAMES = ["minute", "hour", "day", "month", "week"]


class CronError(ValueError):
    """表达式非法。"""


def _parse_field(raw: str, min_v: int, max_v: int) -> set:
    values = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"空字段片段: {raw!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"步长非法: {step_s!r}")
            if step < 1:
                raise CronError(f"步长必须 >= 1: {step}")
        if part == "*" or part == "":
            start, end = min_v, max_v
        elif "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"区间非法: {part!r}")
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise CronError(f"字段值非法: {part!r}")
        if not (min_v <= start <= max_v and min_v <= end <= max_v):
            raise CronError(f"字段越界 [{min_v}-{max_v}]: {part!r}")
        if start > end:
            raise CronError(f"区间起点大于终点: {part!r}")
        values.update(range(start, end + 1, step))
    return values


def parse(expr: str) -> dict:
    """解析 5 段 cron 表达式，返回 {minute:set, hour:set, day:set, month:set, week:set}。"""
    if not isinstance(expr, str) or not expr.strip():
        raise CronError("表达式为空")
    parts = expr.split()
    if len(parts) != 5:
        raise CronError(f"表达式必须为 5 段（分 时 日 月 周）: {expr!r}")
    return {name: _parse_field(p, lo, hi)
            for name, p, (lo, hi) in zip(_FIELD_NAMES, parts, _FIELD_RANGES)}


def match(expr: str, dt: _dt.datetime = None) -> bool:
    """给定时刻（默认当前本地时间）是否命中该表达式。"""
    dt = dt or _dt.datetime.now()
    f = parse(expr)
    if dt.minute not in f["minute"] or dt.hour not in f["hour"] \
            or dt.month not in f["month"]:
        return False
    # 日与周同时受限时为「或」语义（POSIX cron 惯例）
    dom_restricted = f["day"] != set(range(1, 32))
    dow_restricted = f["week"] != set(range(0, 7))
    dom_ok = dt.day in f["day"]
    dow_ok = ((dt.weekday() + 1) % 7) in f["week"]   # Python 周一=0 → cron 0=周日
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_run(expr: str, after: _dt.datetime = None) -> _dt.datetime:
    """after（默认现在）之后的下一次触发时间；一年内未命中抛 CronError。"""
    after = after or _dt.datetime.now()
    t = (after + _dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = after + _dt.timedelta(days=366)
    while t <= limit:
        if match(expr, t):
            return t
        t += _dt.timedelta(minutes=1)
    raise CronError(f"一年内无触发时间: {expr!r}")


def next_runs(expr: str, count: int, after: _dt.datetime = None) -> list:
    """after 之后的 count 次触发时间。"""
    out = []
    cursor = after or _dt.datetime.now()
    for _ in range(max(0, int(count))):
        t = next_run(expr, cursor)
        out.append(t)
        cursor = t
    return out


def describe(expr: str) -> str:
    """人类可读的一句话描述（尽力而为）。"""
    f = parse(expr)
    month = "" if f["month"] == set(range(1, 12 + 1)) \
        else f" {sorted(f['month'])} 月"
    day = "每天" if f["day"] == set(range(1, 31 + 1)) \
        else f"{sorted(f['day'])} 日"
    return f"{month} {day} {min(f['hour']):02d}:{min(f['minute']):02d} 触发".strip()
