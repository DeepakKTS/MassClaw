"""Unit tests for the workflow checkpoint module.

These cover the pure serialisation path (WorkflowCheckpoint ↔ payload ↔
DAG) without touching the database. The :class:`CheckpointStore` write
path goes through the CRDT store which needs Postgres; tests for that
are handled by Day 14's integration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.base import MemoryType, TaskStatus
from app.orchestration.checkpoint import (
    CHECKPOINT_MARKER_KEY,
    CHECKPOINT_PAYLOAD_KEY,
    CHECKPOINT_WORKFLOW_KEY,
    CheckpointDecodeError,
    WorkflowCheckpoint,
    _metadata_for,
    is_checkpoint_record,
)
from app.orchestration.dag import DAG, DAGNode


def _sample_dag() -> DAG:
    return DAG(
        [
            DAGNode(
                node_id="task-1",
                capability="research",
                description="investigate",
                depends_on=[],
                estimated_complexity="medium",
                status=TaskStatus.COMPLETED,
            ),
            DAGNode(
                node_id="task-2",
                capability="writing",
                description="summarise findings",
                depends_on=["task-1"],
                estimated_complexity="medium",
                status=TaskStatus.PENDING,
            ),
            DAGNode(
                node_id="task-3",
                capability="review",
                description="check summary",
                depends_on=["task-2"],
                estimated_complexity="low",
                status=TaskStatus.FAILED,
            ),
        ]
    )


# ---------------------------------------------------------------- from_dag


class TestFromDag:
    def test_captures_task_status_buckets(self) -> None:
        wf_id = uuid.uuid4()
        cp = WorkflowCheckpoint.from_dag(
            workflow_id=wf_id,
            dag=_sample_dag(),
            reason="test",
            current_task_id="task-2",
        )
        assert cp.workflow_id == wf_id
        assert "task-1" in cp.completed_task_ids
        assert "task-2" in cp.pending_task_ids
        assert "task-3" in cp.failed_task_ids

    def test_minimal_pending_dag_is_handled(self) -> None:
        """A single-node DAG where the node has no status buckets yet."""
        wf_id = uuid.uuid4()
        dag = DAG(
            [
                DAGNode(
                    node_id="solo",
                    capability="research",
                    description="only node",
                    depends_on=[],
                    estimated_complexity="low",
                    status=TaskStatus.PENDING,
                ),
            ]
        )
        cp = WorkflowCheckpoint.from_dag(workflow_id=wf_id, dag=dag, reason="minimal")
        assert cp.completed_task_ids == ()
        assert cp.failed_task_ids == ()
        assert cp.pending_task_ids == ("solo",)

    def test_variables_defaults_to_empty(self) -> None:
        cp = WorkflowCheckpoint.from_dag(workflow_id=uuid.uuid4(), dag=_sample_dag(), reason="r")
        assert cp.variables == {}
        assert cp.reflection is None

    def test_reflection_is_roundtripped(self) -> None:
        cp = WorkflowCheckpoint.from_dag(
            workflow_id=uuid.uuid4(),
            dag=_sample_dag(),
            reason="abort",
            reflection={"action": "abort", "confidence": 0.1},
        )
        assert cp.reflection is not None
        assert cp.reflection["action"] == "abort"


# ---------------------------------------------------------------- payload roundtrip


class TestPayloadRoundtrip:
    def test_roundtrips_every_field(self) -> None:
        original = WorkflowCheckpoint.from_dag(
            workflow_id=uuid.uuid4(),
            dag=_sample_dag(),
            reason="partial failure",
            current_task_id="task-2",
            variables={"mode": "planning", "attempts": 1},
            reflection={"action": "retry_task", "confidence": 0.3},
        )
        restored = WorkflowCheckpoint.from_payload(original.to_payload())
        assert restored.workflow_id == original.workflow_id
        assert restored.reason == original.reason
        assert restored.current_task_id == original.current_task_id
        assert restored.completed_task_ids == original.completed_task_ids
        assert restored.pending_task_ids == original.pending_task_ids
        assert restored.failed_task_ids == original.failed_task_ids
        assert restored.variables == original.variables
        assert restored.reflection == original.reflection
        assert restored.version == original.version

    def test_created_at_preserved_within_microseconds(self) -> None:
        ts = datetime.now(UTC)
        cp = WorkflowCheckpoint(
            workflow_id=uuid.uuid4(),
            reason="t",
            dag_snapshot={"nodes": []},
            created_at=ts,
        )
        restored = WorkflowCheckpoint.from_payload(cp.to_payload())
        # ISO format preserves microseconds in Python's default timestamp precision.
        assert restored.created_at == ts

    def test_missing_workflow_id_raises(self) -> None:
        with pytest.raises(CheckpointDecodeError):
            WorkflowCheckpoint.from_payload({"reason": "x"})

    def test_bad_uuid_raises(self) -> None:
        with pytest.raises(CheckpointDecodeError):
            WorkflowCheckpoint.from_payload({"workflow_id": "not-a-uuid"})

    def test_bad_created_at_falls_back_to_now(self) -> None:
        payload = {
            "workflow_id": str(uuid.uuid4()),
            "created_at": "not-a-date",
        }
        cp = WorkflowCheckpoint.from_payload(payload)
        # Should NOT raise; defaults to "now".
        delta = abs(datetime.now(UTC) - cp.created_at)
        assert delta < timedelta(seconds=5)


# ---------------------------------------------------------------- restore_dag


class TestRestoreDag:
    def test_roundtrip_produces_equivalent_dag(self) -> None:
        dag = _sample_dag()
        cp = WorkflowCheckpoint.from_dag(workflow_id=uuid.uuid4(), dag=dag, reason="r")
        restored = cp.restore_dag()
        assert {n.node_id for n in restored.nodes} == {n.node_id for n in dag.nodes}
        for original, rebuilt in zip(
            sorted(dag.nodes, key=lambda n: n.node_id),
            sorted(restored.nodes, key=lambda n: n.node_id),
            strict=True,
        ):
            assert original.capability == rebuilt.capability
            assert original.depends_on == rebuilt.depends_on
            assert original.status == rebuilt.status

    def test_malformed_snapshot_raises(self) -> None:
        cp = WorkflowCheckpoint(
            workflow_id=uuid.uuid4(),
            reason="bad",
            dag_snapshot={"nodes": [{"no_id_field": True}]},
        )
        with pytest.raises(CheckpointDecodeError):
            cp.restore_dag()


# ---------------------------------------------------------------- metadata marker


class TestMetadataMarker:
    def test_metadata_has_canonical_keys(self) -> None:
        cp = WorkflowCheckpoint.from_dag(workflow_id=uuid.uuid4(), dag=_sample_dag(), reason="test")
        meta = _metadata_for(cp)
        assert meta[CHECKPOINT_MARKER_KEY] is True
        assert meta[CHECKPOINT_WORKFLOW_KEY] == str(cp.workflow_id)
        assert CHECKPOINT_PAYLOAD_KEY in meta
        assert meta[CHECKPOINT_PAYLOAD_KEY]["workflow_id"] == str(cp.workflow_id)

    def test_is_checkpoint_record_positive(self) -> None:
        from types import SimpleNamespace

        cp = WorkflowCheckpoint.from_dag(workflow_id=uuid.uuid4(), dag=_sample_dag(), reason="test")
        record = SimpleNamespace(
            metadata_=_metadata_for(cp),
            memory_type=MemoryType.META,
        )
        assert is_checkpoint_record(record) is True

    def test_is_checkpoint_record_negative(self) -> None:
        from types import SimpleNamespace

        # Wrong memory_type.
        record = SimpleNamespace(metadata_={CHECKPOINT_MARKER_KEY: True}, memory_type=MemoryType.RESULT)
        assert is_checkpoint_record(record) is False

        # Missing marker.
        record = SimpleNamespace(metadata_={}, memory_type=MemoryType.META)
        assert is_checkpoint_record(record) is False

        # None metadata.
        record = SimpleNamespace(metadata_=None, memory_type=MemoryType.META)
        assert is_checkpoint_record(record) is False
