"""REST surface for the automation engine.

Additive only: nothing here changes an existing endpoint. Three read/write
routes plus a status tile:

``GET  /api/automation/rules``            list the declarative rules
``GET  /api/automation/runs``             paged run history (filterable)
``POST /api/automation/rules/{id}/run``   run a rule now (manual trigger)
``GET  /api/automation/pending``          the remediation queue (nothing runs it yet)
``GET  /api/automation/status``           engine health for the dashboard

Authorization (see :mod:`app.core.auth`)
----------------------------------------
* Reading rules, run history and engine status is ``OPERATOR``.
* **Executing** a rule (:func:`run_rule`) and reading the remediation queue
  (:func:`list_pending_actions`) are ``ADMIN``: a manual trigger can run
  remediation commands, and the queue lists the commands themselves. Both are
  denied outright until ``NOC_ADMIN_TOKEN`` is configured.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.core.auth import Role, require_role
from app.core.automation import engine as automation_engine

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_role(Role.OPERATOR))])

#: Privileged dependency for the surfaces that can change the control node.
require_admin = require_role(Role.ADMIN)

#: Page-size guard rails: the dashboard asks for 25-50, exports maybe 200.
MAX_PAGE_SIZE = 200


def _clamp(value: int, low: int, high: int) -> int:
    """
    Clamp an integer to an inclusive range.

    Parameters:
        value (int): Requested value.
        low (int): Minimum.
        high (int): Maximum.

    Returns:
        int: Clamped value.
    """
    return max(low, min(int(value), high))


@router.get("/rules")
async def list_rules() -> Dict[str, Any]:
    """
    List the automation rules the engine has loaded.

    Returns:
        dict: ``{"rules": [...], "count": n, "enabled": bool}``.
    """
    rules = [rule.summary() for rule in automation_engine.rules]
    return {
        "rules": rules,
        "count": len(rules),
        "enabled": automation_engine.status()["enabled"],
    }


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    rule_id: Optional[str] = None,
    outcome: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List automation runs, newest first, with pagination metadata.

    Parameters:
        limit (int): Page size (1-200).
        offset (int): Rows to skip.
        rule_id (Optional[str]): Only runs for this rule.
        outcome (Optional[str]): Only runs with this outcome
            (``success``/``failed``/``skipped``/``queued``).

    Returns:
        dict: ``{"runs": [...], "total": n, "limit": l, "offset": o,
        "has_more": bool}``.
    """
    limit = _clamp(limit, 1, MAX_PAGE_SIZE)
    runs, total = automation_engine.store.list_runs(
        limit=limit, offset=offset, rule_id=rule_id, outcome=outcome
    )
    return {
        "runs": runs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(runs) < total,
    }


@router.post(
    "/rules/{rule_id}/run",
    dependencies=[Depends(require_admin)],
)
async def run_rule(
    rule_id: str, payload: Optional[Dict[str, Any]] = Body(default=None)
) -> Dict[str, Any]:
    """
    Run one rule immediately, bypassing its trigger conditions.

    The optional body becomes the synthetic event payload, so an operator can
    exercise a rule with a realistic event without waiting for one to arrive.

    Parameters:
        rule_id (str): Rule id from ``GET /api/automation/rules``.
        payload (Optional[Dict[str, Any]]): Synthetic event body.

    Returns:
        dict: ``{"rule_id": ..., "trigger": "manual", "runs": [...]}``.

    Raises:
        HTTPException: 404 when the rule does not exist.
    """
    try:
        runs = await automation_engine.run_rule(rule_id, payload or {})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown rule '{rule_id}'")
    return {"rule_id": rule_id, "trigger": "manual", "runs": runs}


@router.get(
    "/pending",
    dependencies=[Depends(require_admin)],
)
async def list_pending_actions(
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query("pending"),
) -> Dict[str, Any]:
    """
    List remediation commands queued by ``command`` actions.

    Nothing in the NOC executes these yet — the endpoint exists so the
    security-reviewed executor and the dashboard have a stable contract.

    Parameters:
        limit (int): Page size (1-200).
        offset (int): Rows to skip.
        status (Optional[str]): Filter by status; ``all`` returns every row.

    Returns:
        dict: ``{"actions": [...], "total": n, "limit": l, "offset": o}``.
    """
    limit = _clamp(limit, 1, MAX_PAGE_SIZE)
    effective_status = None if (status or "").lower() == "all" else status
    actions, total = automation_engine.store.list_pending_actions(
        limit=limit, offset=offset, status=effective_status
    )
    return {"actions": actions, "total": total, "limit": limit, "offset": offset}


@router.get("/status")
async def automation_status() -> Dict[str, Any]:
    """
    Report engine health: rule count, workers, queue depth and outcome totals.

    Returns:
        dict: Output of :meth:`app.core.automation.AutomationEngine.status`.
    """
    return automation_engine.status()
