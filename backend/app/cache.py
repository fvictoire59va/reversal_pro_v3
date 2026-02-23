"""Redis cache client.

Lazy initialization — the Redis connection is created on first use,
not at import time.
"""

import json
from typing import Optional, Any

import redis.asyncio as redis

from .config import get_settings

_redis_client = None


def get_redis_client():
    """Return the Redis client, creating it lazily on first call."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


# Module-level accessor for backward compatibility.
def __getattr__(name):
    if name == "redis_client":
        return get_redis_client()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


import logging

_cache_logger = logging.getLogger(__name__)


async def cache_get(key: str) -> Optional[Any]:
    """Get cached value.  Returns None if Redis is unavailable."""
    try:
        val = await get_redis_client().get(key)
        if val:
            return json.loads(val)
    except Exception as exc:
        _cache_logger.warning("cache_get(%s) failed: %s", key, exc)
    return None


async def cache_set(key: str, value: Any, ttl: int = None) -> None:
    """Set cache value with optional TTL.  Silently fails if Redis is down."""
    try:
        if ttl is None:
            settings = get_settings()
            ttl = settings.cache_ttl
        await get_redis_client().set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as exc:
        _cache_logger.warning("cache_set(%s) failed: %s", key, exc)


async def cache_delete(pattern: str) -> None:
    """Delete keys matching pattern.  Silently fails if Redis is down."""
    try:
        client = get_redis_client()
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            await client.delete(*keys)
    except Exception as exc:
        _cache_logger.warning("cache_delete(%s) failed: %s", pattern, exc)
