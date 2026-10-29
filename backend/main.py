import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import alerts, automation, data, monitoring, terminal, websockets
from app.core.automation import engine as automation_engine
from app.core.config import settings
from app.core.streaming import StreamEngine, engine
from nanoc.core.config import settings as nanoc_settings
from nanoc.core.event_bus import EventBus
from nanoc.memory.memory import Memory

logger = logging.getLogger(__name__)


def _latest_event_id() -> int:
    """
    Highest event id already stored in the nanoc database.

    The event bus starts polling from id 0, which on a restart would replay the
    entire event history to every connected client. Seeding the bus cursor at
    the current maximum makes the stream "from now on", which is what a live
    dashboard wants.

    Returns:
        int: Highest ``events.id``, or 0 for an empty database.
    """
    try:
        with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Wire the event bus to the websocket fan-out and run the live producers.

    A single EventBus subscribes ``"*"`` to broadcast every event as a
    websocket message, and the :class:`StreamEngine` publishes metric, alert and
    data-update events into that same bus on its own cadence. On shutdown both
    tasks are cancelled and awaited.
    """
    memory = Memory(nanoc_settings.DB_PATH)
    bus = EventBus(memory)
    bus.last_seen_id = _latest_event_id()

    async def broadcast_event(payload):
        """
        Forward one event-bus payload to every connected websocket client.

        Parameters:
            payload: Event body, already enriched with ``_topic``/``_event_id``/
                ``_timestamp`` by the bus.
        """
        await websockets.manager.broadcast(websockets.envelope(payload))

    # Subscribe to all relevant topics
    bus.subscribe("*", broadcast_event)

    engine.bind(bus)
    polling_task = asyncio.create_task(bus.start_polling())
    stream_task = asyncio.create_task(engine.run())

    # The automation engine listens on the same bus: rules are matched on the
    # polling loop (non-blocking), actions run on a bounded worker pool, and
    # every run is republished as ``automation/run`` for the /ws fan-out.
    automation_engine.bind_bus(bus)
    automation_topics = automation_engine.subscribe(bus)
    await automation_engine.start()
    logger.info(
        "Automation engine active: %d rule(s) on %s",
        len(automation_engine.rules),
        ", ".join(automation_topics) or "no topics",
    )

    logger.info(
        "NOC live streaming active: %s websocket clients at startup",
        websockets.manager.connection_count,
    )

    yield

    engine.stop()
    await automation_engine.stop()
    for task in (stream_task, polling_task):
        task.cancel()
    for task in (stream_task, polling_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"])
app.include_router(automation.router, prefix="/api/automation", tags=["automation"])


def build_hello_message(stream: StreamEngine) -> dict:
    """
    Build the first message a client receives after connecting.

    It carries the topics the server streams and the last metric snapshot, so a
    dashboard that has just reconnected can repaint immediately instead of
    waiting for the next sampling tick.

    Parameters:
        stream (StreamEngine): Engine holding the cached snapshot.

    Returns:
        dict: ``{"type": "hello", ...}`` websocket message.
    """
    return {
        "type": "hello",
        "topic": "system/hello",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "topics": ["metric", "alert", "data_update", "agent", "automation"],
        "snapshot": stream.last_snapshot,
        "active_alerts": len(stream.active_alerts),
        "auth": "none",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Manage a websocket client connection and keep it registered with the manager.

    Registers the socket, sends a ``hello`` frame describing the stream, then
    stays in a receive loop so the connection lifecycle is observed and ``ping``
    frames are answered with ``pong``. The socket is unregistered on disconnect.

    NOTE: ``/ws`` is unauthenticated (unchanged from before this change) — it
    carries read-only monitoring data. Authentication is tracked as part of the
    security pass; the terminal surface keeps its own ``?token=`` handshake.

    Parameters:
        websocket (WebSocket): The client WebSocket connection to manage.
    """
    await websockets.manager.connect(websocket)
    await websockets.manager.send_to(websocket, build_hello_message(engine))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue  # clients are allowed to send opaque keep-alives
            if isinstance(message, dict) and message.get("type") == "ping":
                await websockets.manager.send_to(
                    websocket,
                    {
                        "type": "pong",
                        "topic": "system/pong",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "echo": message.get("ts"),
                    },
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - never leak a registration
        logger.debug("Websocket closed with error: %s", exc)
    finally:
        websockets.manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    """
    Provide a simple health status for the service.

    Returns:
        dict: JSON-compatible object with `"status": "ok"`.
    """
    return {"status": "ok"}


@app.get("/api/stream/status")
async def stream_status():
    """
    Expose the live stream's health for the dashboard's connection chip.

    Returns:
        dict: Connected client count, last snapshot and active alert count.
    """
    snapshot = engine.last_snapshot
    return {
        "clients": websockets.manager.connection_count,
        "last_snapshot_at": (snapshot or {}).get("timestamp"),
        "active_alerts": len(engine.active_alerts),
        "topics": [
            "metric/snapshot",
            "alert/created",
            "alert/resolved",
            "data/updated",
            "automation/run",
        ],
    }
