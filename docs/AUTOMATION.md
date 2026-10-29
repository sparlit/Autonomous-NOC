# Automation engine

The event-driven reaction layer: rules subscribe to the live EventBus topics the
dashboard already streams, and run actions when their conditions match. This is
the "Autonomous" half of the Autonomous NOC — the dashboard *shows*, this layer
*acts*.

```
StreamEngine ──publish──▶ EventBus (SQLite events table)
                              │
                    poll (1s) │  alert/*, metric/*, data/*
                              ▼
                    AutomationEngine.handle_event      ← matching only, no I/O
                              │ put_nowait (bounded queue, 200)
                              ▼
                    worker pool (4 tasks, bounded)
                              │
              ┌───────────────┼────────────────┐
          notify           webhook           command
        (run record)   (POST + retry)   (pending_actions row)
                              │
                              ▼
              automation_runs (SQLite) ──publish──▶ automation/run ──▶ /ws
```

## Components

| File | Role |
| --- | --- |
| `backend/config/automation_rules.json` | Declarative rules (JSON — stdlib only, no YAML dependency) |
| `backend/app/core/automation.py` | Matcher, rule model, engine, action executors |
| `backend/app/core/automation_store.py` | SQLite run history + pending-action queue |
| `backend/app/api/endpoints/automation.py` | REST surface |
| `backend/main.py` | Lifespan wiring: bind bus, subscribe, start/stop workers |

## Rule schema

```json
{
  "id": "latency-sustained-remediation",
  "name": "Sustained latency breach → notify + remediation",
  "description": "Free text, shown in GET /api/automation/rules",
  "enabled": true,
  "trigger": {
    "topic": "metric/snapshot",
    "conditions": [{"field": "latency_ms", "op": "gt", "value": 500}],
    "sustain": {"count": 3},
    "cooldown_seconds": 300
  },
  "actions": [
    {"type": "notify", "message": "Latency {latency_ms}ms sustained"},
    {"type": "command", "command": "noc-remediate latency --threshold-ms 500",
     "reason": "Sustained latency SLO breach", "severity": "critical"}
  ]
}
```

* `trigger.topic` — one of `alert/created`, `alert/resolved`, `metric/snapshot`,
  `data/updated` (any bus topic works; those are the ones produced today).
* `trigger.conditions` — logical AND. Empty list means "every event on the topic".
* `trigger.sustain.count` — the condition must hold on N **consecutive** events on
  that topic before the rule fires; a single non-matching event resets the
  window. `count: 3` on `metric/snapshot` (2 s cadence) ≈ "sustained for 6 s".
* `trigger.cooldown_seconds` — after a fire, ignore the rule for this long.
  Without it a 2 s metric stream would fire the same rule on every tick.
* `actions[].message` / `command` — `{field}` placeholders are filled from the
  event payload (`{devices.critical}` reads nested paths). Unknown fields render
  empty rather than failing the action.

### Operators

`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `startswith`, `in`, `not_in`,
`exists`.

Comparison is type-tolerant: `500`, `"500"` and `500.0` compare equal, and
`"critical"` orders against other strings. `ne`/`not_in` match when the field is
**absent** (an event with no `severity` is "not critical"). Operators are a
lookup table, never `eval`, so a rule file is data and not code.

## Actions

| Type | Behaviour |
| --- | --- |
| `notify` | Renders the message, logs it and records a `success` run. |
| `webhook` | `POST`s `{source, rule_id, rule_name, matched_condition, timestamp, event}` as JSON with a timeout (default 5 s) and bounded retries (default 2, max 5). 4xx (except 429) is not retried. Failures become a `failed` run — the engine never dies. Optional `X-NOC-Signature: sha256=<hmac>` when `AUTOMATION_WEBHOOK_SECRET` is set. |
| `command` | **Queues** the command in `automation_pending_actions` with `status='pending'`. Nothing in this service executes it. |

### Why `command` does not execute

Rule files are configuration; letting configuration run shell commands would make
every rule edit a privilege escalation. The queue is the interface: a later,
security-reviewed executor owns allow-listing, approval and execution, and the
dashboard can render "pending remediation" today.

## Secrets

Webhook URLs never live in the rule file. `"url": "${AUTOMATION_WEBHOOK_URL}"`
resolves from settings/env at execution time. The default is empty, which makes
the action report `skipped` with a clear reason instead of posting to a guessed
host. Same for `${AUTOMATION_WEBHOOK_SECRET}`.

## Settings (`backend/app/core/config.py`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `AUTOMATION_ENABLED` | `true` | Master switch; false loads no rules |
| `AUTOMATION_RULES_PATH` | `backend/config/automation_rules.json` | Rule file (resolved from the backend dir, CWD-independent) |
| `AUTOMATION_WEBHOOK_URL` | `""` | Default webhook target (empty = skipped) |
| `AUTOMATION_WEBHOOK_SECRET` | `""` | HMAC secret for `X-NOC-Signature` |
| `AUTOMATION_WEBHOOK_TIMEOUT_S` | `5` | Per-attempt timeout |
| `AUTOMATION_WEBHOOK_RETRIES` | `2` | Extra attempts |
| `AUTOMATION_MAX_CONCURRENCY` | `4` | Worker pool size |
| `AUTOMATION_QUEUE_SIZE` | `200` | Bounded queue depth |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/automation/rules` | Loaded rules (no secrets) |
| GET | `/api/automation/runs` | Paged run history: `limit` (1-200), `offset`, `rule_id`, `outcome` |
| POST | `/api/automation/rules/{rule_id}/run` | Run a rule now; body = synthetic event payload |
| GET | `/api/automation/pending` | Queued remediation commands (`status=all` for everything) |
| GET | `/api/automation/status` | Engine health: workers, queue depth, outcome totals |

All additive — no existing endpoint changed.

Manual runs bypass the trigger conditions (operator intent) and say so in
`matched_condition` (`"manual override (condition not met: ...)"`), so the
history stays honest.

## WebSocket

Every completed run is republished on the bus as topic `automation/run`, which
the existing `*` subscriber fans out over `/ws` with the flat shape PR #48
established:

```json
{
  "type": "automation",
  "topic": "automation/run",
  "ts": "2026-10-29 17:08:07.605456",
  "event_id": 368,
  "rule_id": "critical-alert-webhook",
  "outcome": "success",
  "action_type": "notify",
  "duration_ms": 0.05,
  "_topic": "automation/run",
  "_event_id": 368
}
```

`type` comes from `websockets.TOPIC_TYPE_PREFIXES` (`automation/` → `automation`).

## Persistence

Two tables in the existing nanoc SQLite file (`nanoc/memory/nanoc.db`), created
idempotently on first use:

* `automation_runs` — one row per (rule, action): rule id/name, trigger topic,
  trigger source (`event`/`manual`), bus event id, matched condition, action
  type/summary, outcome, detail, the triggering payload (truncated at 4 kB),
  timestamp, duration in ms. Trimmed to the newest 5 000 rows.
* `automation_pending_actions` — the remediation queue: command, reason,
  severity, status, `requested_by` (`automation:<rule_id>`), the run that asked
  for it.

## Operating notes

* **The bus loop is never blocked.** Matching runs inline (no I/O); actions run
  on the worker pool. If the queue is full (200) new matches are dropped and
  counted in `/api/automation/status` (`dropped`) rather than stalling the stream.
* **Failure isolation.** A webhook that hangs or 500s, a locked database or a
  malformed rule file all degrade to a log line and an outcome; the engine keeps
  running and other rules keep firing.
* **Rules are loaded once at startup.** Edit the file and restart the backend, or
  call `AutomationEngine.reload_rules()`.

## Verifying end-to-end locally

```bash
# 1. deps (the repo has no venv; keep it off the small /home volume)
python3 -m venv /opt/nocvenv
/opt/nocvenv/bin/pip install -q fastapi uvicorn httpx pydantic python-dotenv \
    pydantic-settings websockets pytest pytest-asyncio

# 2. tests (151 baseline + the automation suite)
cd <repo> && PYTHONPATH=.:backend /opt/nocvenv/bin/python -m pytest backend/tests -q

# 3. a webhook receiver, so the webhook action has somewhere to post
python - <<'EOF' &
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        open("/tmp/hook.log", "a").write(body.decode() + "\n")
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 8111), H).serve_forever()
EOF

# 4. boot the API with the webhook configured
cd <repo> && AUTOMATION_WEBHOOK_URL=http://127.0.0.1:8111/hook \
  PYTHONPATH=.:backend /opt/nocvenv/bin/python -m uvicorn main:app \
  --host 127.0.0.1 --port 8100

# 5. watch a rule fire without touching the stream: Prometheus is usually
#    unreachable locally, so the stream itself raises `prometheus_unreachable`
#    and the `prometheus-blind-escalate` rule fires within ~5 s.
curl -s localhost:8100/api/automation/status
curl -s "localhost:8100/api/automation/runs?limit=5"

# 6. manual run of the webhook rule (body = synthetic event payload)
curl -s -X POST localhost:8100/api/automation/rules/critical-alert-webhook/run \
  -H 'Content-Type: application/json' \
  -d '{"rule":"latency_slo","title":"Latency SLO breach","severity":"critical"}'
cat /tmp/hook.log        # the POST the rule made

# 7. the command action only queues — nothing executes
curl -s -X POST localhost:8100/api/automation/rules/latency-sustained-remediation/run \
  -H 'Content-Type: application/json' -d '{"latency_ms": 912.4}'
curl -s localhost:8100/api/automation/pending
```

## Not in this change

* Executing queued commands (deliberately: needs the security-reviewed executor
  and an allow-list).
* Dashboard rendering of automation runs — the data and the `automation` WS
  message exist for it.
* Rule CRUD over the API (rules are files; a DB-backed editor is a later step).
