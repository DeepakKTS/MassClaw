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
    assessment = service._injection_detector.analyze(body.content)
    return InjectionAnalysisResponse(
        detected=assessment.detected,
        confidence=assessment.confidence,
        injection_type=assessment.injection_type,
        patterns_matched=assessment.patterns_matched,
        explanation=assessment.explanation,
    )
