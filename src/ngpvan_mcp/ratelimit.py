"""Client-side rate limiting and 429 backoff.

VAN limits are per API key and shared across every endpoint, so one chatty
assistant can starve the organization's other integrations. The bucket keeps
this server under a configured request rate; the backoff handles the 429s
that still happen when something else is using the same key.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Awaitable, Callable

import anyio

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

# VAN sends no Retry-After header; the wait is only in the error text,
# e.g. "API rate limit exceeded. Try again in 512 ms."
_RETRY_IN = re.compile(r"try again in\s+(\d+)\s*ms", re.IGNORECASE)


class TokenBucket:
    """Allows `rate` requests per second with bursts up to `burst`."""

    def __init__(
        self,
        rate: float,
        burst: int | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = anyio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = float(burst if burst is not None else max(1, int(rate)))
        self.tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = anyio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    async def acquire(self) -> float:
        """Take one token, sleeping until one is free. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return waited
                delay = (1 - self.tokens) / self.rate
                waited += delay
                await self._sleep(delay)


def retry_delay(attempt: int, error_text: str | None, base: float = 0.5, cap: float = 8.0) -> float:
    """Seconds to wait before retry number `attempt` (0-based).

    Uses VAN's own hint when the error text has one, otherwise exponential
    backoff with jitter.
    """
    if error_text:
        match = _RETRY_IN.search(error_text)
        if match:
            return min(cap, int(match.group(1)) / 1000 + 0.05)
    exp = min(cap, base * (2**attempt))
    return exp * (0.5 + random.random() / 2)
