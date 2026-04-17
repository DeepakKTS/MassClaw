"""Unit tests for DAG data structure."""

import pytest
from app.orchestration.dag import DAG, DAGNode
from app.exceptions import DAGValidationError
from app.models.base import TaskStatus


class TestDAG:
    def _make_dag(self) -> DAG:
        """Create a standard test DAG: t1 -> t2 -> t3, t2 -> t4 (parallel with t3)."""
        return DAG(
            [
                DAGNode(node_id="t1", capability="intake", description="Step 1", depends_on=[]),
                DAGNode(node_id="t2", capability="research", description="Step 2", depends_on=["t1"]),
                DAGNode(node_id="t3", capability="analysis", description="Step 3", depends_on=["t2"]),
                DAGNode(node_id="t4", capability="risk", description="Step 4", depends_on=["t2"]),
                DAGNode(node_id="t5", capability="summary", description="Step 5", depends_on=["t3", "t4"]),
            ]
        )

    def test_valid_dag_creation(self):
        dag = self._make_dag()
        assert len(dag.nodes) == 5

    def test_cycle_detection(self):
        with pytest.raises(DAGValidationError, match="cycle"):
            DAG(
                [
                    DAGNode(node_id="a", capability="x", description="A", depends_on=["c"]),
                    DAGNode(node_id="b", capability="y", description="B", depends_on=["a"]),
                    DAGNode(node_id="c", capability="z", description="C", depends_on=["b"]),
                ]
            )

    def test_invalid_dependency_ref(self):
        with pytest.raises(DAGValidationError, match="non-existent"):
            DAG(
                [
                    DAGNode(node_id="t1", capability="x", description="T1", depends_on=["missing"]),
                ]
            )

    def test_topological_sort(self):
        dag = self._make_dag()
        order = dag.topological_sort()
        ids = [n.node_id for n in order]
        assert ids.index("t1") < ids.index("t2")
        assert ids.index("t2") < ids.index("t3")
        assert ids.index("t2") < ids.index("t4")
        assert ids.index("t3") < ids.index("t5")
        assert ids.index("t4") < ids.index("t5")

    def test_ready_nodes_initial(self):
        dag = self._make_dag()
        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "t1"

    def test_ready_nodes_after_completion(self):
        dag = self._make_dag()
        dag.mark_completed("t1")
        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "t2"

    def test_parallel_ready_nodes(self):
        dag = self._make_dag()
        dag.mark_completed("t1")
        dag.mark_completed("t2")
        ready = dag.get_ready_nodes()
        ids = {n.node_id for n in ready}
        assert ids == {"t3", "t4"}  # Both ready in parallel

    def test_failure_propagation(self):
        dag = self._make_dag()
        dag.mark_completed("t1")
        dag.mark_completed("t2")
        skipped = dag.mark_failed("t3", error="test failure")
        assert "t5" in skipped  # t5 depends on t3, should be skipped

    def test_is_complete(self):
        dag = self._make_dag()
        assert not dag.is_complete
        for nid in ["t1", "t2", "t3", "t4", "t5"]:
            dag.mark_completed(nid)
        assert dag.is_complete

    def test_progress_percent(self):
        dag = self._make_dag()
        assert dag.progress_percent == 0.0
        dag.mark_completed("t1")
        assert dag.progress_percent == 20.0
        dag.mark_completed("t2")
        dag.mark_completed("t3")
        dag.mark_completed("t4")
        dag.mark_completed("t5")
        assert dag.progress_percent == 100.0

    def test_serialize_deserialize(self):
        dag = self._make_dag()
        dag.mark_completed("t1", output="result 1")
        data = dag.to_dict()
        restored = DAG.from_dict(data)
        assert len(restored.nodes) == 5
        assert restored.get_node("t1").status == TaskStatus.COMPLETED
        assert restored.get_node("t1").output == "result 1"

    def test_critical_path(self):
        dag = self._make_dag()
        path = dag.get_critical_path()
        assert len(path) >= 3
        assert path[0].node_id == "t1"
        assert path[-1].node_id == "t5"
