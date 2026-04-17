"""End-to-end pytest wrapper for the three-node federation demo.

This test invokes ``scripts/demo_federation_test.sh`` directly — the
canonical bash scenario is the single source of truth for the
partition-heal-converge demo, and re-implementing it in Python would
drift. The test pipes stderr/stdout into the pytest failure log so an
investigator can see every line the shell script printed.

The test is marked ``@pytest.mark.e2e``. CI excludes this marker by
default (the default Backend job filters to ``tests/unit`` and
``tests/integration``); run it locally with:

    docker compose version  # prerequisite
    pytest tests/e2e -v -m e2e

It takes ~90 seconds on a warm laptop (image build is cached after the
first run).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "demo_federation_test.sh"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.e2e
@pytest.mark.skipif(not _docker_available(), reason="docker daemon not reachable")
def test_federation_partition_heal_convergence() -> None:
    """Partition node-c, write conflicting records, heal, assert convergence.

    The assertions live in the shell script (it exits 1 on any mismatch),
    so this test reduces to: "did the script exit cleanly?". All the
    shell's output is captured so a failure reveals exactly which step
    broke — summary row, by-hash fetch, or the 15-second Merkle-root
    convergence budget.
    """
    assert _SCRIPT.exists(), f"missing script: {_SCRIPT}"
    # Long timeout because the first run has to build the backend image.
    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("FED_E2E_TIMEOUT_SECONDS", "600")),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"demo_federation_test.sh failed.\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
        )
