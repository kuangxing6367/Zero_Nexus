"""ratelimit —— 令牌桶限速（机制包）。

框架提供的一等公民程序化接口：对外 API 调用、消息发送、
任务提交的速率控制标配，不必各自手写 sleep 打点。

稳定 API 表面：
    TokenBucket(rate, burst=None)
    .try_acquire(n=1) -> bool        立即尝试，不足即返回 False
    .acquire(n=1, timeout=None) -> bool  阻塞等待，超时返回 False
    .tokens -> float                 当前令牌数

设计约定：
- rate 为每秒补充令牌数；burst 为桶容量（缺省 = rate）；
- 纯标准库、线程安全（Lock）；timeout=None 表示无限等待。
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate: float, burst: float | None = None):
        if rate <= 0:
            raise ValueError("rate 必须 > 0")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else rate)
        self._tokens = self.burst
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def try_acquire(self, n: float = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire(self, n: float = 1, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                need = (n - self._tokens) / self.rate
            if deadline is None:
                time.sleep(need)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(need, remaining))
