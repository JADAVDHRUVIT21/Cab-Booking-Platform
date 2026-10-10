
import json
import os
import logging

import redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://127.0.0.1:6379/0",
)

# Dedicated synchronous client for synchronous ride endpoints.
ride_event_redis = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


def publish_ride_event(
    *,
    event_type: str,
    ride_id: int,
    rider_id: int,
    driver_id: int | None,
    status: str,
) -> None:
    """Publish a ride lifecycle event to its rider and driver."""
    event = {
        "type": event_type,
        "ride_id": ride_id,
        "rider_id": rider_id,
        "driver_id": driver_id,
        "status": status,
    }

    message = json.dumps(event)

    channels = [f"rider:{rider_id}"]

    if driver_id is not None:
        channels.append(f"driver:{driver_id}")

    for channel in channels:
        try:
            ride_event_redis.publish(channel, message)
        except RedisError:
            # A Redis outage should not undo a committed ride operation.
            logger.exception(
                "Could not publish ride event to Redis channel %s",
                channel,
            )