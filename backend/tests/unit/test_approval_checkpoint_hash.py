"""Unit tests for the Day-13 HITL plumbing on :class:`ApprovalRequest`.

These cover the Pydantic surface only — the Redis round-trip + event
publishing are exercised in the integration tests.
"""

from __future__ import annotations

from app.safety.approval import ApprovalRequest


class TestApprovalRequestCheckpointHash:
    def test_checkpoint_hash_defaults_to_none(self) -> None:
        req = ApprovalRequest(
            workflow_id="wf-1",
            action="execute_risk_assessment",
            policy_rule="high_risk_task",
        )
        assert req.checkpoint_hash is None

    def test_checkpoint_hash_accepts_str(self) -> None:
        req = ApprovalRequest(
            workflow_id="wf-1",
            action="execute_risk_assessment",
            policy_rule="high_risk_task",
            checkpoint_hash="z6MkuFakeCheckpointHashForUnitTest",
        )
        assert req.checkpoint_hash == "z6MkuFakeCheckpointHashForUnitTest"

    def test_json_roundtrip_preserves_checkpoint_hash(self) -> None:
        req = ApprovalRequest(
            workflow_id="wf-1",
            action="execute_risk_assessment",
            policy_rule="high_risk_task",
            checkpoint_hash="z6MkRoundtrip",
        )
        encoded = req.model_dump_json()
        restored = ApprovalRequest.model_validate_json(encoded)
        assert restored.checkpoint_hash == "z6MkRoundtrip"

    def test_legacy_payload_without_checkpoint_hash_still_parses(self) -> None:
        """Older Redis payloads predate the field — they must still decode."""
        payload = (
            '{"request_id":"r-1","workflow_id":"wf-1","task_id":null,'
            '"action":"x","context":{},"policy_rule":"rule",'
            '"status":"pending","requested_at":"2026-04-29T00:00:00+00:00",'
            '"decided_at":null,"decided_by":null,'
            '"expires_at":"2026-04-29T00:05:00+00:00"}'
        )
        req = ApprovalRequest.model_validate_json(payload)
        assert req.checkpoint_hash is None
