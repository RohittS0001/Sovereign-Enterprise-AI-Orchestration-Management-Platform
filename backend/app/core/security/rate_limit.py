"""Sliding-window rate limiter.

In-memory, so limits are per API process. That is enough for a single-node deployment;
a Redis-backed limiter with the same interface can replace it when scaling out.
"""

import asyncio
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable

from backend.app.core.errors import RateLimitError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Record one request for ``key``; raise ``RateLimitError`` if over the limit."""
        async with self._lock:
            now = self._clock()
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + window_seconds - now))
                raise RateLimitError(retry_after)

            hits.append(now)

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._hits.pop(key, None)
