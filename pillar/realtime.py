"""
Pillar Real-Time Hub — WebSocket pub/sub for instant push notifications.

PillarHub is a channel-based broadcast engine:
  • Channels are arbitrary string identifiers ("chat:room-42", "user:123")
  • Any coroutine can publish to a channel; all connected WebSocket clients
    subscribed to that channel receive the message.
  • Optional Redis pub/sub backend for multi-worker / multi-process setups.

Usage (single-process)::

    from pillar.realtime import hub

    @router.websocket("/ws/chat/{room_id}")
    async def chat_ws(websocket: WebSocket, room_id: str):
        await hub.connect(websocket, channel=f"chat:{room_id}")
        try:
            while True:
                data = await websocket.receive_json()
                await hub.broadcast(f"chat:{room_id}", data)
        except WebSocketDisconnect:
            hub.disconnect(websocket, channel=f"chat:{room_id}")

    # Publish from any handler / background task:
    @router.post("/chat/{room_id}/message")
    async def post_message(room_id: str, body: MessageBody):
        await hub.broadcast(f"chat:{room_id}", {"text": body.text, "from": "server"})
        return {"ok": True}

Redis backend (multi-worker)::

    from pillar.realtime import hub
    hub.use_redis("redis://localhost:6379")

    # Now publish/subscribe is coordinated across all workers via Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional, Set

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

logger = logging.getLogger("pillar.hub")


class _Subscription:
    """One subscriber on one channel."""
    __slots__ = ("websocket", "channel", "queue")

    def __init__(self, websocket: WebSocket, channel: str) -> None:
        self.websocket = websocket
        self.channel   = channel
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)


class PillarHub:
    """
    In-process WebSocket broadcast hub.

    Thread-safe within a single asyncio event loop (all state is only
    ever mutated from coroutines, not threads).

    With ``use_redis()`` enabled, every ``broadcast()`` call also publishes
    to a Redis channel so that worker processes on other machines receive it.
    """

    def __init__(self) -> None:
        # channel → set of subscriptions
        self._subs: Dict[str, Set[_Subscription]] = {}
        self._lock = asyncio.Lock()

        # Optional Redis pubsub
        self._redis_url: Optional[str] = None
        self._redis_pub = None   # aioredis / redis.asyncio publisher
        self._redis_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def use_redis(self, url: str) -> None:
        """
        Enable Redis pub/sub backend.

        Call before the server starts (e.g. in lifespan startup).
        Requires ``pip install redis>=4.2`` (async support included).
        """
        self._redis_url = url

    async def _ensure_redis(self) -> None:
        if self._redis_pub is not None:
            return
        try:
            import redis.asyncio as aioredis
        except ImportError:
            raise RuntimeError(
                "Redis backend requires the 'redis' package: pip install redis>=4.2"
            )
        client = aioredis.from_url(self._redis_url, decode_responses=True)
        self._redis_pub = client
        # Start subscriber task
        self._redis_task = asyncio.create_task(
            self._redis_subscriber(client), name="pillar-hub-redis-sub"
        )
        logger.info("PillarHub Redis backend connected: %s", self._redis_url)

    async def _redis_subscriber(self, client: Any) -> None:
        """Listen to Redis and fan out to local WebSocket subscribers."""
        pubsub = client.pubsub()
        await pubsub.psubscribe("pillar:*")  # pattern subscribe
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "pmessage":
                    continue
                channel: str = msg["channel"].removeprefix("pillar:")
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    data = msg["data"]
                await self._fan_out(channel, data)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.punsubscribe()
            await pubsub.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(
        self,
        websocket: WebSocket,
        channel: str,
        *,
        accept: bool = True,
    ) -> None:
        """
        Subscribe *websocket* to *channel* and optionally accept the connection.

        Starts a background delivery task that drains the subscription queue
        and sends messages to the client.
        """
        if accept:
            await websocket.accept()

        sub = _Subscription(websocket, channel)

        async with self._lock:
            self._subs.setdefault(channel, set()).add(sub)

        if self._redis_url:
            await self._ensure_redis()

        # Start delivery loop in the background
        asyncio.ensure_future(self._deliver(sub))
        logger.debug("WS connected → channel %r (total: %d)", channel, self.subscriber_count(channel))

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        """Remove *websocket* from *channel*. Safe to call from sync code."""
        subs = self._subs.get(channel, set())
        to_remove = [s for s in subs if s.websocket is websocket]
        for s in to_remove:
            subs.discard(s)
        if not subs:
            self._subs.pop(channel, None)
        logger.debug("WS disconnected ← channel %r", channel)

    async def broadcast(self, channel: str, data: Any) -> int:
        """
        Broadcast *data* to all subscribers on *channel*.

        Returns the number of subscribers reached.

        If Redis is configured, also publishes to the Redis channel so that
        other workers relay the message to their local subscribers.
        """
        if self._redis_url:
            await self._ensure_redis()
            payload = json.dumps(data) if not isinstance(data, str) else data
            await self._redis_pub.publish(f"pillar:{channel}", payload)
            return self.subscriber_count(channel)

        return await self._fan_out(channel, data)

    async def broadcast_all(self, data: Any) -> int:
        """Broadcast to every connected subscriber across all channels."""
        total = 0
        for channel in list(self._subs.keys()):
            total += await self.broadcast(channel, data)
        return total

    async def send_to(self, websocket: WebSocket, data: Any) -> None:
        """Send directly to one specific WebSocket (bypasses channels)."""
        await self._safe_send(websocket, data)

    def subscriber_count(self, channel: str) -> int:
        return len(self._subs.get(channel, set()))

    def active_channels(self) -> list[str]:
        return list(self._subs.keys())

    def stats(self) -> dict:
        return {
            "channels": len(self._subs),
            "subscribers": sum(len(v) for v in self._subs.values()),
            "redis": bool(self._redis_url),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fan_out(self, channel: str, data: Any) -> int:
        """Fan out to all local subscribers on *channel*."""
        subs = list(self._subs.get(channel, set()))
        for sub in subs:
            try:
                sub.queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("Dropped message for slow subscriber on %r", channel)
        return len(subs)

    async def _deliver(self, sub: _Subscription) -> None:
        """Drain the subscription queue and write to the WebSocket."""
        try:
            while sub.websocket.client_state == WebSocketState.CONNECTED:
                try:
                    data = await asyncio.wait_for(sub.queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # Send a ping to detect dead connections
                    try:
                        await sub.websocket.send_text("")
                    except Exception:
                        break
                    continue
                await self._safe_send(sub.websocket, data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.disconnect(sub.websocket, sub.channel)

    @staticmethod
    async def _safe_send(ws: WebSocket, data: Any) -> None:
        if ws.client_state != WebSocketState.CONNECTED:
            return
        try:
            if isinstance(data, (dict, list)):
                await ws.send_json(data)
            elif isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_text(str(data))
        except Exception:
            pass

    async def shutdown(self) -> None:
        """Gracefully close all connections and the Redis subscriber."""
        if self._redis_task:
            self._redis_task.cancel()
            try:
                await self._redis_task
            except asyncio.CancelledError:
                pass
        if self._redis_pub:
            await self._redis_pub.aclose()
        # Close all WebSocket connections
        for subs in list(self._subs.values()):
            for sub in list(subs):
                try:
                    await sub.websocket.close()
                except Exception:
                    pass
        self._subs.clear()


# Singleton hub — import and use directly
hub = PillarHub()
