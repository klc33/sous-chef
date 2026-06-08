"""Redis client + readiness ping.

Redis is the cache / session store. There is no product use yet; this phase only proves
reachability for /health.
"""

from __future__ import annotations

import redis


class Cache:
    """Thin holder of a redis client built from the configured URL."""

    def __init__(self, url: str) -> None:
        """Create a redis client; decode_responses so values come back as str, not bytes."""
        self._client: redis.Redis = redis.Redis.from_url(url, decode_responses=True)

    @property
    def client(self) -> redis.Redis:
        """Expose the underlying client for later session-memory helpers."""
        return self._client

    def ping(self) -> bool:
        """Return True when Redis answers PING, else False (no raise)."""
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def close(self) -> None:
        """Close the client's connection pool on shutdown."""
        self._client.close()
