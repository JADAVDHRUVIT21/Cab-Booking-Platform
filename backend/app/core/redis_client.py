
import os

from redis.asyncio import Redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://127.0.0.1:6379/0",
)

redis_client = Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)


async def check_redis_connection() -> bool:
    """Check whether Redis is reachable."""
    return bool(await redis_client.ping())