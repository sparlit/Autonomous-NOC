"""Helpers shared by the backend test-suite.

Route introspection lives here because FastAPI changed the shape of
``app.routes``: older releases flatten an included router's child routes into
``app.routes``, while newer ones keep an opaque wrapper object that carries the
router and its include prefix instead of a ``.path`` attribute. Reading
``route.path`` off ``app.routes`` therefore only works on one of the two, so the
tests go through :func:`route_paths` rather than depending on the version.
"""

from typing import Iterable, Set


def _collect(routes: Iterable, paths: Set[str], prefix: str = "") -> None:
    """
    Recursively collect URI paths from a route table.

    Parameters:
        routes (Iterable): Route objects from ``app.routes`` or a router.
        paths (Set[str]): Accumulator, mutated in place.
        prefix (str): Include prefix inherited from parent routers.
    """
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(f"{prefix}{path}")
            continue

        # Newer FastAPI: an included router is kept as a wrapper. Recurse into
        # the original router using the prefix it was included with.
        included_router = getattr(route, "original_router", None)
        if included_router is None:
            included_router = getattr(route, "router", None)
        if included_router is None or not hasattr(included_router, "routes"):
            continue

        include_context = getattr(route, "include_context", None)
        sub_prefix = getattr(include_context, "prefix", "") or ""
        _collect(included_router.routes, paths, f"{prefix}{sub_prefix}")


def route_paths(app) -> Set[str]:
    """
    Return every URI path registered on a FastAPI application.

    Works for both flattened and wrapped ``include_router`` layouts, and
    includes websocket routes.

    Parameters:
        app: FastAPI application instance.

    Returns:
        Set[str]: Full paths such as ``{"/health", "/api/data/tasks"}``.
    """
    paths: Set[str] = set()
    _collect(app.routes, paths)
    return paths
