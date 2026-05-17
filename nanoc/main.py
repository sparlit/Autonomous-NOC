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

from pydantic import BaseModel
class TaskRequest(BaseModel):
    description: str

@app.post("/inbox")
async def post_inbox(task: TaskRequest):
    # Create a project file for the inbox_watcher to pick up
    timestamp = int(time.time())
    filename = f"nanoc/inbox/api_task_{timestamp}.txt"
    with open(filename, "w") as f:
        f.write(task.description)
    return {"status": "Task queued via API", "file": filename}

def inbox_watcher():
    from nanoc.agents.base import ProjectManager
    # Hierarchical Team Setup
    # PM -> 2 Team Leaders -> 3 Members each
    team_leaders = ["NetOps_Lead", "DevOps_Lead"]
    pm = ProjectManager("PM_Agent", memory, team_leaders)

    # In this environment, we still use TeamLeader directly for simplicity in inbox_watcher
    # but we can wrap it to use PM logic if the task is complex.
    while True:
        files = glob.glob("nanoc/inbox/*.txt")
        for f in files:
            with open(f, "r") as file:
                project_desc = file.read()
            # Start the agentic workflow
            print(f"New project detected: {project_desc}")
            import asyncio
            if "Step 1:" in project_desc:
                # Use PM for complex 8-step tasks
                asyncio.run(pm.manage_project(project_desc))
            else:
                leader = TeamLeader("Leader", "Team Leader", memory)
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
    from nanoc.agents.governor import Governor
    from nanoc.agents.security import SecurityAgent
    from nanoc.agents.healer import AutoHealer

    # Initialize Teams with 3 members + 1 leader each as requested
    # Team 1: NetOps
    netops_members = ["Net_Architect", "Net_Coder", "Net_Reviewer"]
    netops_lead = TeamLeader("NetOps_Lead", "NetOps Leader", memory, members=netops_members)

    # Team 2: DevOps
    devops_members = ["Dev_Architect", "Dev_Coder", "Dev_Reviewer"]
    devops_lead = TeamLeader("DevOps_Lead", "DevOps Leader", memory, members=devops_members)

    # Global shared agents for default fallback
    leader = TeamLeader("Leader", "Team Leader", memory)
    architect = Architect("Architect", "Architect", memory)
    planner = Planner("Planner", "Planner", memory)
    coder = Coder("Coder", "Coder", memory)
    reviewer = Reviewer("Reviewer", "Reviewer", memory)
    security_agent = SecurityAgent("SecurityAgent", memory)
    healer = AutoHealer("AutoHealer", memory)

    orchestrator = Orchestrator(memory, leader)
    # Register all hierarchical agents
    orchestrator.add_agent(netops_lead)
    orchestrator.add_agent(devops_lead)
    # Register individual member roles to handle their tasks
    orchestrator.add_agent(Architect("Net_Architect", "Architect", memory))
    orchestrator.add_agent(Coder("Net_Coder", "Coder", memory))
    orchestrator.add_agent(Reviewer("Net_Reviewer", "Reviewer", memory))
    orchestrator.add_agent(Architect("Dev_Architect", "Architect", memory))
    orchestrator.add_agent(Coder("Dev_Coder", "Coder", memory))
    orchestrator.add_agent(Reviewer("Dev_Reviewer", "Reviewer", memory))

    orchestrator.add_agent(leader)
    orchestrator.add_agent(architect)
    orchestrator.add_agent(planner)
    orchestrator.add_agent(coder)
    orchestrator.add_agent(reviewer)
    orchestrator.add_agent(security_agent)
    orchestrator.add_agent(healer)

    # Background threads
    threading.Thread(target=inbox_watcher, daemon=True).start()

    # Start Maintainer
    from nanoc.core.maintainer import main as maintainer_main
    threading.Thread(target=maintainer_main, daemon=True).start()

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

        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        bus.subscribe("task/failed", healer.handle_failure)

        # We need to run the polling in the same loop as the orchestrator
        async def combined_loop():
            asyncio.create_task(bus.start_polling())
            await orchestrator.run_loop()

        loop.run_until_complete(combined_loop())

    threading.Thread(target=run_orchestrator, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
