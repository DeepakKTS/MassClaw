"""Unit tests for CRDT content hashing and canonical body construction."""

from __future__ import annotations

import uuid

import pytest

from app.crdt.hashing import (
    HASH_MULTIBASE_PREFIX,
    CanonicalBody,
    build_canonical_body,
    compute_content_hash,
)
from app.models.base import MemoryType

# ---------------------------------------------------------------- Builder


class TestBuildCanonicalBody:
    def _kwargs(self, **overrides):
        defaults = dict(
            workflow_id=uuid.uuid4(),
            memory_type=MemoryType.RESULT,
            content="hello world",
            confidence=0.8,
            metadata={"k": "v"},
            parent_hashes=["za", "zb"],
            author_did="did:key:z6Mk...",
            source_agent_id=uuid.uuid4(),
        )
        defaults.update(overrides)
        return defaults

    def test_returns_canonical_body(self) -> None:
        body = build_canonical_body(**self._kwargs())
        assert isinstance(body, CanonicalBody)
        assert body.content == "hello world"
        assert body.confidence == 0.8
        assert body.author_did == "did:key:z6Mk..."

    def test_workflow_id_stringified(self) -> None:
        wf = uuid.uuid4()
        body = build_canonical_body(**self._kwargs(workflow_id=wf))
        assert body.workflow_id == str(wf)

    def test_missing_metadata_becomes_empty_dict(self) -> None:
        body = build_canonical_body(**self._kwargs(metadata=None))
        assert body.metadata == {}

    def test_parent_hashes_become_tuple(self) -> None:
        body = build_canonical_body(**self._kwargs(parent_hashes=["za", "zb"]))
        assert body.parent_hashes == ("za", "zb")

    def test_source_agent_id_optional(self) -> None:
        body = build_canonical_body(**self._kwargs(source_agent_id=None))
        assert body.source_agent_id is None

    def test_author_did_optional(self) -> None:
        body = build_canonical_body(**self._kwargs(author_did=None))
        assert body.author_did is None


# ---------------------------------------------------------------- Canonicalisation


class TestCanonicalSerialisation:
    def _body(self, **kw) -> CanonicalBody:
        defaults = dict(
            workflow_id="wf-1",
            memory_type=MemoryType.FACT,
            content="x",
            confidence=0.5,
            metadata={"a": 1, "b": 2},
            parent_hashes=["zb", "za"],
            author_did="did:key:z1",
            source_agent_id="agent-1",
        )
        defaults.update(kw)
        return build_canonical_body(**defaults)

    def test_signable_bytes_is_deterministic(self) -> None:
        body = self._body()
        assert body.to_signable_bytes() == body.to_signable_bytes()

    def test_metadata_key_order_is_irrelevant(self) -> None:
        b1 = self._body(metadata={"a": 1, "b": 2})
        b2 = self._body(metadata={"b": 2, "a": 1})
        assert b1.to_signable_bytes() == b2.to_signable_bytes()

    def test_parent_hashes_input_order_is_irrelevant(self) -> None:
        b1 = self._body(parent_hashes=["za", "zb"])
        b2 = self._body(parent_hashes=["zb", "za"])
        assert b1.to_signable_bytes() == b2.to_signable_bytes()

    def test_content_change_changes_bytes(self) -> None:
        original = self._body()
        mutated = self._body(content="x ")  # trailing space
        assert original.to_signable_bytes() != mutated.to_signable_bytes()

    def test_confidence_change_changes_bytes(self) -> None:
        original = self._body(confidence=0.5)
        mutated = self._body(confidence=0.6)
        assert original.to_signable_bytes() != mutated.to_signable_bytes()

    def test_author_did_swap_changes_bytes(self) -> None:
        original = self._body(author_did="did:key:z1")
        mutated = self._body(author_did="did:key:z2")
        assert original.to_signable_bytes() != mutated.to_signable_bytes()

    def test_confidence_integer_and_float_match(self) -> None:
        """confidence=1 and confidence=1.0 must hash identically — JSON-safe."""
        b1 = self._body(confidence=1)
        b2 = self._body(confidence=1.0)
        assert b1.to_signable_bytes() == b2.to_signable_bytes()

    def test_metadata_nested_roundtrips(self) -> None:
        body = self._body(metadata={"outer": {"inner": [1, 2, 3]}})
        # Ensure serialisation doesn't crash and is stable.
        assert body.to_signable_bytes() == body.to_signable_bytes()

    def test_non_json_metadata_value_degrades_to_string(self) -> None:
        """Callers occasionally pass UUIDs or datetimes — we coerce to str."""
        custom_id = uuid.uuid4()
        body = self._body(metadata={"id": custom_id})
        # Must not crash and must be deterministic across calls.
        a = body.to_signable_bytes()
        b = body.to_signable_bytes()
        assert a == b


# ---------------------------------------------------------------- Hashing


class TestComputeContentHash:
    def _body(self, **kw) -> CanonicalBody:
        defaults = dict(
            workflow_id="wf-1",
            memory_type=MemoryType.FACT,
            content="x",
            confidence=0.5,
            metadata={"a": 1},
            parent_hashes=[],
            author_did=None,
            source_agent_id=None,
        )
        defaults.update(kw)
        return build_canonical_body(**defaults)

    def test_hash_has_multibase_prefix(self) -> None:
        h = compute_content_hash(self._body())
        assert h.startswith(HASH_MULTIBASE_PREFIX)

    def test_same_body_produces_same_hash(self) -> None:
        b1 = self._body()
        b2 = self._body()
        assert compute_content_hash(b1) == compute_content_hash(b2)

    def test_metadata_key_order_is_irrelevant_to_hash(self) -> None:
        b1 = self._body(metadata={"a": 1, "b": 2})
        b2 = self._body(metadata={"b": 2, "a": 1})
        assert compute_content_hash(b1) == compute_content_hash(b2)

    def test_parent_hash_order_is_irrelevant_to_hash(self) -> None:
        b1 = self._body(parent_hashes=["za", "zb"])
        b2 = self._body(parent_hashes=["zb", "za"])
        assert compute_content_hash(b1) == compute_content_hash(b2)

    def test_any_content_change_changes_hash(self) -> None:
        original = self._body(content="hello")
        for mutated in (
            self._body(content="hello "),
            self._body(content="Hello"),
            self._body(confidence=0.51),
            self._body(metadata={"a": 2}),
            self._body(parent_hashes=["zx"]),
            self._body(author_did="did:key:z1"),
            self._body(source_agent_id=str(uuid.uuid4())),
            self._body(workflow_id="wf-2"),
            self._body(memory_type=MemoryType.REASONING),
        ):
            assert compute_content_hash(original) != compute_content_hash(mutated)

    def test_hash_length_is_bounded(self) -> None:
        """SHA-256 + base58btc decodes to ~44-46 characters — well under the DB limit."""
        h = compute_content_hash(self._body())
        assert len(h) <= 128
        assert len(h) >= 30

    @pytest.mark.parametrize("seed", range(20))
    def test_random_variants_produce_unique_hashes(self, seed: int) -> None:
        """Sanity check that hashing doesn't collide on nearby inputs."""
        h1 = compute_content_hash(self._body(content=f"content-{seed}"))
        h2 = compute_content_hash(self._body(content=f"content-{seed + 1000}"))
        assert h1 != h2

    def test_hash_is_stable_across_python_invocations(self) -> None:
        """Anchor a frozen-fixture hash so drift is caught if canonicalisation changes.

        If this assertion ever fails, **every node in the federation will
        disagree** about every record it has ever written. Update the frozen
        value only after confirming the drift is intentional.
        """
        body = build_canonical_body(
            workflow_id="00000000-0000-4000-8000-000000000001",
            memory_type=MemoryType.FACT,
            content="the deadline is April 30",
            confidence=0.9,
            metadata={"domain": "planning"},
            parent_hashes=[],
            author_did="did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
            source_agent_id=None,
        )
        # Frozen hash captured at implementation time.
        assert compute_content_hash(body) == "z263fNLfuqMD4GoCvX5UrXtZguFFPTA8Atr1SvZc5AVrR"
