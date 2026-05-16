import os
import time
import glob
import asyncio
import threading
from fastapi import FastAPI, Depends, HTTPException, Security, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager

from backend.app.api.endpoints import monitoring, alerts, websockets, data
from backend.app.core.config import settings
from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.agents.base import TeamLeader, Architect, Planner, Coder, Reviewer
from nanoc.core.orchestrator import Orchestrator
from nanoc.agents.governor import Governor

# Shared state
memory = Memory(nanoc_settings.DB_PATH)

def inbox_watcher():
    leader = TeamLeader("Leader", "Team Leader", memory)
    while True:
        try:
            files = glob.glob("nanoc/inbox/*.txt")
            for f in files:
                with open(f, "r") as file:
                    project_desc = file.read()
                print(f"[Inbox] New project detected: {project_desc}")
                # Start the agentic workflow
                asyncio.run(leader.delegate_tasks(project_desc))
                os.remove(f)

            # Update heartbeat for watchdog
            os.makedirs(nanoc_settings.LOGS_DIR, exist_ok=True)
            with open(os.path.join(nanoc_settings.LOGS_DIR, "heartbeat.txt"), "w") as hb:
                hb.write(str(time.time()))
        except Exception as e:
            print(f"[Inbox Error] {e}")

        time.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Core NANOC Components
    leader = TeamLeader("Leader", "Team Leader", memory)
    architect = Architect("Architect", "Architect", memory)
    planner = Planner("Planner", "Planner", memory)
    coder = Coder("Coder", "Coder", memory)
    reviewer = Reviewer("Reviewer", "Reviewer", memory)

    orchestrator = Orchestrator(memory, leader)
    orchestrator.add_agent(leader)
    orchestrator.add_agent(architect)
    orchestrator.add_agent(planner)
    orchestrator.add_agent(coder)
    orchestrator.add_agent(reviewer)

    governor = Governor("SystemGovernor", memory, {})

    # Start Event Bus
    bus = EventBus(memory)
    async def broadcast_event(payload):
        await websockets.manager.broadcast(payload)
    bus.subscribe("*", broadcast_event)

    # Start background loops
    polling_task = asyncio.create_task(bus.start_polling())
    orch_task = asyncio.create_task(orchestrator.run_loop())
    gov_task = asyncio.create_task(governor.run_governance_cycle())

    # Start thread-based watchers
    threading.Thread(target=inbox_watcher, daemon=True).start()

    print("[System] NANOC Core and API started.")

    yield

    polling_task.cancel()
    orch_task.cancel()
    gov_task.cancel()
    try:
        await asyncio.gather(polling_task, orch_task, gov_task, return_exceptions=True)
    except asyncio.CancelledError:
        print("[System] Cleanup complete.")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def get_api_key(api_key_header_value: str = Security(api_key_header)):
    if not api_key_header_value or api_key_header_value != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Could not validate API Key")
    return api_key_header_value

app = FastAPI(title="NANOC Autonomous Operating Center", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"], dependencies=[Depends(get_api_key)])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"], dependencies=[Depends(get_api_key)])
app.include_router(data.router, prefix="/api/data", tags=["data"], dependencies=[Depends(get_api_key)])

@app.get("/")
def read_root():
    return {"status": "NANOC is Running", "inbox_path": os.path.abspath("nanoc/inbox")}

@app.get("/logs", dependencies=[Depends(get_api_key)])
def get_logs(limit: int = 50):
    import sqlite3
    with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return {"logs": [dict(row) for row in rows]}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None)):
    """
    WebSocket endpoint with API Key validation via 'token' query parameter.
    """
    if token != settings.API_KEY:
        print(f"[WebSocket] Unauthorized connection attempt with token: {token}")
        await websocket.accept() # Must accept before closing with code
        await websocket.close(code=1008)
        return

    await websockets.manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websockets.manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
