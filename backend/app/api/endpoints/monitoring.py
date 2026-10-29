import asyncio
import logging
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.api.endpoints import data
from app.core import alert_rules
from app.core.auth import Role, require_role
from app.core.config import settings
from app.core.host_metrics import ThroughputSampler
from nanoc.core.config import settings as nanoc_settings
from nanoc.memory.memory import Memory

#: Monitoring is read-only telemetry: ``OPERATOR`` or higher. While
#: ``NOC_OPERATOR_TOKEN`` is unset the surface stays open for local development
#: (see :mod:`app.core.auth`).
router = APIRouter(dependencies=[Depends(require_role(Role.OPERATOR))])

logger = logging.getLogger(__name__)

memory = Memory(nanoc_settings.DB_PATH)

#: Label used whenever a value genuinely cannot be obtained. Deliberately not a
#: plausible-looking number: a fake "99.99%" reads as live data and hides an
#: outage, which is exactly what this endpoint used to do.
UNAVAILABLE = "unknown"

#: Metric names written to the ``metrics`` table so ``/history`` and ``/series``
#: have real data to seed the charts with.
METRIC_LATENCY = "latency_ms"
METRIC_THROUGHPUT_IN = "throughput_in_mbps"
METRIC_THROUGHPUT_OUT = "throughput_out_mbps"
METRIC_BACKLOG = "backlog"

#: Rows kept per metric name before the streamer trims the oldest.
METRIC_RETENTION_ROWS = 720

#: Minimum seconds between retention sweeps. Trimming on every tick meant a
#: ``DELETE ... NOT IN (SELECT ...)`` per metric every two seconds; once a
#: minute keeps the table equally bounded for a fraction of the write cost.
TRIM_INTERVAL_S = 60.0

_throughput_sampler = ThroughputSampler()

#: Process start, used for the honest "the control plane has been up N seconds"
#: figure in ``/status`` (service uptime, not a fabricated availability number).
_PROCESS_STARTED_AT = time.time()

# ---------------------------------------------------------------- SQLite reuse
# The streaming path used to open a fresh SQLite connection for every metric on
# every tick (4 writes + a trim sweep every 2 s). One long-lived connection per
# database file, guarded by a lock, replaces that churn: WAL lets readers proceed
# during writes and the descriptor count stays flat.
_DB_LOCK = threading.Lock()
_DB_CONN: Optional[sqlite3.Connection] = None
_DB_CONN_PATH: Optional[str] = None
_LAST_TRIM = 0.0


def _db_connection() -> sqlite3.Connection:
    """
    Return the shared SQLite connection, opening it on first use.

    The connection is cached per database path, so tests and a reconfigured
    ``DB_PATH`` transparently get a fresh handle instead of writing to the
    previous file.

    Returns:
        sqlite3.Connection: Connection in WAL mode, usable from any thread.
    """
    global _DB_CONN, _DB_CONN_PATH
    path = str(nanoc_settings.DB_PATH)
    if _DB_CONN is not None and _DB_CONN_PATH == path:
        return _DB_CONN
    if _DB_CONN is not None:
        try:
            _DB_CONN.close()
        except Exception:  # noqa: BLE001 - closing a stale handle is best effort
            pass
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    _DB_CONN, _DB_CONN_PATH = conn, path
    return conn


def reset_connection() -> None:
    """Drop the cached connection (used on shutdown and by tests)."""
    global _DB_CONN, _DB_CONN_PATH
    with _DB_LOCK:
        if _DB_CONN is not None:
            try:
                _DB_CONN.close()
            except Exception:  # noqa: BLE001
                pass
        _DB_CONN, _DB_CONN_PATH = None, None


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


def _first_value(results: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """
    Extract the first numeric sample from a Prometheus instant-query result.

    Parameters:
        results (Optional[List[Dict[str, Any]]]): ``data.result`` payload.

    Returns:
        Optional[float]: The sample value, or ``None`` when absent/unparseable.
    """
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
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
    try:
        with _DB_LOCK:
            conn = _db_connection()
            row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _format_traffic(inbound: Optional[float], outbound: Optional[float]) -> str:
    """
    Render the sampled host throughput as a human-readable rate.

    Parameters:
        inbound (Optional[float]): Inbound Mbps.
        outbound (Optional[float]): Outbound Mbps.

    Returns:
        str: e.g. ``"3.4 Mbps"``, ``"1.25 Gbps"`` or ``"unknown"`` when the
        sampler has no baseline yet.
    """
    if inbound is None and outbound is None:
        return UNAVAILABLE
    total = float(inbound or 0.0) + float(outbound or 0.0)
    if total >= 1000.0:
        return f"{total / 1000.0:.2f} Gbps"
    return f"{total:.1f} Mbps"


def derive_status(snapshot: Dict[str, Any]) -> str:
    """
    Derive the overall health label from the live rule engine.

    Uses the same threshold rules the alert stream evaluates, so ``/status``
    cannot disagree with the alerts the dashboard is showing.

    Parameters:
        snapshot (Dict[str, Any]): Output of :func:`build_metric_snapshot`.

    Returns:
        str: ``critical``, ``degraded`` or ``nominal``.
    """
    severities = {alert.get("severity") for alert in alert_rules.evaluate_alerts(snapshot)}
    if alert_rules.SEVERITY_CRITICAL in severities:
        return "critical"
    if severities:
        return "degraded"
    return "nominal"


async def build_metric_snapshot() -> Dict[str, Any]:
    """
    Assemble the single source of truth for both REST and websocket metrics.

    Every surface reads from this function, so the dashboard cards, the charts
    and the alert rules can never disagree with each other. Values that cannot
    be obtained are reported as :data:`UNAVAILABLE` rather than invented.

    Returns:
        Dict[str, Any]: Snapshot with ``latency``/``uptime``/``traffic``/
        ``status``/``backlog`` (the REST contract) plus ``latency_ms``,
        ``uptime_pct``, ``throughput_in_mbps``, ``throughput_out_mbps``,
        ``devices``, ``prometheus_ok`` and ``sources`` for the live stream.
    """
    latency_results, uptime_results = await asyncio.gather(
        _prometheus_query("avg(icmp_latency_ms)"),
        _prometheus_query("avg(up) * 100"),
    )
    prometheus_ok = latency_results is not None
    latency_ms = _first_value(latency_results)
    if latency_ms is not None:
        latency_ms = round(latency_ms, 3)

    uptime_pct: Optional[float] = _first_value(uptime_results)
    if uptime_pct is not None:
        uptime_pct = round(min(max(uptime_pct, 0.0), 100.0), 2)

    sample = _throughput_sampler.sample(time.monotonic())
    devices = _count_devices(data.build_topology())

    snapshot: Dict[str, Any] = {
        # --- REST contract (real values, or "unknown" when unobtainable) ---
        "latency": f"{latency_ms}ms" if latency_ms is not None else UNAVAILABLE,
        "uptime": f"{uptime_pct}%" if uptime_pct is not None else UNAVAILABLE,
        "traffic": _format_traffic(sample.inbound_mbps, sample.outbound_mbps),
        "status": "nominal",
        "backlog": _pending_backlog(),
        # --- live stream extras ---
        "latency_ms": latency_ms,
        "uptime_pct": uptime_pct,
        "throughput_in_mbps": sample.inbound_mbps,
        "throughput_out_mbps": sample.outbound_mbps,
        "devices": devices,
        "prometheus_ok": prometheus_ok,
        # --- provenance, so the UI can label a gap instead of drawing a zero ---
        "sources": {
            "latency": "prometheus" if latency_ms is not None else "unavailable",
            "uptime": "prometheus" if uptime_pct is not None else "unavailable",
            "traffic": "host_sampler",
            "status": "alert_rules",
            "backlog": "sqlite",
        },
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    # Derived last: it reads the snapshot's own fields through the rule engine.
    snapshot["status"] = derive_status(snapshot)
    return snapshot


def record_snapshot(snapshot: Dict[str, Any]) -> None:
    """
    Persist a metric snapshot so ``/history`` and ``/series`` can seed charts.

    ``None`` values (Prometheus down, throughput baseline not yet established)
    are skipped rather than written as zeroes, which would draw a fake crash to
    the bottom of the graph.

    All rows for one tick are written in a single transaction over the shared
    connection, and the retention sweep is throttled to
    :data:`TRIM_INTERVAL_S`.

    Parameters:
        snapshot (Dict[str, Any]): Output of :func:`build_metric_snapshot`.
    """
    global _LAST_TRIM
    rows: List[tuple] = []
    for name, key, unit in (
        (METRIC_LATENCY, "latency_ms", "ms"),
        (METRIC_THROUGHPUT_IN, "throughput_in_mbps", "Mbps"),
        (METRIC_THROUGHPUT_OUT, "throughput_out_mbps", "Mbps"),
        (METRIC_BACKLOG, "backlog", "tasks"),
    ):
        value = snapshot.get(key)
        if value is None:
            continue
        try:
            rows.append((name, float(value), unit, "{}", datetime.now()))
        except (TypeError, ValueError):
            continue

    if not rows:
        return

    now = time.monotonic()
    should_trim = (now - _LAST_TRIM) >= TRIM_INTERVAL_S
    try:
        with _DB_LOCK:
            conn = _db_connection()
            conn.executemany(
                "INSERT INTO metrics (metric_name, value, unit, tags, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            if should_trim:
                _trim_metrics(conn)
        if should_trim:
            _LAST_TRIM = now
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the loop
        logger.debug("Dropping metric snapshot: %s", exc)


def _trim_metrics(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Keep the ``metrics`` table bounded by dropping the oldest rows per metric.

    Parameters:
        conn (Optional[sqlite3.Connection]): Connection to reuse. When omitted a
            connection is taken from the shared cache under the lock.
    """
    try:
        if conn is None:
            with _DB_LOCK:
                _trim_metrics(_db_connection())
            return
        cursor = conn.execute("SELECT DISTINCT metric_name FROM metrics")
        names = [row[0] for row in cursor.fetchall()]
        for name in names:
            conn.execute(
                "DELETE FROM metrics WHERE metric_name = ? AND id NOT IN "
                "(SELECT id FROM metrics WHERE metric_name = ? "
                "ORDER BY id DESC LIMIT ?)",
                (name, name, METRIC_RETENTION_ROWS),
            )
        conn.commit()
    except Exception:  # noqa: BLE001 - retention is housekeeping
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
    Provide high-level system status built from live sources only.

    Every field is real or explicitly ``"unknown"``:

    * ``latency`` — Prometheus ``avg(icmp_latency_ms)``, or ``"unknown"``.
    * ``uptime`` — Prometheus ``avg(up) * 100`` (fraction of scrape targets
      that are up), or ``"unknown"`` when Prometheus cannot be reached.
    * ``traffic`` — host interface throughput from the sampler.
    * ``status`` — derived from the same threshold rules that raise alerts.
    * ``backlog`` — pending tasks in the local SQLite database.

    Returns:
        dict: Mapping with keys ``latency`` (str), ``uptime`` (str), ``traffic``
        (str), ``status`` (str), ``backlog`` (int), plus ``uptime_s`` (process
        uptime in seconds) and ``sources`` (which subsystem produced each
        value).
    """
    snapshot = await build_metric_snapshot()
    return {
        "latency": snapshot["latency"],
        "uptime": snapshot["uptime"],
        "traffic": snapshot["traffic"],
        "status": snapshot["status"],
        "backlog": snapshot["backlog"],
        "uptime_s": round(time.time() - _PROCESS_STARTED_AT, 1),
        "sources": snapshot["sources"],
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
