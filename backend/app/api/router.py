from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

from app.api.agents import router as agents_router
from app.api.memory import router as memory_router
from app.api.tasks import router as tasks_router
from app.api.trust import router as trust_router
from app.api.wallet import router as wallet_router
from app.api.workflows import router as workflows_router

api_router.include_router(agents_router, prefix="/agents", tags=["Agents"])
api_router.include_router(memory_router, prefix="/memory", tags=["Memory"])
api_router.include_router(tasks_router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(trust_router, prefix="/trust", tags=["Trust"])
api_router.include_router(wallet_router, prefix="/wallet", tags=["Wallet"])
api_router.include_router(workflows_router, prefix="/workflows", tags=["Workflows"])

from app.api.audit import router as audit_router
from app.api.policy import router as policy_router

api_router.include_router(audit_router, prefix="/audit", tags=["Audit"])
api_router.include_router(policy_router, prefix="/policy", tags=["Policy"])

from app.api.evolution import router as evolution_router

api_router.include_router(evolution_router, prefix="/evolution", tags=["Evolution"])

from app.api.projects import router as projects_router

api_router.include_router(projects_router, prefix="/projects", tags=["Projects"])


@api_router.get("/", tags=["System"])
async def api_root() -> dict[str, str]:
    return {"message": "MassClaw API v1", "docs": "/docs"}
