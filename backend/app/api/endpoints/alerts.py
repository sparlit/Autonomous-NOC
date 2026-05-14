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
    async with httpx.AsyncClient() as client:
        try:
            # Placeholder for Keep API endpoint
            response = await client.get(f"{settings.KEEP_URL}/api/v1/alerts")
            # response.raise_for_status() # Keep might not be up yet
            return {"alerts": []}
        except Exception as e:
            # If Keep is not available, return empty for now to not break frontend
            return {"alerts": [], "error": str(e)}
