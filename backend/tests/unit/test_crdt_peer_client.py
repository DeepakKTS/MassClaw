"""Unit tests for :class:`PeerClient` — outbound signed CRDT sync calls."""

from __future__ import annotations

import httpx
import pytest

from app.crdt.merkle import BUCKET_COUNT
from app.crdt.peer_auth import (
    PEER_DID_HEADER,
    PEER_SIG_HEADER,
    PEER_TS_HEADER,
    verify_peer_request,
)
from app.crdt.peer_client import (
    PeerClient,
    PeerInvalidResponseError,
    PeerRecord,
    PeerUnauthorizedError,
    PeerUnavailableError,
)
from app.identity.signer import generate_keypair


PEER_BASE = "https://peer.massclaw.test"


def _client(handler, *, keypair=None) -> PeerClient:
    kp = keypair or generate_keypair()
    return PeerClient(
        base_url=PEER_BASE,
        keypair=kp,
        transport=httpx.MockTransport(handler),
    )


def _empty_summary_body() -> dict:
    return {
        "root": "0" * 64,
        "buckets": [None] * BUCKET_COUNT,
        "record_count": 0,
        "scope": "instance",
    }


# ---------------------------------------------------------------- signed headers


class TestSignedHeaders:
    @pytest.mark.asyncio
    async def test_headers_are_attached_on_every_call(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(dict(request.headers))
            return httpx.Response(200, json=_empty_summary_body())

        client = _client(handler)
        await client.fetch_summary()
        assert len(captured) == 1
        headers = captured[0]
        assert PEER_DID_HEADER.lower() in headers
        assert PEER_TS_HEADER.lower() in headers
        assert PEER_SIG_HEADER.lower() in headers
        assert headers[PEER_DID_HEADER.lower()].startswith("did:key:")

    @pytest.mark.asyncio
    async def test_signed_payload_verifies_server_side(self):
        """The client's signature must pass the server's verify_peer_request check."""
        kp = generate_keypair()
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["did"] = request.headers.get(PEER_DID_HEADER.lower())
            captured["ts"] = request.headers.get(PEER_TS_HEADER.lower())
            captured["sig"] = request.headers.get(PEER_SIG_HEADER.lower())
            captured["method"] = request.method
            captured["path"] = request.url.path
            return httpx.Response(200, json=_empty_summary_body())

        client = _client(handler, keypair=kp)
        await client.fetch_summary()

        # Replay the exact verification the server will do.
        verify_peer_request(
            method=captured["method"],
            path=captured["path"],
            did=captured["did"],
            timestamp=captured["ts"],
            signature=captured["sig"],
        )


# ---------------------------------------------------------------- fetch_summary


class TestFetchSummary:
    @pytest.mark.asyncio
    async def test_parses_summary(self):
        def handler(_: httpx.Request) -> httpx.Response:
            body = _empty_summary_body()
            body["root"] = "a" * 64
            body["record_count"] = 5
            return httpx.Response(200, json=body)

        summary = await _client(handler).fetch_summary()
        assert summary.root == "a" * 64
        assert summary.record_count == 5
        assert len(summary.buckets) == BUCKET_COUNT

    @pytest.mark.asyncio
    async def test_passes_workflow_id_param(self):
        import uuid

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["query"] = dict(request.url.params)
            return httpx.Response(200, json=_empty_summary_body())

        wf = uuid.uuid4()
        await _client(handler).fetch_summary(workflow_id=wf)
        assert seen["query"].get("workflow_id") == str(wf)

    @pytest.mark.asyncio
    async def test_wrong_bucket_count_rejected(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"root": "x", "buckets": [None] * 10, "record_count": 0},
            )

        with pytest.raises(PeerInvalidResponseError):
            await _client(handler).fetch_summary()

    @pytest.mark.asyncio
    async def test_non_object_body_rejected(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "an", "object"])

        with pytest.raises(PeerInvalidResponseError):
            await _client(handler).fetch_summary()


# ---------------------------------------------------------------- fetch_bucket_hashes


class TestFetchBucketHashes:
    @pytest.mark.asyncio
    async def test_returns_valid_hashes(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "bucket_index": 10,
                    "scope": "instance",
                    "hashes": ["zaaa", "zbbb", "zccc"],
                },
            )

        result = await _client(handler).fetch_bucket_hashes(10)
        assert result == ["zaaa", "zbbb", "zccc"]

    @pytest.mark.asyncio
    async def test_filters_invalid_prefix(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "hashes": ["zvalid", "not-multibase", "", 123, "zanother"],
                },
            )

        result = await _client(handler).fetch_bucket_hashes(0)
        assert result == ["zvalid", "zanother"]

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_bucket(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hashes": []})

        with pytest.raises(ValueError):
            await _client(handler).fetch_bucket_hashes(BUCKET_COUNT + 1)

    @pytest.mark.asyncio
    async def test_non_list_hashes_rejected(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hashes": "oops"})

        with pytest.raises(PeerInvalidResponseError):
            await _client(handler).fetch_bucket_hashes(0)


# ---------------------------------------------------------------- fetch_records


class TestFetchRecords:
    @pytest.mark.asyncio
    async def test_empty_hashes_returns_empty_list_without_request(self):
        call_count = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, json={"records": [], "requested": 0, "found": 0})

        result = await _client(handler).fetch_records([])
        assert result == []
        assert call_count["n"] == 0

    @pytest.mark.asyncio
    async def test_parses_records(self):
        import uuid

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "requested": 1,
                    "found": 1,
                    "records": [
                        {
                            "hash": "zabc" + "d" * 40,
                            "workflow_id": str(uuid.uuid4()),
                            "source_agent_id": None,
                            "memory_type": "result",
                            "content": "deadline April 30",
                            "confidence": 0.9,
                            "metadata": {"domain": "planning"},
                            "author_did": "did:key:z6Mk...",
                            "parent_hashes": [],
                            "signature": "zsig" + "a" * 80,
                            "record_state": "active",
                        }
                    ],
                },
            )

        result = await _client(handler).fetch_records(["zabc" + "d" * 40])
        assert len(result) == 1
        record = result[0]
        assert isinstance(record, PeerRecord)
        assert record.content == "deadline April 30"
        assert record.confidence == 0.9
        assert record.author_did == "did:key:z6Mk..."

    @pytest.mark.asyncio
    async def test_too_many_hashes_rejected_client_side(self):
        handler = lambda _: httpx.Response(200, json={"records": []})  # noqa: E731
        with pytest.raises(ValueError):
            await _client(handler).fetch_records(["z" + "a" * 40] * 101)

    @pytest.mark.asyncio
    async def test_record_missing_hash_rejected(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"records": [{"content": "no hash here"}]},
            )

        with pytest.raises(PeerInvalidResponseError):
            await _client(handler).fetch_records(["zaaa"])


# ---------------------------------------------------------------- transport errors


class TestErrorSurfaces:
    @pytest.mark.asyncio
    async def test_401_raises_unauthorized(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "not allowed"})

        with pytest.raises(PeerUnauthorizedError):
            await _client(handler).fetch_summary()

    @pytest.mark.asyncio
    async def test_403_raises_unauthorized(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={})

        with pytest.raises(PeerUnauthorizedError):
            await _client(handler).fetch_summary()

    @pytest.mark.asyncio
    async def test_500_raises_unavailable(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(PeerUnavailableError):
            await _client(handler).fetch_summary()

    @pytest.mark.asyncio
    async def test_connect_error_raises_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(PeerUnavailableError):
            await _client(handler).fetch_summary()

    @pytest.mark.asyncio
    async def test_non_json_body_raises_invalid(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(PeerInvalidResponseError):
            await _client(handler).fetch_summary()
