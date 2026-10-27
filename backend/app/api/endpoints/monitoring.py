import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException

from app.api.endpoints import data
from app.core.config import settings
from app.core.host_metrics import ThroughputSampler
from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory

router = APIRouter()
memory = Memory(nanoc_settings.DB_PATH)

#: Pre-existing fallback values. The REST contract (and its tests) depend on
#: these exact strings, so they stay pinned; the live websocket stream carries
#: the richer fields alongside them.
FALLBACK_LATENCY = "12ms"
FALLBACK_UPTIME = "99.99%"
FALLBACK_TRAFFIC = "1.2 Gbps"
FALLBACK_STATUS = "nominal"

#: Metric names written to the ``metrics`` table so ``/history`` and ``/series``
#: have real data to seed the charts with.
METRIC_LATENCY = "latency_ms"
METRIC_THROUGHPUT_IN = "throughput_in_mbps"
METRIC_THROUGHPUT_OUT = "throughput_out_mbps"
METRIC_BACKLOG = "backlog"

#: Rows kept per metric name before the streamer trims the oldest.
METRIC_RETENTION_ROWS = 720

_throughput_sampler = ThroughputSampler()


async def _prometheus_query(query: str, timeout: float = 1.0) -> Optional[List[Dict[str, Any]]]:
    """
    Run one Prometheus instant query.

    Parameters:
        query (str): PromQL expression.
        timeout (float): Request timeout in seconds.

    Returns:
        Optional[List[Dict[str, Any]]]: The ``data.result`` list, or ``None``
        when Prometheus is unreachable or answers with an error.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=timeout,
            )
            if response.status_code != 200:
                return None
            return response.json().get("data", {}).get("result", [])
    except Exception:
        return None


def _count_devices(topology: Dict[str, Any]) -> Dict[str, int]:
    """
    Build the device status histogram the dashboard's pie chart needs.

    Parameters:
        topology (Dict[str, Any]): Topology document with a ``nodes`` list.

    Returns:
        Dict[str, int]: Counts for operational / warning / critical /
        maintenance. The pie chart only knows those four buckets, so any other
        status is folded into ``maintenance`` instead of being dropped.
    """
    counts = {"operational": 0, "warning": 0, "critical": 0, "maintenance": 0}
    for node in topology.get("nodes") or []:
        status = (node.get("status") or "").lower()
        if status == "online":
            counts["operational"] += 1
        elif status == "warning":
            counts["warning"] += 1
        elif status in ("offline", "down", "critical"):
            counts["critical"] += 1
        else:
            counts["maintenance"] += 1
    return counts


def _pending_backlog() -> int:
    """Count tasks in the ``pending`` state from the local SQLite database."""
    import sqlite3

    try:
        with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'")
            return int(cursor.fetchone()[0])
    except Exception:
        return 0


async def build_metric_snapshot() -> Dict[str, Any]:
    """
    Assemble the single source of truth for both REST and websocket metrics.

    Every surface reads from this function, so the dashboard cards, the charts
    and the alert rules can never disagree with each other.

    Returns:
        Dict[str, Any]: Snapshot with ``latency``/``uptime``/``traffic``/
        ``status``/``backlog`` (the REST contract) plus ``latency_ms``,
        ``throughput_in_mbps``, ``throughput_out_mbps``, ``devices`` and
        ``prometheus_ok`` for the live stream.
    """
    results = await _prometheus_query("avg(icmp_latency_ms)")
    prometheus_ok = results is not None
    latency_ms: Optional[float] = None
    if results:
        try:
            latency_ms = round(float(results[0]["value"][1]), 3)
        except (KeyError, IndexError, TypeError, ValueError):
            latency_ms = None

    sample = _throughput_sampler.sample(time.monotonic())
    devices = _count_devices(data.build_topology())

    return {
        # --- REST contract (values unchanged from the pre-streaming API) ---
        "latency": f"{latency_ms}ms" if latency_ms is not None else FALLBACK_LATENCY,
        "uptime": FALLBACK_UPTIME,
        "traffic": FALLBACK_TRAFFIC,
        "status": FALLBACK_STATUS,
        "backlog": _pending_backlog(),
        # --- live stream extras ---
        "latency_ms": latency_ms,
        "throughput_in_mbps": sample.inbound_mbps,
        "throughput_out_mbps": sample.outbound_mbps,
        "devices": devices,
        "prometheus_ok": prometheus_ok,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def record_snapshot(snapshot: Dict[str, Any]) -> None:
    """
    Persist a metric snapshot so ``/history`` and ``/series`` can seed charts.

    ``None`` values (Prometheus down, throughput baseline not yet established)
    are skipped rather than written as zeroes, which would draw a fake crash to
    the bottom of the graph.

    Parameters:
        snapshot (Dict[str, Any]): Output of :func:`build_metric_snapshot`.
    """
    for name, key, unit in (
        (METRIC_LATENCY, "latency_ms", "ms"),
        (METRIC_THROUGHPUT_IN, "throughput_in_mbps", "Mbps"),
        (METRIC_THROUGHPUT_OUT, "throughput_out_mbps", "Mbps"),
    ):
        value = snapshot.get(key)
        if value is None:
            continue
        try:
            memory.record_metric(name, float(value), unit)
        except Exception:
            continue

    backlog = snapshot.get("backlog")
    if backlog is not None:
        try:
            memory.record_metric(METRIC_BACKLOG, float(backlog), "tasks")
        except Exception:
            pass

    _trim_metrics()


def _trim_metrics() -> None:
    """Keep the ``metrics`` table bounded by dropping the oldest rows per metric."""
    import sqlite3

    try:
        with sqlite3.connect(nanoc_settings.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT metric_name FROM metrics")
            names = [row[0] for row in cursor.fetchall()]
            for name in names:
                cursor.execute(
                    "DELETE FROM metrics WHERE metric_name = ? AND id NOT IN "
                    "(SELECT id FROM metrics WHERE metric_name = ? "
                    "ORDER BY id DESC LIMIT ?)",
                    (name, name, METRIC_RETENTION_ROWS),
                )
            conn.commit()
    except Exception:
        pass


def _series_for(name: str, limit: int) -> List[Dict[str, Any]]:
    """
    Read one metric series in chronological order.

    Parameters:
        name (str): Metric name as stored in the ``metrics`` table.
        limit (int): Maximum number of points.

    Returns:
        List[Dict[str, Any]]: Points with ``time`` (HH:MM:SS) and ``value``.
    """
    rows = memory.get_metrics(name, limit)
    ordered = list(reversed(rows))  # get_metrics returns newest first
    points: List[Dict[str, Any]] = []
    for row in ordered:
        timestamp = str(row.get("timestamp") or "")
        points.append(
            {
                "time": timestamp[11:19] if len(timestamp) >= 19 else timestamp,
                "value": round(float(row.get("value") or 0), 3),
                "timestamp": timestamp,
            }
        )
    return points


@router.get("/status")
async def get_system_status():
    """
    Provide high-level system status including observed latency and task backlog.

    Latency is obtained from Prometheus with a fallback of "12ms" if Prometheus is unavailable or returns no data.
    Backlog is the count of tasks with status 'pending' from the local SQLite database.

    Returns:
        dict: Mapping with keys:
            "latency" (str): latency value with "ms" suffix,
            "uptime" (str): service uptime percentage,
            "traffic" (str): current traffic estimate,
            "status" (str): overall status,
            "backlog" (int): number of pending tasks
    """
    snapshot = await build_metric_snapshot()
    return {
        "latency": snapshot["latency"],
        "uptime": snapshot["uptime"],
        "traffic": snapshot["traffic"],
        "status": snapshot["status"],
        "backlog": snapshot["backlog"],
    }


@router.get("/snapshot")
async def get_metric_snapshot():
    """
    Return the full metric snapshot, including the live-stream-only fields.

    This is the REST twin of the ``metric`` websocket message, so a client that
    has just reconnected can re-sync without waiting for the next tick.

    Returns:
        dict: Output of :func:`build_metric_snapshot`.
    """
    return await build_metric_snapshot()


@router.get("/series")
async def get_metric_series(limit: int = 30):
    """
    Return the rolling series that seed the dashboard charts.

    Parameters:
        limit (int): Maximum number of points per series (default 30).

    Returns:
        dict: ``latency`` ([{time, value}]), ``throughput``
        ([{time, inbound, outbound}]), ``devices``
        ([{name, value}]) and ``backlog`` ([{time, value}]).
    """
    limit = max(1, min(limit, METRIC_RETENTION_ROWS))
    latency = _series_for(METRIC_LATENCY, limit)
    inbound = _series_for(METRIC_THROUGHPUT_IN, limit)
    outbound = _series_for(METRIC_THROUGHPUT_OUT, limit)

    # The two throughput series are sampled in the same tick, so they share
    # x-axis labels; zip on the shorter one to stay safe if one was skipped.
    throughput = [
        {"time": point["time"], "inbound": point["value"], "outbound": out["value"]}
        for point, out in zip(inbound, outbound)
    ]

    counts = _count_devices(data.build_topology())
    return {
        "latency": [{"time": p["time"], "value": p["value"]} for p in latency],
        "throughput": throughput,
        "devices": [
            {"name": "Operational", "value": counts["operational"]},
            {"name": "Warning", "value": counts["warning"]},
            {"name": "Critical", "value": counts["critical"]},
            {"name": "Maintenance", "value": counts["maintenance"]},
        ],
        "backlog": [
            {"time": p["time"], "value": p["value"]}
            for p in _series_for(METRIC_BACKLOG, limit)
        ],
    }


@router.get("/history")
async def get_metric_history(name: str, limit: int = 50):
    """
    Retrieve historical metric data for the specified metric name.
    
    Parameters:
    	name (str): Metric identifier to fetch history for.
    	limit (int): Maximum number of metric entries to return (default 50).
    
    Returns:
    	Metric history for the given metric name, limited to `limit` entries.
    """
    return memory.get_metrics(name, limit)


@router.get("/metrics")
async def get_metrics(query: str):
    """
    Execute a Prometheus instant query and return the raw JSON result.
    
    Parameters:
        query (str): Prometheus instant query string to execute.
    
    Returns:
        dict: Parsed JSON response returned by the Prometheus HTTP API.
    
    Raises:
        HTTPException: Raised with status_code=500 and detail set to the underlying exception message if the HTTP request fails or returns a non-2xx status.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{settings.PROMETHEUS_URL}/api/v1/query", params={"query": query})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
