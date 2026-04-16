"""Resolver for agent DIDs.

Turns a DID string into the AgentFacts v1 document that describes the agent,
trying three strategies in order:

1. **Local registry** — the agent is registered on this MassClaw node.
2. **NANDA Index** — the public MIT-hosted phonebook (by username, since the
   real Index does not support lookup by DID).
3. **Well-known URL** — a conventional HTTPS endpoint at
   ``https://<host>/.well-known/agent-facts.json``. Used as a last resort
   when the caller can supply a hint URL.

When the resolved document carries an ``AgentFactsIntegrityCredential`` we
verify it; otherwise we check that ``provider.did`` matches the DID being
resolved (our authenticity anchor in the absence of an embedded proof).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.logging import get_logger
from app.identity.agent_facts import AgentFacts
from app.identity.did import MalformedDIDError, parse_did

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
    async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None: ...


class NandaIndexClientProto(Protocol):
    """Only the handle-resolution flavour of the NANDA Index is used here."""

    async def resolve_by_username(self, username: str) -> AgentFacts | None: ...


class WellKnownFetcherProto(Protocol):
    async def fetch(self, url: str) -> dict[str, Any] | None: ...


class DIDResolver:
    """Coordinates the three resolution strategies above."""

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
        username_hint: str | None = None,
        well_known_url_hint: str | None = None,
    ) -> ResolvedAgent:
        parse_did(did)  # Raises MalformedDIDError on bad DIDs.
        errors: list[str] = []

        # 1. Local registry lookup by DID.
        if self._local is not None:
            try:
                local_doc = await self._local.get_agent_facts_by_did(did)
            except Exception as exc:
                logger.warning("local_registry_lookup_failed", did=did, error=str(exc))
                errors.append(f"local: {exc}")
            else:
                if local_doc is not None:
                    _assert_integrity(local_doc, did)
                    return ResolvedAgent(did=did, agent_facts=local_doc, source="local")

        # 2. NANDA Index lookup — by username because the real Index does
        #    not support DID-based resolution.
        if self._index is not None and username_hint is not None:
            try:
                index_doc = await self._index.resolve_by_username(username_hint)
            except Exception as exc:
                logger.warning("nanda_index_resolve_failed", did=did, error=str(exc))
                errors.append(f"nanda_index: {exc}")
            else:
                if index_doc is not None:
                    _assert_integrity(index_doc, did)
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
                    try:
                        doc = AgentFacts.model_validate(raw)
                    except Exception as exc:
                        raise ResolutionError(
                            f"well-known doc at {well_known_url_hint} failed schema validation: {exc}"
                        ) from exc
                    _assert_integrity(doc, did)
                    return ResolvedAgent(did=did, agent_facts=doc, source="well_known")

        reason = "; ".join(errors) if errors else "no resolver returned a document"
        raise ResolutionError(f"could not resolve {did}: {reason}")


def _assert_integrity(doc: AgentFacts, expected_did: str) -> None:
    """Verify provider/DID authenticity of a resolved document.

    Priority:
    1. If the doc carries at least one ``AgentFactsIntegrityCredential`` and
       one of them verifies against the expected DID's public key, accept.
    2. Otherwise, the doc's ``provider.did`` must equal the expected DID —
       this is the authenticity anchor for unsigned v1 docs.
    """
    # Fast check: provider DID shape matches request.
    provider_did = doc.provider.did
    if provider_did is not None:
        try:
            parse_did(provider_did)
        except MalformedDIDError as exc:
            raise ResolutionError(f"provider.did is malformed: {exc}") from exc

    # If there's an integrity credential, require it to verify.
    if doc.verifiable_credentials:
        for cred in doc.verifiable_credentials:
            if doc.verify_integrity(cred):
                return
        raise ResolutionError("AgentFacts has verifiable_credentials but none verified against the document body")

    # No credential — fall back to provider DID equality as the anchor.
    if provider_did != expected_did:
        raise ResolutionError(
            f"AgentFacts has no integrity credential and provider.did "
            f"({provider_did!r}) does not match expected DID ({expected_did!r})"
        )
