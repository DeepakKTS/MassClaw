from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.dependencies import get_policy_service
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.policy import (
    PolicyDecisionResponse,
    PolicyEvaluationRequest,
    PolicyRuleCreate,
    PolicyRuleResponse,
    PolicyRuleUpdate,
)
from app.services.policy_service import PolicyService

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models specific to API layer
# ---------------------------------------------------------------------------


class ContentAnalyzeRequest(BaseModel):
    """Request body for content safety analysis."""

    content: str = Field(..., min_length=1, max_length=50000, description="Content to analyze")


class ContentAnalysisResponse(BaseModel):
    """Response from content safety analysis."""

    safe: bool
    categories: dict[str, float] = Field(default_factory=dict)
    flagged_categories: list[str] = Field(default_factory=list)
    severity: str
    explanation: str


class InjectionAnalyzeRequest(BaseModel):
    """Request body for injection detection analysis."""

    content: str = Field(..., min_length=1, max_length=50000, description="Content to analyze for injection")


class InjectionAnalysisResponse(BaseModel):
    """Response from injection detection analysis."""

    detected: bool
    confidence: float
    injection_type: str | None
    patterns_matched: list[str] = Field(default_factory=list)
    explanation: str


class ContentSafetyResponse(BaseModel):
    """Combined content safety result."""

    safe: bool
    content_analysis: ContentAnalysisResponse | None = None
    injection_assessment: InjectionAnalysisResponse | None = None
    recommendation: str


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------


@router.post(
    "/evaluate",
    response_model=PolicyDecisionResponse,
    summary="Ad-hoc policy evaluation",
)
async def evaluate_policy(
    body: PolicyEvaluationRequest,
    service: PolicyService = Depends(get_policy_service),
) -> PolicyDecisionResponse:
    """Evaluate an action and context against the active policy rule set."""
    decision = await service.evaluate_action(body.action, body.context)
    return PolicyDecisionResponse(
        approved=decision.approved,
        action=decision.action,
        matched_rule=decision.matched_rule,
        reason=decision.reason,
        violations=decision.violations,
    )


# ---------------------------------------------------------------------------
# Policy rules CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/rules",
    response_model=PaginatedResponse[PolicyRuleResponse],
    summary="List policy rules",
)
async def list_rules(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: PolicyService = Depends(get_policy_service),
) -> PaginatedResponse[PolicyRuleResponse]:
    """List all policy rules, ordered by priority."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.list_rules(pagination)


@router.post(
    "/rules",
    response_model=PolicyRuleResponse,
    status_code=201,
    summary="Create policy rule",
)
async def create_rule(
    body: PolicyRuleCreate,
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRuleResponse:
    """Create a new policy rule."""
    return await service.create_rule(body)


@router.patch(
    "/rules/{rule_id}",
    response_model=PolicyRuleResponse,
    summary="Update policy rule",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: PolicyRuleUpdate,
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRuleResponse:
    """Partially update an existing policy rule."""
    return await service.update_rule(str(rule_id), body)


@router.delete(
    "/rules/{rule_id}",
    status_code=204,
    response_model=None,
    summary="Delete policy rule",
)
async def delete_rule(
    rule_id: uuid.UUID,
    service: PolicyService = Depends(get_policy_service),
) -> None:
    """Permanently delete a policy rule."""
    await service.delete_rule(str(rule_id))


@router.post(
    "/rules/{rule_id}/toggle",
    response_model=PolicyRuleResponse,
    summary="Toggle policy rule",
)
async def toggle_rule(
    rule_id: uuid.UUID,
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRuleResponse:
    """Toggle the enabled/disabled state of a policy rule."""
    return await service.toggle_rule(str(rule_id))


# ---------------------------------------------------------------------------
# Content safety analysis
# ---------------------------------------------------------------------------


@router.post(
    "/content/analyze",
    response_model=ContentSafetyResponse,
    summary="Content safety analysis",
)
async def analyze_content(
    body: ContentAnalyzeRequest,
    service: PolicyService = Depends(get_policy_service),
) -> ContentSafetyResponse:
    """Run combined content safety and injection analysis on the provided text."""
    result = service.evaluate_content(body.content)

    content_resp: ContentAnalysisResponse | None = None
    if result.content_analysis is not None:
        content_resp = ContentAnalysisResponse(
            safe=result.content_analysis.safe,
            categories=result.content_analysis.categories,
            flagged_categories=result.content_analysis.flagged_categories,
            severity=result.content_analysis.severity,
            explanation=result.content_analysis.explanation,
        )

    injection_resp: InjectionAnalysisResponse | None = None
    if result.injection_assessment is not None:
        injection_resp = InjectionAnalysisResponse(
            detected=result.injection_assessment.detected,
            confidence=result.injection_assessment.confidence,
            injection_type=result.injection_assessment.injection_type,
            patterns_matched=result.injection_assessment.patterns_matched,
            explanation=result.injection_assessment.explanation,
        )

    return ContentSafetyResponse(
        safe=result.safe,
        content_analysis=content_resp,
        injection_assessment=injection_resp,
        recommendation=result.recommendation,
    )


# ---------------------------------------------------------------------------
# Injection detection analysis
# ---------------------------------------------------------------------------


@router.post(
    "/injection/analyze",
    response_model=InjectionAnalysisResponse,
    summary="Injection detection analysis",
)
async def analyze_injection(
    body: InjectionAnalyzeRequest,
    service: PolicyService = Depends(get_policy_service),
) -> InjectionAnalysisResponse:
    """Run injection detection analysis on the provided text."""
    assessment = service.analyze_injection(body.content)
    return InjectionAnalysisResponse(
        detected=assessment.detected,
        confidence=assessment.confidence,
        injection_type=assessment.injection_type,
        patterns_matched=assessment.patterns_matched,
        explanation=assessment.explanation,
    )


# ---------------------------------------------------------------------------
# Registry API (new @policy_rule-based engine; Day-15 onwards)
#
# These endpoints talk to :mod:`app.safety.registry`, not the legacy
# JSON-rule service. Both coexist until Day-17's frontend swap retires
# the JSON path for good.
# ---------------------------------------------------------------------------


class RegistryRuleResponse(BaseModel):
    rule_id: str
    description: str
    priority: int
    enabled: bool
    tags: list[str] = Field(default_factory=list)


class RegistryToggleResponse(BaseModel):
    rule_id: str
    enabled: bool


class RegistryDecisionResponse(BaseModel):
    action: str
    rule_id: str | None = None
    reason: str
    metadata: dict = Field(default_factory=dict)
    contributing_rule_ids: list[str] = Field(default_factory=list)


class RegistryEvaluateRequest(BaseModel):
    """Minimal PolicyContext payload for the dry-run API.

    Only fields that round-trip over JSON are accepted — live
    session / redis handles are omitted.
    """

    agent_did: str | None = None
    agent_trust_score: float | None = None
    agent_capabilities: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    action: str = ""
    action_category: str | None = None
    tool_name: str | None = None
    amount: float | None = None
    target: str | None = None
    estimated_cost: float | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    extra: dict = Field(default_factory=dict)
    rule_ids: list[str] | None = Field(
        default=None,
        description="If set, only evaluate these specific rule IDs.",
    )


@router.get(
    "/registry/rules",
    response_model=list[RegistryRuleResponse],
    summary="List registered @policy_rule entries",
)
async def list_registry_rules() -> list[RegistryRuleResponse]:
    from app.safety.registry import PolicyRegistry

    rules = PolicyRegistry.all(include_disabled=True)
    return [
        RegistryRuleResponse(
            rule_id=r.rule_id,
            description=r.description,
            priority=r.priority,
            enabled=r.enabled,
            tags=list(r.tags),
        )
        for r in rules
    ]


@router.get(
    "/registry/rules/{rule_id}",
    response_model=RegistryRuleResponse,
    summary="Fetch a single registered rule",
)
async def get_registry_rule(rule_id: str) -> RegistryRuleResponse:
    from fastapi import HTTPException

    from app.safety.registry import PolicyRegistry

    entry = PolicyRegistry.get(rule_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not registered")
    return RegistryRuleResponse(
        rule_id=entry.rule_id,
        description=entry.description,
        priority=entry.priority,
        enabled=entry.enabled,
        tags=list(entry.tags),
    )


@router.post(
    "/registry/rules/{rule_id}/enable",
    response_model=RegistryToggleResponse,
    summary="Enable a registered rule",
)
async def enable_registry_rule(rule_id: str) -> RegistryToggleResponse:
    from fastapi import HTTPException

    from app.safety.registry import PolicyRegistry

    if not PolicyRegistry.set_enabled(rule_id, True):
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not registered")
    return RegistryToggleResponse(rule_id=rule_id, enabled=True)


@router.post(
    "/registry/rules/{rule_id}/disable",
    response_model=RegistryToggleResponse,
    summary="Disable a registered rule",
)
async def disable_registry_rule(rule_id: str) -> RegistryToggleResponse:
    from fastapi import HTTPException

    from app.safety.registry import PolicyRegistry

    if not PolicyRegistry.set_enabled(rule_id, False):
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not registered")
    return RegistryToggleResponse(rule_id=rule_id, enabled=False)


@router.post(
    "/registry/evaluate",
    response_model=RegistryDecisionResponse,
    summary="Dry-run the registry against a hand-built PolicyContext",
)
async def evaluate_registry(body: RegistryEvaluateRequest) -> RegistryDecisionResponse:
    """Build a :class:`PolicyContext` from the payload and run the engine.

    This is the endpoint the frontend's live tester calls: paste a
    JSON body, see the aggregated decision and which rules
    contributed, without executing anything.
    """

    from app.safety.context import PolicyContext
    from app.safety.registry import evaluate as policy_evaluate

    ctx = PolicyContext(
        agent_did=body.agent_did,
        agent_trust_score=body.agent_trust_score,
        agent_capabilities=tuple(body.agent_capabilities),
        agent_id=uuid.UUID(body.agent_id) if body.agent_id else None,
        action=body.action,
        action_category=body.action_category,
        tool_name=body.tool_name,
        amount=body.amount,
        target=body.target,
        estimated_cost=body.estimated_cost,
        workflow_id=uuid.UUID(body.workflow_id) if body.workflow_id else None,
        task_id=uuid.UUID(body.task_id) if body.task_id else None,
        extra=dict(body.extra),
    )
    decision = await policy_evaluate(ctx, rule_ids=body.rule_ids)
    return RegistryDecisionResponse(
        action=decision.action.value,
        rule_id=decision.rule_id,
        reason=decision.reason,
        metadata=dict(decision.metadata),
        contributing_rule_ids=list(decision.contributing_rule_ids),
    )
