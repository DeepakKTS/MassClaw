"""Mount-integrity guard for the API surface.

A router that silently fails to mount is close to invisible: the app boots,
``/health`` answers, and only the specific calls that needed that router
404. CI caught exactly that class of problem the hard way once — two tests
asserted a route was wired by walking ``app.router.routes``, and when
FastAPI 0.141 stopped flattening included sub-routers into the parent, the
walk reported no path for *anything* mounted under a prefix.

So two rules here:

1. Enumerate through ``app.openapi()["paths"]``. That is the public,
   version-stable view of what is actually reachable. Never walk
   ``app.router.routes`` looking for prefixed paths.
2. Assert per-prefix coverage rather than exact path lists, so adding an
   endpoint never breaks this file but dropping a whole router does.

WebSocket routes are deliberately absent: they do not appear in the
OpenAPI schema. They are covered by ``tests/integration`` instead.
"""

from __future__ import annotations

import pytest

from app.main import app

# Every prefix mounted in app/api/router.py, plus the surfaces mounted at
# the HTTP root in app/main.py. Keep in sync when a router is added.
EXPECTED_API_PREFIXES = [
    "/api/v1/agents",
    "/api/v1/approvals",
    "/api/v1/audit",
    "/api/v1/demo",
    "/api/v1/evolution",
    "/api/v1/identity",
    "/api/v1/mcp",
    "/api/v1/memory",
    "/api/v1/memory/sync",
    "/api/v1/policy",
    "/api/v1/projects",
    "/api/v1/tasks",
    "/api/v1/tools",
    "/api/v1/trust",
    "/api/v1/wallet",
    "/api/v1/workflows",
]

# Paths external agents are told to use (see the ``endpoints`` contract in
# app/api/router.py::discover_capabilities) plus the discovery and probe
# surfaces. A rename here is a breaking change for stock agents, so it
# should have to be made deliberately.
CRITICAL_PATHS = [
    "/.well-known/agent-facts.json",
    "/api/v1/capabilities",
    "/api/v1/approvals/pending",
    "/api/v1/approvals/stream",
    "/api/v1/memory/query",
    "/api/v1/workflows/submit",
    "/health",
    "/ready",
    "/system/metrics",
]


@pytest.fixture(scope="module")
def mounted_paths() -> set[str]:
    return set(app.openapi()["paths"])


@pytest.mark.parametrize("prefix", EXPECTED_API_PREFIXES)
def test_router_is_mounted(prefix: str, mounted_paths: set[str]) -> None:
    """Each sub-router contributes at least one reachable path."""
    matching = [p for p in mounted_paths if p.startswith(prefix)]
    assert matching, (
        f"No mounted path starts with {prefix!r} — that router failed to "
        f"mount, or its prefix changed. Mounted prefixes: "
        f"{sorted({p.rsplit('/', 1)[0] for p in mounted_paths})}"
    )


@pytest.mark.parametrize("path", CRITICAL_PATHS)
def test_critical_path_is_mounted(path: str, mounted_paths: set[str]) -> None:
    """Paths that external agents and probes depend on by name."""
    assert path in mounted_paths, f"{path!r} is not mounted"


def test_openapi_schema_builds_and_is_not_truncated(mounted_paths: set[str]) -> None:
    """A floor on the surface size.

    Generating the schema at all is half the value: it forces FastAPI to
    resolve every response model, so a malformed annotation anywhere fails
    here rather than at first request. The count guards against a partial
    mount that still happens to satisfy the checks above.
    """
    assert len(mounted_paths) >= 90, (
        f"Only {len(mounted_paths)} paths mounted; expected at least 90. A router may have partially failed to mount."
    )
