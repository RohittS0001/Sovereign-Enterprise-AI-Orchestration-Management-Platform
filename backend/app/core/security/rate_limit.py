"""Rate limiters with one interface: ``await limiter.hit(key, limit=..., window_seconds=...)``.

- ``RateLimiter``: in-memory sliding window, per API process (single node, tests).
- ``RedisRateLimiter``: shared by every worker and replica (``REDIS_URL``), a sliding-window
  *counter*: an atomic INCR on the current window's bucket plus the previous bucket weighted
  by how much of it still overlaps the window. Redis keeps two small counters per key, the
  check is one round trip, and limits hold across processes. If Redis is unreachable the
  limiter falls back to the in-memory limiter (per process, never unlimited) and logs it.
"""

import asyncio
import contextlib
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from backend.app.core.errors import RateLimitError
from backend.app.core.logging import get_logger
from backend.app.core.observability import RATE_LIMITED

logger = get_logger(__name__)


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
                RATE_LIMITED.inc()
                raise RateLimitError(retry_after)

            hits.append(now)

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._hits.pop(key, None)


class RedisRateLimiter:
    """Shared limits across workers/replicas, with an in-memory fallback when Redis is down."""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "sovereign:ratelimit:",
        clock: Callable[[], float] = time.time,
        fallback: RateLimiter | None = None,
    ) -> None:
        self.client = client
        self.prefix = prefix
        self._clock = clock
        self.fallback = fallback or RateLimiter()
        self._degraded = False

    def _keys(self, key: str, window: int, bucket: int) -> tuple[str, str]:
        base = f"{self.prefix}{key}:{window}:"
        return f"{base}{bucket}", f"{base}{bucket - 1}"

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = self._clock()
        bucket = int(now // window_seconds)
        elapsed = now - bucket * window_seconds
        current_key, previous_key = self._keys(key, window_seconds, bucket)
        try:
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(current_key)
                pipe.expire(current_key, window_seconds * 2)
                pipe.get(previous_key)
                count, _, previous = await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - Redis down: degrade, never fail open
            if not self._degraded:
                logger.warning(
                    "Rate-limit store unavailable; using per-process limits",
                    extra={"error": type(exc).__name__},
                )
                self._degraded = True
            await self.fallback.hit(key, limit=limit, window_seconds=window_seconds)
            return
        if self._degraded:
            logger.info("Rate-limit store reachable again")
            self._degraded = False
        overlap = (window_seconds - elapsed) / window_seconds
        estimated = int(previous or 0) * overlap + int(count)
        if estimated > limit:
            with contextlib.suppress(Exception):  # best effort: a refusal does not count
                await self.client.decr(current_key)
            RATE_LIMITED.inc()
            raise RateLimitError(max(1, math.ceil(window_seconds - elapsed)))

    async def reset(self, key: str) -> None:
        await self.fallback.reset(key)
        try:
            async for name in self.client.scan_iter(match=f"{self.prefix}{key}:*"):
                await self.client.delete(name)
        except Exception:  # noqa: BLE001, S110 - best effort
            pass

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.client.aclose()


def build_rate_limiter(redis_url: str) -> RateLimiter | RedisRateLimiter:
    """The shared Redis limiter when REDIS_URL is set, otherwise the in-process one."""
    if not redis_url:
        return RateLimiter()
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
    return RedisRateLimiter(client)
