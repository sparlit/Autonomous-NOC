# NOC security model — RBAC, tokens and hardening

Scope of the hardening pass: token-based role-based access control on the
backend, the terminal/WebSocket fixes it depended on, and the configuration
needed to run the stack safely. No new third-party dependencies were added.

## Roles

| Role | Granted by | Can do |
| --- | --- | --- |
| `ADMIN` | `NOC_ADMIN_TOKEN` (or the legacy `TERMINAL_ACCESS_TOKEN`) | Everything `OPERATOR` can, **plus**: terminal WebSocket (`/api/terminal/ws`), manual rule execution (`POST /api/automation/rules/{id}/run`), remediation queue (`GET /api/automation/pending`) |
| `OPERATOR` | `NOC_OPERATOR_TOKEN` | Read-only: `/api/monitoring/*`, `/api/data/*`, `/api/alerts/*`, `/api/automation/{rules,runs,status}`, `/api/stream/status`, and the `/ws` dashboard stream |
| `ANONYMOUS` | nothing presented | `/health` only |

Role checks live in `backend/app/core/auth.py`; enforcement is a FastAPI
dependency (`require_role`) for HTTP and `authorize_websocket` for sockets.

### Fail-closed / fail-open rules

* **Privileged surfaces fail closed.** If `NOC_ADMIN_TOKEN` is unset, the
  terminal, manual runs and the pending queue return `403` (or close the
  WebSocket with `1008`) with an explicit "not configured" reason. No repository
  default can ever open a root shell — the old hard-coded
  `TERMINAL_ACCESS_TOKEN = "secret-foss-token"` default is gone.
* **Read surfaces fail open until configured.** While `NOC_OPERATOR_TOKEN` is
  unset, read endpoints and `/ws` are open so a fresh clone renders a live
  dashboard; the API logs a warning at startup. Set the token for anything
  reachable beyond localhost.
* **Invalid ≠ degraded.** A wrong token resolves to `ANONYMOUS`, never to
  `OPERATOR`. Tokens are compared with `hmac.compare_digest` (constant time) and
  an empty configured token never matches an empty presented token.

`GET /health` reports the mode without leaking secrets:

```json
{"status": "ok", "auth": {"admin_configured": true, "operator_configured": false,
                          "read_endpoints": "open", "privileged_endpoints": "token"}}
```

## Token transport

1. `Authorization: Bearer <token>` — REST clients, curl, the Next.js app
2. `X-NOC-Token: <token>` — proxies that strip `Authorization`
3. `?token=<token>` — WebSocket handshakes (browsers cannot set headers)

## Configuration

```bash
# backend/.env or the compose environment
NOC_ADMIN_TOKEN=$(openssl rand -hex 32)     # unset = privileged surfaces disabled
NOC_OPERATOR_TOKEN=$(openssl rand -hex 32)  # unset = reads are open (dev only)
```

`infra/docker-compose.yml` forwards both from the compose `.env`. Never commit
real tokens; `NOC_*_TOKEN` are read from the environment with empty defaults.

## Frontend wiring

The elevation gate at `/terminal` stores the operator's token in
`sessionStorage` under `noc.terminal.operator-token`
(`TOKEN_STORAGE_KEY` in `frontend/src/hooks/use-live-data.ts`). The same module
exports `withToken()` (appends `?token=` to the `/ws` URL) and `authHeaders()`
(`Authorization: Bearer` for REST calls), so **one elevation authorizes the
shell, the dashboard stream and every read request**. Nothing is sent before the
operator elevates; with no stored token the app behaves exactly as before (open
read endpoints, `/ws` rejected only when an operator token is configured).

## Hardening applied in this pass

* Terminal WebSocket authorized **before** `accept()` — no PTY, no fd, no
  fan-out registration for a rejected peer.
* Terminal frame handling is per-frame: malformed JSON, non-object frames, a
  resize missing `rows`/`cols`, or a non-string `data` field are logged and
  skipped instead of tearing down the operator's shell. Dimensions are clamped
  to `1..4096` before `TIOCSWINSZ`.
* `/ws` (dashboard event stream) requires `OPERATOR`+ before accept.
* `DB_PATH` is resolved to an absolute path (relative values are anchored to the
  repo root), so the backend starts from any working directory.
* `/api/alerts/summary` reads live rule-engine state instead of a hard-coded
  mock; `/api/monitoring/status` returns real values or an explicit `"unknown"`
  with a `sources` provenance map (no fabricated `99.99%` / `1.2 Gbps`).
* One long-lived SQLite connection (WAL, lock-guarded) replaces a
  connection-per-metric-per-tick in the streaming path; retention trimming is
  throttled to once a minute.

## Deployment checklist

1. Set `NOC_ADMIN_TOKEN` and `NOC_OPERATOR_TOKEN` from a secret store.
2. Terminate TLS in front of the backend (Caddy/nginx/Traefik) and never expose
   port 8000 directly; `wss://` for the sockets. Prometheus, Alertmanager, Keep
   and the exporters should sit on an internal network only.
3. Restrict CORS: `main.py` currently allows `*` because the dashboard is served
   from a different origin in development — pin it to the dashboard origin.
4. Rotate tokens by restarting the backend; there is no session state to revoke.

## Known gaps / follow-ups

* **Keycloak/OIDC is not wired yet.** `resolve_role()` is the single seam: an
  OIDC integration would validate a Keycloak JWT there and map realm roles onto
  `Role`. That needs a JWT library (`python-jose`/`authlib`), which this pass
  deliberately did not add; short-lived static tokens are the current mechanism.
* **No rate limiting or lockout** on failed token attempts. Put the API behind a
  proxy that enforces it (Caddy `rate_limit`, nginx `limit_req`).
* **`/api/monitoring/metrics` proxies an arbitrary PromQL string** for any
  `OPERATOR`; Prometheus itself must be network-restricted.
* **The PTY runs as the container user with no command allow-list** — the
  terminal is a full shell by design. Consider `nsjail`/`bubblewrap` or a
  read-only rootfs for the backend container.
* **Audit log**: terminal sessions are logged at open/close with the role; there
  is no per-keystroke audit trail yet.
