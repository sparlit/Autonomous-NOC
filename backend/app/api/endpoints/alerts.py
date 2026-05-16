from fastapi import APIRouter, HTTPException
import httpx
from app.core.config import settings

router = APIRouter()

@router.get("/summary")
async def get_alerts_summary():
    """
    Returns a summary of active alerts from Keep.
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

@router.get("/all")
async def get_all_alerts():
    """
    Fetch all alerts from Keep or local metrics if Keep is unreachable.
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
            # Note: backend might need its own way to access nanoc DB
            mem = Memory(nanoc_settings.DB_PATH)
            failures = mem.get_events(topic="gate/failed", since_id=0)

            alerts = []
            for f in failures:
                import json
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
