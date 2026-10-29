"""Event-driven automation engine — the "Autonomous" part of the NOC.

The engine turns the live event stream into reactions. It subscribes to the same
:class:`nanoc.core.event_bus.EventBus` topics the dashboard streams
(``alert/created``, ``alert/resolved``, ``metric/snapshot``, ``data/updated``)
and, for every event, asks each enabled rule whether its trigger matches. A
matching rule's actions are executed off the bus loop, every execution is
persisted, and a flat ``automation/run`` event is published back onto the bus so
``/ws`` clients can render it (``type: "automation"``).

Design constraints that shaped this module:

* **Never block the bus.** ``EventBus.start_polling`` dispatches subscribers
  synchronously; :meth:`AutomationEngine.handle_event` therefore does only
  matching plus a non-blocking ``put_nowait`` onto a bounded queue. Action
  execution happens on a fixed pool of worker tasks.
* **Bounded.** The queue has a maximum depth and there is a fixed number of
  workers; when the queue is full new matches are dropped and logged rather
  than growing memory or stalling the stream.
* **Fail-safe.** A broken webhook, an unreachable host or an unreadable rule
  file is logged and contained. One bad action never stops the engine and never
  stops the other actions of the same rule.
* **No shell.** The ``command`` action only *queues* a remediation request for a
  later, security-reviewed executor. Nothing in this module executes a process.

Rules are declarative JSON (stdlib only — no YAML dependency), loaded from
``backend/config/automation_rules.json`` by default::

    {
      "id": "critical-alert-webhook",
      "enabled": true,
      "trigger": {
        "topic": "alert/created",
        "conditions": [{"field": "severity", "op": "eq", "value": "critical"}],
        "sustain": {"count": 1},
        "cooldown_seconds": 60
      },
      "actions": [
        {"type": "notify", "message": "CRITICAL {title}"},
        {"type": "webhook", "url": "${AUTOMATION_WEBHOOK_URL}"},
        {"type": "command", "command": "noc-remediate latency", "reason": "..."}
      ]
    }
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from app.core.automation_store import AutomationStore
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Topics the engine listens on by default. A rule may name any topic; these are
#: the ones the streaming layer actually publishes today.
DEFAULT_TOPICS: Tuple[str, ...] = (
    "alert/created",
    "alert/resolved",
    "metric/snapshot",
    "data/updated",
)

#: Topic a completed run is republished on, so ``/ws`` can fan it out with
#: ``type: "automation"`` (see ``websockets.TOPIC_TYPE_PREFIXES``).
TOPIC_AUTOMATION_RUN = "automation/run"

#: Action types this engine knows how to run.
ACTION_TYPES: Tuple[str, ...] = ("notify", "webhook", "command")

#: Outcomes a run can end in.
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOME_QUEUED = "queued"

#: Hard caps so a malformed rule file cannot ask for an unbounded retry storm.
MAX_RETRIES = 5
MAX_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Field access and comparison
# ---------------------------------------------------------------------------
def get_field(payload: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    """
    Read a dotted path out of an event payload.

    Parameters:
        payload (Dict[str, Any]): Event body.
        path (str): Key, or dotted path such as ``"devices.critical"``.

    Returns:
        Tuple[bool, Any]: ``(found, value)``. ``found`` is ``False`` for a
        missing key *and* for an explicit ``None`` so ``exists`` stays honest.
    """
    current: Any = payload
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    if current is None:
        return False, None
    return True, current


def _as_number(value: Any) -> Optional[float]:
    """
    Best-effort numeric view of a value.

    Parameters:
        value (Any): Candidate value.

    Returns:
        Optional[float]: The number, or ``None`` when it is not numeric.
        Booleans are deliberately *not* numbers here.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _compare(actual: Any, expected: Any) -> int:
    """
    Order two values, numerically when both look numeric, else as strings.

    Parameters:
        actual (Any): Observed value.
        expected (Any): Rule value.

    Returns:
        int: ``-1``, ``0`` or ``1``. Falls back to a string comparison so
        ``"critical"``/``"warning"`` style fields order deterministically.
    """
    left, right = _as_number(actual), _as_number(expected)
    if left is None or right is None:
        left, right = str(actual), str(expected)
    return (left > right) - (left < right)


def _equal(actual: Any, expected: Any) -> bool:
    """
    Type-tolerant equality.

    ``"500"`` from JSON matches ``500`` from a rule, ``True`` matches ``"true"``,
    and ``3.0`` matches ``3``. This keeps rules readable without forcing the
    payload producer to pick a Python type.

    Parameters:
        actual (Any): Observed value.
        expected (Any): Rule value.

    Returns:
        bool: ``True`` when the two represent the same value.
    """
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).lower() == str(expected).lower()
    left, right = _as_number(actual), _as_number(expected)
    if left is not None and right is not None:
        return left == right
    return str(actual) == str(expected)


#: Operator name -> predicate. Keeping this a table (rather than ``eval``) is
#: what makes the rule file data instead of code.
OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "eq": _equal,
    "ne": lambda actual, expected: not _equal(actual, expected),
    "gt": lambda actual, expected: _compare(actual, expected) > 0,
    "gte": lambda actual, expected: _compare(actual, expected) >= 0,
    "lt": lambda actual, expected: _compare(actual, expected) < 0,
    "lte": lambda actual, expected: _compare(actual, expected) <= 0,
    "contains": lambda actual, expected: str(expected) in str(actual),
    "startswith": lambda actual, expected: str(actual).startswith(str(expected)),
    "in": lambda actual, expected: any(_equal(actual, item) for item in expected),
    "not_in": lambda actual, expected: not any(
        _equal(actual, item) for item in expected
    ),
    "exists": lambda actual, expected: True,  # presence is checked by the caller
}


@dataclass
class Condition:
    """One ``field op value`` test against an event payload."""

    field: str
    op: str = "eq"
    value: Any = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Condition":
        """
        Build a condition from its JSON form.

        Parameters:
            raw (Dict[str, Any]): ``{"field": ..., "op": ..., "value": ...}``.

        Returns:
            Condition: Parsed condition.

        Raises:
            ValueError: When the field is missing or the operator is unknown.
        """
        field_name = raw.get("field")
        if not field_name:
            raise ValueError("condition is missing 'field'")
        op = str(raw.get("op", "eq")).lower()
        if op not in OPERATORS:
            raise ValueError(f"unknown operator '{op}'")
        return cls(field=str(field_name), op=op, value=raw.get("value"))

    def evaluate(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Test this condition against a payload.

        Parameters:
            payload (Dict[str, Any]): Event body.

        Returns:
            Tuple[bool, str]: ``(matched, human_readable_reason)``.
        """
        found, actual = get_field(payload, self.field)
        if self.op == "exists":
            expected = self.value if self.value is not None else True
            matched = found is bool(expected)
            return matched, f"{self.field} exists={str(matched).lower()}"

        if not found:
            # ``ne``/``not_in`` are true when the field is absent, which is what
            # "is not critical" means for an event that carries no severity.
            matched = self.op in ("ne", "not_in")
            return matched, f"{self.field} missing ({'matched' if matched else 'no match'})"

        try:
            matched = bool(OPERATORS[self.op](actual, self.value))
        except Exception as exc:  # noqa: BLE001 - a bad rule must not kill the loop
            logger.warning("Condition %s %s failed: %s", self.field, self.op, exc)
            return False, f"{self.field} {self.op} error"
        return matched, f"{self.field} {self.op} {self.value!r} (saw {actual!r})"


def render_template(template: str, payload: Dict[str, Any]) -> str:
    """
    Fill ``{field}`` placeholders in an action message from the event payload.

    Deliberately not :meth:`str.format`: alert descriptions contain braces and
    ``format`` would explode on them. Unknown fields render as an empty string so
    a missing key degrades the message instead of failing the action.

    Parameters:
        template (str): Message template.
        payload (Dict[str, Any]): Event body.

    Returns:
        str: Rendered message.
    """
    out = str(template or "")
    for token in sorted(set(_iter_tokens(out))):
        found, value = get_field(payload, token)
        out = out.replace("{" + token + "}", str(value) if found else "")
    return out


def _iter_tokens(text: str):
    """
    Yield the ``{placeholder}`` names inside a template.

    Parameters:
        text (str): Template text.

    Returns:
        Iterator[str]: Placeholder names, e.g. ``"devices.critical"``.
    """
    start = text.find("{")
    while start != -1:
        end = text.find("}", start)
        if end == -1:
            return
        token = text[start + 1 : end]
        if token and all(ch.isalnum() or ch in "_.-" for ch in token):
            yield token
            start = text.find("{", end)
        else:
            start = text.find("{", start + 1)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@dataclass
class AutomationRule:
    """A trigger plus the actions to run when it matches."""

    id: str
    name: str = ""
    description: str = ""
    topic: str = ""
    conditions: List[Condition] = field(default_factory=list)
    sustain_count: int = 1
    cooldown_seconds: float = 0.0
    actions: List[Dict[str, Any]] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AutomationRule":
        """
        Parse and validate one rule.

        Parameters:
            raw (Dict[str, Any]): Rule document from the JSON file.

        Returns:
            AutomationRule: Parsed rule.

        Raises:
            ValueError: On a missing id/topic, an unknown operator, an empty or
            unsupported action list.
        """
        rule_id = str(raw.get("id") or "").strip()
        if not rule_id:
            raise ValueError("rule is missing 'id'")
        trigger = raw.get("trigger") or {}
        topic = str(trigger.get("topic") or "").strip()
        if not topic:
            raise ValueError(f"rule '{rule_id}' is missing trigger.topic")

        conditions = [
            Condition.from_dict(item) for item in (trigger.get("conditions") or [])
        ]

        sustain = trigger.get("sustain") or {}
        sustain_count = sustain.get("count", 1) if isinstance(sustain, dict) else int(sustain)
        try:
            sustain_count = max(1, int(sustain_count))
        except (TypeError, ValueError):
            sustain_count = 1

        actions = list(raw.get("actions") or [])
        if not actions:
            raise ValueError(f"rule '{rule_id}' has no actions")
        for action in actions:
            action_type = str((action or {}).get("type") or "").lower()
            if action_type not in ACTION_TYPES:
                raise ValueError(
                    f"rule '{rule_id}' uses unsupported action '{action_type}'"
                )

        return cls(
            id=rule_id,
            name=str(raw.get("name") or rule_id),
            description=str(raw.get("description") or ""),
            topic=topic,
            conditions=conditions,
            sustain_count=sustain_count,
            cooldown_seconds=max(0.0, float(trigger.get("cooldown_seconds") or 0.0)),
            actions=actions,
            enabled=bool(raw.get("enabled", True)),
        )

    def evaluate(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Test every condition (logical AND) against a payload.

        Parameters:
            payload (Dict[str, Any]): Event body.

        Returns:
            Tuple[bool, str]: ``(matched, joined reason)``. The reason is stored
            on the run record so an operator can see *why* a rule fired.
        """
        if not self.conditions:
            return True, "unconditional"
        reasons: List[str] = []
        for condition in self.conditions:
            matched, reason = condition.evaluate(payload)
            if not matched:
                return False, reason
            reasons.append(reason)
        return True, " AND ".join(reasons)

    def summary(self) -> Dict[str, Any]:
        """
        Serialise the rule for the API (no secrets, no callables).

        Returns:
            Dict[str, Any]: Public rule description.
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "trigger": {
                "topic": self.topic,
                "conditions": [
                    {"field": c.field, "op": c.op, "value": c.value}
                    for c in self.conditions
                ],
                "sustain": {"count": self.sustain_count},
                "cooldown_seconds": self.cooldown_seconds,
            },
            "actions": [
                {"type": a.get("type")} for a in self.actions
            ],
        }


def load_rules(path: Optional[str] = None) -> List[AutomationRule]:
    """
    Load the declarative rule file.

    A missing or malformed file yields an empty rule list plus a warning: an
    automation engine with no rules is inert, which is the safe failure mode.
    Individual invalid rules are skipped so one typo cannot disable the rest.

    Parameters:
        path (Optional[str]): Rule file path; defaults to
            ``settings.AUTOMATION_RULES_PATH``.

    Returns:
        List[AutomationRule]: Enabled rules in file order.
    """
    rules_path = Path(path or settings.AUTOMATION_RULES_PATH)
    try:
        document = json.loads(rules_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Automation rules file not found: %s", rules_path)
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Automation rules file unreadable (%s): %s", rules_path, exc)
        return []

    raw_rules = document.get("rules") if isinstance(document, dict) else document
    if not isinstance(raw_rules, list):
        logger.warning("Automation rules file %s has no 'rules' list", rules_path)
        return []

    rules: List[AutomationRule] = []
    for raw in raw_rules:
        try:
            rule = AutomationRule.from_dict(raw)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("Skipping invalid automation rule: %s", exc)
            continue
        if rule.enabled:
            rules.append(rule)
    logger.info("Loaded %d automation rule(s) from %s", len(rules), rules_path)
    return rules


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def subscription_pattern(topic: str) -> str:
    """
    Map a rule topic onto the bus subscription pattern that delivers it enriched.

    The EventBus only adds ``_topic``/``_event_id``/``_timestamp`` for wildcard
    and ``prefix/*`` subscribers (see ``EventBus.start_polling``), so listening
    on ``alert/created`` verbatim would leave runs without an event id. Any
    namespaced topic is therefore widened to its namespace (``alert/*``);
    a topic with no ``/`` has no namespace and is subscribed verbatim.

    Parameters:
        topic (str): Rule topic, e.g. ``"metric/snapshot"``.

    Returns:
        str: Subscription pattern, e.g. ``"metric/*"``.
    """
    topic = str(topic or "")
    if "/" not in topic:
        return topic
    return topic.rsplit("/", 1)[0] + "/*"


class AutomationEngine:
    """Match events against rules and run their actions off the bus loop."""

    def __init__(
        self,
        store: Optional[AutomationStore] = None,
        rules: Optional[Sequence[AutomationRule]] = None,
        max_concurrency: Optional[int] = None,
        queue_size: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
        retry_backoff_s: float = 0.25,
    ):
        """
        Parameters:
            store (Optional[AutomationStore]): Persistence; built lazily from
                the nanoc database when omitted.
            rules (Optional[Sequence[AutomationRule]]): Pre-loaded rules; loaded
                from the config file when omitted.
            max_concurrency (Optional[int]): Worker count (bounded pool).
            queue_size (Optional[int]): Maximum pending matches.
            clock (Callable[[], float]): Monotonic clock, injectable for tests.
            retry_backoff_s (float): Base backoff between webhook attempts.
        """
        self._store = store
        self._rules: List[AutomationRule] = list(rules) if rules is not None else None
        self._bus = None
        self._max_concurrency = max(
            1, int(max_concurrency or settings.AUTOMATION_MAX_CONCURRENCY)
        )
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=int(queue_size or settings.AUTOMATION_QUEUE_SIZE)
        )
        self._clock = clock
        self._retry_backoff_s = max(0.0, float(retry_backoff_s))
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._workers: List[asyncio.Task] = []
        self._streaks: Dict[str, int] = {}
        self._last_fired: Dict[str, float] = {}
        self._dropped = 0
        self._runs = 0

    # ------------------------------------------------------------- properties
    @property
    def store(self) -> AutomationStore:
        """Persistence layer, created on first use."""
        if self._store is None:
            from nanoc.core.config import settings as nanoc_settings

            self._store = AutomationStore(nanoc_settings.DB_PATH)
        return self._store

    @property
    def rules(self) -> List[AutomationRule]:
        """Rules currently loaded (loaded from disk on first access)."""
        if self._rules is None:
            self._rules = load_rules() if settings.AUTOMATION_ENABLED else []
        return self._rules

    @property
    def queue_depth(self) -> int:
        """Number of matches waiting for a worker."""
        return self._queue.qsize()

    @property
    def running(self) -> bool:
        """Whether the worker pool is active."""
        return bool(self._workers) and not all(t.done() for t in self._workers)

    def rule_by_id(self, rule_id: str) -> Optional[AutomationRule]:
        """
        Look a rule up by id.

        Parameters:
            rule_id (str): Rule identifier.

        Returns:
            Optional[AutomationRule]: The rule, or ``None``.
        """
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def reload_rules(self, path: Optional[str] = None) -> int:
        """
        Re-read the rule file (used by tests and by config-driven restarts).

        Parameters:
            path (Optional[str]): Override path.

        Returns:
            int: Number of rules loaded.
        """
        self._rules = load_rules(path)
        self._streaks.clear()
        return len(self._rules)

    # ------------------------------------------------------------------- wiring
    def bind_bus(self, bus) -> None:
        """
        Attach the event bus used for publishing run events.

        Parameters:
            bus: Object exposing ``publish(topic, payload)`` and ``subscribe``.
        """
        self._bus = bus

    def subscribe(self, bus) -> List[str]:
        """
        Register one bus subscriber per topic *namespace* a rule listens on.

        The bus delivers two kinds of payloads: an exact-topic subscriber gets
        the raw body, while a ``prefix/*`` subscriber gets the body enriched
        with ``_topic``/``_event_id``/``_timestamp``. The engine needs the
        enriched form — the event id is part of the audit trail — so it
        subscribes to the namespace pattern and reads the real topic from
        ``_topic``. :meth:`handle_event` then filters by each rule's exact
        topic, so a subscriber on ``alert/*`` never fires an ``alert/created``
        rule for an ``alert/resolved`` event.

        Parameters:
            bus: Event bus with ``subscribe(topic, callback)``.

        Returns:
            List[str]: Subscription patterns registered (e.g. ``["alert/*"]``).
        """
        patterns = sorted({subscription_pattern(rule.topic) for rule in self.rules})
        for pattern in patterns:
            bus.subscribe(pattern, self._make_handler(pattern))
        return patterns

    def _make_handler(self, pattern: str) -> Callable[[Dict[str, Any]], None]:
        """
        Build the synchronous bus callback for one subscription pattern.

        Parameters:
            pattern (str): Pattern the callback was registered for.

        Returns:
            Callable[[Dict[str, Any]], None]: Callback that only enqueues work.
        """

        def handler(payload: Dict[str, Any]) -> None:
            topic = payload.get("_topic") or pattern
            try:
                self.handle_event(topic, payload)
            except Exception as exc:  # noqa: BLE001 - the bus must survive
                logger.warning("Automation handler failed for %s: %s", topic, exc)

        return handler

    # -------------------------------------------------------------- matching
    def handle_event(
        self, topic: str, payload: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Evaluate every rule for one event and enqueue the matches.

        This is the hot path: it runs inside the EventBus polling loop, so it
        does no I/O and never awaits. Enqueueing is non-blocking; when the queue
        is full the match is dropped and counted.

        Parameters:
            topic (str): Event topic.
            payload (Dict[str, Any]): Event body.

        Returns:
            List[Dict[str, Any]]: Descriptors of the matches that were queued
            (empty when nothing matched, was cooling down or was dropped).
        """
        queued: List[Dict[str, Any]] = []
        for rule in self.rules:
            if not rule.enabled or rule.topic != topic:
                continue
            matched, reason = rule.evaluate(payload)

            if not matched:
                self._streaks[rule.id] = 0
                continue

            streak = self._streaks.get(rule.id, 0) + 1
            self._streaks[rule.id] = streak
            if streak < rule.sustain_count:
                logger.debug(
                    "Rule %s sustaining (%d/%d)", rule.id, streak, rule.sustain_count
                )
                continue

            now = self._clock()
            last = self._last_fired.get(rule.id)
            if last is not None and rule.cooldown_seconds > 0:
                if now - last < rule.cooldown_seconds:
                    logger.debug("Rule %s in cooldown", rule.id)
                    continue

            # Reset the streak so a sustained condition re-fires only after
            # another full sustain window, not on every subsequent event.
            self._streaks[rule.id] = 0
            self._last_fired[rule.id] = now

            item = {
                "rule": rule,
                "payload": payload,
                "reason": reason,
                "source": "event",
                "event_id": payload.get("_event_id"),
                "topic": topic,
            }
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                self._dropped += 1
                logger.warning(
                    "Automation queue full (%d); dropped match for rule %s",
                    self._queue.maxsize,
                    rule.id,
                )
                continue
            queued.append(
                {"rule_id": rule.id, "topic": topic, "reason": reason}
            )
        return queued

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the worker pool (idempotent)."""
        if self.running:
            return
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"automation-worker-{index}")
            for index in range(self._max_concurrency)
        ]
        logger.info(
            "Automation engine started: %d rule(s), %d worker(s)",
            len(self.rules),
            self._max_concurrency,
        )

    async def stop(self) -> None:
        """Cancel the workers and wait for them to unwind."""
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            try:
                await worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._workers = []

    async def _worker(self, index: int) -> None:
        """
        Drain the queue until cancelled.

        Parameters:
            index (int): Worker number, used in log lines only.
        """
        while True:
            item = await self._queue.get()
            try:
                await self._execute(**item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad run is contained
                logger.warning("Automation worker %d run failed: %s", index, exc)
            finally:
                self._queue.task_done()

    # ---------------------------------------------------------------- execution
    async def _execute(
        self,
        rule: AutomationRule,
        payload: Dict[str, Any],
        reason: str,
        source: str = "event",
        event_id: Optional[int] = None,
        topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run every action of a rule and persist one run record per action.

        Parameters:
            rule (AutomationRule): Rule that fired.
            payload (Dict[str, Any]): Triggering event.
            reason (str): Human-readable matched condition.
            source (str): ``"event"`` or ``"manual"``.
            event_id (Optional[int]): Bus event id of the trigger.
            topic (Optional[str]): Trigger topic.

        Returns:
            List[Dict[str, Any]]: The run records that were persisted.
        """
        runs: List[Dict[str, Any]] = []
        for action in rule.actions:
            started = self._clock()
            action_type = str(action.get("type") or "").lower()
            extra: Dict[str, Any] = {}
            try:
                outcome, detail, summary, extra = await self._run_action(
                    rule, action, payload, reason
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - actions never kill the run
                logger.warning(
                    "Action %s of rule %s raised: %s", action_type, rule.id, exc
                )
                outcome, detail, summary = OUTCOME_FAILED, str(exc), action_type

            run = {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "trigger_topic": topic,
                "trigger_source": source,
                "trigger_event_id": event_id,
                "matched_condition": reason,
                "action_type": action_type,
                "action_summary": summary,
                "outcome": outcome,
                "detail": detail,
                "event_payload": payload,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "duration_ms": round((self._clock() - started) * 1000, 2),
            }
            run["id"] = self.store.record_run(run)
            pending_id = extra.get("pending_action_id")
            if pending_id and run["id"]:
                # The command was queued before the run row existed; back-fill
                # the link so the executor can cite the run that asked for it.
                self.store.link_pending_run(pending_id, run["id"])
            self._runs += 1
            runs.append(run)
            self._publish_run(run)
        return runs

    async def _run_action(
        self,
        rule: AutomationRule,
        action: Dict[str, Any],
        payload: Dict[str, Any],
        reason: str,
    ) -> Tuple[str, str, str, Dict[str, Any]]:
        """
        Dispatch one action to its handler.

        Parameters:
            rule (AutomationRule): Owning rule.
            action (Dict[str, Any]): Action document.
            payload (Dict[str, Any]): Triggering event.
            reason (str): Matched condition, used for command rationale.

        Returns:
            Tuple[str, str, str, Dict[str, Any]]: ``(outcome, detail, summary,
            extra)`` where ``extra`` carries bookkeeping such as the id of a
            queued remediation action.
        """
        action_type = str(action.get("type") or "").lower()
        if action_type == "notify":
            message = render_template(action.get("message") or rule.name, payload)
            logger.info("[automation:%s] %s", rule.id, message)
            return OUTCOME_SUCCESS, message, message, {}
        if action_type == "webhook":
            return await self._run_webhook(rule, action, payload, reason)
        if action_type == "command":
            return self._run_command(rule, action, payload, reason)
        return (
            OUTCOME_FAILED,
            f"unsupported action type '{action_type}'",
            action_type,
            {},
        )

    def _resolve_secret(self, value: Optional[str]) -> str:
        """
        Resolve a ``${SETTING_NAME}`` reference against the app settings.

        Parameters:
            value (Optional[str]): Literal value or ``${NAME}`` reference.

        Returns:
            str: Resolved value; empty when unset, which callers treat as
            "not configured" rather than guessing a URL.
        """
        text = str(value or "")
        if text.startswith("${") and text.endswith("}"):
            name = text[2:-1]
            return str(getattr(settings, name, "") or "")
        return text

    async def _run_webhook(
        self,
        rule: AutomationRule,
        action: Dict[str, Any],
        payload: Dict[str, Any],
        reason: str,
    ) -> Tuple[str, str, str]:
        """
        POST the event to a configured webhook, with timeout and bounded retry.

        Failures are returned as an outcome, never raised: a flapping receiver
        must not take the engine down.

        Parameters:
            rule (AutomationRule): Owning rule.
            action (Dict[str, Any]): Action document (``url``, ``timeout_s``,
                ``retries``).
            payload (Dict[str, Any]): Triggering event.
            reason (str): Matched condition.

        Returns:
            Tuple[str, str, str, Dict[str, Any]]: ``(outcome, detail, summary,
            extra)``.
        """
        url = self._resolve_secret(action.get("url") or "${AUTOMATION_WEBHOOK_URL}")
        if not url:
            return (
                OUTCOME_SKIPPED,
                "no webhook URL configured (set AUTOMATION_WEBHOOK_URL)",
                "webhook not configured",
                {},
            )

        timeout = min(
            MAX_TIMEOUT_S,
            float(action.get("timeout_s") or settings.AUTOMATION_WEBHOOK_TIMEOUT_S),
        )
        retries = min(MAX_RETRIES, max(0, int(action.get("retries", settings.AUTOMATION_WEBHOOK_RETRIES))))
        body = {
            "source": "autonomous-noc",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "matched_condition": reason,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": payload,
        }
        raw = json.dumps(body, default=str)
        headers = {"Content-Type": "application/json", "User-Agent": "autonomous-noc/1"}
        secret = self._resolve_secret("${AUTOMATION_WEBHOOK_SECRET}")
        if secret:
            digest = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
            headers["X-NOC-Signature"] = f"sha256={digest}"

        summary = f"POST {url}"
        last_error = ""
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, content=raw, headers=headers)
                if 200 <= response.status_code < 300:
                    return (
                        OUTCOME_SUCCESS,
                        f"HTTP {response.status_code}",
                        summary,
                        {"status_code": response.status_code},
                    )
                last_error = f"HTTP {response.status_code}"
                # 4xx (except 429) will not improve on retry.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    break
            except Exception as exc:  # noqa: BLE001 - network failure is an outcome
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries and self._retry_backoff_s:
                await asyncio.sleep(min(self._retry_backoff_s * (2**attempt), 2.0))
        return OUTCOME_FAILED, last_error or "webhook failed", summary, {}

    def _run_command(
        self,
        rule: AutomationRule,
        action: Dict[str, Any],
        payload: Dict[str, Any],
        reason: str,
    ) -> Tuple[str, str, str]:
        """
        Queue a remediation command — **without executing it**.

        The row in ``automation_pending_actions`` is the whole deliverable: a
        later, security-reviewed executor owns approval and execution.

        Parameters:
            rule (AutomationRule): Owning rule.
            action (Dict[str, Any]): Action document.
            payload (Dict[str, Any]): Triggering event.
            reason (str): Matched condition, recorded as the rationale.

        Returns:
            Tuple[str, str, str, Dict[str, Any]]: ``(outcome, detail, summary,
            extra)``; ``extra`` carries ``pending_action_id``.
        """
        command = render_template(action.get("command") or "", payload).strip()
        if not command:
            return (
                OUTCOME_SKIPPED,
                "command action has no command",
                "empty command",
                {},
            )
        action_id = self.store.enqueue_action(
            {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "command": command,
                "reason": render_template(action.get("reason") or reason, payload),
                "severity": action.get("severity") or "warning",
                "requested_by": f"automation:{rule.id}",
                "trigger_topic": payload.get("_topic") or rule.topic,
            }
        )
        if action_id is None:
            return OUTCOME_FAILED, "could not queue command", command, {}
        logger.info("[automation:%s] queued remediation #%s: %s", rule.id, action_id, command)
        return (
            OUTCOME_QUEUED,
            f"pending action #{action_id}",
            command,
            {"pending_action_id": action_id},
        )

    # ------------------------------------------------------------------ manual
    async def run_rule(
        self,
        rule_id: str,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "manual",
    ) -> List[Dict[str, Any]]:
        """
        Run a rule immediately, regardless of its conditions.

        Used by ``POST /api/automation/rules/{rule_id}/run`` so an operator can
        test a rule or apply remediation by hand. The recorded
        ``matched_condition`` says it was forced, so history stays honest.

        Parameters:
            rule_id (str): Rule to run.
            payload (Optional[Dict[str, Any]]): Synthetic event body.
            source (str): Trigger source recorded on the run.

        Returns:
            List[Dict[str, Any]]: Persisted run records.

        Raises:
            KeyError: When no rule has that id.
        """
        rule = self.rule_by_id(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        payload = dict(payload or {})
        matched, reason = rule.evaluate(payload)
        if not matched:
            reason = f"manual override (condition not met: {reason})"
        async with self._semaphore:
            return await self._execute(
                rule=rule,
                payload=payload,
                reason=reason,
                source=source,
                event_id=payload.get("_event_id"),
                topic=payload.get("_topic") or rule.topic,
            )

    # --------------------------------------------------------------- publishing
    def _publish_run(self, run: Dict[str, Any]) -> None:
        """
        Publish one completed run onto the event bus for ``/ws`` fan-out.

        The message is flat, following the shape PR #48 established: the run
        fields sit at the root, ``type``/``topic``/``ts`` are added by
        ``websockets.envelope`` when the wildcard subscriber forwards it.

        Parameters:
            run (Dict[str, Any]): Run record that was just persisted.
        """
        if self._bus is None:
            return
        try:
            self._bus.publish(TOPIC_AUTOMATION_RUN, run)
        except Exception as exc:  # noqa: BLE001 - telemetry must not break a run
            logger.warning("Failed to publish automation run: %s", exc)

    def status(self) -> Dict[str, Any]:
        """
        Snapshot the engine's state for ``GET /api/automation/status``.

        Returns:
            Dict[str, Any]: Enabled flag, rule count, worker/queue state and
            run outcome totals.
        """
        return {
            "enabled": bool(settings.AUTOMATION_ENABLED),
            "rules": len(self.rules),
            "running": self.running,
            "workers": self._max_concurrency,
            "queue_depth": self.queue_depth,
            "queue_capacity": self._queue.maxsize,
            "dropped": self._dropped,
            "runs_this_process": self._runs,
            "outcomes": self.store.outcome_counts(),
            "topics": list(DEFAULT_TOPICS),
            "run_topic": TOPIC_AUTOMATION_RUN,
        }


#: Process-wide engine, bound to the bus in the app lifespan (same pattern as
#: ``app.core.streaming.engine``).
engine = AutomationEngine()
