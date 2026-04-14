from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import get_tool_registry

logger = get_logger(__name__)


class ToolExecutor:
    """Executes tools with safety validation, content filtering, audit logging, and cost tracking."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        registry = get_tool_registry()
        tool = registry.get(tool_name)
        if tool is None:
            return ToolResult(content=f"Tool not found: {tool_name}", success=False)

        # Pre-execution safety: check arguments for injection (5s timeout)
        try:
            import asyncio

            from app.safety.injection_detector import InjectionDetector

            detector = InjectionDetector()
            assessment = await asyncio.wait_for(detector.analyze(str(arguments)), timeout=5.0)
            if assessment.is_suspicious and assessment.recommendation == "block":
                logger.warning("tool_execution_blocked", tool=tool_name, risk_score=assessment.risk_score)
                return ToolResult(
                    content=f"Tool execution blocked: input flagged as potentially unsafe (risk: {assessment.risk_score:.2f}).",
                    success=False,
                    metadata={"blocked_reason": "injection_detection"},
                )
        except TimeoutError:
            logger.warning("injection_check_timeout", tool=tool_name)
        except Exception as e:
            logger.warning("injection_check_skipped", error=str(e))

        # Execute the tool
        logger.info("tool_execution_started", tool=tool_name, workflow_id=str(context.workflow_id))
        try:
            result = await tool.execute(arguments, context)
        except Exception as e:
            logger.error("tool_execution_error", tool=tool_name, error=str(e))
            return ToolResult(content=f"Tool execution failed: {e}", success=False)

        # Post-execution safety: filter content for PII (5s timeout)
        if result.success and result.content:
            try:
                import asyncio

                from app.safety.content_filter import ContentFilter

                cf = ContentFilter()
                analysis = await asyncio.wait_for(cf.analyze(result.content), timeout=5.0)
                if analysis.pii_detected:
                    result.content = await asyncio.wait_for(cf.redact_pii(result.content), timeout=5.0)
                    result.metadata["pii_redacted"] = True
            except TimeoutError:
                logger.warning("content_filter_timeout", tool=tool_name)
            except Exception as e:
                logger.warning("content_filter_skipped", error=str(e))

        # Audit log
        try:
            from app.models.base import ActorType, AuditEventType
            from app.services.audit_service import AuditService

            audit = AuditService(session=self.session, redis=self.redis)
            await audit.log(
                event_type=AuditEventType.TOOL_EXECUTED if result.success else AuditEventType.TOOL_BLOCKED,
                actor_type=ActorType.AGENT,
                actor_id=str(context.agent_id),
                workflow_id=context.workflow_id,
                input_summary=f"tool={tool_name} args={str(arguments)[:200]}",
                output_summary=result.content[:200] if result.content else None,
                metadata={"tool": tool_name, "success": result.success},
            )
        except Exception as e:
            logger.warning("tool_audit_log_failed", error=str(e))

        result.cost_credits = tool.estimated_cost_credits
        logger.info(
            "tool_execution_completed", tool=tool_name, success=result.success, cost=tool.estimated_cost_credits
        )
        return result
