"""SQLite state store — runs, agent_turns, http_calls, fix_commits."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER,
    scenario TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    outcome TEXT,
    failure_class TEXT,
    narrative TEXT,
    FOREIGN KEY (cycle_id) REFERENCES cycles(id)
);

CREATE INDEX IF NOT EXISTS idx_runs_scenario_started ON runs(scenario, started_at DESC);

CREATE TABLE IF NOT EXISTS agent_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_turns_run_idx ON agent_turns(run_id, idx);

CREATE TABLE IF NOT EXISTS http_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    body_snippet TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_http_run ON http_calls(run_id, ts);

CREATE TABLE IF NOT EXISTS fix_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha TEXT NOT NULL UNIQUE,
    scenario TEXT,
    message TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fix_ts ON fix_commits(ts DESC);

CREATE TABLE IF NOT EXISTS federation_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_a_root TEXT,
    node_b_root TEXT,
    node_c_root TEXT,
    in_sync INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_probe_ts ON federation_probes(ts DESC);
"""


async def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def start_cycle(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO cycles(started_at) VALUES (?)", (time.time(),)
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def close_cycle(self, cycle_id: int, summary: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE cycles SET ended_at = ?, summary = ? WHERE id = ?",
                (time.time(), summary, cycle_id),
            )
            await db.commit()

    async def start_run(self, scenario: str, cycle_id: int | None) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO runs(cycle_id, scenario, started_at) VALUES (?, ?, ?)",
                (cycle_id, scenario, time.time()),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def close_run(
        self,
        run_id: int,
        *,
        outcome: str,
        failure_class: str | None,
        narrative: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE runs
                   SET ended_at = ?, outcome = ?, failure_class = ?, narrative = ?
                   WHERE id = ?""",
                (time.time(), outcome, failure_class, narrative, run_id),
            )
            await db.commit()

    async def add_turn(
        self, run_id: int, idx: int, role: str, content: str
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO agent_turns(run_id, idx, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                (run_id, idx, role, content, time.time()),
            )
            await db.commit()

    async def add_http(
        self,
        run_id: int,
        method: str,
        url: str,
        status: int | None,
        body_snippet: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO http_calls(run_id, ts, method, url, status, body_snippet)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, time.time(), method, url, status, body_snippet[:2000]),
            )
            await db.commit()

    async def add_fix(self, sha: str, scenario: str | None, message: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO fix_commits(sha, scenario, message, ts) VALUES (?, ?, ?, ?)",
                (sha, scenario, message, time.time()),
            )
            await db.commit()

    async def add_probe(
        self, roots: dict[str, str | None], in_sync: bool
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO federation_probes(ts, node_a_root, node_b_root, node_c_root, in_sync)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    roots.get("node-a"),
                    roots.get("node-b"),
                    roots.get("node-c"),
                    1 if in_sync else 0,
                ),
            )
            await db.commit()

    async def recent_runs_per_scenario(self, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
        scenarios = ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9")
        out: dict[str, list[dict[str, Any]]] = {}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            for s in scenarios:
                cursor = await db.execute(
                    """SELECT id, started_at, ended_at, outcome, failure_class
                       FROM runs WHERE scenario = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (s, limit),
                )
                rows = await cursor.fetchall()
                out[s] = [dict(r) for r in rows]
        return out

    async def run_detail(self, run_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            run = dict(row)
            cursor = await db.execute(
                "SELECT idx, role, content, ts FROM agent_turns WHERE run_id = ? ORDER BY idx",
                (run_id,),
            )
            turns = [dict(r) for r in await cursor.fetchall()]
            cursor = await db.execute(
                "SELECT ts, method, url, status, body_snippet FROM http_calls WHERE run_id = ? ORDER BY ts",
                (run_id,),
            )
            http = [dict(r) for r in await cursor.fetchall()]
            return {"run": run, "turns": turns, "http_calls": http}

    async def recent_fixes(self, limit: int = 20) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT sha, scenario, message, ts FROM fix_commits ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def latest_probe(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM federation_probes ORDER BY ts DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
