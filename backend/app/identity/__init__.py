"""NANDA-native identity layer for MassClaw.

Provides Ed25519-signed AgentFacts documents that give every agent a
cryptographically verifiable identity compatible with the NANDA Index.

Public surface:

    from app.identity import (
        AgentFacts, AgentFactsBuilder, AgentFactsProof,
        AgentKeyPair, KeyStore,
        canonicalize, sign_bytes, verify_bytes,
        parse_did, build_did_from_public_key,
    )
"""

from __future__ import annotations

from app.identity.agent_facts import (
    AgentFacts,
    AgentFactsBuilder,
    AgentFactsProof,
    AgentFactsSubject,
    AgentLimits,
    ToolDescriptor,
)
from app.identity.canonicalize import canonicalize
from app.identity.did import (
    DID_METHOD,
    build_did_from_public_key,
    parse_did,
    public_key_from_did,
)
from app.identity.key_store import AgentKeyPair, KeyStore
from app.identity.signer import (
    decode_multibase,
    encode_multibase,
    generate_keypair,
    public_key_multibase,
    sign_bytes,
    verify_bytes,
)

__all__ = [
    "DID_METHOD",
    "AgentFacts",
    "AgentFactsBuilder",
    "AgentFactsProof",
    "AgentFactsSubject",
    "AgentKeyPair",
    "AgentLimits",
    "KeyStore",
    "ToolDescriptor",
    "build_did_from_public_key",
    "canonicalize",
    "decode_multibase",
    "encode_multibase",
    "generate_keypair",
    "parse_did",
    "public_key_from_did",
    "public_key_multibase",
    "sign_bytes",
    "verify_bytes",
]
