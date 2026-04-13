from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.base import PolicyAction, PolicyRuleType
from app.models.policy import PolicyRule
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.policy import (
    PolicyRuleCreate,
    PolicyRuleResponse,
    PolicyRuleUpdate,
    PolicyViolationDetail,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses for internal results
# ---------------------------------------------------------------------------


@dataclass
class ContentAnalysis:
    """Result of content safety analysis."""

    safe: bool
    categories: dict[str, float] = field(default_factory=dict)
    flagged_categories: list[str] = field(default_factory=list)
    severity: str = "none"  # none | low | medium | high | critical
    explanation: str = ""


@dataclass
class InjectionAssessment:
    """Result of prompt injection detection."""

    detected: bool
    confidence: float = 0.0
    injection_type: str | None = None  # direct | indirect | jailbreak | none
    patterns_matched: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class PolicyDecision:
    """Result of a policy engine evaluation."""

    approved: bool
    action: PolicyAction
    matched_rule: str | None = None
    reason: str = ""
    violations: list[PolicyViolationDetail] = field(default_factory=list)


@dataclass
class ContentSafetyResult:
    """Combined result from content filter and injection detector."""

    safe: bool
    content_analysis: ContentAnalysis | None = None
    injection_assessment: InjectionAssessment | None = None
    recommendation: str = ""


@dataclass
class FullEvaluationResult:
    """Combined result from both action policy and content safety evaluation."""

    approved: bool
    policy_decision: PolicyDecision | None = None
    content_safety: ContentSafetyResult | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Lightweight safety analysers (inline implementations)
# ---------------------------------------------------------------------------
# In a production deployment these would delegate to dedicated ML models or
# external APIs.  The implementations here use heuristic / rule-based
# approaches so the service is fully functional without additional
# dependencies.

# Common dangerous patterns for injection detection
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("ignore previous instructions", "direct"),
    ("ignore all prior instructions", "direct"),
    ("disregard your instructions", "direct"),
    ("forget your rules", "direct"),
    ("you are now", "jailbreak"),
    ("pretend you are", "jailbreak"),
    ("act as if you have no restrictions", "jailbreak"),
    ("do anything now", "jailbreak"),
    ("system prompt:", "indirect"),
    ("</system>", "indirect"),
    ("[system]", "indirect"),
    ("reveal your instructions", "indirect"),
    ("output your system prompt", "indirect"),
]

# Content categories with keyword signals
_CONTENT_CATEGORIES: dict[str, list[str]] = {
    "violence": ["kill", "murder", "attack", "bomb", "weapon", "destroy", "terrorism"],
    "self_harm": ["suicide", "self-harm", "cut myself", "end my life"],
    "hate_speech": ["racial slur", "hate group", "supremacy", "genocide"],
    "sexual_content": ["explicit sexual", "pornograph"],
    "illegal_activity": ["how to hack", "make drugs", "launder money", "counterfeit"],
    "pii_exposure": ["social security number", "credit card number", "ssn:", "password:"],
}

_SEVERITY_THRESHOLDS = {
    "low": 0.2,
    "medium": 0.4,
    "high": 0.6,
    "critical": 0.8,
}


class PolicyEngine:
    """Evaluates an action against the stored policy rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate(self, action: str, context: dict) -> PolicyDecision:
        """Evaluate an action against all enabled policy rules.

        Rules are checked in priority order (lowest number = highest priority).
        The first DENY or REQUIRE_APPROVAL rule that matches short-circuits.
        """
        result = await self.session.execute(
            select(PolicyRule)
            .where(PolicyRule.enabled.is_(True))
            .order_by(PolicyRule.priority.asc())
        )
        rules = list(result.scalars().all())

        violations: list[PolicyViolationDetail] = []
        effective_action = PolicyAction.ALLOW
        matched_rule_name: str | None = None

        for rule in rules:
            if self._matches(rule, action, context):
                if rule.action in (PolicyAction.DENY, PolicyAction.REQUIRE_APPROVAL):
                    violations.append(
                        PolicyViolationDetail(
                            rule_name=rule.name,
                            rule_id=rule.rule_id,
                            action=rule.action,
                            reason=rule.description or f"Rule '{rule.name}' triggered",
                        )
                    )
                    # The highest-priority (lowest number) blocking rule wins
                    if effective_action == PolicyAction.ALLOW:
                        effective_action = rule.action
                        matched_rule_name = rule.name
                elif rule.action == PolicyAction.FLAG:
                    violations.append(
                        PolicyViolationDetail(
                            rule_name=rule.name,
                            rule_id=rule.rule_id,
                            action=rule.action,
                            reason=rule.description or f"Rule '{rule.name}' flagged",
                        )
                    )

        approved = effective_action in (PolicyAction.ALLOW, PolicyAction.LOG, PolicyAction.FLAG)
        reason = (
            f"Blocked by rule '{matched_rule_name}'"
            if not approved and matched_rule_name
            else "All policy checks passed"
        )

        return PolicyDecision(
            approved=approved,
            action=effective_action,
            matched_rule=matched_rule_name,
            reason=reason,
            violations=violations,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _matches(rule: PolicyRule, action: str, context: dict) -> bool:
        """Evaluate whether a rule's condition matches the given context.

        Condition format:
            {"field": "agent.trust_score", "op": "lt", "value": 0.3}
            {"action": "memory_write"}
        """
        cond = rule.condition

        # Simple action match
        if "action" in cond:
            if cond["action"] != action:
                return False

        # Rule-type scoping
        if rule.rule_type == PolicyRuleType.CONTENT and "content" not in context:
            return False

        # Field-based condition with dot-notation resolution
        if "field" in cond and "op" in cond and "value" in cond:
            field_path = cond["field"]
            expected = cond["value"]
            # Resolve dot-notation: "agent.trust_score" -> context["agent"]["trust_score"]
            actual = context
            for part in field_path.split("."):
                if isinstance(actual, dict):
                    actual = actual.get(part)
                else:
                    actual = None
                    break

            if actual is None:
                return False

            op = cond["op"]
            try:
                if op == "eq":
                    return actual == expected
                elif op == "ne":
                    return actual != expected
                elif op == "lt":
                    return float(actual) < float(expected)
                elif op == "le":
                    return float(actual) <= float(expected)
                elif op == "gt":
                    return float(actual) > float(expected)
                elif op == "ge":
                    return float(actual) >= float(expected)
                elif op == "in":
                    return actual in expected
                elif op == "contains":
                    return expected in actual
            except (TypeError, ValueError):
                return False

        # If condition only specifies action and it matched, the rule applies
        if "action" in cond and "field" not in cond:
            return True

        return False


class ContentFilter:
    """Heuristic content safety analyser."""

    def analyze(self, content: str) -> ContentAnalysis:
        """Analyse content for safety violations."""
        content_lower = content.lower()
        category_scores: dict[str, float] = {}
        flagged: list[str] = []

        for category, keywords in _CONTENT_CATEGORIES.items():
            matches = sum(1 for kw in keywords if kw in content_lower)
            score = min(1.0, matches / max(len(keywords) * 0.3, 1))
            category_scores[category] = round(score, 3)
            if score >= 0.2:
                flagged.append(category)

        max_score = max(category_scores.values()) if category_scores else 0.0
        severity = "none"
        for level, threshold in sorted(_SEVERITY_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
            if max_score >= threshold:
                severity = level
                break

        safe = len(flagged) == 0

        explanation = "Content is safe." if safe else (
            f"Content flagged in categories: {', '.join(flagged)}. "
            f"Highest severity: {severity}."
        )

        return ContentAnalysis(
            safe=safe,
            categories=category_scores,
            flagged_categories=flagged,
            severity=severity,
            explanation=explanation,
        )


class InjectionDetector:
    """Heuristic prompt injection detector."""

    def analyze(self, content: str) -> InjectionAssessment:
        """Analyse content for prompt injection attempts."""
        content_lower = content.lower()
        matched_patterns: list[str] = []
        injection_types: set[str] = set()

        for pattern, inj_type in _INJECTION_PATTERNS:
            if pattern in content_lower:
                matched_patterns.append(pattern)
                injection_types.add(inj_type)

        if not matched_patterns:
            return InjectionAssessment(
                detected=False,
                confidence=0.0,
                injection_type=None,
                patterns_matched=[],
                explanation="No injection patterns detected.",
            )

        confidence = min(1.0, len(matched_patterns) * 0.25)

        # Pick the most severe injection type
        type_priority = {"jailbreak": 3, "direct": 2, "indirect": 1}
        primary_type = max(injection_types, key=lambda t: type_priority.get(t, 0))

        return InjectionAssessment(
            detected=True,
            confidence=round(confidence, 3),
            injection_type=primary_type,
            patterns_matched=matched_patterns,
            explanation=(
                f"Detected {len(matched_patterns)} injection pattern(s). "
                f"Primary type: {primary_type}. Confidence: {confidence:.1%}."
            ),
        )


# ---------------------------------------------------------------------------
# Orchestration service
# ---------------------------------------------------------------------------


class PolicyService:
    """Orchestration layer combining policy engine, content filter, and
    injection detector into a unified safety evaluation API."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self._engine = PolicyEngine(session)
        self._content_filter = ContentFilter()
        self._injection_detector = InjectionDetector()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate_action(self, action: str, context: dict) -> PolicyDecision:
        """Evaluate an action against the policy rule set."""
        decision = await self._engine.evaluate(action, context)

        logger.info(
            "policy_action_evaluated",
            action=action,
            approved=decision.approved,
            effective_action=decision.action.value,
            matched_rule=decision.matched_rule,
        )

        return decision

    def evaluate_content(self, content: str) -> ContentSafetyResult:
        """Run both content filter and injection detector, combine results."""
        content_analysis = self._content_filter.analyze(content)
        injection_assessment = self._injection_detector.analyze(content)

        safe = content_analysis.safe and not injection_assessment.detected

        parts: list[str] = []
        if not content_analysis.safe:
            parts.append(content_analysis.explanation)
        if injection_assessment.detected:
            parts.append(injection_assessment.explanation)
        recommendation = " ".join(parts) if parts else "Content passed all safety checks."

        logger.info(
            "content_evaluated",
            safe=safe,
            content_safe=content_analysis.safe,
            injection_detected=injection_assessment.detected,
        )

        return ContentSafetyResult(
            safe=safe,
            content_analysis=content_analysis,
            injection_assessment=injection_assessment,
            recommendation=recommendation,
        )

    async def evaluate_full(
        self,
        action: str,
        context: dict,
        content: str | None = None,
    ) -> FullEvaluationResult:
        """Run both action policy and content safety evaluation."""
        policy_decision = await self.evaluate_action(action, context)

        content_safety: ContentSafetyResult | None = None
        if content is not None:
            content_safety = self.evaluate_content(content)

        approved = policy_decision.approved
        if content_safety is not None and not content_safety.safe:
            approved = False

        reasons: list[str] = []
        if not policy_decision.approved:
            reasons.append(policy_decision.reason)
        if content_safety is not None and not content_safety.safe:
            reasons.append(content_safety.recommendation)
        reason = " | ".join(reasons) if reasons else "All checks passed."

        return FullEvaluationResult(
            approved=approved,
            policy_decision=policy_decision,
            content_safety=content_safety,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # CRUD for policy rules
    # ------------------------------------------------------------------

    async def list_rules(
        self,
        pagination: PaginationParams,
    ) -> PaginatedResponse[PolicyRuleResponse]:
        """List all policy rules with pagination."""
        count_result = await self.session.execute(
            select(func.count()).select_from(PolicyRule)
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            select(PolicyRule)
            .order_by(PolicyRule.priority.asc(), PolicyRule.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        rules = list(result.scalars().all())

        return PaginatedResponse(
            items=[PolicyRuleResponse.model_validate(r) for r in rules],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def create_rule(self, data: PolicyRuleCreate) -> PolicyRuleResponse:
        """Create a new policy rule."""
        rule = PolicyRule(
            name=data.name,
            description=data.description,
            rule_type=data.rule_type,
            condition=data.condition,
            action=data.action,
            priority=data.priority,
            enabled=data.enabled,
        )
        self.session.add(rule)
        await self.session.flush()
        await self.session.refresh(rule)

        logger.info("policy_rule_created", rule_id=str(rule.rule_id), name=rule.name)

        return PolicyRuleResponse.model_validate(rule)

    async def get_rule(self, rule_id: str | uuid.UUID) -> PolicyRule:
        """Fetch a single rule by ID or raise NotFoundError."""
        uid = rule_id if isinstance(rule_id, uuid.UUID) else uuid.UUID(str(rule_id))
        result = await self.session.execute(
            select(PolicyRule).where(PolicyRule.rule_id == uid)
        )
        rule = result.scalar_one_or_none()
        if rule is None:
            raise NotFoundError("PolicyRule", str(rule_id))
        return rule

    async def update_rule(
        self,
        rule_id: str,
        data: PolicyRuleUpdate,
    ) -> PolicyRuleResponse:
        """Update an existing policy rule."""
        uid = uuid.UUID(str(rule_id))
        rule = await self.get_rule(uid)

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return PolicyRuleResponse.model_validate(rule)

        for field_name, value in update_data.items():
            setattr(rule, field_name, value)

        await self.session.flush()
        await self.session.refresh(rule)

        logger.info(
            "policy_rule_updated",
            rule_id=str(rule_id),
            fields=list(update_data.keys()),
        )

        return PolicyRuleResponse.model_validate(rule)

    async def delete_rule(self, rule_id: str) -> None:
        """Hard-delete a policy rule."""
        uid = uuid.UUID(str(rule_id))
        rule = await self.get_rule(uid)
        await self.session.delete(rule)
        await self.session.flush()

        logger.info("policy_rule_deleted", rule_id=str(rule_id))

    async def toggle_rule(self, rule_id: str) -> PolicyRuleResponse:
        """Toggle a policy rule's enabled state."""
        uid = uuid.UUID(str(rule_id))
        rule = await self.get_rule(uid)
        rule.enabled = not rule.enabled
        await self.session.flush()
        await self.session.refresh(rule)

        logger.info(
            "policy_rule_toggled",
            rule_id=str(rule_id),
            enabled=rule.enabled,
        )

        return PolicyRuleResponse.model_validate(rule)
