"""Harness FastAPI server — state + telemetry only, no LLM work happens here."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config
from .events import bus
from .federation_probe import ProbeLoop
from .process_logs import streamer
from .state import Store, init_db

store = Store(config.STATE_DB_PATH)
probe_loop = ProbeLoop(store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(config.STATE_DB_PATH)
    await streamer.start()
    await probe_loop.start()
    try:
        yield
    finally:
        await probe_loop.stop()
        await streamer.stop()


app = FastAPI(title="OpenClaw Harness", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "openclaw-harness"}


@app.get("/harness/nodes")
async def nodes_snapshot() -> dict[str, Any]:
    """Everything a dashboard needs to paint initial state."""
    probe = await store.latest_probe()
    log_snapshot = streamer.snapshot()
    return {
        "nodes": [
            {
                "name": name,
                "url": url,
                "log_tail": log_snapshot.get(name, []),
            }
            for name, url in config.NODE_URLS.items()
        ],
        "latest_probe": probe,
    }


@app.get("/harness/scenarios/recent")
async def scenarios_recent() -> dict[str, list[dict[str, Any]]]:
    return await store.recent_runs_per_scenario(limit=config.RECENT_RUNS_PER_SCENARIO)


@app.get("/harness/fixes")
async def recent_fixes(limit: int = 20) -> list[dict[str, Any]]:
    return await store.recent_fixes(limit=limit)


@app.get("/harness/runs/{run_id}")
async def run_detail(run_id: int) -> dict[str, Any]:
    detail = await store.run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="run not found")
    return detail


class StartCycleRequest(BaseModel):
    note: str | None = None


class CycleResponse(BaseModel):
    cycle_id: int


@app.post("/harness/cycles", response_model=CycleResponse)
async def start_cycle(body: StartCycleRequest) -> CycleResponse:
    cid = await store.start_cycle()
    await bus.publish("cycle.started", {"cycle_id": cid, "note": body.note or ""})
    return CycleResponse(cycle_id=cid)


class CloseCycleRequest(BaseModel):
    summary: str


@app.post("/harness/cycles/{cycle_id}/close")
async def close_cycle(cycle_id: int, body: CloseCycleRequest) -> dict[str, str]:
    await store.close_cycle(cycle_id, body.summary)
    await bus.publish("cycle.closed", {"cycle_id": cycle_id, "summary": body.summary})
    return {"status": "closed"}


class StartRunRequest(BaseModel):
    scenario: str = Field(pattern=r"^s[1-9]$")
    cycle_id: int | None = None


class RunResponse(BaseModel):
    run_id: int


@app.post("/harness/runs", response_model=RunResponse)
async def start_run(body: StartRunRequest) -> RunResponse:
    run_id = await store.start_run(body.scenario, body.cycle_id)
    await bus.publish(
        "scenario.started",
        {"run_id": run_id, "scenario": body.scenario, "cycle_id": body.cycle_id},
    )
    return RunResponse(run_id=run_id)


class CloseRunRequest(BaseModel):
    outcome: str
    failure_class: str | None = None
    narrative: str = ""


@app.post("/harness/runs/{run_id}/close")
async def close_run(run_id: int, body: CloseRunRequest) -> dict[str, str]:
    await store.close_run(
        run_id,
        outcome=body.outcome,
        failure_class=body.failure_class,
        narrative=body.narrative,
    )
    topic = "scenario.passed" if body.outcome == "passed" else "scenario.failed"
    await bus.publish(
        topic,
        {
            "run_id": run_id,
            "outcome": body.outcome,
            "failure_class": body.failure_class,
        },
    )
    return {"status": "closed"}


class TurnRequest(BaseModel):
    idx: int
    role: str
    content: str


@app.post("/harness/runs/{run_id}/turns")
async def add_turn(run_id: int, body: TurnRequest) -> dict[str, str]:
    await store.add_turn(run_id, body.idx, body.role, body.content)
    await bus.publish(
        "agent.turn",
        {"run_id": run_id, "idx": body.idx, "role": body.role, "content": body.content},
    )
    return {"status": "ok"}


class HttpCallRequest(BaseModel):
    method: str
    url: str
    status: int | None = None
    body_snippet: str = ""


@app.post("/harness/runs/{run_id}/http")
async def add_http(run_id: int, body: HttpCallRequest) -> dict[str, str]:
    await store.add_http(run_id, body.method, body.url, body.status, body.body_snippet)
    await bus.publish(
        "agent.http",
        {
            "run_id": run_id,
            "method": body.method,
            "url": body.url,
            "status": body.status,
        },
    )
    return {"status": "ok"}


class FixCommitRequest(BaseModel):
    sha: str
    scenario: str | None = None
    message: str


@app.post("/harness/fixes")
async def add_fix(body: FixCommitRequest) -> dict[str, str]:
    await store.add_fix(body.sha, body.scenario, body.message)
    await bus.publish("fix.committed", body.model_dump())
    return {"status": "ok"}


@app.websocket("/harness/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await bus.subscribe()
    try:
        probe = await store.latest_probe()
        await websocket.send_json(
            {"topic": "hello", "payload": {"latest_probe": probe}}
        )
        while True:
            event = await queue.get()
            await websocket.send_json({"topic": event.topic, "payload": event.payload})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await bus.unsubscribe(queue)
