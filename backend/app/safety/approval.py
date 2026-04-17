"""Approval workflow manager for high-risk agent actions.

Provides a Redis-backed approval queue where human reviewers can
approve or deny actions flagged by the safety layer.  Notifications
are broadcast over the EventBus so dashboards and webhooks react
in real time.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from app.core.events import EventBus
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEY_PREFIX: str = "approval"
_PENDING_SET: str = f"{_KEY_PREFIX}:pending"
_POLL_INTERVAL_SECONDS: float = 0.5

# ---------------------------------------------------------------------------
# Pydantic model (Redis-only; not a DB model)
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """An approval request stored in Redis for fast, ephemeral access."""

    request_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    task_id: str | None = None
    action: str
    context: dict[str, Any] = Field(default_factory=dict)
    policy_rule: str
    status: str = "pending"  # pending | approved | denied | expired
    requested_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    decided_at: str | None = None
    decided_by: str | None = None
    expires_at: str = Field(default_factory=lambda: (datetime.now(UTC) + timedelta(seconds=300)).isoformat())
    # Content hash of the signed WorkflowCheckpoint persisted at request time.
    # Any federated peer that has the hash can pull the checkpoint via the
    # CRDT store and resume the workflow — that's the Day-13 cross-node
    # resume primitive. ``None`` means no checkpoint was written (e.g. for
    # legacy approval flows that predate checkpointing).
    checkpoint_hash: str | None = None


# ---------------------------------------------------------------------------
# ApprovalManager
# ---------------------------------------------------------------------------


class ApprovalManager:
    """Manages the lifecycle of approval requests via Redis.

    Parameters
    ----------
    redis:
        An ``aioredis.Redis`` client used for storing and retrieving
        approval requests.  The caller is responsible for providing a
        client that uses ``decode_responses=True``.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(request_id: str) -> str:
        """Return the Redis key for a given approval request."""
        return f"{_KEY_PREFIX}:{request_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        workflow_id: str,
        task_id: str | None,
        action: str,
        context: dict[str, Any],
        policy_rule: str,
        timeout_seconds: int = 300,
        checkpoint_hash: str | None = None,
    ) -> ApprovalRequest:
        """Create a new approval request and persist it to Redis.

        Parameters
        ----------
        workflow_id:
            The workflow that triggered the approval.
        task_id:
            Optional task within the workflow.
        action:
            Human-readable description of the action requiring approval.
        context:
            Arbitrary context dict to help the reviewer decide.
        policy_rule:
            Identifier of the safety / policy rule that triggered the request.
        timeout_seconds:
            Seconds until the request auto-expires.  Also used as the
            Redis TTL for the key.

        Returns
        -------
        ApprovalRequest
            The newly created request with status ``"pending"``.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=timeout_seconds)

        request = ApprovalRequest(
            workflow_id=workflow_id,
            task_id=task_id,
            action=action,
            context=context,
            policy_rule=policy_rule,
            status="pending",
            requested_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            checkpoint_hash=checkpoint_hash,
        )

        key = self._key(request.request_id)
        payload = request.model_dump_json()

        # Store with TTL and add to the pending set.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, payload, ex=timeout_seconds)
            pipe.sadd(_PENDING_SET, request.request_id)
            await pipe.execute()

        logger.info(
            "approval_requested",
            request_id=request.request_id,
            workflow_id=workflow_id,
            action=action,
            policy_rule=policy_rule,
            expires_at=request.expires_at,
        )

        # Broadcast event so dashboards / webhooks can react.
        await EventBus.publish_dict(
            channel_parts=["approval", workflow_id, "requested"],
            event_type="approval.requested",
            data={
                "request_id": request.request_id,
                "workflow_id": workflow_id,
                "task_id": task_id,
                "action": action,
                "policy_rule": policy_rule,
                "checkpoint_hash": checkpoint_hash,
            },
        )

        return request

    async def get_pending(self, workflow_id: str | None = None) -> list[ApprovalRequest]:
        """Return all pending approval requests.

        Parameters
        ----------
        workflow_id:
            If provided, only return requests for this workflow.

        Returns
        -------
        list[ApprovalRequest]
            Pending requests.  Expired entries are cleaned up lazily.
        """
        request_ids: set[str] = await self._redis.smembers(_PENDING_SET)
        results: list[ApprovalRequest] = []
        expired_ids: list[str] = []

        for rid in request_ids:
            raw: str | None = await self._redis.get(self._key(rid))
            if raw is None:
                # Key expired in Redis; clean up the pending set entry.
                expired_ids.append(rid)
                continue

            request = ApprovalRequest.model_validate_json(raw)

            # Skip if already decided (set not yet cleaned up).
            if request.status != "pending":
                expired_ids.append(rid)
                continue

            # Check wall-clock expiry.
            if datetime.fromisoformat(request.expires_at) <= datetime.now(UTC):
                expired_ids.append(rid)
                continue

            if workflow_id is not None and request.workflow_id != workflow_id:
                continue

            results.append(request)

        # Lazy cleanup of stale pending-set entries.
        if expired_ids:
            await self._redis.srem(_PENDING_SET, *expired_ids)

        return results

    async def approve(self, request_id: str, decided_by: str) -> ApprovalRequest:
        """Mark a pending request as approved.

        Parameters
        ----------
        request_id:
            The unique ID of the request.
        decided_by:
            Identifier of the person or system approving the request.

        Returns
        -------
        ApprovalRequest
            The updated request with status ``"approved"``.

        Raises
        ------
        ValueError
            If the request does not exist or is no longer pending.
        """
        return await self._decide(request_id, "approved", decided_by)

    async def deny(
        self,
        request_id: str,
        decided_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Mark a pending request as denied.

        Parameters
        ----------
        request_id:
            The unique ID of the request.
        decided_by:
            Identifier of the person or system denying the request.
        reason:
            Optional human-readable reason for the denial.

        Returns
        -------
        ApprovalRequest
            The updated request with status ``"denied"``.

        Raises
        ------
        ValueError
            If the request does not exist or is no longer pending.
        """
        return await self._decide(request_id, "denied", decided_by, reason=reason)

    async def check_status(self, request_id: str) -> ApprovalRequest:
        """Retrieve the current state of an approval request.

        Parameters
        ----------
        request_id:
            The unique ID of the request.

        Returns
        -------
        ApprovalRequest
            The current request object.

        Raises
        ------
        ValueError
            If the request does not exist (or has expired from Redis).
        """
        raw: str | None = await self._redis.get(self._key(request_id))
        if raw is None:
            raise ValueError(f"Approval request '{request_id}' not found or expired.")
        return ApprovalRequest.model_validate_json(raw)

    async def wait_for_decision(self, request_id: str, timeout: int = 300) -> ApprovalRequest:
        """Poll Redis until a decision is made or the timeout elapses.

        Parameters
        ----------
        request_id:
            The unique ID of the request to watch.
        timeout:
            Maximum seconds to wait before returning an expired result.

        Returns
        -------
        ApprovalRequest
            The decided (or expired) request.
        """
        deadline = datetime.now(UTC) + timedelta(seconds=timeout)

        while datetime.now(UTC) < deadline:
            raw: str | None = await self._redis.get(self._key(request_id))

            if raw is None:
                # Key evicted -- treat as expired.
                logger.warning("approval_wait_expired_key_missing", request_id=request_id)
                return ApprovalRequest(
                    request_id=request_id,
                    workflow_id="unknown",
                    action="unknown",
                    policy_rule="unknown",
                    status="expired",
                    requested_at=datetime.now(UTC).isoformat(),
                    expires_at=datetime.now(UTC).isoformat(),
                )

            request = ApprovalRequest.model_validate_json(raw)

            if request.status in ("approved", "denied"):
                return request

            # Check if the request itself has expired.
            if datetime.fromisoformat(request.expires_at) <= datetime.now(UTC):
                return await self._mark_expired(request)

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        # Timeout reached -- mark as expired if still pending.
        try:
            request = await self.check_status(request_id)
            if request.status == "pending":
                return await self._mark_expired(request)
            return request
        except ValueError:
            return ApprovalRequest(
                request_id=request_id,
                workflow_id="unknown",
                action="unknown",
                policy_rule="unknown",
                status="expired",
                requested_at=datetime.now(UTC).isoformat(),
                expires_at=datetime.now(UTC).isoformat(),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _decide(
        self,
        request_id: str,
        status: str,
        decided_by: str,
        *,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Apply a decision (approved / denied) to a pending request."""
        raw: str | None = await self._redis.get(self._key(request_id))
        if raw is None:
            raise ValueError(f"Approval request '{request_id}' not found or expired.")

        request = ApprovalRequest.model_validate_json(raw)

        if request.status != "pending":
            raise ValueError(f"Approval request '{request_id}' is already '{request.status}', cannot change decision.")

        now = datetime.now(UTC)
        request.status = status
        request.decided_at = now.isoformat()
        request.decided_by = decided_by

        if reason is not None:
            request.context["denial_reason"] = reason

        # Persist the updated request.  Keep the existing TTL so it
        # doesn't linger forever.
        ttl: int = await self._redis.ttl(self._key(request_id))
        if ttl < 0:
            ttl = 60  # Fallback: keep for 60 s after decision.

        await self._redis.set(self._key(request_id), request.model_dump_json(), ex=ttl)
        await self._redis.srem(_PENDING_SET, request_id)

        logger.info(
            f"approval_{status}",
            request_id=request_id,
            decided_by=decided_by,
            workflow_id=request.workflow_id,
        )

        await EventBus.publish_dict(
            channel_parts=["approval", request.workflow_id, status],
            event_type=f"approval.{status}",
            data={
                "request_id": request_id,
                "workflow_id": request.workflow_id,
                "task_id": request.task_id,
                "action": request.action,
                "decided_by": decided_by,
                "reason": reason,
                "checkpoint_hash": request.checkpoint_hash,
            },
        )

        return request

    async def _mark_expired(self, request: ApprovalRequest) -> ApprovalRequest:
        """Transition a pending request to expired status."""
        request.status = "expired"
        request.decided_at = datetime.now(UTC).isoformat()

        key = self._key(request.request_id)
        ttl: int = await self._redis.ttl(key)
        if ttl < 0:
            ttl = 60

        await self._redis.set(key, request.model_dump_json(), ex=ttl)
        await self._redis.srem(_PENDING_SET, request.request_id)

        logger.info(
            "approval_expired",
            request_id=request.request_id,
            workflow_id=request.workflow_id,
        )

        await EventBus.publish_dict(
            channel_parts=["approval", request.workflow_id, "expired"],
            event_type="approval.expired",
            data={
                "request_id": request.request_id,
                "workflow_id": request.workflow_id,
                "task_id": request.task_id,
                "action": request.action,
            },
        )

        return request
