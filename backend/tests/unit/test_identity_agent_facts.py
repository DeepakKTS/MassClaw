"""Unit tests for the AgentFacts v1 schema + integrity credentials."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.identity.agent_facts import (
    INTEGRITY_CRYPTOSUITE,
    INTEGRITY_PROOF_TYPE,
    AgentFacts,
    AgentFactsBuilder,
    AgentFactsExtensions,
    Skill,
    TelemetryMetrics,
)
from app.identity.did import build_did_key
from app.identity.signer import decode_multibase, encode_multibase, generate_keypair


def _builder(**overrides) -> AgentFactsBuilder:
    kp = overrides.pop("keypair", None) or generate_keypair()
    did = build_did_key(kp.public_bytes)
    defaults = dict(
        agent_id="urn:agent:massclaw:test",
        label="Test Agent",
        description="A fixture agent for unit tests",
        version="1.0.0",
        provider_name="MassClaw",
        provider_url="https://massclaw.example",
    )
    defaults.update(overrides)
    builder = (
        AgentFactsBuilder(**defaults)
        .provider_did(did)
        .endpoint("https://massclaw.example/api/v1")
        .modalities("text")
        .auth_methods("bearer")
        .add_skill(
            Skill(
                id="workflow.submit",
                description="submit a workflow",
                **{"inputModes": ["text"], "outputModes": ["text"]},
            )
        )
    )
    builder._keypair_for_tests = kp  # type: ignore[attr-defined]
    return builder


class TestSchemaShape:
    def test_top_level_fields_are_flat(self) -> None:
        doc = _builder().build()
        payload = doc.to_document()
        assert set(payload.keys()) >= {
            "id",
            "agent_name",
            "label",
            "description",
            "version",
            "provider",
            "endpoints",
            "capabilities",
            "skills",
        }
        # v1 must NOT have any of the old VC-envelope keys.
        for forbidden in ("@context", "type", "issuer", "validFrom", "validUntil", "credentialSubject", "proof"):
            assert forbidden not in payload

    def test_provider_is_required_object(self) -> None:
        doc = _builder().build()
        assert doc.provider.name == "MassClaw"
        assert doc.provider.url == "https://massclaw.example"
        assert doc.provider.did is not None
        assert doc.provider.did.startswith("did:key:z")

    def test_capabilities_is_object_not_list(self) -> None:
        doc = _builder().modalities("text", "structured-output").auth_methods("bearer").build()
        payload = doc.to_document()
        assert isinstance(payload["capabilities"], dict)
        assert set(payload["capabilities"]["modalities"]) == {"text", "structured-output"}

    def test_skills_are_objects_with_camel_case(self) -> None:
        doc = _builder().build()
        payload = doc.to_document()
        assert isinstance(payload["skills"], list)
        assert payload["skills"][0]["id"] == "workflow.submit"
        assert "inputModes" in payload["skills"][0]
        assert "outputModes" in payload["skills"][0]
        assert "input_modes" not in payload["skills"][0]

    def test_endpoints_uses_static_array(self) -> None:
        doc = _builder().endpoints(["https://a.example", "https://a.example/mcp"]).build()
        payload = doc.to_document()
        assert payload["endpoints"]["static"] == [
            "https://a.example",
            "https://a.example/mcp",
        ]

    def test_extensions_namespaced_under_x_massclaw(self) -> None:
        doc = (
            _builder()
            .extensions(AgentFactsExtensions(nanda_index_handle="massclaw-demo", limits={"rate_limit_per_min": 60}))
            .build()
        )
        payload = doc.to_document()
        assert "x-massclaw" in payload
        assert payload["x-massclaw"]["nanda_index_handle"] == "massclaw-demo"
        assert payload["x-massclaw"]["limits"] == {"rate_limit_per_min": 60}

    def test_forbids_unknown_top_level_fields(self) -> None:
        with pytest.raises(ValidationError):
            AgentFacts.model_validate(
                {
                    **_builder().build().to_document(),
                    "unexpected_field": "nope",
                }
            )


class TestIntegrityCredential:
    def _signed_doc(self):
        b = _builder()
        kp = b._keypair_for_tests  # type: ignore[attr-defined]
        doc = b.build()
        doc.attach_integrity_credential(kp)
        return doc, kp

    def test_credential_added_to_array(self) -> None:
        doc, _ = self._signed_doc()
        assert len(doc.verifiable_credentials) == 1
        cred = doc.verifiable_credentials[0]
        assert cred.proof.type == INTEGRITY_PROOF_TYPE
        assert cred.proof.cryptosuite == INTEGRITY_CRYPTOSUITE

    def test_credential_verifies(self) -> None:
        doc, _ = self._signed_doc()
        assert doc.verify_integrity() is True

    def test_tampered_body_fails_verification(self) -> None:
        doc, _ = self._signed_doc()
        doc.label = "TAMPERED"
        assert doc.verify_integrity() is False

    def test_tampered_credential_signature_fails(self) -> None:
        doc, _ = self._signed_doc()
        raw = bytearray(decode_multibase(doc.verifiable_credentials[0].proof.proof_value))
        raw[0] ^= 0x01
        doc.verifiable_credentials[0].proof.proof_value = encode_multibase(bytes(raw))
        assert doc.verify_integrity() is False

    def test_multiple_credentials_any_valid_is_accepted(self) -> None:
        doc, kp = self._signed_doc()
        # Add a second credential from a different key — should still verify
        # as long as at least one credential matches the body.
        other_kp = generate_keypair()
        doc.attach_integrity_credential(other_kp)
        assert len(doc.verifiable_credentials) == 2
        assert doc.verify_integrity(doc.verifiable_credentials[0]) is True
        assert doc.verify_integrity(doc.verifiable_credentials[1]) is True

    def test_verify_without_credentials_raises(self) -> None:
        doc = _builder().build()
        assert len(doc.verifiable_credentials) == 0
        with pytest.raises(ValueError):
            doc.verify_integrity()

    def test_signable_body_excludes_credentials(self) -> None:
        doc, kp = self._signed_doc()
        body = doc.signable_body()
        assert "verifiable_credentials" not in body
        # And the body bytes match what was signed.
        from app.identity.canonicalize import canonicalize

        payload = canonicalize(body)
        # Re-sign with same key; result must be deterministic (Ed25519).
        from app.identity.signer import sign_bytes

        expected = encode_multibase(sign_bytes(payload, kp.private_seed))
        assert doc.verifiable_credentials[0].proof.proof_value == expected


class TestRoundtrip:
    def test_serialize_and_validate(self) -> None:
        doc = _builder().certification("verified-enterprise").build()
        payload = doc.to_document()
        revived = AgentFacts.model_validate(payload)
        assert revived.label == doc.label
        assert revived.certification is not None
        assert revived.certification.level == "verified-enterprise"

    def test_telemetry_roundtrip(self) -> None:
        doc = _builder().telemetry(TelemetryMetrics(latency_p95_ms=500)).build()
        payload = doc.to_document()
        assert payload["telemetry"]["metrics"]["latency_p95_ms"] == 500

    def test_documentation_url_and_jurisdiction(self) -> None:
        doc = _builder().documentation_url("https://example/docs").jurisdiction("US").build()
        payload = doc.to_document()
        assert payload["documentationUrl"] == "https://example/docs"
        assert payload["jurisdiction"] == "US"


class TestBuilderFluency:
    def test_chained_calls_return_builder(self) -> None:
        b = _builder()
        ret = (
            b.modalities("text")
            .auth_methods("bearer")
            .endpoint("https://example")
            .documentation_url("https://example/docs")
        )
        assert ret is b

    def test_build_and_sign_convenience(self) -> None:
        b = _builder()
        kp = b._keypair_for_tests  # type: ignore[attr-defined]
        doc = b.build_and_sign(kp)
        assert doc.verify_integrity() is True
