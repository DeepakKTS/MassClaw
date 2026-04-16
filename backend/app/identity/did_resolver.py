"""Resolver for NANDA DIDs.

A resolver turns a ``did:nanda:<id>`` string into the AgentFacts document that
describes the agent. Three strategies are attempted, in order:

1. **Local registry** — the DID belongs to an agent registered on this
   MassClaw node, so we can build the document from local state.
2. **NANDA Index** — the public MIT-hosted phonebook.
3. **Well-known URL** — a conventional HTTPS endpoint at
   ``https://<host>/.well-known/agent-facts.json``. Used as a last resort
   when the caller can supply a hint URL (e.g. from an MCP tool description).

Every returned document is cryptographically verified against the public key
embedded in its DID before it is handed back to callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.logging import get_logger
from app.identity.agent_facts import AgentFacts
from app.identity.did import parse_did, public_key_from_did
from app.identity.signer import decode_multibase

logger = get_logger(__name__)


class ResolutionError(Exception):
    """Raised when a DID cannot be resolved to an AgentFacts document."""


@dataclass(frozen=True)
class ResolvedAgent:
    """Result of resolving a DID."""

    did: str
    agent_facts: AgentFacts
    source: str  # 'local' | 'nanda_index' | 'well_known'


class LocalRegistry(Protocol):
    """Minimal protocol for looking up an agent registered on this node."""

    async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None: ...


class NandaIndexClientProto(Protocol):
    """Minimal protocol for the NANDA Index HTTP client."""

    async def resolve(self, did: str) -> AgentFacts | None: ...


class WellKnownFetcherProto(Protocol):
    """Minimal protocol for fetching ``.well-known/agent-facts.json`` docs."""

    async def fetch(self, url: str) -> dict[str, Any] | None: ...


class DIDResolver:
    """Coordinates the three resolution strategies above.

    The resolver is dependency-injected: tests can pass fakes for any of the
    three slots; production code wires in the real :class:`NandaIndexClient`
    and a local registry adapter backed by the database.
    """

    def __init__(
        self,
        *,
        local_registry: LocalRegistry | None = None,
        nanda_index: NandaIndexClientProto | None = None,
        well_known_fetcher: WellKnownFetcherProto | None = None,
    ) -> None:
        self._local = local_registry
        self._index = nanda_index
        self._well_known = well_known_fetcher

    async def resolve(
        self,
        did: str,
        *,
        well_known_url_hint: str | None = None,
    ) -> ResolvedAgent:
        parsed = parse_did(did)  # Raises MalformedDIDError on bad DIDs.
        errors: list[str] = []

        # 1. Local registry.
        if self._local is not None:
            try:
                local_doc = await self._local.get_agent_facts_by_did(did)
            except Exception as exc:
                logger.warning("local_registry_lookup_failed", did=did, error=str(exc))
                errors.append(f"local: {exc}")
            else:
                if local_doc is not None:
                    _assert_signature(local_doc)
                    return ResolvedAgent(did=did, agent_facts=local_doc, source="local")

        # 2. NANDA Index lookup — only meaningful for did:nanda DIDs.
        if parsed.method == "nanda" and self._index is not None:
            try:
                index_doc = await self._index.resolve(did)
            except Exception as exc:
                logger.warning("nanda_index_resolve_failed", did=did, error=str(exc))
                errors.append(f"nanda_index: {exc}")
            else:
                if index_doc is not None:
                    _assert_signature(index_doc)
                    return ResolvedAgent(did=did, agent_facts=index_doc, source="nanda_index")

        # 3. Well-known fallback — requires a URL hint we can GET.
        if self._well_known is not None and well_known_url_hint is not None:
            try:
                raw = await self._well_known.fetch(well_known_url_hint)
            except Exception as exc:
                logger.warning("well_known_fetch_failed", did=did, url=well_known_url_hint, error=str(exc))
                errors.append(f"well_known: {exc}")
            else:
                if raw is not None:
                    doc = AgentFacts.model_validate(raw)
                    _assert_signature(doc)
                    if doc.credential_subject.id != did:
                        raise ResolutionError(
                            f"well-known doc at {well_known_url_hint} resolves to "
                            f"{doc.credential_subject.id!r}, not {did!r}"
                        )
                    return ResolvedAgent(did=did, agent_facts=doc, source="well_known")

        reason = "; ".join(errors) if errors else "no resolver returned a document"
        raise ResolutionError(f"could not resolve {did}: {reason}")


def _assert_signature(doc: AgentFacts) -> None:
    """Cryptographically verify every resolved document before returning it."""
    if doc.proof is None:
        raise ResolutionError("resolved AgentFacts document has no proof")
    if not doc.verify():
        raise ResolutionError("AgentFacts signature verification failed")
    issuer_pub = public_key_from_did(doc.issuer)
    declared_pub = decode_multibase(doc.credential_subject.public_key_multibase)
    if doc.issuer == doc.credential_subject.id and declared_pub != issuer_pub:
        raise ResolutionError("public_key_multibase disagrees with the DID it claims to authenticate")
