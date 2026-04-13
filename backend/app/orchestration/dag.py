from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.exceptions import DAGValidationError
from app.models.base import TaskStatus


@dataclass
class DAGNode:
    """A single node in the workflow DAG representing one task."""

    node_id: str
    capability: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    estimated_complexity: str = "medium"  # low, medium, high
    assigned_agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    status: TaskStatus = TaskStatus.PENDING
    output: str | None = None
    error: str | None = None


class DAG:
    """Directed acyclic graph for workflow task orchestration.

    Supports:
    - Cycle detection via Kahn's algorithm (topological sort)
    - Dependency-aware ready-node computation
    - Failure propagation (skip dependents of failed nodes)
    - Serialization for persistence and recovery
    """

    def __init__(self, nodes: list[DAGNode]) -> None:
        self._nodes: dict[str, DAGNode] = {n.node_id: n for n in nodes}
        self._adjacency: dict[str, list[str]] = defaultdict(list)  # parent -> children
        self._reverse: dict[str, list[str]] = defaultdict(list)    # child -> parents

        for node in nodes:
            for dep in node.depends_on:
                self._adjacency[dep].append(node.node_id)
                self._reverse[node.node_id].append(dep)

        self.validate()

    def validate(self) -> None:
        """Validate the DAG has no cycles using Kahn's algorithm.

        Also checks that all dependency references are valid and
        there's at least one root node and one leaf node.
        """
        # Check all dependency references are valid
        all_ids = set(self._nodes.keys())
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in all_ids:
                    raise DAGValidationError(
                        f"Node '{node.node_id}' depends on non-existent node '{dep}'"
                    )

        # Kahn's algorithm for cycle detection
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        for node in self._nodes.values():
            for dep in node.depends_on:
                in_degree[node.node_id] += 1

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)

        if not queue:
            raise DAGValidationError("DAG has no root nodes — all nodes have dependencies (cycle)")

        sorted_count = 0
        while queue:
            nid = queue.popleft()
            sorted_count += 1
            for child in self._adjacency[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if sorted_count != len(self._nodes):
            remaining = [nid for nid, deg in in_degree.items() if deg > 0]
            raise DAGValidationError(
                f"DAG contains a cycle involving nodes: {remaining}"
            )

        # Check at least one leaf node
        leaves = [nid for nid in self._nodes if not self._adjacency[nid]]
        if not leaves:
            raise DAGValidationError("DAG has no leaf nodes")

    def topological_sort(self) -> list[DAGNode]:
        """Return nodes in topological order (respecting dependencies)."""
        in_degree: dict[str, int] = {nid: len(self._reverse[nid]) for nid in self._nodes}
        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        result: list[DAGNode] = []

        while queue:
            nid = queue.popleft()
            result.append(self._nodes[nid])
            for child in self._adjacency[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return result

    def get_ready_nodes(self) -> list[DAGNode]:
        """Get nodes whose dependencies are ALL completed and that are still pending/todo."""
        ready = []
        for node in self._nodes.values():
            if node.status not in (TaskStatus.PENDING, TaskStatus.TODO):
                continue
            # Check if any dependency failed/skipped → block this node
            any_dep_failed = any(
                self._nodes[dep].status in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                for dep in node.depends_on
            )
            if any_dep_failed:
                continue  # Will be handled by mark_failed cascade
            deps_met = all(
                self._nodes[dep].status == TaskStatus.COMPLETED
                for dep in node.depends_on
            )
            if deps_met:
                ready.append(node)
        return ready

    def mark_completed(self, node_id: str, output: str | None = None) -> list[DAGNode]:
        """Mark a node as completed and return newly ready nodes."""
        node = self.get_node(node_id)
        node.status = TaskStatus.COMPLETED
        node.output = output
        return self.get_ready_nodes()

    def mark_failed(self, node_id: str, error: str | None = None) -> list[str]:
        """Mark a node as failed and propagate to skip dependent nodes.

        Returns list of skipped node IDs.
        """
        node = self.get_node(node_id)
        node.status = TaskStatus.FAILED
        node.error = error

        # Recursively skip all descendants
        skipped: list[str] = []
        to_skip = deque(self._adjacency[node_id])
        seen = set()

        while to_skip:
            child_id = to_skip.popleft()
            if child_id in seen:
                continue
            seen.add(child_id)

            child = self._nodes[child_id]
            if child.status in (TaskStatus.PENDING, TaskStatus.TODO, TaskStatus.BLOCKED):
                child.status = TaskStatus.SKIPPED
                child.error = f"Skipped: dependency '{node_id}' failed"
                skipped.append(child_id)
                to_skip.extend(self._adjacency[child_id])

        return skipped

    def mark_blocked(self, node_id: str, reason: str | None = None) -> None:
        """Mark a node as blocked."""
        node = self.get_node(node_id)
        node.status = TaskStatus.BLOCKED
        node.error = reason or "Blocked by dependency or manual action"

    def mark_running(self, node_id: str) -> None:
        """Mark a node as currently running."""
        self.get_node(node_id).status = TaskStatus.RUNNING

    def get_node(self, node_id: str) -> DAGNode:
        """Get a node by ID."""
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found in DAG")
        return self._nodes[node_id]

    @property
    def nodes(self) -> list[DAGNode]:
        return list(self._nodes.values())

    @property
    def is_complete(self) -> bool:
        """True if all nodes are in a terminal state (completed, failed, or skipped)."""
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}
        return all(n.status in terminal for n in self._nodes.values())

    @property
    def completed_count(self) -> int:
        return sum(1 for n in self._nodes.values() if n.status == TaskStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for n in self._nodes.values() if n.status == TaskStatus.FAILED)

    @property
    def progress_percent(self) -> float:
        total = len(self._nodes)
        if total == 0:
            return 100.0
        done = sum(
            1 for n in self._nodes.values()
            if n.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
        )
        return round(done / total * 100, 1)

    def get_critical_path(self) -> list[DAGNode]:
        """Compute the longest path through the DAG (critical path)."""
        # Topological order
        topo = self.topological_sort()
        distances: dict[str, int] = {n.node_id: 0 for n in topo}
        predecessors: dict[str, str | None] = {n.node_id: None for n in topo}

        for node in topo:
            for child_id in self._adjacency[node.node_id]:
                if distances[child_id] < distances[node.node_id] + 1:
                    distances[child_id] = distances[node.node_id] + 1
                    predecessors[child_id] = node.node_id

        # Find the node with maximum distance (end of critical path)
        end_id = max(distances, key=distances.get)  # type: ignore[arg-type]
        path: list[DAGNode] = []
        current: str | None = end_id
        while current is not None:
            path.append(self._nodes[current])
            current = predecessors[current]

        path.reverse()
        return path

    def to_dict(self) -> dict[str, Any]:
        """Serialize DAG for storage (workflow.dag_snapshot)."""
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "capability": n.capability,
                    "description": n.description,
                    "depends_on": n.depends_on,
                    "estimated_complexity": n.estimated_complexity,
                    "assigned_agent_id": str(n.assigned_agent_id) if n.assigned_agent_id else None,
                    "task_id": str(n.task_id) if n.task_id else None,
                    "status": n.status.value,
                    "output": n.output,
                    "error": n.error,
                }
                for n in self._nodes.values()
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAG:
        """Deserialize a DAG from stored dict."""
        nodes = []
        for nd in data["nodes"]:
            node = DAGNode(
                node_id=nd["node_id"],
                capability=nd["capability"],
                description=nd["description"],
                depends_on=nd.get("depends_on", []),
                estimated_complexity=nd.get("estimated_complexity", "medium"),
                assigned_agent_id=uuid.UUID(nd["assigned_agent_id"]) if nd.get("assigned_agent_id") else None,
                task_id=uuid.UUID(nd["task_id"]) if nd.get("task_id") else None,
                status=TaskStatus(nd.get("status", "pending")),
                output=nd.get("output"),
                error=nd.get("error"),
            )
            nodes.append(node)
        return cls(nodes)
