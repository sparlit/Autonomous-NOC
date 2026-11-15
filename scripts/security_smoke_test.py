"""Live smoke test for the hardened NOC control plane (run against a real server).

Checks the Definition-of-Done items:
  (a) /ws rejects an unauthenticated client and accepts a tokened one
  (b) the terminal WS survives a garbage text frame and a malformed resize
  (c) manual automation run is denied without the admin token, allowed with it
"""
import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8011"
WS = "ws://127.0.0.1:8011"
ADMIN = "smoke-admin-token"
OPERATOR = "smoke-operator-token"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def check_dashboard_ws():
    # (a) unauthenticated
    try:
        async with websockets.connect(f"{WS}/ws"):
            record("a1 /ws rejects unauthenticated client", False, "connected")
    except Exception as exc:
        record("a1 /ws rejects unauthenticated client", True, type(exc).__name__)

    # (a) tokened
    try:
        async with websockets.connect(f"{WS}/ws?token={OPERATOR}") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            record("a2 /ws accepts tokened client", hello.get("type") == "hello",
                   f"auth={hello.get('auth')}")
            await ws.send("garbage frame")
            await ws.send(json.dumps({"type": "ping", "ts": "t"}))
            pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            record("a3 /ws survives garbage frame", pong.get("type") == "pong", pong.get("type"))
    except Exception as exc:
        record("a2 /ws accepts tokened client", False, repr(exc))


async def check_terminal():
    # unauthenticated terminal
    try:
        async with websockets.connect(f"{WS}/api/terminal/ws"):
            record("b0 terminal rejects unauthenticated client", False, "connected")
    except Exception as exc:
        record("b0 terminal rejects unauthenticated client", True, type(exc).__name__)

    async with websockets.connect(f"{WS}/api/terminal/ws?token={ADMIN}") as ws:
        await asyncio.sleep(0.4)
        await ws.send("this is not json at all")
        await ws.send(json.dumps({"type": "resize"}))                 # missing rows+cols
        await ws.send(json.dumps({"type": "resize", "cols": 100}))    # missing rows
        await ws.send(json.dumps({"type": "input", "data": 42}))      # wrong type
        await asyncio.sleep(0.2)
        await ws.send(json.dumps({"type": "input", "data": "echo SMOKE_ALIVE_$((6*7))\n"}))
        out = b""
        deadline = asyncio.get_event_loop().time() + 6
        while asyncio.get_event_loop().time() < deadline:
            try:
                out += await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                break
            if b"SMOKE_ALIVE_42" in out:
                break
        record("b1 terminal survives garbage text frame", True, f"{len(out)} bytes echoed")
        record("b2 terminal survives malformed resize frames", True, "session still open")
        record("b3 terminal still executes input after bad frames",
               b"SMOKE_ALIVE_42" in out, out[-40:].decode("utf-8", "replace").strip())


async def check_automation():
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
        r = await client.post("/api/automation/rules/any/run", json={})
        record("c1 manual run denied without token", r.status_code == 401, f"{r.status_code}")

        r = await client.post("/api/automation/rules/any/run", json={},
                              headers={"Authorization": f"Bearer {OPERATOR}"})
        record("c2 manual run denied for OPERATOR", r.status_code == 403, f"{r.status_code}")

        rules = (await client.get("/api/automation/rules",
                                  headers={"Authorization": f"Bearer {ADMIN}"})).json()
        rule_id = (rules.get("rules") or [{}])[0].get("id")
        if not rule_id:
            record("c3 manual run allowed with admin token", False, "no rules loaded")
            return
        r = await client.post(f"/api/automation/rules/{rule_id}/run", json={"severity": "critical"},
                              headers={"Authorization": f"Bearer {ADMIN}"})
        record("c3 manual run allowed with admin token", r.status_code == 200, f"{r.status_code}")

        r = await client.get("/api/automation/pending")
        record("c4 pending queue denied without token", r.status_code == 401, f"{r.status_code}")
        r = await client.get("/api/automation/pending",
                             headers={"Authorization": f"Bearer {ADMIN}"})
        record("c5 pending queue allowed with admin token", r.status_code == 200, f"{r.status_code}")

        r = await client.get("/health")
        record("d1 /health public", r.status_code == 200 and "auth" in r.json(),
               json.dumps(r.json().get("auth", {})))
        r = await client.get("/api/monitoring/status")
        # This server is started WITH an operator token, so reads must be gated.
        # The open-read (no operator token) mode is covered by the pytest suite.
        record("d2 reads require the operator token when configured",
               r.status_code == 401, f"{r.status_code}")
        r = await client.get("/api/monitoring/status",
                             headers={"Authorization": f"Bearer {OPERATOR}"})
        record("d3 reads allowed with the operator token", r.status_code == 200, f"{r.status_code}")


async def main():
    await check_dashboard_ws()
    await check_terminal()
    await check_automation()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
