from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

__all__ = ["Base", "AuditMixin", "AgentStatus", "WorkflowStatus", "TaskStatus", "MemoryType", "WalletActionType", "AuditEventType", "ActorType", "PolicyAction", "PolicyRuleType"]


class AuditMixin:
    """Mixin adding created_at and updated_at timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --- Enums ---


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    DEGRADED = "degraded"


class WorkflowStatus(str, enum.Enum):
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoryType(str, enum.Enum):
    FACT = "fact"
    CONTEXT = "context"
    RESULT = "result"
    REASONING = "reasoning"
    META = "meta"


class WalletActionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    RESERVE = "reserve"
    RELEASE = "release"


class AuditEventType(str, enum.Enum):
    # Workflow events
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_CANCELLED = "workflow.cancelled"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"
    # Task events
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_RETRIED = "task.retried"
    TASK_SKIPPED = "task.skipped"
    # Agent events
    AGENT_REGISTERED = "agent.registered"
    AGENT_UPDATED = "agent.updated"
    AGENT_DEACTIVATED = "agent.deactivated"
    AGENT_HEALTH_CHANGED = "agent.health_changed"
    # Trust events
    TRUST_UPDATED = "trust.updated"
    TRUST_DECAYED = "trust.decayed"
    # Memory events
    MEMORY_WRITTEN = "memory.written"
    MEMORY_QUERIED = "memory.queried"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_GC = "memory.gc"
    # Wallet events
    WALLET_CHARGED = "wallet.charged"
    WALLET_RESERVED = "wallet.reserved"
    WALLET_RELEASED = "wallet.released"
    WALLET_CREDITED = "wallet.credited"
    # Policy events
    POLICY_EVALUATED = "policy.evaluated"
    POLICY_VIOLATION = "policy.violation"
    POLICY_RULE_CREATED = "policy.rule_created"
    POLICY_RULE_UPDATED = "policy.rule_updated"
    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_EXPIRED = "approval.expired"
    # Evolution events
    AGENT_PROMOTED = "agent.promoted"
    AGENT_DEMOTED = "agent.demoted"
    SCORE_RECORDED = "score.recorded"
    # Consensus events
    CONSENSUS_REQUESTED = "consensus.requested"
    CONSENSUS_REACHED = "consensus.reached"
    CONSENSUS_FAILED = "consensus.failed"


class ActorType(str, enum.Enum):
    SYSTEM = "system"
    AGENT = "agent"
    USER = "user"
    POLICY = "policy"


class PolicyAction(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    FLAG = "flag"
    LOG = "log"


class PolicyRuleType(str, enum.Enum):
    CONTENT = "content"
    ACCESS = "access"
    RESOURCE = "resource"
    WORKFLOW = "workflow"
