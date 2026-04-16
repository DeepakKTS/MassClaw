"""AgentFacts — a signed, self-describing identity document for an agent.

An AgentFacts document is what a stock OpenClaw agent fetches to learn what
MassClaw can do. It is the NANDA-native equivalent of an OpenAPI spec plus a
verifiable credential. The signature proves the document was produced by the
holder of the private key that matches the DID embedded in ``credentialSubject.id``.

The document layout is deliberately close to the W3C Verifiable Credentials v2
envelope so it can be upgraded to full VC compliance without breaking existing
consumers:

    {
      "@context": ["https://www.w3.org/ns/credentials/v2", "https://nanda.mit.edu/ns/agent-facts/v1"],
      "type": ["VerifiableCredential", "AgentFacts"],
      "id": "urn:massclaw:agent-facts:<uuid>",
      "issuer": "did:nanda:<issuer pubkey>",
      "validFrom": "2026-04-17T00:00:00Z",
      "validUntil": "2026-10-17T00:00:00Z",
      "credentialSubject": {
         "id": "did:nanda:<agent pubkey>",
         "name": "MassClaw",
         "description": "...",
         "capabilities": ["workflow.submit", "memory.read", ...],
         "tools": [{"name": "web_search", "description": "..."}, ...],
         "endpoints": {"http": "https://...", "mcp": "https://..."},
         "limits": {"max_transaction_usd": 5000, "rate_limit_per_min": 60},
         "trust_zone": "verified-enterprise",
         "public_key_multibase": "z...",
         "example_requests": [...],
         "error_schema": {...}
      },
      "proof": {
         "type": "Ed25519Signature2020",
         "created": "2026-04-17T00:00:00Z",
         "verificationMethod": "did:nanda:<issuer pubkey>#ed25519",
         "proofPurpose": "assertionMethod",
         "signatureValue": "z<base58btc signature>"
      }
    }
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.identity.canonicalize import canonicalize
from app.identity.did import build_did_from_public_key, public_key_from_did
from app.identity.signer import (
    KeyPair,
    decode_multibase,
    encode_multibase,
    sign_bytes,
    verify_bytes,
)

AGENT_FACTS_CONTEXT: list[str] = [
    "https://www.w3.org/ns/credentials/v2",
    "https://nanda.mit.edu/ns/agent-facts/v1",
]
AGENT_FACTS_TYPES: list[str] = ["VerifiableCredential", "AgentFacts"]
PROOF_TYPE = "Ed25519Signature2020"


def _utc_now_isoformat() -> str:
    """Return a timezone-aware UTC timestamp with second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AgentLimits(BaseModel):
    """Declared limits for an agent — consumed by the policy engine."""

    model_config = ConfigDict(extra="forbid")

    max_transaction_usd: float | None = Field(
        default=None,
        description="Maximum single financial transaction the agent may authorise.",
    )
    rate_limit_per_min: int | None = Field(
        default=None,
        description="Maximum tool calls per 60s window.",
    )
    cost_cap_per_hour_usd: float | None = Field(
        default=None,
        description="Maximum spend this agent may incur per rolling hour.",
    )
    max_concurrent_workflows: int | None = Field(default=None)


class ToolDescriptor(BaseModel):
    """A single tool exposed by the agent."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class AgentFactsSubject(BaseModel):
    """The ``credentialSubject`` of an AgentFacts document."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="DID of the subject agent, e.g. 'did:nanda:z...'.")
    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    endpoints: dict[str, str] = Field(
        default_factory=dict,
        description="Keyed by protocol: 'http', 'mcp', 'a2a', 'websocket'.",
    )
    limits: AgentLimits = Field(default_factory=AgentLimits)
    trust_zone: str = Field(
        default="unverified",
        description="Named bucket used by the policy engine (e.g. 'verified-enterprise').",
    )
    public_key_multibase: str = Field(
        description="Multibase base58btc-encoded Ed25519 public key for the subject.",
    )
    example_requests: list[dict[str, Any]] = Field(default_factory=list)
    error_schema: dict[str, Any] | None = None
    nanda_index_handle: str | None = None


class AgentFactsProof(BaseModel):
    """The signature block attached to an AgentFacts document."""

    model_config = ConfigDict(extra="forbid")

    type: str = PROOF_TYPE
    created: str
    verification_method: str = Field(alias="verificationMethod")
    proof_purpose: str = Field(default="assertionMethod", alias="proofPurpose")
    signature_value: str = Field(alias="signatureValue")


class AgentFacts(BaseModel):
    """A full AgentFacts Verifiable Credential."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    context: list[str] = Field(alias="@context", default_factory=lambda: list(AGENT_FACTS_CONTEXT))
    type: list[str] = Field(default_factory=lambda: list(AGENT_FACTS_TYPES))
    id: str
    issuer: str
    valid_from: str = Field(alias="validFrom")
    valid_until: str | None = Field(default=None, alias="validUntil")
    credential_subject: AgentFactsSubject = Field(alias="credentialSubject")
    proof: AgentFactsProof | None = None

    def to_signable_dict(self) -> dict[str, Any]:
        """Return the JSON form minus the proof block — what gets signed."""
        return self.model_dump(by_alias=True, exclude={"proof"}, exclude_none=True)

    def to_document(self) -> dict[str, Any]:
        """Return the full JSON form, including the proof if present."""
        return self.model_dump(by_alias=True, exclude_none=True)

    def signable_bytes(self) -> bytes:
        """Canonical bytes that should be Ed25519-signed."""
        return canonicalize(self.to_signable_dict())

    def verify(self) -> bool:
        """Verify the proof against the issuer's public key.

        Returns ``False`` on a bad signature. Raises on structurally invalid
        documents (missing proof, unknown proof type, malformed DID).
        """
        if self.proof is None:
            raise ValueError("AgentFacts.verify called on a document with no proof")
        if self.proof.type != PROOF_TYPE:
            raise ValueError(f"unsupported proof type: {self.proof.type}")
        issuer_pub = public_key_from_did(self.issuer)
        try:
            signature = decode_multibase(self.proof.signature_value)
        except Exception as exc:
            raise ValueError(f"malformed signatureValue: {exc}") from exc
        return verify_bytes(self.signable_bytes(), signature, issuer_pub)


class AgentFactsBuilder:
    """Fluent-ish builder for constructing a signed AgentFacts document."""

    def __init__(
        self,
        *,
        subject_did: str,
        issuer_did: str,
        document_id: str,
        name: str,
        description: str,
    ) -> None:
        self._subject_did = subject_did
        self._issuer_did = issuer_did
        self._document_id = document_id
        self._name = name
        self._description = description
        self._capabilities: list[str] = []
        self._tools: list[ToolDescriptor] = []
        self._endpoints: dict[str, str] = {}
        self._limits = AgentLimits()
        self._trust_zone = "unverified"
        self._public_key_multibase: str | None = None
        self._example_requests: list[dict[str, Any]] = []
        self._error_schema: dict[str, Any] | None = None
        self._nanda_index_handle: str | None = None
        self._valid_from: str | None = None
        self._valid_until: str | None = None

    def capabilities(self, *caps: str) -> AgentFactsBuilder:
        self._capabilities = list(caps)
        return self

    def add_capability(self, cap: str) -> AgentFactsBuilder:
        self._capabilities.append(cap)
        return self

    def tools(self, tools: list[ToolDescriptor]) -> AgentFactsBuilder:
        self._tools = list(tools)
        return self

    def add_tool(self, tool: ToolDescriptor) -> AgentFactsBuilder:
        self._tools.append(tool)
        return self

    def endpoints(self, endpoints: dict[str, str]) -> AgentFactsBuilder:
        self._endpoints = dict(endpoints)
        return self

    def endpoint(self, protocol: str, url: str) -> AgentFactsBuilder:
        self._endpoints[protocol] = url
        return self

    def limits(self, limits: AgentLimits) -> AgentFactsBuilder:
        self._limits = limits
        return self

    def trust_zone(self, zone: str) -> AgentFactsBuilder:
        self._trust_zone = zone
        return self

    def public_key(self, public_bytes_or_multibase: bytes | str) -> AgentFactsBuilder:
        if isinstance(public_bytes_or_multibase, str):
            # Trust the caller gave us an already-multibase-encoded key; round-trip to
            # validate.
            decoded = decode_multibase(public_bytes_or_multibase)
            if len(decoded) != 32:
                raise ValueError("Ed25519 public key must decode to 32 bytes")
            self._public_key_multibase = public_bytes_or_multibase
        else:
            self._public_key_multibase = encode_multibase(public_bytes_or_multibase)
        return self

    def example_requests(self, examples: list[dict[str, Any]]) -> AgentFactsBuilder:
        self._example_requests = list(examples)
        return self

    def error_schema(self, schema: dict[str, Any]) -> AgentFactsBuilder:
        self._error_schema = dict(schema)
        return self

    def nanda_index_handle(self, handle: str) -> AgentFactsBuilder:
        self._nanda_index_handle = handle
        return self

    def validity(self, *, valid_from: str, valid_until: str | None = None) -> AgentFactsBuilder:
        self._valid_from = valid_from
        self._valid_until = valid_until
        return self

    def _build_unsigned(self) -> AgentFacts:
        if self._public_key_multibase is None:
            # Default to deriving the subject's public key from the DID when the
            # caller did not override it. This is the common case where the agent
            # signs its own AgentFacts.
            self._public_key_multibase = _multibase_from_did(self._subject_did)

        subject = AgentFactsSubject(
            id=self._subject_did,
            name=self._name,
            description=self._description,
            capabilities=list(self._capabilities),
            tools=list(self._tools),
            endpoints=dict(self._endpoints),
            limits=self._limits,
            trust_zone=self._trust_zone,
            public_key_multibase=self._public_key_multibase,
            example_requests=list(self._example_requests),
            error_schema=self._error_schema,
            nanda_index_handle=self._nanda_index_handle,
        )
        valid_from = self._valid_from or _utc_now_isoformat()
        return AgentFacts(
            **{
                "@context": list(AGENT_FACTS_CONTEXT),
                "type": list(AGENT_FACTS_TYPES),
                "id": self._document_id,
                "issuer": self._issuer_did,
                "validFrom": valid_from,
                "validUntil": self._valid_until,
                "credentialSubject": subject,
            }
        )

    def build_and_sign(self, issuer_keypair: KeyPair) -> AgentFacts:
        """Build the document and attach an Ed25519 signature.

        The issuer DID must match the public key of ``issuer_keypair``; this is
        enforced to prevent accidentally signing with the wrong key. Self-issuance
        (issuer_did == subject_did) is allowed and common.
        """
        issuer_pub = public_key_from_did(self._issuer_did)
        if issuer_pub != issuer_keypair.public_bytes:
            raise ValueError(
                "issuer_keypair does not match the public key embedded in issuer DID"
            )
        unsigned = self._build_unsigned()
        signable = unsigned.signable_bytes()
        signature = sign_bytes(signable, issuer_keypair.private_seed)
        unsigned.proof = AgentFactsProof(
            created=_utc_now_isoformat(),
            **{
                "verificationMethod": f"{self._issuer_did}#ed25519",
                "proofPurpose": "assertionMethod",
                "signatureValue": encode_multibase(signature),
            },
        )
        return unsigned


def _multibase_from_did(did: str) -> str:
    """Pull the multibase-encoded public key fragment back out of a did:nanda DID."""
    public_bytes = public_key_from_did(did)
    return encode_multibase(public_bytes)


def build_agent_facts(
    *,
    keypair: KeyPair,
    document_id: str,
    name: str,
    description: str,
    capabilities: list[str] | None = None,
    tools: list[ToolDescriptor] | None = None,
    endpoints: dict[str, str] | None = None,
    limits: AgentLimits | None = None,
    trust_zone: str = "unverified",
    example_requests: list[dict[str, Any]] | None = None,
    error_schema: dict[str, Any] | None = None,
    nanda_index_handle: str | None = None,
    valid_until: str | None = None,
) -> AgentFacts:
    """Convenience: build a self-issued AgentFacts document in one call."""
    did = build_did_from_public_key(keypair.public_bytes)
    builder = (
        AgentFactsBuilder(
            subject_did=did,
            issuer_did=did,
            document_id=document_id,
            name=name,
            description=description,
        )
        .capabilities(*(capabilities or []))
        .tools(tools or [])
        .endpoints(endpoints or {})
        .trust_zone(trust_zone)
        .public_key(keypair.public_bytes)
    )
    if limits is not None:
        builder.limits(limits)
    if example_requests is not None:
        builder.example_requests(example_requests)
    if error_schema is not None:
        builder.error_schema(error_schema)
    if nanda_index_handle is not None:
        builder.nanda_index_handle(nanda_index_handle)
    if valid_until is not None:
        builder.validity(valid_from=_utc_now_isoformat(), valid_until=valid_until)
    return builder.build_and_sign(keypair)
