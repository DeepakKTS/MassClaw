from __future__ import annotations

from app.core.logging import get_logger
from app.orchestration.dag import DAG, DAGNode

logger = get_logger(__name__)


class DynamicReplanner:
    """Adds compensating tasks to DAG when tasks fail or need reinforcement."""

    @staticmethod
    def replan(
        dag: DAG,
        failed_node_id: str,
        reflection_action: str,
        *,
        context: str = "",
    ) -> list[DAGNode] | None:
        """Add compensating tasks for a failed or inadequate task.

        Returns list of new nodes added, or None if no replan needed.
        """
        if reflection_action != "re_plan":
            return None

        failed_node = dag.get_node(failed_node_id)
        if failed_node is None:
            return None

        # Create an alternative task with a different approach
        alt_node_id = f"{failed_node_id}_alt"
        alt_node = DAGNode(
            node_id=alt_node_id,
            capability=failed_node.capability,
            description=(
                f"[RETRY with different approach] {failed_node.description}\n"
                f"Previous attempt was inadequate. {context}\n"
                "Use a fundamentally different methodology or perspective."
            ),
            depends_on=failed_node.depends_on,
            estimated_complexity=failed_node.estimated_complexity,
        )

        try:
            dag.add_node(alt_node)
            logger.info(
                "dynamic_replan_added_node",
                original=failed_node_id,
                alternative=alt_node_id,
                capability=failed_node.capability,
            )
            return [alt_node]
        except Exception as e:
            logger.warning("dynamic_replan_failed", error=str(e))
            return None
