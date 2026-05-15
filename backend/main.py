from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from app.api.endpoints import monitoring, alerts, websockets, data
from app.core.config import settings
import asyncio
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.config import settings as nanoc_settings
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Event Bus to bridge SQLite events to WebSockets
    memory = Memory(nanoc_settings.DB_PATH)
    bus = EventBus(memory)

    async def broadcast_event(payload):
        await websockets.manager.broadcast(payload)

    # Subscribe to all relevant topics
    bus.subscribe("*", broadcast_event)
    polling_task = asyncio.create_task(bus.start_polling())

    yield

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(data.router, prefix="/api/data", tags=["data"])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websockets.manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websockets.manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
