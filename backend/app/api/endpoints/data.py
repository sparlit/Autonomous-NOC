import copy
from typing import Any, Dict, List

from fastapi import APIRouter

from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory
import sqlite3

router = APIRouter()
memory = Memory(nanoc_settings.DB_PATH)

#: Knowledge key the topology is persisted under.
TOPOLOGY_KNOWLEDGE_KEY = "network_topology"

#: Default graph used when nothing has been persisted yet.
DEFAULT_TOPOLOGY: Dict[str, List[Dict[str, Any]]] = {
    "nodes": [
        {"id": "Core-Rtr-01", "label": "Core Router", "type": "router", "status": "online"},
        {"id": "Dist-Sw-01", "label": "Distribution Switch 1", "type": "switch", "status": "online"},
        {"id": "Dist-Sw-02", "label": "Distribution Switch 2", "type": "switch", "status": "online"},
        {"id": "Access-Sw-01", "label": "Access Switch 1", "type": "switch", "status": "warning"},
    ],
    "edges": [
        {"from": "Core-Rtr-01", "to": "Dist-Sw-01", "label": "10Gbps"},
        {"from": "Core-Rtr-01", "to": "Dist-Sw-02", "label": "10Gbps"},
        {"from": "Dist-Sw-01", "to": "Access-Sw-01", "label": "1Gbps"},
    ],
}

def build_topology() -> Dict[str, Any]:
    """
    Provide the network topology used by the live map.

    Prefers the stored knowledge document and falls back to the default graph.
    Shared by the REST endpoint and the metric/alert snapshot so device counts
    on the dashboard always match what the map draws.

    Returns:
        dict: A mapping with ``nodes`` and ``edges`` lists.
    """
    topology = memory.get_knowledge(TOPOLOGY_KNOWLEDGE_KEY)
    if topology:
        return topology
    # Copy so a caller cannot mutate the module-level default in place.
    return copy.deepcopy(DEFAULT_TOPOLOGY)


@router.get("/topology")
async def get_topology():
    """
    Provide the network topology used by the live map, preferring stored knowledge and falling back to a default graph.
    
    If a stored topology named "network_topology" exists in memory it is returned; otherwise a default topology is returned.
    
    Returns:
        dict: A mapping with two keys:
            - "nodes": list of node dictionaries each containing:
                - "id": node identifier string
                - "label": human-readable label
                - "type": device type (e.g., "router", "switch")
                - "status": operational status (e.g., "online", "warning")
            - "edges": list of edge dictionaries each containing:
                - "from": source node id string
                - "to": destination node id string
                - "label": link descriptor (e.g., bandwidth)
    """
    return build_topology()


@router.get("/agents")
async def get_agents_status():
    """
    Retrieve all agent records from the configured SQLite database.
    
    Returns:
        agents (list[dict]): A list of agent rows where each item is a dict mapping column names to their values.
    """
    with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents")
        return [dict(row) for row in cursor.fetchall()]


@router.get("/tasks")
async def get_tasks(project_id: str = None):
    """
    Retrieve recent task records, optionally filtered by project.
    
    Parameters:
        project_id (str, optional): If provided, returns tasks for this project ordered by `created_at` descending. If omitted, returns the 50 most recent tasks ordered by `created_at` descending.
    
    Returns:
        list[dict]: A list of task rows converted to dictionaries, each representing a task record.
    """
    with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if project_id:
            cursor.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50")
        return [dict(row) for row in cursor.fetchall()]


def data_fingerprint() -> Dict[str, Any]:
    """
    Cheap fingerprint of the data the websocket stream watches for changes.

    The streamer compares this every tick and only emits a ``data_update`` when
    something actually moved, so idle systems stay quiet on the wire.

    Returns:
        dict: ``tasks`` (count + newest update), ``agents`` (count) and
        ``topology`` (node count + a digest of the drawn graph).
    """
    import hashlib
    import json

    topology = build_topology()
    digest = hashlib.sha1(
        json.dumps(topology, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]

    tasks_count = 0
    tasks_updated = None
    agents_count = 0
    try:
        with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), MAX(updated_at) FROM tasks")
            row = cursor.fetchone()
            tasks_count = int(row[0] or 0)
            tasks_updated = str(row[1]) if row[1] else None
            cursor.execute("SELECT COUNT(*) FROM agents")
            agents_count = int(cursor.fetchone()[0] or 0)
    except Exception:
        pass

    return {
        "tasks": f"{tasks_count}:{tasks_updated}",
        "agents": str(agents_count),
        "topology": f"{len(topology.get('nodes') or [])}:{digest}",
    }
