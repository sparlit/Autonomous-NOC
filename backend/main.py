from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from app.api.endpoints import monitoring, alerts, websockets, data, terminal
from app.core.config import settings
import asyncio
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.config import settings as nanoc_settings
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Event Bus to bridge SQLite events to WebSockets
    """
    Manage the application lifespan by starting an EventBus backed by SQLite and wiring its events to WebSocket broadcasts.
    
    Initializes Memory using nanoc_settings.DB_PATH, creates an EventBus, subscribes a broadcast handler to all topics, and starts the EventBus polling loop as a background task for the app lifetime. On shutdown, cancels the polling task and awaits its completion, suppressing asyncio.CancelledError.
    """
    memory = Memory(nanoc_settings.DB_PATH)
    bus = EventBus(memory)

    async def broadcast_event(payload):
        """
        Broadcasts a payload to all connected WebSocket clients.
        
        Parameters:
            payload: The message to send to connected WebSocket clients (format depends on the WebSocket manager).
        """
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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Manage a WebSocket client connection and keep it registered with the connection manager.
    
    Registers the provided WebSocket with the application's connection manager, awaits incoming messages to maintain the connection lifecycle, and unregisters the socket from the manager when the client disconnects.
    
    Parameters:
        websocket (WebSocket): The client WebSocket connection to manage.
    """
    await websockets.manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websockets.manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    """
    Provide a simple health status for the service.
    
    Returns:
        dict: JSON-compatible object with `"status": "ok"`.
    """
    return {"status": "ok"}
