"""Shared state for several workers/replicas: the Redis rate limiter (with its fallback),
the report-scheduler leader lock, readiness for load balancers and the HNSW index DDL."""

import fnmatch

import pytest

from backend.app.core.errors import RateLimitError
from backend.app.core.security.rate_limit import (
    RateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)
from backend.app.domains.knowledge.vector_index import create_index_sql, index_name
from backend.app.domains.reporting.scheduler import leader_lock


class FakeRedis:
    """The few commands the limiter uses, shared like one Redis server."""

    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.down = False

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)

    async def decr(self, key: str) -> int:
        self.data[key] = self.data.get(key, 0) - 1
        return self.data[key]

    async def scan_iter(self, match: str):
        for key in list(self.data):
            if fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis, self.ops = redis, []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key))

    def get(self, key):
        self.ops.append(("get", key))

    async def execute(self):
        if self.redis.down:
            raise ConnectionError("redis down")
        out = []
        for op, key in self.ops:
            if op == "incr":
                self.redis.data[key] = self.redis.data.get(key, 0) + 1
                out.append(self.redis.data[key])
            elif op == "expire":
                out.append(True)
            else:
                out.append(self.redis.data.get(key))
        return out


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_limits_are_shared_by_every_process() -> None:
    redis, clock = FakeRedis(), Clock(1000.0)
    # Two API processes (or replicas) pointing at the same Redis.
    a, b = RedisRateLimiter(redis, clock=clock), RedisRateLimiter(redis, clock=clock)
    for limiter in (a, b, a):
        await limiter.hit("login:1.2.3.4", limit=3, window_seconds=60)
    with pytest.raises(RateLimitError):
        await b.hit("login:1.2.3.4", limit=3, window_seconds=60)
    await a.hit("login:5.6.7.8", limit=3, window_seconds=60)  # other keys are independent


async def test_sliding_window_counts_the_overlapping_part_of_the_previous_window() -> None:
    redis, clock = FakeRedis(), Clock(1020.0)  # window 1020–1080 for 60 s buckets
    limiter = RedisRateLimiter(redis, clock=clock)
    for _ in range(4):
        await limiter.hit("k", limit=4, window_seconds=60)
    clock.now = 1095.0  # 15 s into the next bucket: 75% of the previous one still counts
    with pytest.raises(RateLimitError) as refused:
        await limiter.hit("k", limit=4, window_seconds=60)  # 4 × 0.75 + 1 + ... > 4 after 2
        await limiter.hit("k", limit=4, window_seconds=60)
    assert refused.value.headers["Retry-After"] == "45"
    clock.now = 1200.0  # two windows later everything has expired
    await limiter.hit("k", limit=4, window_seconds=60)


async def test_refused_requests_do_not_count() -> None:
    redis, clock = FakeRedis(), Clock(0.0)
    limiter = RedisRateLimiter(redis, clock=clock)
    await limiter.hit("k", limit=1, window_seconds=60)
    for _ in range(5):
        with pytest.raises(RateLimitError):
            await limiter.hit("k", limit=1, window_seconds=60)
    assert redis.data["sovereign:ratelimit:k:60:0"] == 1


async def test_redis_outage_falls_back_to_per_process_limits_not_unlimited() -> None:
    redis, clock = FakeRedis(), Clock(0.0)
    limiter = RedisRateLimiter(redis, clock=clock, fallback=RateLimiter(clock=clock))
    redis.down = True
    await limiter.hit("k", limit=2, window_seconds=60)
    await limiter.hit("k", limit=2, window_seconds=60)
    with pytest.raises(RateLimitError):
        await limiter.hit("k", limit=2, window_seconds=60)
    redis.down = False
    await limiter.hit("k", limit=2, window_seconds=60)  # back on the shared counters


async def test_reset_clears_shared_and_local_counters() -> None:
    redis, clock = FakeRedis(), Clock(0.0)
    limiter = RedisRateLimiter(redis, clock=clock)
    await limiter.hit("k", limit=1, window_seconds=60)
    await limiter.reset("k")
    await limiter.hit("k", limit=1, window_seconds=60)


def test_redis_is_used_only_when_configured() -> None:
    assert isinstance(build_rate_limiter(""), RateLimiter)
    limiter = build_rate_limiter("redis://localhost:6379/0")  # lazy: no connection yet
    assert isinstance(limiter, RedisRateLimiter)


async def test_single_node_databases_are_always_the_scheduler_leader(database) -> None:
    async with leader_lock(database) as leader:
        assert leader is True


async def test_readiness_is_503_when_the_database_is_down(client, app) -> None:
    ok = await client.get("/api/v1/health/ready")
    assert ok.status_code == 200

    async def down() -> bool:
        return False

    app.state.database.ping = down
    failing = await client.get("/api/v1/health/ready")
    assert failing.status_code == 503
    assert (await client.get("/api/v1/health")).status_code == 200  # liveness unaffected


def test_hnsw_index_ddl_is_validated() -> None:
    sql = create_index_sql("nomic-embed-text", 768)
    assert "USING hnsw ((embedding::vector(768)) vector_cosine_ops)" in sql
    assert "WHERE embedding_model = 'nomic-embed-text'" in sql and "CONCURRENTLY" in sql
    assert index_name("nomic-embed-text", 768).startswith("ix_chunks_hnsw_768_")
    with pytest.raises(ValueError):
        create_index_sql("x'; DROP TABLE users; --", 768)
    with pytest.raises(ValueError):
        create_index_sql("bge-m3", 4096)
