"""Unit tests for AgentFacts document construction and signature verification."""

from __future__ import annotations

import pytest

from app.identity.agent_facts import (
    AGENT_FACTS_CONTEXT,
    AGENT_FACTS_TYPES,
    AgentFacts,
    AgentFactsBuilder,
    AgentLimits,
    ToolDescriptor,
    build_agent_facts,
)
from app.identity.did import build_did_from_public_key
from app.identity.signer import encode_multibase, generate_keypair


def _example_doc(keypair=None):
    kp = keypair or generate_keypair()
    return build_agent_facts(
        keypair=kp,
        document_id="urn:test:agent-facts:1",
        name="test-agent",
        description="fixture agent for unit tests",
        capabilities=["workflow.submit"],
        tools=[ToolDescriptor(name="echo", description="echo tool")],
        endpoints={"http": "https://example.invalid/agent"},
        limits=AgentLimits(max_transaction_usd=100.0, rate_limit_per_min=30),
        trust_zone="test-zone",
    ), kp


class TestBuildAndSign:
    def test_document_has_proof_after_build(self) -> None:
        doc, _ = _example_doc()
        assert doc.proof is not None
        assert doc.proof.type == "Ed25519Signature2020"
        assert doc.proof.signature_value.startswith("z")

    def test_valid_signature_verifies(self) -> None:
        doc, _ = _example_doc()
        assert doc.verify() is True

    def test_context_and_types_defaulted(self) -> None:
        doc, _ = _example_doc()
        assert doc.context == AGENT_FACTS_CONTEXT
        assert doc.type == AGENT_FACTS_TYPES

    def test_tampering_with_subject_breaks_signature(self) -> None:
        doc, _ = _example_doc()
        # Mutate a field and re-verify; signature must fail now.
        doc.credential_subject.name = "tampered"
        assert doc.verify() is False

    def test_tampering_with_signature_breaks_verification(self) -> None:
        doc, _ = _example_doc()
        assert doc.proof is not None
        # Flip a byte in the signature by re-encoding a mutated buffer.
        from app.identity.signer import decode_multibase, encode_multibase

        raw = bytearray(decode_multibase(doc.proof.signature_value))
        raw[0] ^= 0x01
        doc.proof.signature_value = encode_multibase(bytes(raw))
        assert doc.verify() is False

    def test_issuer_public_key_must_match_keypair(self) -> None:
        kp = generate_keypair()
        wrong = generate_keypair()
        builder = AgentFactsBuilder(
            subject_did=build_did_from_public_key(kp.public_bytes),
            issuer_did=build_did_from_public_key(kp.public_bytes),
            document_id="urn:test:1",
            name="x",
            description="y",
        ).public_key(kp.public_bytes)
        with pytest.raises(ValueError):
            builder.build_and_sign(wrong)

    def test_verify_on_unsigned_doc_raises(self) -> None:
        doc, _ = _example_doc()
        doc.proof = None
        with pytest.raises(ValueError):
            doc.verify()

    def test_subject_public_key_matches_did(self) -> None:
        doc, kp = _example_doc()
        assert doc.credential_subject.public_key_multibase == encode_multibase(kp.public_bytes)


class TestCanonicalSignable:
    def test_signable_bytes_excludes_proof(self) -> None:
        doc, _ = _example_doc()
        body = doc.signable_bytes()
        assert b"proof" not in body

    def test_signable_bytes_are_deterministic(self) -> None:
        doc, _ = _example_doc()
        assert doc.signable_bytes() == doc.signable_bytes()

    def test_signable_bytes_change_when_subject_changes(self) -> None:
        doc, _ = _example_doc()
        original = doc.signable_bytes()
        doc.credential_subject.name = "renamed"
        assert doc.signable_bytes() != original


class TestSerialization:
    def test_to_document_includes_proof(self) -> None:
        doc, _ = _example_doc()
        out = doc.to_document()
        assert "proof" in out
        assert out["proof"]["type"] == "Ed25519Signature2020"

    def test_roundtrip_through_dict(self) -> None:
        doc, _ = _example_doc()
        dumped = doc.to_document()
        revived = AgentFacts.model_validate(dumped)
        assert revived.verify() is True

    def test_forbids_extra_fields(self) -> None:
        from pydantic import ValidationError

        doc, _ = _example_doc()
        dumped = doc.to_document()
        dumped["credentialSubject"]["extra_field"] = "nope"
        with pytest.raises(ValidationError):
            AgentFacts.model_validate(dumped)


class TestBuilder:
    def test_add_capability(self) -> None:
        kp = generate_keypair()
        did = build_did_from_public_key(kp.public_bytes)
        doc = (
            AgentFactsBuilder(
                subject_did=did,
                issuer_did=did,
                document_id="urn:test:2",
                name="a",
                description="b",
            )
            .add_capability("x")
            .add_capability("y")
            .public_key(kp.public_bytes)
            .build_and_sign(kp)
        )
        assert doc.credential_subject.capabilities == ["x", "y"]

    def test_add_tool(self) -> None:
        kp = generate_keypair()
        did = build_did_from_public_key(kp.public_bytes)
        doc = (
            AgentFactsBuilder(
                subject_did=did,
                issuer_did=did,
                document_id="urn:test:3",
                name="a",
                description="b",
            )
            .add_tool(ToolDescriptor(name="t1", description="desc"))
            .public_key(kp.public_bytes)
            .build_and_sign(kp)
        )
        assert [t.name for t in doc.credential_subject.tools] == ["t1"]

    def test_endpoint_helper(self) -> None:
        kp = generate_keypair()
        did = build_did_from_public_key(kp.public_bytes)
        doc = (
            AgentFactsBuilder(
                subject_did=did,
                issuer_did=did,
                document_id="urn:test:4",
                name="a",
                description="b",
            )
            .endpoint("http", "https://a.example/")
            .endpoint("mcp", "https://a.example/mcp")
            .public_key(kp.public_bytes)
            .build_and_sign(kp)
        )
        assert doc.credential_subject.endpoints == {
            "http": "https://a.example/",
            "mcp": "https://a.example/mcp",
        }
