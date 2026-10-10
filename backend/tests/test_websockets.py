
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.core.redis_client import redis_client
from app.main import app


client = TestClient(app)


def make_token(user_id: int, role: str) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "exp": int(time.time()) + 300,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def test_rider_websocket_rejects_missing_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/rider/1"):
            pass


def test_rider_websocket_rejects_wrong_user():
    token = make_token(user_id=2, role="rider")

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws/rider/1?token={token}"
        ):
            pass


def test_rider_websocket_receives_redis_event():
    rider_id = 987654
    token = make_token(user_id=rider_id, role="rider")
    channel = f"rider:{rider_id}"

    event = {
        "type": "test_event",
        "message": "Redis WebSocket test",
    }

    async def publish_event():
        """Wait for the WebSocket subscriber, then publish the event."""
        try:
            for _ in range(50):
                subscribers = await redis_client.pubsub_numsub(channel)

                if subscribers and subscribers[0][1] > 0:
                    await redis_client.publish(
                        channel,
                        json.dumps(event),
                    )
                    return True

                await asyncio.sleep(0.1)

            return False
        finally:
            # Cleanup uses the same event loop as publishing.
            await redis_client.delete(channel)

    with client.websocket_connect(
        f"/ws/rider/{rider_id}?token={token}"
    ) as websocket:
        published = asyncio.run(publish_event())

        assert published, (
            "WebSocket did not subscribe to Redis channel"
        )

        received = websocket.receive_json()
        assert received == event
