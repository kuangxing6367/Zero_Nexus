"""retry —— 通用重试/指数退避（机制包）。

框架提供的一等公民程序化接口：网络调用、外部服务请求的标配，
不再各自手写 for 循环 + time.sleep。

稳定 API 表面：
    retry(fn, *, attempts=3, base_delay=0.5, max_delay=10.0,
          exceptions=(Exception,), backoff=2.0, jitter=0.1) -> Any
    retryable(**同上) -> 装饰器
    RetryExhausted 异常（attempts 次全败后抛出，.last 为最后一次异常）

设计约定：
- 只对 exceptions 白名单内的异常重试，其他异常立即穿透；
- 第 n 次失败后等待 min(base_delay * backoff**(n-1), max_delay) + 抖动；
- jitter 为比例（0~1），实际延迟在 [delay*(1-j), delay*(1+j)] 内随机。
"""
from __future__ import annotations

import functools
import random
import time
from typing import Callable, Tuple, Type


class RetryExhausted(Exception):
    """重试次数耗尽。.last 为最后一次触发的异常对象。"""

    def __init__(self, fn_name: str, attempts: int, last: BaseException):
        self.last = last
        super().__init__(f"{fn_name} 重试 {attempts} 次仍失败: {last}")


def retry(fn: Callable, *, attempts: int = 3, base_delay: float = 0.5,
          max_delay: float = 10.0, exceptions: Tuple[Type[BaseException], ...] = (Exception,),
          backoff: float = 2.0, jitter: float = 0.1):
    """执行 fn(...)，失败按白名单与退避策略重试。attempts=1 等价直接调用。"""
    if attempts < 1:
        raise ValueError("attempts 必须 >= 1")
    last = None
    for i in range(attempts):
        try:
            return fn()
        except exceptions as e:
            last = e
            if i == attempts - 1:
                break
            delay = min(base_delay * (backoff ** i), max_delay)
            if jitter > 0:
                delay *= 1 + random.uniform(-jitter, jitter)
            time.sleep(max(0.0, delay))
    raise RetryExhausted(getattr(fn, "__name__", "fn"), attempts, last)


def retryable(*, attempts: int = 3, base_delay: float = 0.5,
              max_delay: float = 10.0,
              exceptions: Tuple[Type[BaseException], ...] = (Exception,),
              backoff: float = 2.0, jitter: float = 0.1):
    """装饰器版 retry。用法：@retryable(attempts=5, exceptions=(OSError,))"""
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            return retry(lambda: fn(*args, **kw),
                         attempts=attempts, base_delay=base_delay,
                         max_delay=max_delay, exceptions=exceptions,
                         backoff=backoff, jitter=jitter)
        return wrapper
    return deco
