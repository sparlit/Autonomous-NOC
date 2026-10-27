"""Threshold rules that turn a metric snapshot into alert lifecycle events.

The NOC has no alert *source* of its own without Keep wired in, so alerting is
driven off the same telemetry the dashboard renders. Every rule is a pure
function of a snapshot, which makes the create/resolve diff easy to unit-test:
given the same snapshot twice, nothing fires; given a snapshot where a
condition newly holds, exactly one alert is created.

Alert identity is the rule id, so a condition that flaps does not spawn
duplicates — the streamer only emits transitions.
"""

from datetime import datetime
from typing import Any, Callable, Dict, List

#: Latency above this many milliseconds is treated as an SLO breach.
LATENCY_WARN_MS = 50.0
#: Host throughput above this many Mbps is treated as link saturation.
THROUGHPUT_WARN_MBPS = 800.0
#: Pending autonomous tasks above this many means the queue is stalling.
BACKLOG_WARN = 10

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"


def _device_counts(snapshot: Dict[str, Any]) -> Dict[str, int]:
    """Return the device status histogram from a snapshot (never ``None``)."""
    devices = snapshot.get("devices") or {}
    return {
        "operational": int(devices.get("operational") or 0),
        "warning": int(devices.get("warning") or 0),
        "critical": int(devices.get("critical") or 0),
        "maintenance": int(devices.get("maintenance") or 0),
    }


def _latency_breach(snapshot: Dict[str, Any]) -> bool:
    value = snapshot.get("latency_ms")
    return value is not None and float(value) > LATENCY_WARN_MS


def _latency_blind(snapshot: Dict[str, Any]) -> bool:
    """True when Prometheus could not be reached — the NOC is flying blind."""
    return snapshot.get("prometheus_ok") is False


def _backlog_breach(snapshot: Dict[str, Any]) -> bool:
    return int(snapshot.get("backlog") or 0) >= BACKLOG_WARN


def _link_saturated(snapshot: Dict[str, Any]) -> bool:
    inbound = snapshot.get("throughput_in_mbps")
    outbound = snapshot.get("throughput_out_mbps")
    return any(
        value is not None and float(value) > THROUGHPUT_WARN_MBPS
        for value in (inbound, outbound)
    )


def _degraded_devices(snapshot: Dict[str, Any]) -> bool:
    return _device_counts(snapshot)["warning"] >= 1


def _down_devices(snapshot: Dict[str, Any]) -> bool:
    return _device_counts(snapshot)["critical"] >= 1


class AlertRule:
    """A named threshold with a stable alert id."""

    def __init__(
        self,
        rule_id: str,
        title: str,
        severity: str,
        describe: Callable[[Dict[str, Any]], str],
        fires: Callable[[Dict[str, Any]], bool],
    ):
        """
        Parameters:
            rule_id (str): Stable identity used to diff create/resolve.
            title (str): Short human-readable alert title.
            severity (str): ``critical`` or ``warning``.
            describe (Callable[[Dict[str, Any]], str]): Builds the long form.
            fires (Callable[[Dict[str, Any]], bool]): Condition test.
        """
        self.id = f"rule:{rule_id}"
        self.rule = rule_id
        self.title = title
        self.severity = severity
        self._describe = describe
        self._fires = fires

    def evaluate(self, snapshot: Dict[str, Any], now: str) -> Dict[str, Any]:
        """
        Build the alert record for this rule from a snapshot.

        Parameters:
            snapshot (Dict[str, Any]): Metric snapshot.
            now (str): ISO timestamp to stamp on the record.

        Returns:
            Dict[str, Any]: Alert payload, including its stable ``id``.
        """
        return {
            "id": self.id,
            "rule": self.rule,
            "title": self.title,
            "description": self._describe(snapshot),
            "severity": self.severity,
            "source": "noc-rules",
            "status": "active",
            "timestamp": now,
        }

    def fires_on(self, snapshot: Dict[str, Any]) -> bool:
        """
        Report whether the condition currently holds.

        Parameters:
            snapshot (Dict[str, Any]): Metric snapshot.

        Returns:
            bool: ``True`` when the alert should be active.
        """
        return self._fires(snapshot)


RULES: List[AlertRule] = [
    AlertRule(
        "latency_slo",
        "Latency SLO breach",
        SEVERITY_CRITICAL,
        lambda s: (
            f"Observed latency {s.get('latency_ms')}ms exceeds the "
            f"{LATENCY_WARN_MS:.0f}ms SLO threshold."
        ),
        _latency_breach,
    ),
    AlertRule(
        "prometheus_unreachable",
        "Prometheus unreachable",
        SEVERITY_WARNING,
        lambda s: (
            "Prometheus queries are failing; latency is reported from the last "
            "known fallback value. Metrics-driven alerting is degraded."
        ),
        _latency_blind,
    ),
    AlertRule(
        "task_backlog",
        "Task backlog above threshold",
        SEVERITY_WARNING,
        lambda s: (
            f"{s.get('backlog')} autonomous tasks are pending (threshold "
            f"{BACKLOG_WARN})."
        ),
        _backlog_breach,
    ),
    AlertRule(
        "link_saturation",
        "Link saturation",
        SEVERITY_WARNING,
        lambda s: (
            f"Host throughput {s.get('throughput_in_mbps')}Mbps in / "
            f"{s.get('throughput_out_mbps')}Mbps out exceeds "
            f"{THROUGHPUT_WARN_MBPS:.0f}Mbps."
        ),
        _link_saturated,
    ),
    AlertRule(
        "device_degraded",
        "Device in warning state",
        SEVERITY_WARNING,
        lambda s: (
            f"{_device_counts(s)['warning']} device(s) reported a warning "
            "status in the topology."
        ),
        _degraded_devices,
    ),
    AlertRule(
        "device_down",
        "Device offline",
        SEVERITY_CRITICAL,
        lambda s: (
            f"{_device_counts(s)['critical']} device(s) are offline in the "
            "topology."
        ),
        _down_devices,
    ),
]


def evaluate_alerts(
    snapshot: Dict[str, Any], now: str = None
) -> List[Dict[str, Any]]:
    """
    Evaluate every rule against a snapshot.

    Parameters:
        snapshot (Dict[str, Any]): Metric snapshot as produced by the monitor.
        now (str, optional): ISO timestamp; defaults to the current time.

    Returns:
        List[Dict[str, Any]]: Alert records for the conditions that hold.
    """
    timestamp = now or datetime.now().isoformat(timespec="seconds")
    return [rule.evaluate(snapshot, timestamp) for rule in RULES if rule.fires_on(snapshot)]


def diff_alerts(previous: Dict[str, Dict[str, Any]], current: List[Dict[str, Any]]):
    """
    Compare two active-alert sets and report the transitions.

    Parameters:
        previous (Dict[str, Dict[str, Any]]): Active alerts keyed by id, from
            the previous evaluation.
        current (List[Dict[str, Any]]): Alerts that are active now.

    Returns:
        Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]: ``(created, resolved)``
        where ``resolved`` records carry the previous alert body plus
        ``status="resolved"``.
    """
    current_by_id = {alert["id"]: alert for alert in current}
    created = [alert for key, alert in current_by_id.items() if key not in previous]
    resolved = [
        {**alert, "status": "resolved"}
        for key, alert in previous.items()
        if key not in current_by_id
    ]
    return created, resolved
