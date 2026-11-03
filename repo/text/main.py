"""text —— 文本处理工具（机制包）。

框架提供的一等公民程序化接口：日志/消息截断、敏感信息掩码、
显示宽度计算（中文占 2 列）、slug 生成，不必各处重复实现。

稳定 API 表面：
    width(s) -> int                      显示宽度（东亚宽字符算 2）
    truncate(s, limit, suffix="…") -> str 按显示宽度截断
    mask(s, keep_head=0, keep_tail=0, char="*") -> str
    slugify(s) -> str                    URL 友好 slug（ASCII）

设计约定：纯标准库（unicodedata + re），不改动传入字符串。
"""
from __future__ import annotations

import re
import unicodedata


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width(s: str) -> int:
    """返回字符串显示宽度（组合符 0，东亚宽/全角 2，其余 1）。"""
    return sum(_char_width(ch) for ch in s)


def truncate(s: str, limit: int, suffix: str = "…") -> str:
    """按显示宽度截断 s，超长时追加 suffix。"""
    if width(s) <= limit:
        return s
    room = max(0, limit - width(suffix))
    out, w = [], 0
    for ch in s:
        cw = _char_width(ch)
        if w + cw > room:
            break
        out.append(ch)
        w += cw
    return "".join(out) + suffix


def mask(s: str, keep_head: int = 0, keep_tail: int = 0, char: str = "*") -> str:
    """保留首尾若干字符，中间用 char 替换（敏感信息脱敏）。"""
    n = len(s)
    if keep_head + keep_tail >= n:
        return s
    tail = s[n - keep_tail:] if keep_tail else ""
    return s[:keep_head] + char * (n - keep_head - keep_tail) + tail


def slugify(s: str) -> str:
    """转为 URL 友好 slug：非 ASCII 去除、空格/下划线转连字符、小写。"""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)
