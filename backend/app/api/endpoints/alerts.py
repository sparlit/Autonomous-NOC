"""Alert surfaces.

``/summary`` used to be a hard-coded mock: three active alerts and a list of
invented events that could disagree with the alerts the dashboard was actually
streaming. It now reads the **live** rule-engine state
(:attr:`app.core.streaming.StreamEngine.active_alerts`), which is the same
source the ``alert/created`` / ``alert/resolved`` websocket events are derived
from — so the tile, the alert feed and the stream can never disagree.

Both routes are read-only and therefore require ``OPERATOR`` or higher.
"""

import json
import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends

from app.core.auth import Role, require_role
from app.core.config import settings
from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory

logger = logging.getLogger(__name__)

#: Alerts are read-only state: ``OPERATOR`` or higher (open while
#: ``NOC_OPERATOR_TOKEN`` is unset — see :mod:`app.core.auth`).
router = APIRouter(dependencies=[Depends(require_role(Role.OPERATOR))])

#: Topic the event bus uses for gate failures, which are treated as alerts.
GATE_FAILURE_TOPIC = "gate/failed"

#: How many recent events the dashboard tile shows.
RECENT_EVENT_LIMIT = 10

#: Severity -> the status label the summary list renders.
_STATUS_BY_SEVERITY = {"critical": "Firing", "warning": "Investigating"}


def _live_alerts() -> List[Dict[str, Any]]:
    """
    Return the alerts the rule engine currently considers active.

    Imported lazily: :mod:`app.core.streaming` imports this module for its
    producers, so a module-level import would be circular.

    Returns:
        List[Dict[str, Any]]: Active alert records (empty before the first tick).
    """
    try:
        from app.core.streaming import engine as stream_engine

        return list(stream_engine.active_alerts)
    except Exception as exc:  # noqa: BLE001 - a summary must never 500
        logger.debug("Live alert state unavailable: %s", exc)
        return []


def build_alerts_summary() -> Dict[str, Any]:
    """
    Build the dashboard alert summary from live rule-engine state.

    Returns:
        dict: ``active_alerts``, ``urgent`` (critical count), ``warning``,
        ``recent_events`` (newest first) plus ``source`` and
        ``severities`` for provenance.
    """
    active = _live_alerts()
    critical = [alert for alert in active if alert.get("severity") == "critical"]
    warning = [alert for alert in active if alert.get("severity") == "warning"]

    # Newest first: the rule engine stamps every alert with an ISO timestamp.
    ordered = sorted(active, key=lambda alert: str(alert.get("timestamp") or ""), reverse=True)
    recent_events = [
        {
            "time": (str(alert.get("timestamp") or "")[11:19] or "--:--:--"),
            "event": alert.get("title") or alert.get("id"),
            "status": _STATUS_BY_SEVERITY.get(str(alert.get("severity")), "Active"),
            "severity": alert.get("severity"),
            "id": alert.get("id"),
        }
        for alert in ordered[:RECENT_EVENT_LIMIT]
    ]

    return {
        "active_alerts": len(active),
        "urgent": len(critical),
        "warning": len(warning),
        "recent_events": recent_events,
        "severities": {
            "critical": len(critical),
            "warning": len(warning),
            "other": len(active) - len(critical) - len(warning),
        },
        "source": "alert_rules",
    }


async def build_all_alerts() -> Dict[str, Any]:
    """
    Fetch all alerts from Keep, falling back to the live rule engine.

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

            # Fallback 1: the live rule engine (what the dashboard is showing).
            alerts: List[Dict[str, Any]] = _live_alerts()
            if alerts:
                return {"alerts": alerts, "source": "alert_rules"}

            # Fallback 2: local gate failures, which are alert-like events.
            mem = Memory(nanoc_settings.DB_PATH)
            failures = mem.get_events(topic=GATE_FAILURE_TOPIC, since_id=0)

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
    Return the live alert summary (active counts and the most recent events).
    """
    return build_alerts_summary()


@router.get("/all")
async def get_all_alerts():
    """
    Fetch all alerts from Keep or local metrics if Keep is unreachable.
    """
    return await build_all_alerts()
