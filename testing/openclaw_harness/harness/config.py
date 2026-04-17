from __future__ import annotations

import os
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = HARNESS_ROOT.parent.parent

STATE_DB_PATH = HARNESS_ROOT / "state.db"
ESCALATIONS_DIR = HARNESS_ROOT / "escalations"
CYCLES_LOG = HARNESS_ROOT / "cycles.log"

HARNESS_HOST = os.environ.get("HARNESS_HOST", "127.0.0.1")
HARNESS_PORT = int(os.environ.get("HARNESS_PORT", "19000"))

NODE_URLS: dict[str, str] = {
    "node-a": "http://localhost:18001",
    "node-b": "http://localhost:18002",
    "node-c": "http://localhost:18003",
}
NODE_NAMES = tuple(NODE_URLS.keys())

FEDERATION_RUNTIME_DIR = Path(os.environ.get("MASSCLAW_FED_RUNTIME_DIR", "/tmp/massclaw_fed"))
NODE_LOG_PATHS: dict[str, Path] = {
    name: FEDERATION_RUNTIME_DIR / f"{name}.log" for name in NODE_NAMES
}

LOG_RINGBUFFER_LINES = 2000

FEDERATION_PROBE_INTERVAL_SECONDS = 5.0

RECENT_RUNS_PER_SCENARIO = 5

SCENARIO_ROTATION = (
    ("s1", "s2", "s3"),
    ("s4", "s5", "s6"),
    ("s7", "s8", "s9"),
)

FIX_SAFETY_ALLOWLIST = (
    ".github/workflows/",
    "alembic/versions/",
    "deploy/fly/",
    ".env",
)
MAX_CONSECUTIVE_PATCH_ATTEMPTS = 3
