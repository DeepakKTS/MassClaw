"""Unit tests for the CRDT / provenance fields on MemoryRecord.

These cover the schema-layer invariants: enum values, default values on the
Pydantic request/response models, the ``is_signed`` helper, and the shape
expected by the content-addressed retrieval endpoint. Database-level tests
(migration upgrade/downgrade, real inserts, GIN index) live in
``tests/integration/test_memory_crdt_migration.py`` where Alembic + Postgres
are available.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.schemas.memory import (
    MemoryQueryRequest,
    MemoryResponse,
    MemoryWriteRequest,
)


# ---------------------------------------------------------------- RecordState


class TestRecordStateEnum:
    def test_values_are_stable_strings(self) -> None:
        assert RecordState.ACTIVE.value == "active"
        assert RecordState.SUPERSEDED.value == "superseded"
        assert RecordState.HISTORICAL.value == "historical"
        assert RecordState.TOMBSTONED.value == "tombstoned"

    def test_roundtrip_through_value(self) -> None:
        for state in RecordState:
            assert RecordState(state.value) is state

    def test_is_str_subclass(self) -> None:
        """Callers use record_state.value or str(record_state) interchangeably."""
        assert isinstance(RecordState.ACTIVE, str)
        assert RecordState.ACTIVE == "active"


# ---------------------------------------------------------------- Write request


class TestMemoryWriteRequest:
    def _minimal_payload(self) -> dict:
        return {
            "workflow_id": str(uuid.uuid4()),
            "content": "hello world",
        }

    def test_defaults_when_crdt_fields_absent(self) -> None:
        req = MemoryWriteRequest.model_validate(self._minimal_payload())
        assert req.author_did is None
        assert req.parent_hashes == []
        assert req.signature is None
        assert req.content_hash is None

    def test_accepts_full_crdt_payload(self) -> None:
        did = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"
        parent = "z" + "a" * 50
        payload = {
            **self._minimal_payload(),
            "author_did": did,
            "parent_hashes": [parent],
            "signature": "z" + "b" * 80,
            "hash": "z" + "c" * 50,
        }
        req = MemoryWriteRequest.model_validate(payload)
        assert req.author_did == did
        assert req.parent_hashes == [parent]
        assert req.signature is not None and req.signature.startswith("z")
        assert req.content_hash is not None and req.content_hash.startswith("z")

    def test_content_hash_alias_accepts_hash_key(self) -> None:
        """HTTP clients post ``hash`` (schema v1 key); the alias must resolve it."""
        payload = {**self._minimal_payload(), "hash": "z" + "d" * 40}
        req = MemoryWriteRequest.model_validate(payload)
        assert req.content_hash == "z" + "d" * 40

    def test_author_did_max_length(self) -> None:
        payload = {**self._minimal_payload(), "author_did": "x" * 600}
        with pytest.raises(ValidationError):
            MemoryWriteRequest.model_validate(payload)

    def test_signature_max_length(self) -> None:
        payload = {**self._minimal_payload(), "signature": "z" + "b" * 300}
        with pytest.raises(ValidationError):
            MemoryWriteRequest.model_validate(payload)

    def test_hash_max_length(self) -> None:
        payload = {**self._minimal_payload(), "hash": "z" + "c" * 200}
        with pytest.raises(ValidationError):
            MemoryWriteRequest.model_validate(payload)

    def test_parent_hashes_defaults_to_empty_list(self) -> None:
        req = MemoryWriteRequest.model_validate(self._minimal_payload())
        assert req.parent_hashes == []
        # And a missing value doesn't become ``None``.
        assert req.parent_hashes is not None


# ---------------------------------------------------------------- Query request


class TestMemoryQueryRequest:
    def test_include_states_defaults_to_active(self) -> None:
        req = MemoryQueryRequest(query="deadline")
        assert req.include_states == [RecordState.ACTIVE]

    def test_accepts_multiple_states(self) -> None:
        req = MemoryQueryRequest(
            query="deadline",
            include_states=[RecordState.ACTIVE, RecordState.SUPERSEDED],
        )
        assert RecordState.SUPERSEDED in req.include_states

    def test_accepts_string_state_values(self) -> None:
        req = MemoryQueryRequest.model_validate({"query": "deadline", "include_states": ["active", "historical"]})
        assert set(req.include_states) == {RecordState.ACTIVE, RecordState.HISTORICAL}

    def test_rejects_unknown_state(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQueryRequest.model_validate({"query": "deadline", "include_states": ["nonsense"]})

    def test_author_did_filter(self) -> None:
        req = MemoryQueryRequest(query="x", author_did="did:key:z6Mk...")
        assert req.author_did == "did:key:z6Mk..."


# ---------------------------------------------------------------- Response


class TestMemoryResponse:
    def _base_attrs(self) -> dict:
        now = datetime.now(UTC)
        return {
            "memory_id": uuid.uuid4(),
            "workflow_id": uuid.uuid4(),
            "source_agent_id": None,
            "memory_type": MemoryType.RESULT,
            "content": "hello",
            "metadata_": {},
            "confidence": 0.9,
            "freshness": 1.0,
            "version": 1,
            "parent_version_id": None,
            "expires_at": None,
            "created_at": now,
            "author_did": None,
            "parent_hashes": [],
            "signature": None,
            "content_hash": None,
            "record_state": RecordState.ACTIVE,
        }

    def test_from_attributes_maps_metadata_alias(self) -> None:
        attrs = SimpleNamespace(**self._base_attrs())
        resp = MemoryResponse.model_validate(attrs, from_attributes=True)
        assert resp.metadata == {}

    def test_preserves_crdt_fields(self) -> None:
        attrs_dict = self._base_attrs()
        attrs_dict["author_did"] = "did:key:z6Mk..."
        attrs_dict["parent_hashes"] = ["zabc", "zdef"]
        attrs_dict["signature"] = "z" + "a" * 80
        attrs_dict["content_hash"] = "z" + "b" * 50
        attrs_dict["record_state"] = RecordState.SUPERSEDED
        attrs = SimpleNamespace(**attrs_dict)
        resp = MemoryResponse.model_validate(attrs, from_attributes=True)
        assert resp.author_did == "did:key:z6Mk..."
        assert resp.parent_hashes == ["zabc", "zdef"]
        assert resp.record_state == RecordState.SUPERSEDED

    def test_hash_serialises_under_alias(self) -> None:
        attrs_dict = self._base_attrs()
        attrs_dict["content_hash"] = "z" + "b" * 50
        attrs = SimpleNamespace(**attrs_dict)
        resp = MemoryResponse.model_validate(attrs, from_attributes=True)
        dumped = resp.model_dump(by_alias=True)
        assert "hash" in dumped
        assert dumped["hash"] == "z" + "b" * 50


# ---------------------------------------------------------------- Model helper


class TestMemoryRecordIsSigned:
    def test_signed_when_both_hash_and_signature_present(self) -> None:
        record = MemoryRecord(
            workflow_id=uuid.uuid4(),
            memory_type=MemoryType.RESULT,
            content="x",
            confidence=0.8,
            freshness=1.0,
            version=1,
            parent_hashes=[],
            record_state=RecordState.ACTIVE,
            content_hash="z" + "a" * 40,
            signature="z" + "b" * 80,
        )
        assert record.is_signed is True

    def test_unsigned_when_either_missing(self) -> None:
        record_no_hash = MemoryRecord(
            workflow_id=uuid.uuid4(),
            memory_type=MemoryType.RESULT,
            content="x",
            confidence=0.8,
            freshness=1.0,
            version=1,
            parent_hashes=[],
            record_state=RecordState.ACTIVE,
            content_hash=None,
            signature="z" + "b" * 80,
        )
        record_no_sig = MemoryRecord(
            workflow_id=uuid.uuid4(),
            memory_type=MemoryType.RESULT,
            content="x",
            confidence=0.8,
            freshness=1.0,
            version=1,
            parent_hashes=[],
            record_state=RecordState.ACTIVE,
            content_hash="z" + "a" * 40,
            signature=None,
        )
        assert record_no_hash.is_signed is False
        assert record_no_sig.is_signed is False

    def test_repr_hides_full_hash(self) -> None:
        full_hash = "zABCDEFG" + ("0" * 40)
        record = MemoryRecord(
            workflow_id=uuid.uuid4(),
            memory_type=MemoryType.RESULT,
            content="x",
            confidence=0.8,
            freshness=1.0,
            version=1,
            parent_hashes=[],
            record_state=RecordState.ACTIVE,
            content_hash=full_hash,
            signature="z" + "b" * 80,
        )
        rendered = repr(record)
        # First 8 characters of the hash must appear (content-addressed ID prefix).
        assert full_hash[:8] in rendered
        # Full hash's tail (40 zeroes) must not leak.
        assert ("0" * 40) not in rendered
