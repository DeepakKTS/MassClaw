"""Unit tests for :mod:`app.safety.federated_approval` — pure logic only.

The CRDT round-trip (write + scan back) needs a live DB session and
lives in ``tests/integration/test_federated_approval_store.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.base import MemoryType
from app.safety.approval import ApprovalRequest
from app.safety.federated_approval import (
    APPROVAL_MARKER_KEY,
    APPROVAL_NODE_ID_KEY,
    APPROVAL_PAYLOAD_KEY,
    APPROVAL_REQUEST_ID_KEY,
    APPROVAL_STATUS_KEY,
    _metadata_for,
    is_approval_record,
)


def _request(**overrides) -> ApprovalRequest:
    base = {
        "workflow_id": "wf-1",
        "action": "execute_compliance-check",
        "policy_rule": "high_risk_task",
    }
    base.update(overrides)
    return ApprovalRequest(**base)


class TestMetadataShape:
    def test_metadata_has_canonical_keys(self) -> None:
        req = _request()
        meta = _metadata_for(req, node_id="task-2")
        assert meta[APPROVAL_MARKER_KEY] is True
        assert meta[APPROVAL_REQUEST_ID_KEY] == req.request_id
        assert meta[APPROVAL_STATUS_KEY] == "pending"
        assert meta[APPROVAL_NODE_ID_KEY] == "task-2"
        assert isinstance(meta[APPROVAL_PAYLOAD_KEY], dict)
        assert meta[APPROVAL_PAYLOAD_KEY]["request_id"] == req.request_id

    def test_node_id_is_optional(self) -> None:
        meta = _metadata_for(_request(), node_id=None)
        assert APPROVAL_NODE_ID_KEY not in meta

    def test_decision_metadata_carries_status(self) -> None:
        req = _request()
        req.status = "approved"
        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "reviewer"
        meta = _metadata_for(req, node_id=None)
        assert meta[APPROVAL_STATUS_KEY] == "approved"
        assert meta[APPROVAL_PAYLOAD_KEY]["status"] == "approved"
        assert meta[APPROVAL_PAYLOAD_KEY]["decided_by"] == "reviewer"


class TestIsApprovalRecord:
    def test_positive_case(self) -> None:
        record = SimpleNamespace(
            metadata_=_metadata_for(_request(), node_id="task-1"),
            memory_type=MemoryType.META,
        )
        assert is_approval_record(record) is True

    def test_wrong_memory_type(self) -> None:
        record = SimpleNamespace(
            metadata_=_metadata_for(_request(), node_id="task-1"),
            memory_type=MemoryType.RESULT,
        )
        assert is_approval_record(record) is False

    def test_missing_marker(self) -> None:
        record = SimpleNamespace(metadata_={}, memory_type=MemoryType.META)
        assert is_approval_record(record) is False

    def test_none_metadata(self) -> None:
        record = SimpleNamespace(metadata_=None, memory_type=MemoryType.META)
        assert is_approval_record(record) is False
