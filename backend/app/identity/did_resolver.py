"""Resolver for NANDA DIDs.

A resolver turns a ``did:nanda:<id>`` string into the AgentFacts document that
describes the agent. Three strategies are attempted, in order:

1. Local lookup — the agent is registered on this MassClaw node.
2. NANDA Index lookup — the public phonebook run by the MIT Media Lab.
3. Well-known URL — a conventional HTTPS endpoint at
   ``https://<host>/.well-known/agent-facts.json`` when the DID carries a
   companion endpoint hint or the caller supplies a fallback URL.

The Index client is stubbed on Day 1 and completed on Day 3. This module is
designed so swap-in is a one-class change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.identity.agent_facts import AgentFacts
from app.identity.did import parse_did, public_key_from_did
from app.identity.signer import verify_bytes


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


class NandaIndexClient(Protocol):
    """Minimal protocol for the NANDA Index HTTP client — filled in Day 3."""

    async def resolve(self, did: str) -> AgentFacts | None: ...


class WellKnownFetcher(Protocol):
    """Minimal protocol for fetching ``.well-known/agent-facts.json`` docs."""

    async def fetch(self, url: str) -> dict[str, Any] | None: ...


class DIDResolver:
    """Coordinates the three resolution strategies above."""

    def __init__(
        self,
        *,
        local_registry: LocalRegistry | None = None,
        nanda_index: NandaIndexClient | None = None,
        well_known_fetcher: WellKnownFetcher | None = None,
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
        parsed = parse_did(did)  # raises on malformed DIDs.
        # 1. Local registry.
        if self._local is not None:
            local_doc = await self._local.get_agent_facts_by_did(did)
            if local_doc is not None:
                _assert_signature(local_doc)
                return ResolvedAgent(did=did, agent_facts=local_doc, source="local")

        # 2. NANDA Index lookup — NANDA DIDs only.
        if parsed.method == "nanda" and self._index is not None:
            index_doc = await self._index.resolve(did)
            if index_doc is not None:
                _assert_signature(index_doc)
                return ResolvedAgent(did=did, agent_facts=index_doc, source="nanda_index")

        # 3. Well-known fallback.
        if self._well_known is not None and well_known_url_hint is not None:
            raw = await self._well_known.fetch(well_known_url_hint)
            if raw is not None:
                doc = AgentFacts.model_validate(raw)
                _assert_signature(doc)
                return ResolvedAgent(did=did, agent_facts=doc, source="well_known")

        raise ResolutionError(f"could not resolve {did} via any configured strategy")


def _assert_signature(doc: AgentFacts) -> None:
    """Verify the signature of a resolved document.

    Every resolver result must be cryptographically verified before being
    returned to callers — otherwise we would trust whatever the network says.
    """
    if doc.proof is None:
        raise ResolutionError("resolved AgentFacts document has no proof")
    if not doc.verify():
        raise ResolutionError("AgentFacts signature verification failed")
    # Sanity check: the embedded public key must match the one implied by the DID.
    issuer_pub = public_key_from_did(doc.issuer)
    from app.identity.signer import decode_multibase

    declared_pub = decode_multibase(doc.credential_subject.public_key_multibase)
    if doc.issuer == doc.credential_subject.id and declared_pub != issuer_pub:
        raise ResolutionError(
            "public_key_multibase disagrees with the DID it claims to authenticate"
        )
    # Dummy reference to silence unused-import linters — keeps verify_bytes
    # importable for callers that want to skip the AgentFacts wrapper.
    _ = verify_bytes  # noqa: F841
