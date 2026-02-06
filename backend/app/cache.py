"""Redis cache client."""

import json
from typing import Optional, Any

import redis.asyncio as redis

from .config import get_settings

settings = get_settings()

redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def cache_get(key: str) -> Optional[Any]:
    """Get cached value."""
    val = await redis_client.get(key)
    if val:
        return json.loads(val)
    return None


async def cache_set(key: str, value: Any, ttl: int = None) -> None:
    """Set cache value with optional TTL."""
    if ttl is None:
        ttl = settings.cache_ttl
    await redis_client.set(key, json.dumps(value, default=str), ex=ttl)


async def cache_delete(pattern: str) -> None:
    """Delete keys matching pattern."""
    keys = []
    async for key in redis_client.scan_iter(match=pattern):
        keys.append(key)
    if keys:
        await redis_client.delete(*keys)
