from fastapi import APIRouter
from nanoc.memory.memory import Memory
from nanoc.core.config import settings as nanoc_settings
import sqlite3

router = APIRouter()
memory = Memory(nanoc_settings.DB_PATH)

@router.get("/topology")
async def get_topology():
    """
    Returns the network topology for the live map.
    Integrates with knowledge base for agent-discovered data.
    """
    topology = memory.get_knowledge("network_topology")
    if topology:
        return topology

    # Default fallback
    nodes = [
        {"id": "Core-Rtr-01", "label": "Core Router", "type": "router", "status": "online"},
        {"id": "Dist-Sw-01", "label": "Distribution Switch 1", "type": "switch", "status": "online"},
        {"id": "Dist-Sw-02", "label": "Distribution Switch 2", "type": "switch", "status": "online"},
        {"id": "Access-Sw-01", "label": "Access Switch 1", "type": "switch", "status": "warning"},
    ]
    edges = [
        {"from": "Core-Rtr-01", "to": "Dist-Sw-01", "label": "10Gbps"},
        {"from": "Core-Rtr-01", "to": "Dist-Sw-02", "label": "10Gbps"},
        {"from": "Dist-Sw-01", "to": "Access-Sw-01", "label": "1Gbps"},
    ]
    return {"nodes": nodes, "edges": edges}

@router.get("/agents")
async def get_agents_status():
    with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents")
        return [dict(row) for row in cursor.fetchall()]

@router.get("/tasks")
async def get_tasks(project_id: str = None):
    with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if project_id:
            cursor.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50")
        return [dict(row) for row in cursor.fetchall()]
