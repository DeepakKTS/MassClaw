"""Persistence for Ed25519 keypairs.

Two classes of keys live in MassClaw:

1. **Instance key** — there is exactly one per running MassClaw node. It signs
   the node's own AgentFacts document, signed memory records authored by the
   node itself (e.g. policy-engine decisions), and messages exchanged during
   peer-to-peer gossip. The instance key lives on disk at a path configured
   by ``IDENTITY_INSTANCE_KEY_PATH`` so it survives restarts.

2. **Agent keys** — one per registered agent. These live in the database so
   they can be retrieved alongside agent metadata. At rest they are encrypted
   with the instance's key-encryption key (KEK) before being stored as bytes.

Both surfaces are exposed through :class:`KeyStore` so callers never touch
raw file IO or the KEK.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from app.identity.signer import KeyPair, generate_keypair, load_private_key

# Default path for the instance key. Preference order:
#   1. IDENTITY_INSTANCE_KEY_PATH env var (explicit override)
#   2. /var/lib/massclaw/instance.key  (production — root-owned dir, 0600 file)
#   3. ~/.massclaw/instance.key        (fallback for dev boxes where /var/lib is not writable)
# The fallback keeps scheduler + resume endpoints usable in local dev
# without needing to export env vars; production deployments always set
# IDENTITY_INSTANCE_KEY_PATH explicitly.
_PRIMARY_INSTANCE_KEY_PATH = "/var/lib/massclaw/instance.key"
_USER_INSTANCE_KEY_PATH = "~/.massclaw/instance.key"
_ENV_INSTANCE_KEY_PATH = "IDENTITY_INSTANCE_KEY_PATH"
_ENV_KEY_ENCRYPTION_KEY = "IDENTITY_KEY_ENCRYPTION_KEY"  # hex-encoded 32-byte KEK
_NONCE_SIZE = 12  # ChaCha20-Poly1305 nonce size in bytes


def _default_instance_key_path() -> Path:
    """Pick a writable default for the instance key file.

    Tries the production path first (``/var/lib/massclaw``) and falls
    back to the user-home path on permission error. Tested on every
    call because during app startup /var/lib may or may not be mounted.
    """
    primary = Path(_PRIMARY_INSTANCE_KEY_PATH)
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        # Smoke-test writability without leaving a file around.
        probe = primary.parent / ".write_probe"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return primary
    except (PermissionError, OSError):
        return Path(_USER_INSTANCE_KEY_PATH).expanduser()


class KeyStoreError(Exception):
    """Raised for any identity persistence error."""


@dataclass(frozen=True)
class AgentKeyPair:
    """A keypair tied to a specific MassClaw agent."""

    agent_id: str
    keypair: KeyPair

    @property
    def public_bytes(self) -> bytes:
        return self.keypair.public_bytes


class KeyStore:
    """Loads and persists keypairs for the instance and for per-agent identities."""

    def __init__(
        self,
        *,
        instance_key_path: str | Path | None = None,
        key_encryption_key: bytes | None = None,
    ) -> None:
        env_override = os.getenv(_ENV_INSTANCE_KEY_PATH)
        if instance_key_path is not None:
            self._instance_key_path = Path(instance_key_path)
        elif env_override:
            self._instance_key_path = Path(env_override)
        else:
            self._instance_key_path = _default_instance_key_path()
        self._kek = key_encryption_key or _load_kek_from_env()
        self._instance_keypair: KeyPair | None = None

    # ------------------------------------------------------------------ Instance

    def instance_keypair(self) -> KeyPair:
        """Return the instance keypair, loading from disk or creating if missing."""
        if self._instance_keypair is not None:
            return self._instance_keypair
        if self._instance_key_path.exists():
            seed = self._instance_key_path.read_bytes()
            if len(seed) != 32:
                raise KeyStoreError(
                    f"instance key at {self._instance_key_path} is corrupt (expected 32 bytes, got {len(seed)})"
                )
            priv = load_private_key(seed)
            pub = priv.public_key()
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            pub_bytes = pub.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
            self._instance_keypair = KeyPair(private_seed=seed, public_bytes=pub_bytes)
            return self._instance_keypair
        # Create new keypair on first run.
        keypair = generate_keypair()
        self._persist_instance_key(keypair)
        self._instance_keypair = keypair
        return keypair

    def rotate_instance_keypair(self) -> KeyPair:
        """Generate a new instance keypair, archiving the old one.

        Callers must republish AgentFacts after rotating — the DID changes.
        The previous key, if any, is renamed with a ``.rotated.<epoch>`` suffix
        so rollback is possible.
        """
        if self._instance_key_path.exists():
            archive_path = self._instance_key_path.with_suffix(f".rotated.{int(_monotonic_ns())}")
            self._instance_key_path.rename(archive_path)
        keypair = generate_keypair()
        self._persist_instance_key(keypair)
        self._instance_keypair = keypair
        return keypair

    # ------------------------------------------------------------------ Agents

    def wrap_agent_seed(self, raw_seed: bytes) -> bytes:
        """Encrypt an agent's 32-byte private seed for at-rest storage."""
        if len(raw_seed) != 32:
            raise KeyStoreError("agent private seed must be exactly 32 bytes")
        if self._kek is None:
            raise KeyStoreError(f"no KEK configured; set {_ENV_KEY_ENCRYPTION_KEY} or pass one in")
        nonce = secrets.token_bytes(_NONCE_SIZE)
        cipher = ChaCha20Poly1305(self._kek)
        ct = cipher.encrypt(nonce, raw_seed, associated_data=None)
        return nonce + ct

    def unwrap_agent_seed(self, wrapped: bytes) -> bytes:
        """Decrypt an agent's private seed stored via :meth:`wrap_agent_seed`."""
        if len(wrapped) < _NONCE_SIZE + 16:  # nonce + minimum GCM tag
            raise KeyStoreError("wrapped agent seed is truncated")
        if self._kek is None:
            raise KeyStoreError(f"no KEK configured; set {_ENV_KEY_ENCRYPTION_KEY} or pass one in")
        nonce, ct = wrapped[:_NONCE_SIZE], wrapped[_NONCE_SIZE:]
        cipher = ChaCha20Poly1305(self._kek)
        try:
            return cipher.decrypt(nonce, ct, associated_data=None)
        except Exception as exc:
            raise KeyStoreError(f"agent seed decryption failed: {exc}") from exc

    def generate_agent_keypair(self, agent_id: str) -> AgentKeyPair:
        """Generate a fresh keypair bound to an agent identifier.

        The caller is responsible for persisting ``wrap_agent_seed(kp.keypair.private_seed)``
        into the agent row — this method does no DB IO itself.
        """
        kp = generate_keypair()
        return AgentKeyPair(agent_id=agent_id, keypair=kp)

    # ------------------------------------------------------------------ Internals

    def _persist_instance_key(self, keypair: KeyPair) -> None:
        self._instance_key_path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: tmp + rename.
        tmp = self._instance_key_path.with_suffix(".tmp")
        tmp.write_bytes(keypair.private_seed)
        os.chmod(tmp, 0o600)
        tmp.rename(self._instance_key_path)


def _load_kek_from_env() -> bytes | None:
    raw = os.getenv(_ENV_KEY_ENCRYPTION_KEY)
    if not raw:
        return None
    try:
        kek = bytes.fromhex(raw)
    except ValueError as exc:
        raise KeyStoreError(f"{_ENV_KEY_ENCRYPTION_KEY} must be a 64-char hex string (32 bytes)") from exc
    if len(kek) != 32:
        raise KeyStoreError(f"{_ENV_KEY_ENCRYPTION_KEY} must decode to exactly 32 bytes (got {len(kek)})")
    return kek


def _monotonic_ns() -> int:
    import time

    return time.monotonic_ns()
