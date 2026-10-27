"""Live producers that push telemetry onto the websocket fan-out.

Everything here prefers the existing plumbing: the producers publish through the
nanoc :class:`EventBus`, the lifespan's ``"*"`` subscriber picks the event up,
and :class:`ConnectionManager` broadcasts it to every ``/ws`` client. No second
delivery mechanism is introduced — the REST endpoints and the stream read from
the *same* builder functions, so they cannot drift.

One loop drives all three cadences (metrics, alerts, data) so there is no
contention between tasks all opening the same SQLite file at once, and every
tick is exception-isolated: a broken producer must never take the loop down.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.api.endpoints import alerts as alerts_endpoint
from app.api.endpoints import data as data_endpoint
from app.api.endpoints import monitoring
from app.core import alert_rules

logger = logging.getLogger(__name__)

#: Topics published on the bus. The prefixes must stay aligned with
#: ``websockets.TOPIC_TYPE_PREFIXES`` so each message lands in the right
#: frontend bucket (``metric`` / ``alert`` / ``data_update``).
TOPIC_METRIC = "metric/snapshot"
TOPIC_ALERT_CREATED = "alert/created"
TOPIC_ALERT_RESOLVED = "alert/resolved"
TOPIC_DATA_UPDATE = "data/updated"

#: Default cadences in seconds. The loop wakes every ``TICK_INTERVAL_S`` and
#: publishes whatever is due.
METRIC_INTERVAL_S = 2.0
ALERT_INTERVAL_S = 5.0
DATA_INTERVAL_S = 5.0
TICK_INTERVAL_S = 0.5


class StreamEngine:
    """
    Turn periodic sampling into event-bus publications.

    The engine is inert until :meth:`bind` receives a bus and :meth:`run` is
    scheduled, which keeps importing it in tests free of side effects.
    """

    def __init__(self) -> None:
        """Create an unbound engine with no active alerts."""
        self._bus = None
        self._running = False
        self._next_due: Dict[str, float] = {}
        self._previous_fingerprint: Optional[Dict[str, Any]] = None
        self._active_alerts: Dict[str, Dict[str, Any]] = {}
        self._last_snapshot: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ setup
    def bind(self, bus) -> None:
        """
        Attach the engine to an event bus.

        Parameters:
            bus: Object exposing ``publish(topic, payload)``.
        """
        self._bus = bus

    @property
    def last_snapshot(self) -> Optional[Dict[str, Any]]:
        """Most recent metric snapshot, or ``None`` before the first tick."""
        return self._last_snapshot

    @property
    def active_alerts(self) -> List[Dict[str, Any]]:
        """Alerts currently firing (rule evaluation order)."""
        return list(self._active_alerts.values())

    # --------------------------------------------------------------- publishing
    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """
        Publish one event on the bus.

        Failures are logged, never raised: a missing bus or a locked database
        must not stop the dashboard from being updated on the next tick.

        Parameters:
            topic (str): Event-bus topic.
            payload (Dict[str, Any]): JSON-serialisable event body.
        """
        if self._bus is None:
            return
        try:
            self._bus.publish(topic, payload)
        except Exception as exc:  # noqa: BLE001 - telemetry must not crash the loop
            logger.warning("Failed to publish %s: %s", topic, exc)

    async def publish_metric(self) -> Dict[str, Any]:
        """
        Sample the system and publish one ``metric`` event.

        The snapshot is also persisted so ``/history`` and ``/series`` keep a
        rolling window for cold starts.

        Returns:
            Dict[str, Any]: The snapshot that was published.
        """
        snapshot = await monitoring.build_metric_snapshot()
        self._last_snapshot = snapshot
        monitoring.record_snapshot(snapshot)
        self.publish(TOPIC_METRIC, snapshot)
        return snapshot

    async def publish_alerts(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        """
        Evaluate the threshold rules and publish alert transitions only.

        Parameters:
            snapshot (Optional[Dict[str, Any]]): Snapshot to evaluate. When
                omitted a fresh one is sampled.
        """
        if snapshot is None:
            snapshot = await monitoring.build_metric_snapshot()
        current = alert_rules.evaluate_alerts(snapshot)
        created, resolved = alert_rules.diff_alerts(self._active_alerts, current)

        for alert in created:
            logger.info("Alert created: %s", alert["id"])
            self.publish(TOPIC_ALERT_CREATED, {**alert, "action": "created"})
        for alert in resolved:
            logger.info("Alert resolved: %s", alert["id"])
            self.publish(TOPIC_ALERT_RESOLVED, {**alert, "action": "resolved"})

        self._active_alerts = {alert["id"]: alert for alert in current}

    async def publish_data_updates(self, fingerprint: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        Publish a ``data_update`` when the topology, tasks or agents changed.

        The first call only records a baseline, so a fresh process does not
        fire a burst of "everything changed" messages.

        Parameters:
            fingerprint (Optional[Dict[str, Any]]): Fingerprint to compare.
                Computed from the database when omitted.

        Returns:
            List[str]: Names of the data sets that changed (empty when quiet).
        """
        current = fingerprint if fingerprint is not None else data_endpoint.data_fingerprint()
        previous, self._previous_fingerprint = self._previous_fingerprint, current
        if previous is None:
            return []

        changed = [key for key, value in current.items() if previous.get(key) != value]
        if changed:
            self.publish(
                TOPIC_DATA_UPDATE,
                {
                    "kinds": changed,
                    "fingerprint": current,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                },
            )
        return changed

    # -------------------------------------------------------------------- loop
    def _due(self, name: str, interval: float, now: float) -> bool:
        """
        Report whether a producer is due, and advance its schedule.

        Parameters:
            name (str): Producer name used as the schedule key.
            interval (float): Minimum seconds between fires.
            now (float): Monotonic clock reading.

        Returns:
            bool: ``True`` when the producer should fire on this tick.
        """
        next_due = self._next_due.get(name)
        if next_due is not None and now < next_due:
            return False
        self._next_due[name] = now + interval
        return True

    async def tick(self, now: Optional[float] = None) -> None:
        """
        Run whichever producers are due, isolating each one's failures.

        Parameters:
            now (float, optional): Monotonic clock reading; injectable for tests.
        """
        now = time.monotonic() if now is None else now

        if self._due("metric", METRIC_INTERVAL_S, now):
            try:
                await self.publish_metric()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Metric stream tick failed: %s", exc)

        if self._due("alert", ALERT_INTERVAL_S, now):
            try:
                # Reuse the snapshot the metric producer just took rather than
                # sampling twice; fall back to a fresh sample when it has not
                # run yet.
                await self.publish_alerts(self._last_snapshot)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Alert stream tick failed: %s", exc)

        if self._due("data", DATA_INTERVAL_S, now):
            try:
                await self.publish_data_updates()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Data stream tick failed: %s", exc)

    async def run(self, tick_interval: float = TICK_INTERVAL_S) -> None:
        """
        Drive the producers until :meth:`stop` is called.

        Parameters:
            tick_interval (float): Seconds to sleep between scheduling passes.
        """
        self._running = True
        logger.info("Live stream engine started")
        try:
            while self._running:
                await self.tick()
                await asyncio.sleep(tick_interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Ask the loop to exit after the current tick."""
        self._running = False


#: Process-wide engine, bound to the bus created in the app lifespan.
engine = StreamEngine()
