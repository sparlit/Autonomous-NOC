from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import monitoring, alerts
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])

@app.get("/health")
async def health_check():
    return {"status": "ok"}
