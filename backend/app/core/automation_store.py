"""SQLite persistence for the automation engine.

The NOC already keeps its state in one SQLite file (``nanoc/memory/nanoc.db``,
see :mod:`nanoc.memory.memory`). Rather than growing that class — it is owned by
the nanoc orchestrator — this module keeps its own two tables in the *same*
database, created idempotently on first use. That reuses the deployment's
existing storage, backup and permissions story without a migration step, and
keeps the automation schema next to the code that understands it.

Two tables:

``automation_runs``
    One row per (rule, action) execution: what fired, on which event, which
    condition matched, what the action did, how it turned out and how long it
    took. This is the audit trail the dashboard reads.
``automation_pending_actions``
    The queue of remediation commands a rule *asked for*. Nothing here is ever
    executed by this process: a later, security-reviewed executor drains the
    queue. Keeping the request durable (rather than in memory) means a restart
    cannot silently drop a remediation.
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Runs kept before the oldest rows are trimmed (roughly a week of noisy NOC).
RETENTION_ROWS = 5000
#: Trim every N inserts instead of on every write, to keep the hot path cheap.
TRIM_EVERY = 50
#: Longest event payload stored verbatim; keeps a chatty topic from bloating DB.
MAX_PAYLOAD_CHARS = 4000

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS automation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id TEXT NOT NULL,
        rule_name TEXT,
        trigger_topic TEXT,
        trigger_source TEXT,
        trigger_event_id INTEGER,
        matched_condition TEXT,
        action_type TEXT,
        action_summary TEXT,
        outcome TEXT,
        detail TEXT,
        event_payload TEXT,
        timestamp TEXT,
        duration_ms REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_pending_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id TEXT,
        rule_name TEXT,
        command TEXT,
        reason TEXT,
        severity TEXT,
        status TEXT DEFAULT 'pending',
        requested_by TEXT,
        trigger_topic TEXT,
        run_id INTEGER,
        created_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_rule ON automation_runs(rule_id)",
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_ts ON automation_runs(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_automation_pending_status "
    "ON automation_pending_actions(status)",
)


def _now() -> str:
    """Current local time as an ISO string (sortable, matches the REST layer)."""
    return datetime.now().isoformat(timespec="seconds")


def _dumps(payload: Any) -> str:
    """
    Serialise an event payload defensively.

    Parameters:
        payload (Any): Value to store.

    Returns:
        str: JSON text, truncated to :data:`MAX_PAYLOAD_CHARS`. Values that
        cannot be serialised are stored as ``null`` rather than losing the row.
    """
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return "null"
    if len(text) > MAX_PAYLOAD_CHARS:
        return text[:MAX_PAYLOAD_CHARS] + "...(truncated)"
    return text


class AutomationStore:
    """Durable run history and pending-action queue for the rule engine."""

    def __init__(self, db_path: str):
        """
        Parameters:
            db_path (str): SQLite file, normally ``nanoc_settings.DB_PATH``.
        """
        self.db_path = db_path
        self._ensure_schema()

    # ------------------------------------------------------------------ schema
    def _connect(self) -> sqlite3.Connection:
        """
        Open a connection to the NOC database.

        Returns:
            sqlite3.Connection: Connection with ``Row`` factory enabled.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create the automation tables if they are missing (idempotent)."""
        try:
            with self._connect() as conn:
                for statement in SCHEMA:
                    conn.execute(statement)
                conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover - disk/permission failure
            logger.warning("Automation schema init failed: %s", exc)

    # -------------------------------------------------------------------- runs
    def record_run(self, run: Dict[str, Any]) -> Optional[int]:
        """
        Persist one automation run and return its row id.

        The write is best-effort: a locked or missing database must never take
        the engine down, so failures are logged and ``None`` is returned.

        Parameters:
            run (Dict[str, Any]): Run record; unknown keys are ignored.

        Returns:
            Optional[int]: Inserted row id, or ``None`` when the write failed.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO automation_runs (
                        rule_id, rule_name, trigger_topic, trigger_source,
                        trigger_event_id, matched_condition, action_type,
                        action_summary, outcome, detail, event_payload,
                        timestamp, duration_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.get("rule_id"),
                        run.get("rule_name"),
                        run.get("trigger_topic"),
                        run.get("trigger_source", "event"),
                        run.get("trigger_event_id"),
                        run.get("matched_condition"),
                        run.get("action_type"),
                        run.get("action_summary"),
                        run.get("outcome"),
                        run.get("detail"),
                        _dumps(run.get("event_payload")),
                        run.get("timestamp") or _now(),
                        run.get("duration_ms"),
                    ),
                )
                conn.commit()
                run_id = cursor.lastrowid
            if run_id and run_id % TRIM_EVERY == 0:
                self._trim_runs()
            return run_id
        except sqlite3.Error as exc:
            logger.warning("Failed to record automation run: %s", exc)
            return None

    def _trim_runs(self) -> None:
        """Keep the run table bounded by dropping the oldest rows."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM automation_runs WHERE id NOT IN "
                    "(SELECT id FROM automation_runs ORDER BY id DESC LIMIT ?)",
                    (RETENTION_ROWS,),
                )
                conn.commit()
        except sqlite3.Error:
            pass

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        rule_id: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Read a page of run history, newest first.

        Parameters:
            limit (int): Page size.
            offset (int): Rows to skip.
            rule_id (Optional[str]): Only runs for this rule.
            outcome (Optional[str]): Only runs with this outcome.

        Returns:
            Tuple[List[Dict[str, Any]], int]: The page and the total number of
            matching rows (so the caller can paginate).
        """
        clauses: List[str] = []
        params: List[Any] = []
        if rule_id:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            with self._connect() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM automation_runs{where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM automation_runs{where} "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                return [self._decode_run(dict(row)) for row in rows], int(total)
        except sqlite3.Error as exc:
            logger.warning("Failed to read automation runs: %s", exc)
            return [], 0

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        """
        Read one run by id.

        Parameters:
            run_id (int): ``automation_runs.id``.

        Returns:
            Optional[Dict[str, Any]]: The run, or ``None`` when absent.
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM automation_runs WHERE id = ?", (run_id,)
                ).fetchone()
                return self._decode_run(dict(row)) if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _decode_run(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turn a stored row into the JSON shape the API returns.

        Parameters:
            row (Dict[str, Any]): Raw SQLite row.

        Returns:
            Dict[str, Any]: Row with ``event_payload`` parsed back to an object.
        """
        payload = row.get("event_payload")
        if isinstance(payload, str):
            try:
                row["event_payload"] = json.loads(payload)
            except (TypeError, ValueError):
                row["event_payload"] = None
        return row

    def outcome_counts(self) -> Dict[str, int]:
        """
        Count runs per outcome, for the dashboard's automation health tile.

        Returns:
            Dict[str, int]: ``{"success": n, "failed": n, ...}``.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT outcome, COUNT(*) AS n FROM automation_runs GROUP BY outcome"
                ).fetchall()
                return {row["outcome"] or "unknown": int(row["n"]) for row in rows}
        except sqlite3.Error:
            return {}

    # ----------------------------------------------------------- pending queue
    def enqueue_action(self, action: Dict[str, Any]) -> Optional[int]:
        """
        Queue a remediation command for the (not yet built) privileged executor.

        Nothing is executed here — the row *is* the request.

        Parameters:
            action (Dict[str, Any]): ``rule_id``, ``command``, ``reason``,
                ``severity``, ``run_id``, ``trigger_topic`` and ``requested_by``.

        Returns:
            Optional[int]: Inserted row id, or ``None`` when the write failed.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO automation_pending_actions (
                        rule_id, rule_name, command, reason, severity, status,
                        requested_by, trigger_topic, run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        action.get("rule_id"),
                        action.get("rule_name"),
                        action.get("command"),
                        action.get("reason"),
                        action.get("severity", "warning"),
                        action.get("requested_by", "automation"),
                        action.get("trigger_topic"),
                        action.get("run_id"),
                        _now(),
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except sqlite3.Error as exc:
            logger.warning("Failed to enqueue automation action: %s", exc)
            return None

    def link_pending_run(self, action_id: int, run_id: int) -> None:
        """
        Point a queued action at the run record that requested it.

        The command is queued *while* the run is being built, so the run id only
        exists afterwards; this back-fills the link that lets the executor (and
        the dashboard) show "which automation run asked for this".

        Parameters:
            action_id (int): ``automation_pending_actions.id``.
            run_id (int): ``automation_runs.id``.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE automation_pending_actions SET run_id = ? WHERE id = ?",
                    (run_id, action_id),
                )
                conn.commit()
        except sqlite3.Error:
            pass

    def list_pending_actions(
        self, limit: int = 50, offset: int = 0, status: Optional[str] = "pending"
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Read a page of queued remediation actions, newest first.

        Parameters:
            limit (int): Page size.
            offset (int): Rows to skip.
            status (Optional[str]): Filter by status; ``None`` means "all".

        Returns:
            Tuple[List[Dict[str, Any]], int]: The page and the total count.
        """
        where = " WHERE status = ?" if status else ""
        params: List[Any] = [status] if status else []
        try:
            with self._connect() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM automation_pending_actions{where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM automation_pending_actions{where} "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                return [dict(row) for row in rows], int(total)
        except sqlite3.Error:
            return [], 0
