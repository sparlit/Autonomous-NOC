import json
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter

from app.core.config import settings
from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory

router = APIRouter()

#: Topic the event bus uses for gate failures, which are treated as alerts.
GATE_FAILURE_TOPIC = "gate/failed"


def build_alerts_summary() -> Dict[str, Any]:
    """
    Provide the dashboard alert summary.

    Returns:
        dict: ``active_alerts``, ``urgent``, ``warning`` and ``recent_events``.
    """
    # Mocking for now
    return {
        "active_alerts": 3,
        "urgent": 2,
        "warning": 1,
        "recent_events": [
            {"time": "14:23:01", "event": "Switch-04 port Gi0/1 bounce", "status": "Resolved"},
            {"time": "14:20:15", "event": "High latency detected in US-EAST", "status": "Investigating"},
            {"time": "14:15:30", "event": "Backup job completed", "status": "Success"}
        ]
    }


async def build_all_alerts() -> Dict[str, Any]:
    """
    Fetch all alerts from Keep, falling back to local gate failures.

    Returns:
        dict: ``{"alerts": [...], "source": ...}`` or ``{"alerts": [], "error": ...}``.
    """
    async with httpx.AsyncClient() as client:
        try:
            # Try to fetch from Keep if URL is configured
            if settings.KEEP_URL and "example.com" not in settings.KEEP_URL:
                response = await client.get(f"{settings.KEEP_URL}/api/v1/alerts", timeout=5.0)
                if response.status_code == 200:
                    return response.json()

            # Fallback: check local events for gate failures, which are alert-like
            mem = Memory(nanoc_settings.DB_PATH)
            failures = mem.get_events(topic=GATE_FAILURE_TOPIC, since_id=0)

            alerts: List[Dict[str, Any]] = []
            for failure in failures:
                payload = json.loads(failure["payload"])
                alerts.append({
                    "id": failure["id"],
                    "source": "nanoc-internal",
                    "title": f"Gate Failure: {payload.get('type')}",
                    "description": f"Project {payload.get('project_id')} failed at {payload.get('type')} gate.",
                    "severity": "critical",
                    "timestamp": failure["timestamp"],
                })

            return {"alerts": alerts, "source": "local_fallback"}
        except Exception as e:
            return {"alerts": [], "error": str(e)}


@router.get("/summary")
async def get_alerts_summary():
    """
    Returns a summary of active alerts from Keep.
    """
    return build_alerts_summary()


@router.get("/all")
async def get_all_alerts():
    """
    Fetch all alerts from Keep or local metrics if Keep is unreachable.
    """
    return await build_all_alerts()
