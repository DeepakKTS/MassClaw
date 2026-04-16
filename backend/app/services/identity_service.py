"""IdentityService — builds and caches AgentFacts v1 documents.

Produces two flavours:

- **Instance AgentFacts**: describes this MassClaw node. Published at
  ``/.well-known/agent-facts.json``; signed with the instance key via an
  optional ``AgentFactsIntegrityCredential`` so stock agents can verify
  authenticity without fetching an external DID Document.
- **Agent AgentFacts**: describes a specific registered agent. Each agent
  has its own keypair (generated lazily on first AgentFacts request); the
  private seed is ChaCha20-Poly1305-wrapped with the instance KEK and stored
  in the agent's ``metadata`` JSONB column.

Docs are cached in Redis for ``identity_facts_cache_ttl_seconds`` so discovery
requests do not pay the signing cost on every call.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.identity.agent_facts import (
    AgentFacts,
    AgentFactsBuilder,
    AgentFactsExtensions,
    Skill,
)
from app.identity.did import build_did_key, parse_did
from app.identity.did_resolver import DIDResolver, ResolvedAgent
from app.identity.key_store import KeyStore, KeyStoreError
from app.identity.nanda_index import NandaIndexClient, NandaIndexConfig
from app.identity.signer import KeyPair, encode_multibase
from app.identity.well_known_fetcher import HttpxWellKnownFetcher
from app.models.agent import Agent

logger = get_logger(__name__)

_INSTANCE_FACTS_CACHE_KEY = "identity:instance_facts"
_AGENT_FACTS_CACHE_KEY_FMT = "identity:agent_facts:{agent_id}"
_AGENT_KEY_METADATA_FIELD = "agent_facts_key"


class IdentityServiceError(Exception):
    """Raised for identity service failures (missing config, encryption errors)."""


@lru_cache(maxsize=1)
def get_instance_key_store() -> KeyStore:
    """Return the shared process-wide :class:`KeyStore` for the instance."""
    settings = get_settings()
    kek_bytes: bytes | None = None
    if settings.identity_key_encryption_key:
        try:
            kek_bytes = bytes.fromhex(settings.identity_key_encryption_key)
        except ValueError as exc:
            raise IdentityServiceError("IDENTITY_KEY_ENCRYPTION_KEY must be a hex-encoded 32-byte key") from exc
        if len(kek_bytes) != 32:
            raise IdentityServiceError(f"IDENTITY_KEY_ENCRYPTION_KEY must decode to 32 bytes (got {len(kek_bytes)})")
    return KeyStore(
        instance_key_path=settings.identity_instance_key_path,
        key_encryption_key=kek_bytes,
    )


class IdentityService:
    """Build, sign, and cache AgentFacts v1 documents for the instance and per-agent."""

    def __init__(
        self,
        session: AsyncSession,
        redis: aioredis.Redis,
        *,
        key_store: KeyStore | None = None,
    ) -> None:
        self.session = session
        self.redis = redis
        self._key_store = key_store or get_instance_key_store()
        self._settings = get_settings()

    # ------------------------------------------------------------------ Instance

    async def get_instance_facts(self) -> AgentFacts:
        cached = await self._read_cache(_INSTANCE_FACTS_CACHE_KEY)
        if cached is not None:
            return cached
        facts = await self._build_instance_facts()
        await self._write_cache(_INSTANCE_FACTS_CACHE_KEY, facts)
        return facts

    async def invalidate_instance_facts(self) -> None:
        await self.redis.delete(_INSTANCE_FACTS_CACHE_KEY)

    # ------------------------------------------------------------------ Agents

    async def get_agent_facts(self, agent_id: uuid.UUID | str) -> AgentFacts:
        agent_id_str = str(agent_id)
        cache_key = _AGENT_FACTS_CACHE_KEY_FMT.format(agent_id=agent_id_str)
        cached = await self._read_cache(cache_key)
        if cached is not None:
            return cached
        agent = await self._load_agent(agent_id_str)
        facts = await self._build_agent_facts(agent)
        await self._write_cache(cache_key, facts)
        return facts

    async def invalidate_agent_facts(self, agent_id: uuid.UUID | str) -> None:
        await self.redis.delete(_AGENT_FACTS_CACHE_KEY_FMT.format(agent_id=str(agent_id)))

    async def ensure_agent_keypair(self, agent: Agent) -> KeyPair:
        """Return the agent's keypair, generating + persisting it if missing."""
        meta_entry = (agent.metadata_ or {}).get(_AGENT_KEY_METADATA_FIELD)
        if meta_entry and "wrapped_private_seed_hex" in meta_entry:
            wrapped = bytes.fromhex(meta_entry["wrapped_private_seed_hex"])
            seed = self._key_store.unwrap_agent_seed(wrapped)
            from app.identity.signer import load_private_key

            sk = load_private_key(seed)
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            return KeyPair(private_seed=seed, public_bytes=pub)

        kp = self._key_store.generate_agent_keypair(str(agent.agent_id)).keypair
        try:
            wrapped = self._key_store.wrap_agent_seed(kp.private_seed)
        except KeyStoreError as exc:
            raise IdentityServiceError("cannot persist agent key — IDENTITY_KEY_ENCRYPTION_KEY must be set") from exc

        metadata = dict(agent.metadata_ or {})
        metadata[_AGENT_KEY_METADATA_FIELD] = {
            "wrapped_private_seed_hex": wrapped.hex(),
            "public_key_multibase": encode_multibase(kp.public_bytes),
            "did": build_did_key(kp.public_bytes),
            "key_created_at": _utc_now_iso(),
        }
        agent.metadata_ = metadata
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(agent, "metadata_")
        await self.session.flush()
        return kp

    # ------------------------------------------------------------------ Resolution

    async def resolve_did(
        self,
        did: str,
        *,
        username_hint: str | None = None,
        well_known_url_hint: str | None = None,
    ) -> ResolvedAgent:
        """Resolve a DID to a verified AgentFacts document."""
        cache_key = _resolve_cache_key(did)
        cached = await self._read_cache(cache_key)
        if cached is not None:
            return ResolvedAgent(did=did, agent_facts=cached, source="cache")

        resolver = self._build_resolver()
        resolved = await resolver.resolve(
            did,
            username_hint=username_hint,
            well_known_url_hint=well_known_url_hint,
        )
        await self._write_cache(
            cache_key,
            resolved.agent_facts,
            ttl=self._settings.nanda_index_resolve_cache_ttl_seconds,
        )
        return resolved

    async def invalidate_resolution(self, did: str) -> None:
        await self.redis.delete(_resolve_cache_key(did))

    def _build_resolver(self) -> DIDResolver:
        local = _LocalRegistryAdapter(self)
        index: NandaIndexClient | None = None
        if self._settings.nanda_index_enabled and self._settings.nanda_index_base_url:
            index = NandaIndexClient(
                NandaIndexConfig(
                    base_url=self._settings.nanda_index_base_url,
                    api_token=self._settings.nanda_index_api_token,
                    session_cookie=self._settings.nanda_index_session_cookie,
                    retry_attempts=self._settings.nanda_index_retry_attempts,
                )
            )
        well_known = HttpxWellKnownFetcher()
        return DIDResolver(
            local_registry=local,
            nanda_index=index,
            well_known_fetcher=well_known,
        )

    async def _lookup_agent_facts_by_did(self, did: str) -> AgentFacts | None:
        """Return a local agent's AgentFacts if its DID matches, else None."""
        parse_did(did)
        from sqlalchemy import select

        result = await self.session.execute(
            select(Agent).where(Agent.metadata_[_AGENT_KEY_METADATA_FIELD]["did"].as_string() == did)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return await self._build_agent_facts(agent)

    # ------------------------------------------------------------------ NANDA registration

    async def register_instance_with_nanda_index(self) -> dict[str, Any]:
        """Publish our AgentFacts pointer to the NANDA Index.

        The Index stores only the ``agent_facts_link`` pointer + ``username``.
        The AgentFacts document itself must already be live at the URL we
        advertise (``identity_public_base_url/.well-known/agent-facts.json``).
        """
        if not self._settings.nanda_index_enabled:
            raise IdentityServiceError("NANDA Index is disabled (set nanda_index_enabled=true to register)")
        if not self._settings.nanda_index_username:
            raise IdentityServiceError(
                "nanda_index_username must be set before registering — this is the handle "
                "you reserve on the NANDA Index"
            )
        client = NandaIndexClient(
            NandaIndexConfig(
                base_url=self._settings.nanda_index_base_url,
                api_token=self._settings.nanda_index_api_token,
                session_cookie=self._settings.nanda_index_session_cookie,
                retry_attempts=self._settings.nanda_index_retry_attempts,
            )
        )
        base = self._settings.identity_public_base_url.rstrip("/")
        facts_url = f"{base}/.well-known/agent-facts.json"
        result = await client.register(
            username=self._settings.nanda_index_username,
            agent_facts_link=facts_url,
        )
        logger.info(
            "nanda_index_instance_registered",
            username=result.username,
            mongo_id=result.mongo_id,
            facts_url=result.agent_facts_link,
        )
        return result.raw_response

    # ------------------------------------------------------------------ Verification

    @staticmethod
    def verify_document(raw: dict[str, Any]) -> dict[str, Any]:
        """Verify a posted AgentFacts document; returns a structured report."""
        report: dict[str, Any] = {"valid": False, "errors": []}
        try:
            doc = AgentFacts.model_validate(raw)
        except Exception as exc:
            report["errors"].append(f"document failed schema validation: {exc}")
            return report

        if not doc.verifiable_credentials:
            report["errors"].append("document has no integrity credential in verifiable_credentials[]")
            return report

        any_valid = False
        for cred in doc.verifiable_credentials:
            try:
                ok = doc.verify_integrity(cred)
            except Exception as exc:
                report["errors"].append(f"credential verification raised: {exc}")
                continue
            if ok:
                any_valid = True
                break
            report["errors"].append("integrity credential signature did not verify")

        report["valid"] = any_valid
        report["provider_did"] = doc.provider.did
        report["agent_id"] = doc.id
        report["label"] = doc.label
        return report

    # ------------------------------------------------------------------ Internals

    async def _load_agent(self, agent_id_str: str) -> Agent:
        from sqlalchemy import select

        try:
            agent_uuid = uuid.UUID(agent_id_str)
        except ValueError as exc:
            raise NotFoundError(f"Agent {agent_id_str!r} not found") from exc

        result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_uuid))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError(f"Agent {agent_id_str!r} not found")
        return agent

    async def _build_instance_facts(self) -> AgentFacts:
        keypair = self._key_store.instance_keypair()
        provider_did = build_did_key(keypair.public_bytes)
        base = self._settings.identity_public_base_url.rstrip("/")

        skills = _skills_from_registry()
        extensions = AgentFactsExtensions(
            example_requests=_INSTANCE_EXAMPLE_REQUESTS,
            error_schema=_INSTANCE_ERROR_SCHEMA,
            nanda_index_handle=self._settings.nanda_index_username or None,
        )

        builder = (
            AgentFactsBuilder(
                agent_id=f"urn:agent:massclaw:{_safe_name(self._settings.identity_instance_name)}",
                label=self._settings.identity_instance_name,
                description=self._settings.identity_instance_description,
                version=self._settings.app_version,
                provider_name=self._settings.identity_instance_name,
                provider_url=base,
            )
            .provider_did(provider_did)
            .endpoints(
                [
                    base,
                    f"{base}/api/v1",
                    f"{base}/api/v1/mcp",
                    f"{base}/.well-known/agent-facts.json",
                ]
            )
            .modalities("text", "structured-output", "tool-use", "workflow")
            .auth_methods("bearer", "api-key", "agent-facts-signature")
            .skills(skills)
            .documentation_url(f"{base}/docs")
            .certification(self._settings.identity_trust_zone)
            .extensions(extensions)
        )
        return builder.build_and_sign(keypair)

    async def _build_agent_facts(self, agent: Agent) -> AgentFacts:
        keypair = await self.ensure_agent_keypair(agent)
        did = build_did_key(keypair.public_bytes)
        base = self._settings.identity_public_base_url.rstrip("/")

        supported = agent.supported_tools or []
        skills: list[Skill] = []
        for entry in supported:
            if isinstance(entry, dict) and "name" in entry:
                skills.append(
                    Skill(
                        id=str(entry["name"]),
                        description=str(entry.get("description", "")),
                        **{
                            "inputModes": list(entry.get("input_modes", ["text"])),
                            "outputModes": list(entry.get("output_modes", ["text"])),
                        },
                    )
                )
            elif isinstance(entry, str):
                skills.append(
                    Skill(
                        id=entry,
                        description=f"{entry} tool",
                        **{"inputModes": ["text"], "outputModes": ["text"]},
                    )
                )

        modalities = _modalities_from_capabilities(agent.capabilities or [])
        limits = _limits_payload_from_cost_profile(agent.cost_profile or {})
        trust_zone = _trust_zone_for_agent(agent)

        extensions = AgentFactsExtensions(
            limits=limits if limits else None,
            example_requests=[],
        )

        builder = (
            AgentFactsBuilder(
                agent_id=f"urn:agent:massclaw:{agent.agent_id}",
                label=agent.name,
                description=agent.description,
                version=agent.version or "1.0.0",
                provider_name=self._settings.identity_instance_name,
                provider_url=base,
            )
            .provider_did(did)
            .endpoint(f"{base}/api/v1/agents/{agent.agent_id}")
            .modalities(*modalities)
            .auth_methods("bearer", "api-key")
            .skills(skills)
            .certification(trust_zone)
            .extensions(extensions)
        )
        return builder.build_and_sign(keypair)

    async def _read_cache(self, key: str) -> AgentFacts | None:
        try:
            raw = await self.redis.get(key)
        except Exception as exc:
            logger.warning("identity_cache_read_failed", key=key, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return AgentFacts.model_validate(json.loads(raw))
        except Exception as exc:
            logger.warning("identity_cache_decode_failed", key=key, error=str(exc))
            return None

    async def _write_cache(self, key: str, facts: AgentFacts, *, ttl: int | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._settings.identity_facts_cache_ttl_seconds
        payload = json.dumps(facts.to_document(), separators=(",", ":"))
        try:
            await self.redis.set(key, payload, ex=effective_ttl)
        except Exception as exc:
            logger.warning("identity_cache_write_failed", key=key, error=str(exc))


class _LocalRegistryAdapter:
    """Adapts :class:`IdentityService` to the :class:`LocalRegistry` protocol."""

    def __init__(self, service: IdentityService) -> None:
        self._service = service

    async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None:
        return await self._service._lookup_agent_facts_by_did(did)


def _resolve_cache_key(did: str) -> str:
    return f"identity:resolve:{did}"


# ------------------------------------------------------------------ helpers

_INSTANCE_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error", "message", "next_steps"],
    "properties": {
        "error": {"type": "string", "description": "Short machine-readable error code."},
        "message": {"type": "string", "description": "Human-readable explanation."},
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Actionable suggestions for recovering from this error.",
        },
        "correlation_id": {"type": "string"},
    },
}


_INSTANCE_EXAMPLE_REQUESTS: list[dict[str, Any]] = [
    {
        "name": "submit_workflow",
        "method": "POST",
        "path": "/api/v1/workflows/submit",
        "body": {
            "task": "Plan a dinner for 6 with vegetarian options.",
            "domain": "general",
            "budget_usd": 1.0,
        },
    },
    {"name": "poll_status", "method": "GET", "path": "/api/v1/workflows/{workflow_id}/status"},
    {"name": "read_result", "method": "GET", "path": "/api/v1/workflows/{workflow_id}/result"},
    {
        "name": "query_memory",
        "method": "POST",
        "path": "/api/v1/memory/query",
        "body": {"query": "what is the deadline", "k": 10},
    },
    {
        "name": "resolve_did",
        "method": "GET",
        "path": "/api/v1/identity/resolve?did=did:key:z...",
    },
]


def _skills_from_registry() -> list[Skill]:
    """Return the platform skill catalogue as AgentFacts v1 ``Skill`` objects."""
    platform_skills = [
        Skill(
            id="workflow.submit",
            description="Submit a multi-agent workflow with optional budget and constraints.",
            **{"inputModes": ["text", "structured-output"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="workflow.status",
            description="Read current status and progress of a running workflow.",
            **{"inputModes": ["text"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="workflow.result",
            description="Fetch the final result of a completed workflow.",
            **{"inputModes": ["text"], "outputModes": ["structured-output", "text"]},
        ),
        Skill(
            id="memory.query",
            description="Semantic + vector search over shared memory with provenance chains.",
            **{"inputModes": ["text"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="memory.write",
            description="Write a signed memory record into the shared CRDT store.",
            **{"inputModes": ["structured-output"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="agents.search",
            description="Discover agents by capability, trust score, and cost.",
            **{"inputModes": ["text"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="identity.resolve",
            description="Resolve a DID to a verified AgentFacts document.",
            **{"inputModes": ["text"], "outputModes": ["structured-output"]},
        ),
        Skill(
            id="policy.evaluate",
            description="Evaluate whether a proposed action is allowed by current policy rules.",
            **{"inputModes": ["structured-output"], "outputModes": ["structured-output"]},
        ),
    ]
    try:
        from app.tools.registry import get_tool_registry

        registry = get_tool_registry()
    except Exception as exc:
        logger.warning("tool_registry_unavailable_for_agent_facts", error=str(exc))
        return platform_skills

    seen = {s.id for s in platform_skills}
    for tool in registry.list_tools():
        name = getattr(tool, "name", None) or getattr(tool, "tool_name", None)
        if not name or name in seen:
            continue
        description = getattr(tool, "description", "") or f"{name} tool"
        platform_skills.append(
            Skill(
                id=str(name),
                description=str(description),
                **{"inputModes": ["text"], "outputModes": ["text"]},
            )
        )
        seen.add(name)
    return platform_skills


def _modalities_from_capabilities(capabilities: list[str]) -> list[str]:
    modality_map = {
        "code_execution": "code",
        "web_search": "text",
        "analysis": "structured-output",
        "research": "text",
        "test": "text",
        "image": "image",
        "audio": "audio",
        "video": "video",
    }
    out: list[str] = []
    for cap in capabilities:
        key = cap.lower().strip()
        mod = modality_map.get(key, "text")
        if mod not in out:
            out.append(mod)
    if not out:
        out.append("text")
    return out


def _limits_payload_from_cost_profile(cost_profile: dict[str, Any]) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    for key in (
        "max_transaction_usd",
        "rate_limit_per_min",
        "cost_cap_per_hour_usd",
        "max_concurrent_workflows",
    ):
        value = cost_profile.get(key)
        if isinstance(value, (int, float)):
            limits[key] = value
    return limits


def _trust_zone_for_agent(agent: Agent) -> str:
    override = (agent.metadata_ or {}).get("trust_zone")
    if isinstance(override, str) and override:
        return override
    if agent.safety_level >= 7:
        return "verified-enterprise"
    if agent.safety_level >= 4:
        return "verified-community"
    return "unverified"


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-") or "instance"
