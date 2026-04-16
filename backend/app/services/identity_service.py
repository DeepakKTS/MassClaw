"""IdentityService — builds and caches AgentFacts documents.

Two flavours of AgentFacts live in MassClaw:

- **Instance AgentFacts**: one per MassClaw node, signed by the node's instance
  key. Published at ``/.well-known/agent-facts.json`` so a stock OpenClaw agent
  can discover the platform, the tool catalogue, and the endpoints it should
  call.

- **Agent AgentFacts**: one per registered agent in the ``agents`` table. Each
  agent gets its own keypair on first access; the private seed is wrapped with
  the instance KEK and stored inside the agent's ``metadata`` JSONB column.

The service caches the signed documents in Redis for
``identity_facts_cache_ttl_seconds`` (default 60s) so discovery requests do not
pay the signing cost on every call.
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
    AgentLimits,
    ToolDescriptor,
)
from app.identity.did import build_did_from_public_key, parse_did
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
    """Build, sign, and cache AgentFacts documents for the instance and per-agent."""

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
        """Return the signed AgentFacts document describing this MassClaw node.

        Cached in Redis for ``identity_facts_cache_ttl_seconds``; regenerated
        automatically when the cached copy is missing or malformed.
        """
        cached = await self._read_cache(_INSTANCE_FACTS_CACHE_KEY)
        if cached is not None:
            return cached

        facts = await self._build_instance_facts()
        await self._write_cache(_INSTANCE_FACTS_CACHE_KEY, facts)
        return facts

    async def invalidate_instance_facts(self) -> None:
        """Drop the cached instance AgentFacts document — call after tool changes."""
        await self.redis.delete(_INSTANCE_FACTS_CACHE_KEY)

    # ------------------------------------------------------------------ Agents

    async def get_agent_facts(self, agent_id: uuid.UUID | str) -> AgentFacts:
        """Return the signed AgentFacts document for a specific agent."""
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
        """Return the agent's keypair, generating + persisting it if missing.

        The private seed is stored inside ``agent.metadata_[agent_facts_key]``
        encrypted with the instance KEK. The public half plus the DID are stored
        in cleartext alongside so the frontend can render them without
        unwrapping.
        """
        meta_entry = (agent.metadata_ or {}).get(_AGENT_KEY_METADATA_FIELD)
        if meta_entry and "wrapped_private_seed_hex" in meta_entry:
            wrapped = bytes.fromhex(meta_entry["wrapped_private_seed_hex"])
            seed = self._key_store.unwrap_agent_seed(wrapped)
            # Derive public bytes rather than trusting the stored value.
            from app.identity.signer import load_private_key

            sk = load_private_key(seed)
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            return KeyPair(private_seed=seed, public_bytes=pub)

        # Generate a fresh keypair.
        kp = self._key_store.generate_agent_keypair(str(agent.agent_id)).keypair
        try:
            wrapped = self._key_store.wrap_agent_seed(kp.private_seed)
        except KeyStoreError as exc:
            raise IdentityServiceError("cannot persist agent key — IDENTITY_KEY_ENCRYPTION_KEY must be set") from exc

        metadata = dict(agent.metadata_ or {})
        metadata[_AGENT_KEY_METADATA_FIELD] = {
            "wrapped_private_seed_hex": wrapped.hex(),
            "public_key_multibase": encode_multibase(kp.public_bytes),
            "did": build_did_from_public_key(kp.public_bytes),
            "key_created_at": _utc_now_iso(),
        }
        agent.metadata_ = metadata
        # SQLAlchemy needs an explicit flag for mutable JSONB in-place updates.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(agent, "metadata_")
        await self.session.flush()
        return kp

    # ------------------------------------------------------------------ Resolution

    async def resolve_did(
        self,
        did: str,
        *,
        well_known_url_hint: str | None = None,
    ) -> ResolvedAgent:
        """Resolve a DID to a verified AgentFacts document.

        Tries the local agent registry first, then the NANDA Index (when
        enabled), then a well-known URL hint. Results are cached in Redis
        for ``nanda_index_resolve_cache_ttl_seconds`` — enough to keep
        lookups fast without outliving key rotations.
        """
        cache_key = _resolve_cache_key(did)
        cached = await self._read_cache(cache_key)
        if cached is not None:
            source = "cache"
            return ResolvedAgent(did=did, agent_facts=cached, source=source)

        resolver = self._build_resolver()
        resolved = await resolver.resolve(did, well_known_url_hint=well_known_url_hint)
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
        if self._settings.nanda_index_enabled:
            index = NandaIndexClient(
                NandaIndexConfig(
                    base_url=self._settings.nanda_index_base_url,
                    api_token=self._settings.nanda_index_api_token,
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
        """Return a local agent's AgentFacts if its DID matches, else None.

        Used as the LocalRegistry strategy for :class:`DIDResolver`. Matches
        on the DID stored in ``agent.metadata_[agent_facts_key][did]``.
        """
        parse_did(did)  # Validate shape.
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
        """Publish this instance's AgentFacts to the configured NANDA Index.

        Callers are responsible for deciding when to call this (typically once
        on startup if ``nanda_index_register_on_startup`` is true). Returns the
        raw Index response for logging.
        """
        if not self._settings.nanda_index_enabled:
            raise IdentityServiceError("NANDA Index is disabled (set nanda_index_enabled=true to register)")
        client = NandaIndexClient(
            NandaIndexConfig(
                base_url=self._settings.nanda_index_base_url,
                api_token=self._settings.nanda_index_api_token,
                retry_attempts=self._settings.nanda_index_retry_attempts,
            )
        )
        facts = await self.get_instance_facts()
        base = self._settings.identity_public_base_url.rstrip("/")
        facts_url = f"{base}/.well-known/agent-facts.json"
        result = await client.register(facts, facts_url=facts_url)
        logger.info(
            "nanda_index_instance_registered",
            did=result.did,
            facts_url=result.facts_url,
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

        if doc.proof is None:
            report["errors"].append("document has no proof block")
            return report

        try:
            ok = doc.verify()
        except Exception as exc:
            report["errors"].append(f"verification raised: {exc}")
            return report

        report["valid"] = ok
        report["issuer"] = doc.issuer
        report["subject"] = doc.credential_subject.id
        report["valid_from"] = doc.valid_from
        report["valid_until"] = doc.valid_until
        if not ok:
            report["errors"].append("Ed25519 signature did not verify")
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
        did = build_did_from_public_key(keypair.public_bytes)
        now = _utc_now_iso()
        valid_until = _iso_n_days_from_now(self._settings.identity_facts_validity_days)

        tools = _load_tool_descriptors()
        endpoints = _compose_instance_endpoints(self._settings.identity_public_base_url)

        builder = (
            AgentFactsBuilder(
                subject_did=did,
                issuer_did=did,
                document_id=f"urn:massclaw:instance:{did.split(':')[-1]}",
                name=self._settings.identity_instance_name,
                description=self._settings.identity_instance_description,
            )
            .capabilities(*_INSTANCE_CAPABILITIES)
            .tools(tools)
            .endpoints(endpoints)
            .trust_zone(self._settings.identity_trust_zone)
            .public_key(keypair.public_bytes)
            .example_requests(_INSTANCE_EXAMPLE_REQUESTS)
            .error_schema(_INSTANCE_ERROR_SCHEMA)
            .validity(valid_from=now, valid_until=valid_until)
        )
        if self._settings.identity_nanda_index_handle:
            builder.nanda_index_handle(self._settings.identity_nanda_index_handle)

        return builder.build_and_sign(keypair)

    async def _build_agent_facts(self, agent: Agent) -> AgentFacts:
        keypair = await self.ensure_agent_keypair(agent)
        did = build_did_from_public_key(keypair.public_bytes)

        # Convert the agent's ``supported_tools`` + ``input_schema`` into the
        # AgentFacts tool descriptor shape. Supported_tools is usually a list
        # of tool names; we don't require per-tool schemas here.
        supported = agent.supported_tools or []
        tools: list[ToolDescriptor] = []
        for entry in supported:
            if isinstance(entry, dict) and "name" in entry:
                tools.append(
                    ToolDescriptor(
                        name=str(entry["name"]),
                        description=str(entry.get("description", "")),
                        input_schema=entry.get("input_schema"),
                        output_schema=entry.get("output_schema"),
                    )
                )
            elif isinstance(entry, str):
                tools.append(ToolDescriptor(name=entry, description=f"{entry} tool"))

        endpoints = _compose_agent_endpoints(agent, self._settings.identity_public_base_url)
        limits = _limits_from_cost_profile(agent.cost_profile or {}, agent.latency_profile or {})
        trust_zone = _trust_zone_for_agent(agent)

        now = _utc_now_iso()
        valid_until = _iso_n_days_from_now(self._settings.identity_facts_validity_days)

        builder = (
            AgentFactsBuilder(
                subject_did=did,
                issuer_did=did,
                document_id=f"urn:massclaw:agent:{agent.agent_id}",
                name=agent.name,
                description=agent.description,
            )
            .capabilities(*(agent.capabilities or []))
            .tools(tools)
            .endpoints(endpoints)
            .trust_zone(trust_zone)
            .public_key(keypair.public_bytes)
            .limits(limits)
            .validity(valid_from=now, valid_until=valid_until)
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

_INSTANCE_CAPABILITIES: list[str] = [
    "workflow.submit",
    "workflow.status",
    "workflow.result",
    "memory.query",
    "memory.write",
    "memory.by_hash",
    "agents.list",
    "agents.search",
    "agents.discover",
    "audit.search",
    "policy.evaluate",
    "tools.list",
    "tools.invoke",
    "mcp.server",
]

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
    {
        "name": "poll_status",
        "method": "GET",
        "path": "/api/v1/workflows/{workflow_id}/status",
    },
    {
        "name": "read_result",
        "method": "GET",
        "path": "/api/v1/workflows/{workflow_id}/result",
    },
    {
        "name": "query_memory",
        "method": "POST",
        "path": "/api/v1/memory/query",
        "body": {"query": "what is the deadline", "k": 10},
    },
]


def _compose_instance_endpoints(base_url: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    return {
        "http": base,
        "api": f"{base}/api/v1",
        "docs": f"{base}/docs",
        "mcp": f"{base}/api/v1/mcp",
        "memory": f"{base}/api/v1/memory",
        "workflows": f"{base}/api/v1/workflows",
        "agents": f"{base}/api/v1/agents",
        "agent_facts": f"{base}/.well-known/agent-facts.json",
    }


def _compose_agent_endpoints(agent: Agent, base_url: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    endpoints: dict[str, str] = {
        "facts": f"{base}/api/v1/agents/{agent.agent_id}/agent-facts.json",
    }
    if agent.endpoint:
        # The agent's declared endpoint might be an absolute URL (external
        # agent) or an opaque handler identifier (internal). We expose it
        # under 'agent_endpoint' so the AgentFacts surface is self-describing
        # without losing the provenance of the original value.
        endpoints["agent_endpoint"] = agent.endpoint
    if agent.protocol_type:
        endpoints["protocol"] = agent.protocol_type
    return endpoints


def _limits_from_cost_profile(
    cost_profile: dict[str, Any],
    latency_profile: dict[str, Any],
) -> AgentLimits:
    limits = AgentLimits()
    # max_transaction_usd: if an agent declares a per-call cost, its declared cap
    # is interpreted as a soft limit; otherwise we leave it unset.
    max_tx = cost_profile.get("max_transaction_usd")
    if isinstance(max_tx, (int, float)):
        limits.max_transaction_usd = float(max_tx)
    cost_cap = cost_profile.get("cost_cap_per_hour_usd")
    if isinstance(cost_cap, (int, float)):
        limits.cost_cap_per_hour_usd = float(cost_cap)
    rate = cost_profile.get("rate_limit_per_min")
    if isinstance(rate, int):
        limits.rate_limit_per_min = rate
    concurrent = cost_profile.get("max_concurrent_workflows")
    if isinstance(concurrent, int):
        limits.max_concurrent_workflows = concurrent
    # latency_profile is not surfaced as a hard limit yet — it remains informational.
    _ = latency_profile
    return limits


def _trust_zone_for_agent(agent: Agent) -> str:
    """Derive the declared trust zone for an AgentFacts document.

    We lean on the existing ``safety_level`` integer: agents declared at
    safety level >=7 are treated as ``verified-enterprise``, 4-6 as
    ``verified-community``, and below as ``unverified``. Callers who need
    a different scheme can override by writing ``trust_zone`` into the
    agent's metadata.
    """
    override = (agent.metadata_ or {}).get("trust_zone")
    if isinstance(override, str) and override:
        return override
    if agent.safety_level >= 7:
        return "verified-enterprise"
    if agent.safety_level >= 4:
        return "verified-community"
    return "unverified"


def _load_tool_descriptors() -> list[ToolDescriptor]:
    """Return the platform tool catalogue as AgentFacts ``ToolDescriptor`` objects."""
    try:
        from app.tools.registry import get_tool_registry

        registry = get_tool_registry()
    except Exception as exc:
        logger.warning("tool_registry_unavailable_for_agent_facts", error=str(exc))
        return []
    descriptors: list[ToolDescriptor] = []
    for tool in registry.list_tools():
        name = getattr(tool, "name", None) or getattr(tool, "tool_name", None)
        if not name:
            continue
        description = getattr(tool, "description", "") or ""
        input_schema = getattr(tool, "input_schema", None)
        output_schema = getattr(tool, "output_schema", None)
        descriptors.append(
            ToolDescriptor(
                name=str(name),
                description=str(description),
                input_schema=input_schema if isinstance(input_schema, dict) else None,
                output_schema=output_schema if isinstance(output_schema, dict) else None,
            )
        )
    return descriptors


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_n_days_from_now(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
