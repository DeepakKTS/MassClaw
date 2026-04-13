from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.core.security import TokenPayload, decode_token, verify_api_key
from app.exceptions import AuthenticationError, AuthorizationError
from app.services.agent_service import AgentService
from app.services.audit_service import AuditService
from app.services.evolution_service import EvolutionService
from app.services.memory_service import MemoryService
from app.services.policy_service import PolicyService
from app.services.trust_service import TrustService
from app.services.wallet_service import WalletService
from app.services.workflow_service import WorkflowService

# Optional bearer token — doesn't reject if missing (endpoints choose to require it)
_optional_bearer = HTTPBearer(auto_error=False)


# --- Auth dependencies ---


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer)
    ] = None,
) -> TokenPayload:
    """Extract and validate JWT from Authorization: Bearer header.

    Raises AuthenticationError if token is missing or invalid.
    """
    if credentials is None:
        raise AuthenticationError("Authorization header required")
    return decode_token(credentials.credentials)


async def get_optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer)
    ] = None,
) -> TokenPayload | None:
    """Extract JWT if present, return None if absent. No error on missing token."""
    if credentials is None:
        return None
    try:
        return decode_token(credentials.credentials)
    except Exception:
        return None


def require_scopes(*required_scopes: str) -> Callable:
    """Return a dependency that checks the current token has all required scopes.

    Usage:
        @router.post("/admin", dependencies=[Depends(require_scopes("admin", "write"))])
    """

    async def _check_scopes(
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        missing = set(required_scopes) - set(user.scopes)
        if missing:
            raise AuthorizationError(
                f"Missing required scopes: {', '.join(sorted(missing))}"
            )
        return user

    return _check_scopes


async def get_api_key_agent(
    x_api_key: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> str | None:
    """Validate agent API key from X-API-Key header.

    Returns the agent_id if valid, None if no key provided.
    Raises AuthenticationError if key is present but invalid.
    """
    if x_api_key is None:
        return None

    from sqlalchemy import select
    from app.models.agent import Agent

    # API keys would be stored hashed in agent.metadata_.api_key_hash
    # For now, look up by the key prefix pattern
    result = await session.execute(select(Agent))
    agents = result.scalars().all()

    for agent in agents:
        stored_hash = (agent.metadata_ or {}).get("api_key_hash")
        if stored_hash and verify_api_key(x_api_key, stored_hash):
            return str(agent.agent_id)

    raise AuthenticationError("Invalid API key")


# --- Service dependencies ---


async def get_agent_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> AgentService:
    return AgentService(session=session, redis=redis)


async def get_memory_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> MemoryService:
    return MemoryService(session=session, redis=redis)


async def get_trust_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> TrustService:
    return TrustService(session=session, redis=redis)


async def get_wallet_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> WalletService:
    return WalletService(session=session, redis=redis)


async def get_workflow_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> WorkflowService:
    return WorkflowService(session=session, redis=redis)


async def get_audit_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuditService:
    return AuditService(session=session, redis=redis)


async def get_policy_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> PolicyService:
    return PolicyService(session=session, redis=redis)


async def get_evolution_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> EvolutionService:
    return EvolutionService(session=session, redis=redis)


from app.services.project_service import ProjectService
from app.services.task_service import TaskService
from app.services.task_test_service import TaskTestService


async def get_task_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> TaskService:
    return TaskService(session=session, redis=redis)


async def get_task_test_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> TaskTestService:
    return TaskTestService(session=session, redis=redis)


async def get_project_service(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> ProjectService:
    return ProjectService(session=session, redis=redis)
