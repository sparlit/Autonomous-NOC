from fastapi import APIRouter, HTTPException
import httpx
from app.core.config import settings
import json

router = APIRouter()

async def fetch_all_alerts_logic():
    """
    Internal logic to fetch all alerts from Keep or local metrics.
    """
    async with httpx.AsyncClient() as client:
        try:
            # Try to fetch from Keep if URL is configured
            if settings.KEEP_URL and "example.com" not in settings.KEEP_URL:
                response = await client.get(f"{settings.KEEP_URL}/api/v1/alerts", timeout=5.0)
                if response.status_code == 200:
                    return response.json()

            # Fallback: Check local events for gate failures which are like alerts
            from nanoc.memory.memory import Memory
            from nanoc.core.config import settings as nanoc_settings
            mem = Memory(nanoc_settings.DB_PATH)
            failures = mem.get_events(topic="gate/failed", since_id=0)

            alerts = []
            for f in failures:
                payload = json.loads(f['payload'])
                alerts.append({
                    "id": f['id'],
                    "source": "nanoc-internal",
                    "title": f"Gate Failure: {payload.get('type')}",
                    "description": f"Project {payload.get('project_id')} failed at {payload.get('type')} gate.",
                    "severity": "critical",
                    "timestamp": f['timestamp']
                })

            return {"alerts": alerts, "source": "local_fallback"}
        except Exception as e:
            return {"alerts": [], "error": str(e)}

@router.get("/all")
async def get_all_alerts():
    """
    Fetch all alerts from Keep or local metrics if Keep is unreachable.
    """
    return await fetch_all_alerts_logic()

@router.get("/summary")
async def get_alerts_summary():
    """
    Returns a summary of active alerts from Keep and internal state.
    """
    all_alerts_resp = await fetch_all_alerts_logic()
    alerts = all_alerts_resp.get("alerts", [])

    urgent = len([a for a in alerts if (a.get("severity") == "critical" or a.get("severity") == "urgent")])
    warning = len([a for a in alerts if a.get("severity") == "warning"])

    recent = []
    for a in alerts[:5]:
        recent.append({
            "time": str(a.get("timestamp", "")).split("T")[-1][:8] if "T" in str(a.get("timestamp", "")) else "unknown",
            "event": a.get("title", "Unknown Event"),
            "status": "Active"
        })

    return {
        "active_alerts": len(alerts),
        "urgent": urgent,
        "warning": warning,
        "recent_events": recent or [
            {"time": "00:00:00", "event": "No active alerts", "status": "Nominal"}
        ]
    }
