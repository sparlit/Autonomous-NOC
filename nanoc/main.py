from fastapi import FastAPI, BackgroundTasks
import os
import time
import glob
from nanoc.core.config import settings
from nanoc.memory.memory import Memory
from nanoc.agents.base import TeamLeader

app = FastAPI(title="NANOC Dashboard")
memory = Memory(settings.DB_PATH)

@app.get("/")
def read_root():
    return {"status": "NANOC is Running", "inbox_path": os.path.abspath("nanoc/inbox")}

@app.get("/logs")
def get_logs(limit: int = 50):
    # Fetch logs from SQLite
    import sqlite3
    with sqlite3.connect(settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return {"logs": [dict(row) for row in rows]}

def inbox_watcher():
    leader = TeamLeader("Leader", "Team Leader", memory)
    while True:
        files = glob.glob("nanoc/inbox/*.txt")
        for f in files:
            with open(f, "r") as file:
                project_desc = file.read()
            # Start the agentic workflow
            print(f"New project detected: {project_desc}")
            import asyncio
            asyncio.run(leader.delegate_tasks(project_desc))
            # delete file after processing or move to 'processed'
            os.remove(f)

        # Update heartbeat for watchdog
        with open("nanoc/logs/heartbeat.txt", "w") as hb:
            hb.write(str(time.time()))

        time.sleep(10)

@app.on_event("startup")
async def startup_event():
    import threading
    from nanoc.core.orchestrator import Orchestrator
    from nanoc.agents.base import TeamLeader, Architect, Planner, Coder, Reviewer

    # Initialize Agents
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

    # Background threads
    threading.Thread(target=inbox_watcher, daemon=True).start()

    from nanoc.agents.governor import Governor
    governor = Governor("SystemGovernor", memory, {})

    def run_governor():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(governor.run_governance_cycle())

    threading.Thread(target=run_governor, daemon=True).start()

    def run_orchestrator():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(orchestrator.run_loop())

    threading.Thread(target=run_orchestrator, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
