"""Poll each node's /api/v1/memory/sync/summary periodically; detect convergence."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

try:
    from app.identity.signer import encode_multibase, generate_keypair, sign_bytes
    from app.identity.did import build_did_key
except ImportError:  # backend import failed — federation_probe runs in degraded mode
    generate_keypair = None  # type: ignore[assignment]
    sign_bytes = None  # type: ignore[assignment]
    encode_multibase = None  # type: ignore[assignment]
    build_did_key = None  # type: ignore[assignment]

from . import config
from .events import bus


def _signed_headers(method: str, path: str) -> dict[str, str] | None:
    if not (generate_keypair and sign_bytes and encode_multibase and build_did_key):
        return None
    keypair = generate_keypair()
    did = build_did_key(keypair.public_bytes)
    ts = str(int(time.time()))
    payload = f"{ts}|{method}|{path}".encode("utf-8")
    sig = sign_bytes(payload, keypair.private_seed)
    return {
        "X-Peer-DID": did,
        "X-Peer-Timestamp": ts,
        "X-Peer-Signature": encode_multibase(sig),
    }


async def fetch_summary(client: httpx.AsyncClient, base_url: str) -> str | None:
    path = "/api/v1/memory/sync/summary"
    headers = _signed_headers("GET", path) or {}
    try:
        response = await client.get(f"{base_url}{path}", headers=headers, timeout=5.0)
        if response.status_code != 200:
            return None
        data: Any = response.json()
        return data.get("root") if isinstance(data, dict) else None
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        return None


async def probe_once() -> tuple[dict[str, str | None], bool]:
    async with httpx.AsyncClient() as client:
        tasks = {
            name: asyncio.create_task(fetch_summary(client, url))
            for name, url in config.NODE_URLS.items()
        }
        roots: dict[str, str | None] = {}
        for name, task in tasks.items():
            roots[name] = await task
    populated = [r for r in roots.values() if r]
    in_sync = len(populated) >= 2 and len(set(populated)) == 1
    return roots, in_sync


class ProbeLoop:
    def __init__(self, store, interval: float = config.FEDERATION_PROBE_INTERVAL_SECONDS) -> None:
        self.store = store
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                roots, in_sync = await probe_once()
                await self.store.add_probe(roots, in_sync)
                await bus.publish(
                    "federation.probe",
                    {"roots": roots, "in_sync": in_sync, "ts": time.time()},
                )
            except Exception as exc:  # defensive — never crash the loop
                await bus.publish("federation.probe.error", {"error": str(exc)})
            await asyncio.sleep(self.interval)
