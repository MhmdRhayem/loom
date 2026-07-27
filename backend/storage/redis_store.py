from __future__ import annotations

import redis.asyncio as redis


class RedisStore:
    """Owns the async Redis client's lifecycle.

    Deliberately thin: Redis is used for ephemeral counters (the storefront's login
    lockout and per-account chat rate limit), which the consumer drives through
    `.client` directly. Postgres is the system of record, so there is nothing here
    that caches or duplicates it.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: redis.Redis | None = None

    async def open(self) -> None:
        if self._client is not None:
            return
        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisStore is not open; call await store.open() first")
        return self._client
