"""Token-based role-based access control for the NOC control plane.

The product needs exactly two privileges right now:

``OPERATOR``
    Read-only. Monitoring, telemetry, topology, alert and automation *history*
    endpoints, plus the dashboard event stream (``/ws``).

``ADMIN``
    Everything an operator can do **and** the surfaces that can change the
    control node: the privileged terminal WebSocket, manual rule execution and
    the remediation queue (``pending actions``).

Unauthenticated peers are ``ANONYMOUS`` and are denied every privileged
surface.

Design decisions
----------------
* **Dependency-light.** Standard library only (``hmac`` for constant-time
  comparison) plus FastAPI primitives the project already depends on. No new
  packages, no JWTs, no session store — a bearer token is enough for a control
  plane that is deployed behind its own network boundary.
* **Fail closed on privilege.** An ``ADMIN`` surface can only be reached when
  ``NOC_ADMIN_TOKEN`` is actually configured *and* presented. A deployment that
  forgets to set the secret gets a 403/1008 denial with an explanatory reason —
  never an open shell.
* **Reads stay usable.** ``OPERATOR`` surfaces are open while
  ``NOC_OPERATOR_TOKEN`` is unset (a fresh clone still shows a live dashboard)
  and require the token the moment one is configured. The asymmetry is
  deliberate: a non-destructive read is not worth breaking local development
  over, a root shell is.
* **Constant-time comparison.** Tokens are compared with
  :func:`hmac.compare_digest` so a wrong guess cannot be refined by timing.

Token transport
---------------
Accepted, in order of precedence:

1. ``Authorization: Bearer <token>`` (REST clients, curl, the Next.js app)
2. ``X-NOC-Token: <token>`` (proxies that strip ``Authorization``)
3. ``?token=<token>`` (WebSocket handshakes — browsers cannot set headers on a
   ``WebSocket`` constructor)

Configuration lives in :mod:`app.core.config`
(``NOC_ADMIN_TOKEN`` / ``NOC_OPERATOR_TOKEN``). ``TERMINAL_ACCESS_TOKEN`` from
the legacy ``nanoc`` settings is still honoured as an admin alias so existing
deployments keep working, but it no longer has a hard-coded default.
"""

import hmac
import logging
from enum import Enum
from typing import Any, Optional

from fastapi import HTTPException, Request, WebSocket, status

from app.core.config import settings as app_settings
from nanoc.core.config import settings as nanoc_settings

logger = logging.getLogger(__name__)

#: WebSocket close code used for every authorization denial (RFC 6455 §7.4.1,
#: "policy violation"). The frontend's elevation gate keys its error banner off
#: this value, so it must not change.
WS_POLICY_VIOLATION = 1008


class Role(str, Enum):
    """Ordered roles. Higher privilege compares greater in :data:`_RANK`."""

    ANONYMOUS = "anonymous"
    OPERATOR = "operator"
    ADMIN = "admin"


#: Privilege ordering used by :func:`role_allows`.
_RANK = {Role.ANONYMOUS: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


def _clean(token: Any) -> str:
    """
    Normalise a token candidate to a stripped string.

    Parameters:
        token (Any): Raw value from a header, query parameter or settings field.

    Returns:
        str: The stripped token, or ``""`` when the value is not a string.
    """
    if not isinstance(token, str):
        return ""
    return token.strip()


def admin_token() -> str:
    """
    The configured administrative secret, or ``""`` when none is configured.

    ``NOC_ADMIN_TOKEN`` wins; ``TERMINAL_ACCESS_TOKEN`` is the legacy alias from
    the pre-RBAC terminal pane so an existing deployment keeps working.

    Returns:
        str: Admin token (never ``None``).
    """
    return _clean(getattr(app_settings, "NOC_ADMIN_TOKEN", "")) or _clean(
        getattr(nanoc_settings, "TERMINAL_ACCESS_TOKEN", "")
    )


def operator_token() -> str:
    """
    The configured read-only secret, or ``""`` when none is configured.

    Returns:
        str: Operator token (never ``None``).
    """
    return _clean(getattr(app_settings, "NOC_OPERATOR_TOKEN", ""))


def _matches(candidate: str, expected: str) -> bool:
    """
    Constant-time token comparison that is safe for non-ASCII input.

    Parameters:
        candidate (str): Token presented by the peer.
        expected (str): Configured token. An empty value never matches.

    Returns:
        bool: ``True`` only when both are non-empty and equal.
    """
    if not candidate or not expected:
        return False
    try:
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, UnicodeError):  # pragma: no cover - defensive
        return False


def resolve_role(token: Optional[str]) -> Role:
    """
    Map a presented token onto the role it grants.

    Parameters:
        token (Optional[str]): Token from header or query string.

    Returns:
        Role: ``ADMIN``, ``OPERATOR`` or ``ANONYMOUS``. Unknown tokens are
        anonymous — an invalid credential is not a degraded credential.
    """
    candidate = _clean(token)
    if not candidate:
        return Role.ANONYMOUS
    if _matches(candidate, admin_token()):
        return Role.ADMIN
    if _matches(candidate, operator_token()):
        return Role.OPERATOR
    return Role.ANONYMOUS


def role_allows(role: Role, required: Role) -> bool:
    """
    Report whether ``role`` satisfies ``required``.

    Parameters:
        role (Role): Role the peer actually holds.
        required (Role): Minimum role the surface demands.

    Returns:
        bool: ``True`` when the peer's rank is at least the required rank.
    """
    return _RANK.get(role, 0) >= _RANK.get(required, 0)


def extract_token(connection: Any) -> str:
    """
    Pull the token out of a request-like object.

    Parameters:
        connection (Any): ``Request``, ``WebSocket`` or any object exposing
            ``headers`` (mapping) and optionally ``query_params``.

    Returns:
        str: The presented token, or ``""`` when the peer sent none.
    """
    headers = getattr(connection, "headers", None) or {}
    try:
        authorization = headers.get("authorization") or headers.get("Authorization") or ""
    except AttributeError:  # pragma: no cover - non-mapping headers
        authorization = ""
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        token = _clean(authorization[7:])
        if token:
            return token

    try:
        header_token = headers.get("x-noc-token") or headers.get("X-NOC-Token") or ""
    except AttributeError:  # pragma: no cover - non-mapping headers
        header_token = ""
    if _clean(header_token):
        return _clean(header_token)

    query_params = getattr(connection, "query_params", None)
    if query_params is not None:
        try:
            return _clean(query_params.get("token"))
        except AttributeError:  # pragma: no cover - plain dict-like
            return ""
    return ""


def authenticate(connection: Any) -> Role:
    """
    Resolve the role of the peer behind a request or WebSocket.

    Parameters:
        connection (Any): ``Request`` / ``WebSocket``.

    Returns:
        Role: Granted role (``ANONYMOUS`` when nothing valid was presented).
    """
    return resolve_role(extract_token(connection))


def is_open(required: Role) -> bool:
    """
    Report whether a surface runs in unauthenticated development mode.

    Reads are open until an operator token is configured; privileged surfaces
    are never open.

    Parameters:
        required (Role): Minimum role the surface demands.

    Returns:
        bool: ``True`` when anonymous peers are allowed through.
    """
    if required == Role.OPERATOR:
        return not operator_token()
    return False


def denial_detail(role: Role, required: Role) -> str:
    """
    Human-readable reason for a denial, safe to return to the client.

    Parameters:
        role (Role): Role the peer holds.
        required (Role): Role the surface demands.

    Returns:
        str: Explanation that names the missing configuration when relevant.
    """
    if required == Role.ADMIN and not admin_token():
        return (
            "privileged surface disabled: NOC_ADMIN_TOKEN is not configured on "
            "the control plane"
        )
    if role == Role.ANONYMOUS:
        return f"authentication required: this surface needs role '{required.value}'"
    return f"forbidden: role '{role.value}' cannot access a '{required.value}' surface"


def http_denial(role: Role, required: Role) -> HTTPException:
    """
    Build the HTTP exception for a denied request.

    ``401`` means "you sent no usable credential", ``403`` means "your
    credential is real but insufficient" (or the surface is unconfigured).

    Parameters:
        role (Role): Role the peer holds.
        required (Role): Role the surface demands.

    Returns:
        HTTPException: Ready to raise from a dependency.
    """
    if role == Role.ANONYMOUS and required == Role.ADMIN and not admin_token():
        # No credential can ever succeed here: the surface is switched off.
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=denial_detail(role, required),
        )
    if role == Role.ANONYMOUS:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=denial_detail(role, required),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=denial_detail(role, required),
    )


def require_role(required: Role):
    """
    FastAPI dependency factory enforcing a minimum role.

    Usage::

        @router.get("/status", dependencies=[Depends(require_role(Role.OPERATOR))])
        async def status(): ...

    Parameters:
        required (Role): Minimum role allowed through.

    Returns:
        Callable: Dependency returning the granted :class:`Role`.
    """

    def dependency(request: Request) -> Role:
        """
        Resolve and enforce the caller's role.

        Parameters:
            request (Request): Incoming HTTP request.

        Returns:
            Role: Granted role.

        Raises:
            HTTPException: 401/403 when the role is insufficient.
        """
        role = authenticate(request)
        if role_allows(role, required) or is_open(required):
            return role
        raise http_denial(role, required)

    dependency.__name__ = f"require_{required.value}"
    return dependency


async def authorize_websocket(websocket: WebSocket, required: Role) -> Optional[Role]:
    """
    Authorize a WebSocket **before** accepting it.

    Rejecting prior to ``accept()`` means an unauthenticated peer never gets a
    connected socket, never forks a PTY and never joins the broadcast fan-out —
    the handshake fails with a 403 instead of a live connection that is closed a
    moment later.

    Parameters:
        websocket (WebSocket): Incoming handshake.
        required (Role): Minimum role for the endpoint.

    Returns:
        Optional[Role]: The granted role, or ``None`` when the peer was rejected
        (the socket has already been closed with
        :data:`WS_POLICY_VIOLATION`).
    """
    role = authenticate(websocket)
    if role_allows(role, required) or is_open(required):
        return role

    reason = denial_detail(role, required)
    logger.warning(
        "WebSocket denied on %s (role=%s required=%s): %s",
        getattr(websocket, "url", "unknown"),
        role.value,
        required.value,
        reason,
    )
    try:
        await websocket.close(code=WS_POLICY_VIOLATION, reason=reason)
    except Exception:  # noqa: BLE001 - the peer may already be gone
        pass
    return None


def auth_status() -> dict:
    """
    Describe the current auth configuration for ``/health`` and operators.

    Never leaks a token: only whether one is configured.

    Returns:
        dict: ``admin_configured``, ``operator_configured``, ``read_endpoints``
        (``"open"`` or ``"token"``) and ``privileged_endpoints``.
    """
    return {
        "admin_configured": bool(admin_token()),
        "operator_configured": bool(operator_token()),
        "read_endpoints": "token" if operator_token() else "open",
        "privileged_endpoints": "token" if admin_token() else "disabled",
    }
