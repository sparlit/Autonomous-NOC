from fastapi import APIRouter, HTTPException
import httpx
from app.core.config import settings

router = APIRouter()

@router.get("/status")
async def get_system_status():
    """
    Returns high-level system status by querying Prometheus.
    """
    # Mocking for now, will implement actual Prometheus queries
    return {
        "latency": "12ms",
        "uptime": "99.99%",
        "traffic": "1.2 Gbps",
        "status": "nominal"
    }

@router.get("/metrics")
async def get_metrics(query: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{settings.PROMETHEUS_URL}/api/v1/query", params={"query": query})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
