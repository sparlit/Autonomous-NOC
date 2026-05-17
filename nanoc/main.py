from fastapi import FastAPI, BackgroundTasks
import os
import time
import glob
from nanoc.core.config import settings
from nanoc.memory.memory import Memory
from nanoc.agents.base import TeamLeader, ProjectManager

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
    pm = ProjectManager("PM", memory)
    while True:
        files = glob.glob("nanoc/inbox/*.txt")
        for f in files:
            with open(f, "r") as file:
                project_desc = file.read()
            # Start the agentic workflow via PM
            print(f"New project detected by CEO: {project_desc}")
            import asyncio
            # Simplified delegation through TL for now
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
    from nanoc.agents.base import TeamLeader, Architect, Planner, Coder, Reviewer, ProjectManager, SubAgent
    from nanoc.agents.governor import Governor
    from nanoc.agents.security import SecurityAgent
    from nanoc.agents.healer import AutoHealer

    # Initialize Agents
    pm = ProjectManager("PM", memory)
    leader = TeamLeader("Leader", "Team Leader", memory)
    architect = Architect("Architect", "Architect", memory)
    planner = Planner("Planner", "Planner", memory)
    coder = Coder("Coder", "Coder", memory)
    reviewer = Reviewer("Reviewer", "Reviewer", memory)
    security_agent = SecurityAgent("SecurityAgent", memory)
    healer = AutoHealer("AutoHealer", memory)

    orchestrator = Orchestrator(memory, leader)
    orchestrator.add_agent(pm)
    orchestrator.add_agent(leader)
    orchestrator.add_agent(architect)
    orchestrator.add_agent(planner)
    orchestrator.add_agent(coder)
    orchestrator.add_agent(reviewer)
    orchestrator.add_agent(security_agent)
    orchestrator.add_agent(healer)

    # Initialize 3 Agents + 6 Sub-agents for the main team
    for i in range(1, 4):
        agent = Coder(f"Agent_{i}", "Coder", memory)
        orchestrator.add_agent(agent)
    for i in range(1, 7):
        sub_agent = SubAgent(f"SubAgent_{i}", "Worker", memory)
        orchestrator.add_agent(sub_agent)

    # Background threads
    threading.Thread(target=inbox_watcher, daemon=True).start()

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
