"""Recursive boolean condition evaluator for policy rules.

This module implements the core policy engine for MassClaw's safety layer.
Rules are stored in the database (``PolicyRule`` model) and cached in Redis
with a 5-minute TTL that is invalidated on any mutation.  Conditions are
expressed as nested JSON structures supporting boolean combinators
(``all``, ``any``, ``not``) and comparison operators.

Typical condition document::

    {
        "all": [
            {"field": "agent.trust_score", "op": "lt", "value": 0.3},
            {"any": [
                {"field": "action_type", "op": "eq", "value": "write"},
                {"field": "resource.sensitivity", "op": "ge", "value": 0.8}
            ]}
        ]
    }
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import NotFoundError, ValidationError
from app.models.base import PolicyAction, PolicyRuleType
from app.models.policy import PolicyRule
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.policy import (
    PolicyDecisionResponse,
    PolicyRuleCreate,
    PolicyRuleResponse,
    PolicyRuleUpdate,
    PolicyViolationDetail,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_KEY = "massclaw:policy:active_rules"
_CACHE_TTL_SECONDS = 300  # 5 minutes

_SUPPORTED_OPS: frozenset[str] = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "exists"})

# Operator dispatch table -- avoids repetitive if/elif chains.
_OP_DISPATCH: dict[str, Any] = {
    "eq": lambda v, ref: v == ref,
    "ne": lambda v, ref: v != ref,
    "lt": lambda v, ref: v < ref,
    "le": lambda v, ref: v <= ref,
    "gt": lambda v, ref: v > ref,
    "ge": lambda v, ref: v >= ref,
    "in": lambda v, ref: v in ref,
    "not_in": lambda v, ref: v not in ref,
    "contains": lambda v, ref: ref in v,
    # ``exists`` is handled specially in _evaluate_condition
}


class PolicyEngine:
    """Evaluate actions against stored policy rules.

    Parameters
    ----------
    session:
        An async SQLAlchemy session for database access.
    redis:
        An async Redis client for caching active rules.
    """

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    # ------------------------------------------------------------------
    # Public evaluation API
    # ------------------------------------------------------------------

    async def evaluate(self, action: str, context: dict[str, Any]) -> PolicyDecisionResponse:
        """Evaluate an action against all active policy rules.

        The method loads active rules (from Redis cache or DB), filters them by
        ``rule_type`` matching the *action* string, sorts by priority (ascending
        -- lower number is higher priority), and evaluates each rule's condition
        recursively against *context*.

        The first matching ``deny`` or ``require_approval`` rule wins.  If no
        such rule matches, the default decision is **allow**.

        Parameters
        ----------
        action:
            The action being evaluated, e.g. ``"content"``, ``"access"``,
            ``"resource.write"``.
        context:
            Arbitrary dict that the rule conditions reference via dot-notation
            field paths.

        Returns
        -------
        PolicyDecisionResponse
            The resulting policy decision including any violations.
        """
        rules = await self._load_active_rules()

        # Filter rules whose rule_type matches the action string.
        # We match if the action string *starts with* the rule_type value,
        # enabling hierarchical matching (e.g. rule_type "resource" matches
        # action "resource.write").
        matching_rules = [r for r in rules if action == r["rule_type"] or action.startswith(f"{r['rule_type']}.")]

        # Sort by priority ascending (lower number = higher priority).
        matching_rules.sort(key=lambda r: r["priority"])

        violations: list[PolicyViolationDetail] = []
        decision_action = PolicyAction.ALLOW
        matched_rule_name: str | None = None
        reason = "No matching policy rules triggered; action allowed by default."

        for rule in matching_rules:
            try:
                condition_met = self._evaluate_condition(rule["condition"], context)
            except Exception:
                logger.warning(
                    "policy_condition_eval_error",
                    rule_name=rule["name"],
                    rule_id=rule["rule_id"],
                )
                continue

            if not condition_met:
                continue

            rule_action = PolicyAction(rule["action"])

            # Accumulate all violations for reporting purposes.
            violations.append(
                PolicyViolationDetail(
                    rule_name=rule["name"],
                    rule_id=uuid.UUID(rule["rule_id"]),
                    action=rule_action,
                    reason=rule.get("description") or f"Rule '{rule['name']}' triggered.",
                )
            )

            # First deny / require_approval wins.
            if rule_action in (PolicyAction.DENY, PolicyAction.REQUIRE_APPROVAL):
                decision_action = rule_action
                matched_rule_name = rule["name"]
                reason = rule.get("description") or f"Action blocked by policy rule '{rule['name']}'."
                break

            # FLAG and LOG do not block; continue evaluating.
            if rule_action in (PolicyAction.FLAG, PolicyAction.LOG):
                logger.info(
                    "policy_rule_flagged",
                    rule_name=rule["name"],
                    action=action,
                )

        approved = decision_action in (PolicyAction.ALLOW, PolicyAction.FLAG, PolicyAction.LOG)

        logger.info(
            "policy_evaluated",
            action=action,
            approved=approved,
            decision=decision_action.value,
            matched_rule=matched_rule_name,
            violations_count=len(violations),
        )

        return PolicyDecisionResponse(
            approved=approved,
            action=decision_action,
            matched_rule=matched_rule_name,
            reason=reason,
            violations=violations,
        )

    async def evaluate_content(self, content: str) -> dict[str, Any]:
        """Evaluate content safety by delegating to the content filter.

        This is a convenience stub that imports and invokes the content filter
        module.  It exists so callers can use the PolicyEngine as a single
        entry-point for all safety checks.

        Parameters
        ----------
        content:
            The text to analyse.

        Returns
        -------
        dict
            The ``ContentAnalysis`` result serialised as a dict.
        """
        from app.safety.content_filter import ContentFilter

        cf = ContentFilter()
        analysis = await cf.analyze(content)
        return {
            "is_safe": analysis.is_safe,
            "categories": analysis.categories,
            "pii_detected": [
                {
                    "type": m.type,
                    "pattern": m.pattern,
                    "location": m.location,
                    "redacted": m.redacted,
                }
                for m in analysis.pii_detected
            ],
            "flags": analysis.flags,
            "confidence": analysis.confidence,
        }

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def create_rule(self, data: PolicyRuleCreate) -> PolicyRule:
        """Create a new policy rule.

        Parameters
        ----------
        data:
            Validated creation payload.

        Returns
        -------
        PolicyRule
            The newly persisted rule.

        Raises
        ------
        ValidationError
            If the condition JSON is structurally invalid.
        """
        self._validate_condition(data.condition)

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

        await self._invalidate_cache()

        logger.info(
            "policy_rule_created",
            rule_id=str(rule.rule_id),
            name=rule.name,
            action=rule.action.value,
            priority=rule.priority,
        )

        return rule

    async def update_rule(self, rule_id: uuid.UUID, data: PolicyRuleUpdate) -> PolicyRule:
        """Update an existing policy rule.

        Only the fields present in *data* (non-``None``) are applied.

        Parameters
        ----------
        rule_id:
            The UUID of the rule to update.
        data:
            The update payload.

        Returns
        -------
        PolicyRule
            The updated rule.

        Raises
        ------
        NotFoundError
            If no rule with *rule_id* exists.
        ValidationError
            If the supplied condition JSON is structurally invalid.
        """
        rule = await self._get_rule_or_raise(rule_id)

        update_fields = data.model_dump(exclude_unset=True)
        if not update_fields:
            return rule

        if "condition" in update_fields:
            self._validate_condition(update_fields["condition"])

        for field_name, value in update_fields.items():
            setattr(rule, field_name, value)

        await self.session.flush()
        await self._invalidate_cache()

        logger.info(
            "policy_rule_updated",
            rule_id=str(rule_id),
            updated_fields=list(update_fields.keys()),
        )

        return rule

    async def toggle_rule(self, rule_id: uuid.UUID, enabled: bool) -> PolicyRule:
        """Enable or disable a policy rule.

        Parameters
        ----------
        rule_id:
            The UUID of the rule.
        enabled:
            Whether the rule should be active.

        Returns
        -------
        PolicyRule
            The updated rule.
        """
        rule = await self._get_rule_or_raise(rule_id)
        rule.enabled = enabled
        await self.session.flush()
        await self._invalidate_cache()

        logger.info(
            "policy_rule_toggled",
            rule_id=str(rule_id),
            enabled=enabled,
        )

        return rule

    async def list_rules(
        self,
        pagination: PaginationParams,
        *,
        rule_type: PolicyRuleType | None = None,
        enabled: bool | None = None,
    ) -> PaginatedResponse[PolicyRuleResponse]:
        """Return a paginated list of policy rules.

        Parameters
        ----------
        pagination:
            Page number and page size.
        rule_type:
            Optional filter by rule type.
        enabled:
            Optional filter by enabled status.

        Returns
        -------
        PaginatedResponse[PolicyRuleResponse]
        """
        query = select(PolicyRule)
        count_query = select(func.count()).select_from(PolicyRule)

        if rule_type is not None:
            query = query.where(PolicyRule.rule_type == rule_type)
            count_query = count_query.where(PolicyRule.rule_type == rule_type)

        if enabled is not None:
            query = query.where(PolicyRule.enabled == enabled)
            count_query = count_query.where(PolicyRule.enabled == enabled)

        total_result = await self.session.execute(count_query)
        total: int = total_result.scalar_one()

        query = (
            query.order_by(PolicyRule.priority.asc(), PolicyRule.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        result = await self.session.execute(query)
        rules = list(result.scalars().all())

        items = [PolicyRuleResponse.model_validate(r) for r in rules]

        return PaginatedResponse[PolicyRuleResponse](
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    # ------------------------------------------------------------------
    # Recursive condition evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_field(context: dict[str, Any], field_path: str) -> tuple[bool, Any]:
        """Resolve a dot-notation field path against a nested context dict.

        Parameters
        ----------
        context:
            The context dict to look up values in.
        field_path:
            A dot-delimited path, e.g. ``"agent.trust_score"``.

        Returns
        -------
        tuple[bool, Any]
            A ``(found, value)`` pair.  *found* is ``False`` when the path
            does not exist in the context.
        """
        parts = field_path.split(".")
        current: Any = context
        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return False, None
                current = current[part]
            else:
                # Attempt attribute access for non-dict objects.
                if hasattr(current, part):
                    current = getattr(current, part)
                else:
                    return False, None
        return True, current

    @classmethod
    def _evaluate_condition(cls, condition: dict[str, Any], context: dict[str, Any]) -> bool:
        """Recursively evaluate a condition tree against *context*.

        Supported structures:

        * **Leaf condition**:
          ``{"field": "...", "op": "...", "value": ...}``
        * **Boolean combinators**:
          ``{"all": [<conditions>]}``, ``{"any": [<conditions>]}``,
          ``{"not": <condition>}``

        Parameters
        ----------
        condition:
            The JSON condition structure.
        context:
            The context dict that field paths are resolved against.

        Returns
        -------
        bool
        """
        # -- Boolean combinators ----------------------------------------
        if "all" in condition:
            children = condition["all"]
            if not isinstance(children, list):
                raise ValidationError("'all' combinator must contain a list of conditions.")
            return all(cls._evaluate_condition(c, context) for c in children)

        if "any" in condition:
            children = condition["any"]
            if not isinstance(children, list):
                raise ValidationError("'any' combinator must contain a list of conditions.")
            return any(cls._evaluate_condition(c, context) for c in children)

        if "not" in condition:
            child = condition["not"]
            if not isinstance(child, dict):
                raise ValidationError("'not' combinator must contain a single condition dict.")
            return not cls._evaluate_condition(child, context)

        # -- Leaf condition ---------------------------------------------
        field_path: str | None = condition.get("field")
        op: str | None = condition.get("op")

        if field_path is None or op is None:
            raise ValidationError(
                f"Invalid condition structure; expected 'field' and 'op', got keys: {list(condition.keys())}"
            )

        if op not in _SUPPORTED_OPS:
            raise ValidationError(f"Unsupported operator '{op}'. Supported: {sorted(_SUPPORTED_OPS)}")

        found, resolved_value = cls._resolve_field(context, field_path)

        # Special handling for the ``exists`` operator.
        if op == "exists":
            expected = condition.get("value", True)
            return found is bool(expected)

        # For all other operators the field must exist.
        if not found:
            return False

        ref_value = condition.get("value")

        try:
            return _OP_DISPATCH[op](resolved_value, ref_value)
        except TypeError:
            # Incompatible types (e.g. comparing str to int) -- treat as
            # non-match rather than crashing the evaluation loop.
            logger.debug(
                "policy_condition_type_mismatch",
                field=field_path,
                op=op,
                resolved_type=type(resolved_value).__name__,
                ref_type=type(ref_value).__name__,
            )
            return False

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    async def _load_active_rules(self) -> list[dict[str, Any]]:
        """Load active rules from Redis cache, falling back to the database.

        Each rule is stored as a serialised JSON dict containing only the
        fields required for evaluation, keeping the cache payload small.
        """
        try:
            cached = await self.redis.get(_CACHE_KEY)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            logger.warning("policy_cache_read_failed", exc_info=True)

        # Cache miss or error -- query DB.
        result = await self.session.execute(
            select(PolicyRule).where(PolicyRule.enabled.is_(True)).order_by(PolicyRule.priority.asc())
        )
        rules = list(result.scalars().all())

        serialised: list[dict[str, Any]] = [
            {
                "rule_id": str(r.rule_id),
                "name": r.name,
                "description": r.description,
                "rule_type": r.rule_type.value,
                "condition": r.condition,
                "action": r.action.value,
                "priority": r.priority,
            }
            for r in rules
        ]

        try:
            await self.redis.set(
                _CACHE_KEY,
                json.dumps(serialised),
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.warning("policy_cache_write_failed", exc_info=True)

        return serialised

    async def _invalidate_cache(self) -> None:
        """Delete the cached active rules so the next evaluation reloads."""
        try:
            await self.redis.delete(_CACHE_KEY)
        except Exception:
            logger.warning("policy_cache_invalidation_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_rule_or_raise(self, rule_id: uuid.UUID) -> PolicyRule:
        """Fetch a rule by ID or raise ``NotFoundError``."""
        result = await self.session.execute(select(PolicyRule).where(PolicyRule.rule_id == rule_id))
        rule = result.scalar_one_or_none()
        if rule is None:
            raise NotFoundError("PolicyRule", str(rule_id))
        return rule

    @classmethod
    def _validate_condition(cls, condition: dict[str, Any]) -> None:
        """Validate the structure of a condition JSON document.

        Raises ``ValidationError`` for structural problems that would cause
        evaluation to fail at runtime.
        """
        if not isinstance(condition, dict):
            raise ValidationError("Condition must be a JSON object (dict).")

        # Boolean combinators
        if "all" in condition:
            children = condition["all"]
            if not isinstance(children, list) or len(children) == 0:
                raise ValidationError("'all' combinator requires a non-empty list.")
            for child in children:
                cls._validate_condition(child)
            return

        if "any" in condition:
            children = condition["any"]
            if not isinstance(children, list) or len(children) == 0:
                raise ValidationError("'any' combinator requires a non-empty list.")
            for child in children:
                cls._validate_condition(child)
            return

        if "not" in condition:
            child = condition["not"]
            if not isinstance(child, dict):
                raise ValidationError("'not' combinator requires a condition object.")
            cls._validate_condition(child)
            return

        # Leaf condition
        if "field" not in condition or "op" not in condition:
            raise ValidationError(f"Leaf condition must include 'field' and 'op' keys. Got: {sorted(condition.keys())}")

        op = condition["op"]
        if op not in _SUPPORTED_OPS:
            raise ValidationError(f"Unsupported operator '{op}'. Supported: {sorted(_SUPPORTED_OPS)}")

        # ``exists`` does not require a ``value`` key.
        if op != "exists" and "value" not in condition:
            raise ValidationError(f"Operator '{op}' requires a 'value' key in the condition.")
