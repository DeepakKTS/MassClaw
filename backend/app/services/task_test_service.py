from __future__ import annotations

import json
import re
import time
import uuid

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.task import Task
from app.models.task_test import TaskTest

logger = get_logger(__name__)


class TaskTestService:
    """Run and manage test assertions against task outputs."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def run_test(
        self,
        task_id: uuid.UUID,
        test_name: str,
        test_type: str,
        assertion: dict,
    ) -> TaskTest:
        """Run a test assertion against a task's output."""
        # Get task
        result = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("Task", str(task_id))

        start = time.perf_counter()
        status = "pass"
        actual_value = None
        error_message = None

        try:
            output = task.output or {}
            content = output.get("content", "") if isinstance(output, dict) else str(output)

            if test_type == "assertion":
                status, actual_value = self._eval_assertion(content, assertion)
            elif test_type == "regex":
                status, actual_value = self._eval_regex(content, assertion)
            elif test_type == "schema":
                status, actual_value = self._eval_schema(output, assertion)
            elif test_type == "llm_eval":
                status = "skipped"
                actual_value = "LLM evaluation not implemented in this version"
            else:
                status = "error"
                error_message = f"Unknown test type: {test_type}"

        except Exception as e:
            status = "error"
            error_message = str(e)

        execution_time_ms = (time.perf_counter() - start) * 1000

        test = TaskTest(
            task_id=task_id,
            test_name=test_name,
            test_type=test_type,
            status=status,
            assertion=assertion,
            actual_value=actual_value,
            error_message=error_message,
            execution_time_ms=round(execution_time_ms, 2),
        )
        self.session.add(test)
        await self.session.flush()
        await self.session.refresh(test)

        # Publish event
        await EventBus.publish_dict(
            ["task", str(task_id), "test_result"],
            "task.test_result",
            {
                "task_id": str(task_id),
                "test_id": str(test.test_id),
                "test_name": test_name,
                "status": status,
            },
        )

        return test

    async def list_tests(self, task_id: uuid.UUID) -> list[TaskTest]:
        result = await self.session.execute(
            select(TaskTest)
            .where(TaskTest.task_id == task_id)
            .order_by(TaskTest.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_summary(self, task_id: uuid.UUID) -> dict:
        result = await self.session.execute(
            select(
                func.count().label("total"),
                func.count().filter(TaskTest.status == "pass").label("passed"),
                func.count().filter(TaskTest.status == "fail").label("failed"),
                func.count().filter(TaskTest.status == "error").label("errors"),
                func.count().filter(TaskTest.status == "skipped").label("skipped"),
            ).where(TaskTest.task_id == task_id)
        )
        row = result.one()
        return {
            "task_id": str(task_id),
            "total": row.total,
            "passed": row.passed,
            "failed": row.failed,
            "errors": row.errors,
            "skipped": row.skipped,
        }

    @staticmethod
    def _eval_assertion(content: str, assertion: dict) -> tuple[str, str]:
        """Evaluate a content assertion."""
        assert_type = assertion.get("type", "contains")
        expected = assertion.get("expected", "")

        if assert_type == "contains":
            found = expected.lower() in content.lower()
            return ("pass" if found else "fail", f"contains '{expected}': {found}")

        elif assert_type == "not_contains":
            found = expected.lower() in content.lower()
            return ("pass" if not found else "fail", f"not_contains '{expected}': {not found}")

        elif assert_type == "equals":
            match = content.strip() == expected.strip()
            return ("pass" if match else "fail", f"equals: {match}")

        elif assert_type == "min_length":
            min_len = int(expected)
            actual_len = len(content)
            return ("pass" if actual_len >= min_len else "fail", f"length={actual_len}, min={min_len}")

        elif assert_type == "max_length":
            max_len = int(expected)
            actual_len = len(content)
            return ("pass" if actual_len <= max_len else "fail", f"length={actual_len}, max={max_len}")

        return ("error", f"Unknown assertion type: {assert_type}")

    @staticmethod
    def _eval_regex(content: str, assertion: dict) -> tuple[str, str]:
        """Evaluate a regex assertion."""
        pattern = assertion.get("pattern", "")
        match = re.search(pattern, content, re.IGNORECASE)
        return ("pass" if match else "fail", f"regex '{pattern}': {'matched' if match else 'no match'}")

    @staticmethod
    def _eval_schema(output: dict, assertion: dict) -> tuple[str, str]:
        """Evaluate a schema assertion (check required keys exist)."""
        required_keys = assertion.get("required_keys", [])
        missing = [k for k in required_keys if k not in output]
        if missing:
            return ("fail", f"missing keys: {missing}")
        return ("pass", f"all {len(required_keys)} required keys present")
