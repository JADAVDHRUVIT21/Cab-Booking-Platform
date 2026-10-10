
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from starlette.websockets import WebSocketState

from app.core.config import settings
from app.core.redis_client import redis_client


router = APIRouter(tags=["Real-Time Updates"])


async def _authenticate_websocket(
    websocket: WebSocket,
    expected_user_id: int,
    expected_role: str,
) -> bool:
    """Authenticate a WebSocket connection using a JWT query parameter."""
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(
            code=1008,
            reason="Authentication required",
        )
        return False

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        token_user_id = int(payload.get("sub", "0"))
        token_role = str(payload.get("role", "")).upper()

        if (
            token_user_id != expected_user_id
            or token_role != expected_role
        ):
            await websocket.close(
                code=1008,
                reason="Not authorized for this connection",
            )
            return False

    except (JWTError, TypeError, ValueError):
        await websocket.close(
            code=1008,
            reason="Invalid or expired token",
        )
        return False

    return True


async def _stream_channel(
    websocket: WebSocket,
    channel: str,
) -> None:
    """Forward Redis Pub/Sub events to a WebSocket client."""
    await websocket.accept()

    try:
        async with redis_client.pubsub() as pubsub:
            await pubsub.subscribe(channel)

            async def receive_messages():
                while True:
                    await websocket.receive_text()

            async def forward_events():
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )

                    if message and message.get("data") is not None:
                        data = message["data"]

                        try:
                            event = json.loads(data)
                        except (TypeError, json.JSONDecodeError):
                            event = {
                                "type": "message",
                                "data": data,
                            }

                        await websocket.send_json(event)

                    await asyncio.sleep(0.01)

            receiver = asyncio.create_task(receive_messages())
            sender = asyncio.create_task(forward_events())
            tasks = {receiver, sender}

            try:
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                results = await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

                for result in results:
                    if isinstance(
                        result,
                        (WebSocketDisconnect, asyncio.CancelledError),
                    ):
                        continue

                    if isinstance(result, BaseException):
                        raise result

            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()

                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

    except WebSocketDisconnect:
        pass

    finally:
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass


@router.websocket("/ws/rider/{rider_id}")
async def rider_websocket(
    websocket: WebSocket,
    rider_id: int,
):
    authenticated = await _authenticate_websocket(
        websocket,
        expected_user_id=rider_id,
        expected_role="RIDER",
    )

    if not authenticated:
        return

    await _stream_channel(
        websocket,
        f"rider:{rider_id}",
    )


@router.websocket("/ws/driver/{driver_id}")
async def driver_websocket(
    websocket: WebSocket,
    driver_id: int,
):
    authenticated = await _authenticate_websocket(
        websocket,
        expected_user_id=driver_id,
        expected_role="DRIVER",
    )

    if not authenticated:
        return

    await _stream_channel(
        websocket,
        f"driver:{driver_id}",
    )