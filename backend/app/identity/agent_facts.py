"""AgentFacts v1 — the NANDA-canonical identity document.

Schema source: ``projnanda/agentfacts-format`` on GitHub
(`agentfacts_schema.json`, ``$id: https://agentfacts.org/schema/v1``).

An AgentFacts document is what a stock OpenClaw agent fetches to discover what
MassClaw (or any agent on the Internet of Agents) can do. The v1 schema is a
flat object — NOT a W3C Verifiable Credential envelope:

    {
      "id": "urn:agent:massclaw:instance",
      "agent_name": "urn:agent:massclaw:instance",
      "label": "MassClaw",
      "description": "...",
      "version": "1.0.0",
      "documentationUrl": "https://...",
      "jurisdiction": "US",
      "provider": {
         "name": "MassClaw",
         "url": "https://massclaw.example",
         "did": "did:key:z..."              ← verification anchor
      },
      "endpoints": {
         "static": ["https://massclaw.example/api/v1"],
         "adaptive_resolver": {"url": "...", "policies": [...]}
      },
      "capabilities": {
         "modalities": ["text","structured-output"],
         "authentication": {"methods": ["bearer","api-key","oauth2"]}
      },
      "skills": [
         {"id":"workflow.submit","description":"...","inputModes":["text"],...}
      ],
      "certification": {"level":"verified","issuer":"..."},
      "evaluations": [...],
      "telemetry": {"metrics": {...}},
      "verifiable_credentials": [ ... ]     ← optional integrity attestation
    }

Ed25519 signing is expressed as an OPTIONAL entry in ``verifiable_credentials``,
not as a top-level ``proof``. We use W3C ``DataIntegrityProof`` with cryptosuite
``eddsa-rdfc-2022``, signing a canonicalized hash of the document with the
``verifiable_credentials`` array cleared — so the credential is self-contained
and reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.identity.canonicalize import canonicalize
from app.identity.did import (
    MalformedDIDError,
    build_did_key,
    decode_did_key,
    parse_did,
)
from app.identity.signer import (
    KeyPair,
    decode_multibase,
    encode_multibase,
    sign_bytes,
    verify_bytes,
)

INTEGRITY_CRYPTOSUITE = "eddsa-rdfc-2022"
INTEGRITY_PROOF_TYPE = "DataIntegrityProof"
INTEGRITY_CREDENTIAL_TYPE = "AgentFactsIntegrityCredential"


def _utc_now_isoformat() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Provider(BaseModel):
    """The operator/issuer of the agent (required in AgentFacts v1)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    did: str | None = Field(
        default=None,
        description="DID of the provider — did:web:<domain> or did:key:<multibase>.",
    )


class Endpoints(BaseModel):
    """Callable surfaces an agent exposes."""

    model_config = ConfigDict(extra="forbid")

    static: list[str] = Field(default_factory=list, description="Fixed URLs, preferred for v1 consumers.")
    adaptive_resolver: dict[str, Any] | None = Field(
        default=None,
        description="Opt-in adaptive endpoint discovery — {url, policies}.",
    )


class CapabilityAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methods: list[str] = Field(
        default_factory=list,
        description="Accepted auth methods: bearer, api-key, oauth2, mutual-tls, agent-facts-signature, etc.",
    )


class Capabilities(BaseModel):
    """Structured capabilities — MODALITIES + AUTH, not a flat list of strings."""

    model_config = ConfigDict(extra="forbid")

    modalities: list[str] = Field(
        default_factory=list,
        description="text, image, audio, video, structured-output, code, tool-use, workflow, etc.",
    )
    authentication: CapabilityAuthentication = Field(default_factory=CapabilityAuthentication)


class Skill(BaseModel):
    """A single capability exposed as a callable skill."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable skill identifier, e.g. 'workflow.submit'.")
    description: str
    input_modes: list[str] = Field(alias="inputModes", default_factory=list)
    output_modes: list[str] = Field(alias="outputModes", default_factory=list)
    supported_languages: list[str] | None = Field(alias="supportedLanguages", default=None)
    latency_budget_ms: int | None = Field(alias="latencyBudgetMs", default=None)
    max_tokens: int | None = Field(alias="maxTokens", default=None)


class Certification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = Field(description="e.g. 'verified', 'verified-enterprise', 'unverified'.")
    issuer: str | None = None
    issued_at: str | None = Field(alias="issuedAt", default=None)
    valid_until: str | None = Field(alias="validUntil", default=None)


class TelemetryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    throughput_rps: float | None = None
    availability: float | None = None
    latency_p95_ms: float | None = None


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: TelemetryMetrics = Field(default_factory=TelemetryMetrics)


class IntegrityProof(BaseModel):
    """W3C DataIntegrityProof block used inside an integrity credential."""

    model_config = ConfigDict(extra="forbid")

    type: str = INTEGRITY_PROOF_TYPE
    cryptosuite: str = INTEGRITY_CRYPTOSUITE
    created: str
    verification_method: str = Field(alias="verificationMethod")
    proof_purpose: str = Field(default="assertionMethod", alias="proofPurpose")
    proof_value: str = Field(alias="proofValue")


class IntegrityCredential(BaseModel):
    """A verifiable credential attesting to the integrity of this document."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: list[str] = Field(default_factory=lambda: [INTEGRITY_CREDENTIAL_TYPE])
    issuer: str
    issued: str
    proof: IntegrityProof


class AgentFactsExtensions(BaseModel):
    """MassClaw-specific extras not covered by the v1 schema.

    Kept under a namespaced key inside the top-level document so schema
    validators ignore them but consumers who know about MassClaw can still
    read them. This is how we preserve rich metadata (example requests,
    error schema, wallet limits) without violating v1.
    """

    model_config = ConfigDict(extra="forbid")

    example_requests: list[dict[str, Any]] = Field(default_factory=list)
    error_schema: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    nanda_index_handle: str | None = None


class AgentFacts(BaseModel):
    """The full AgentFacts v1 document."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(description="Stable URN identifying this agent across time.")
    agent_name: str = Field(description="URN-shaped stable name, typically equal to id.")
    label: str = Field(description="Human-readable name (e.g. 'MassClaw').")
    description: str
    version: str
    provider: Provider
    endpoints: Endpoints
    capabilities: Capabilities
    skills: list[Skill] = Field(default_factory=list)
    documentation_url: str | None = Field(alias="documentationUrl", default=None)
    jurisdiction: str | None = None
    certification: Certification | None = None
    telemetry: Telemetry | None = None
    evaluations: list[dict[str, Any]] | None = None
    verifiable_credentials: list[IntegrityCredential] = Field(default_factory=list)
    # MassClaw-specific extensions live under an x-namespaced key so we don't
    # silently violate the v1 schema's ``additionalProperties`` policy.
    x_massclaw: AgentFactsExtensions | None = Field(default=None, alias="x-massclaw")

    def to_document(self) -> dict[str, Any]:
        """Serialise to the canonical v1 JSON shape (camelCase where the schema says so)."""
        return self.model_dump(by_alias=True, exclude_none=True)

    def signable_body(self) -> dict[str, Any]:
        """The body that gets hashed for an integrity credential.

        We exclude the credentials array itself so a credential can sign the
        doc without signing itself — the standard recursive-signature trick.
        """
        return self.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude={"verifiable_credentials"},
        )

    def signable_bytes(self) -> bytes:
        return canonicalize(self.signable_body())

    def attach_integrity_credential(self, keypair: KeyPair) -> IntegrityCredential:
        """Sign the document and append an integrity credential.

        The verification method references a ``did:key`` derived from the
        public half of ``keypair``; consumers can verify the signature
        without fetching any external document.
        """
        issuer_did = build_did_key(keypair.public_bytes)
        signature = sign_bytes(self.signable_bytes(), keypair.private_seed)
        now = _utc_now_isoformat()
        cred = IntegrityCredential(
            issuer=issuer_did,
            issued=now,
            proof=IntegrityProof(
                created=now,
                **{
                    "verificationMethod": f"{issuer_did}#ed25519",
                    "proofPurpose": "assertionMethod",
                    "proofValue": encode_multibase(signature),
                },
            ),
        )
        self.verifiable_credentials.append(cred)
        return cred

    def verify_integrity(self, credential: IntegrityCredential | None = None) -> bool:
        """Verify a specific integrity credential or the first one found.

        Returns ``False`` on any structural/cryptographic failure. Raises only
        for programmer errors (no credentials at all when one was requested).
        """
        if credential is None:
            if not self.verifiable_credentials:
                raise ValueError("AgentFacts has no verifiable_credentials to verify")
            credential = self.verifiable_credentials[0]
        if credential.proof.type != INTEGRITY_PROOF_TYPE:
            return False
        if credential.proof.cryptosuite != INTEGRITY_CRYPTOSUITE:
            return False
        try:
            verification_did = credential.proof.verification_method.split("#", 1)[0]
            issuer_pub = _public_key_from_any_did(verification_did)
            signature = decode_multibase(credential.proof.proof_value)
        except Exception:
            return False
        # Re-create the body as signed: exclude *this* credential only.
        body = self.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude={"verifiable_credentials"},
        )
        payload = canonicalize(body)
        return verify_bytes(payload, signature, issuer_pub)


class AgentFactsBuilder:
    """Fluent builder for AgentFacts v1 documents.

    Minimal required inputs are enforced by :class:`AgentFacts` (Pydantic);
    the builder simply keeps the call-site readable and the defaults sane.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        label: str,
        description: str,
        version: str,
        provider_name: str,
        provider_url: str,
    ) -> None:
        self._id = agent_id
        self._label = label
        self._description = description
        self._version = version
        self._provider_name = provider_name
        self._provider_url = provider_url
        self._provider_did: str | None = None
        self._endpoints_static: list[str] = []
        self._endpoints_adaptive: dict[str, Any] | None = None
        self._modalities: list[str] = []
        self._auth_methods: list[str] = []
        self._skills: list[Skill] = []
        self._documentation_url: str | None = None
        self._jurisdiction: str | None = None
        self._certification: Certification | None = None
        self._telemetry: Telemetry | None = None
        self._extensions: AgentFactsExtensions | None = None

    def provider_did(self, did: str) -> AgentFactsBuilder:
        # Validate shape up front.
        parse_did(did)
        self._provider_did = did
        return self

    def endpoint(self, url: str) -> AgentFactsBuilder:
        self._endpoints_static.append(url)
        return self

    def endpoints(self, urls: list[str]) -> AgentFactsBuilder:
        self._endpoints_static = list(urls)
        return self

    def adaptive_endpoints(self, url: str, policies: list[str] | None = None) -> AgentFactsBuilder:
        entry: dict[str, Any] = {"url": url}
        if policies:
            entry["policies"] = list(policies)
        self._endpoints_adaptive = entry
        return self

    def modalities(self, *modalities: str) -> AgentFactsBuilder:
        self._modalities = list(modalities)
        return self

    def auth_methods(self, *methods: str) -> AgentFactsBuilder:
        self._auth_methods = list(methods)
        return self

    def add_skill(self, skill: Skill) -> AgentFactsBuilder:
        self._skills.append(skill)
        return self

    def skills(self, skills: list[Skill]) -> AgentFactsBuilder:
        self._skills = list(skills)
        return self

    def documentation_url(self, url: str) -> AgentFactsBuilder:
        self._documentation_url = url
        return self

    def jurisdiction(self, code: str) -> AgentFactsBuilder:
        self._jurisdiction = code
        return self

    def certification(self, level: str, *, issuer: str | None = None) -> AgentFactsBuilder:
        self._certification = Certification(level=level, issuer=issuer)
        return self

    def telemetry(self, metrics: TelemetryMetrics) -> AgentFactsBuilder:
        self._telemetry = Telemetry(metrics=metrics)
        return self

    def extensions(self, extensions: AgentFactsExtensions) -> AgentFactsBuilder:
        self._extensions = extensions
        return self

    def build(self) -> AgentFacts:
        return AgentFacts(
            id=self._id,
            agent_name=self._id,
            label=self._label,
            description=self._description,
            version=self._version,
            provider=Provider(
                name=self._provider_name,
                url=self._provider_url,
                did=self._provider_did,
            ),
            endpoints=Endpoints(
                static=list(self._endpoints_static),
                adaptive_resolver=self._endpoints_adaptive,
            ),
            capabilities=Capabilities(
                modalities=list(self._modalities),
                authentication=CapabilityAuthentication(methods=list(self._auth_methods)),
            ),
            skills=list(self._skills),
            certification=self._certification,
            telemetry=self._telemetry,
            **{"documentationUrl": self._documentation_url},
            jurisdiction=self._jurisdiction,
            **{"x-massclaw": self._extensions} if self._extensions is not None else {},
        )

    def build_and_sign(self, keypair: KeyPair) -> AgentFacts:
        doc = self.build()
        doc.attach_integrity_credential(keypair)
        return doc


def _public_key_from_any_did(did: str) -> bytes:
    """Extract an Ed25519 public key regardless of the DID method.

    Supports did:key, did:nanda (legacy), and — when the corresponding DID
    document is resolvable — did:web. For did:web we currently delegate to
    a thin resolver used by callers that have a DID Document in hand.
    """
    parsed = parse_did(did)
    if parsed.method == "key":
        return decode_did_key(did)
    if parsed.method == "nanda":
        # Legacy support for the pre-v1 MassClaw DID method — identifier is
        # the same multibase-pubkey form as did:key.
        from app.identity.did import decode_did_nanda

        return decode_did_nanda(did)
    raise MalformedDIDError(f"cannot extract Ed25519 public key from did:{parsed.method}: without a resolver")
