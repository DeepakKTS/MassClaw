"""Unit tests for KeyStore persistence and at-rest encryption of agent keys."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.identity.key_store import KeyStore, KeyStoreError


class TestInstanceKeyPersistence:
    def test_creates_new_key_on_first_call(self, tmp_path: Path) -> None:
        path = tmp_path / "instance.key"
        store = KeyStore(instance_key_path=path)
        kp = store.instance_keypair()
        assert path.exists()
        assert len(kp.private_seed) == 32

    def test_second_call_returns_same_key(self, tmp_path: Path) -> None:
        path = tmp_path / "instance.key"
        store = KeyStore(instance_key_path=path)
        kp1 = store.instance_keypair()
        kp2 = store.instance_keypair()
        assert kp1.private_seed == kp2.private_seed
        assert kp1.public_bytes == kp2.public_bytes

    def test_new_store_reloads_same_key(self, tmp_path: Path) -> None:
        path = tmp_path / "instance.key"
        kp = KeyStore(instance_key_path=path).instance_keypair()
        kp_reload = KeyStore(instance_key_path=path).instance_keypair()
        assert kp.private_seed == kp_reload.private_seed
        assert kp.public_bytes == kp_reload.public_bytes

    def test_corrupt_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "instance.key"
        path.write_bytes(b"\x00" * 10)
        with pytest.raises(KeyStoreError):
            KeyStore(instance_key_path=path).instance_keypair()

    def test_rotate_archives_previous_key(self, tmp_path: Path) -> None:
        path = tmp_path / "instance.key"
        store = KeyStore(instance_key_path=path)
        original = store.instance_keypair()
        rotated = store.rotate_instance_keypair()
        assert rotated.private_seed != original.private_seed
        archived = list(tmp_path.glob("instance.rotated.*"))
        assert len(archived) == 1


class TestAgentKeyWrapping:
    def _kek(self) -> bytes:
        return bytes.fromhex("00" * 32)

    def test_wrap_unwrap_roundtrip(self, tmp_path: Path) -> None:
        store = KeyStore(
            instance_key_path=tmp_path / "instance.key",
            key_encryption_key=self._kek(),
        )
        seed = b"\x11" * 32
        wrapped = store.wrap_agent_seed(seed)
        assert wrapped != seed
        assert store.unwrap_agent_seed(wrapped) == seed

    def test_wrap_different_each_time(self, tmp_path: Path) -> None:
        store = KeyStore(
            instance_key_path=tmp_path / "instance.key",
            key_encryption_key=self._kek(),
        )
        seed = b"\x22" * 32
        w1 = store.wrap_agent_seed(seed)
        w2 = store.wrap_agent_seed(seed)
        # Nonce is random so ciphertext should differ even for the same plaintext.
        assert w1 != w2
        assert store.unwrap_agent_seed(w1) == seed
        assert store.unwrap_agent_seed(w2) == seed

    def test_rejects_wrong_length_seed(self, tmp_path: Path) -> None:
        store = KeyStore(
            instance_key_path=tmp_path / "instance.key",
            key_encryption_key=self._kek(),
        )
        with pytest.raises(KeyStoreError):
            store.wrap_agent_seed(b"\x00" * 16)

    def test_without_kek_raises(self, tmp_path: Path) -> None:
        store = KeyStore(instance_key_path=tmp_path / "instance.key", key_encryption_key=None)
        with pytest.raises(KeyStoreError):
            store.wrap_agent_seed(b"\x00" * 32)

    def test_tampered_wrapper_raises(self, tmp_path: Path) -> None:
        store = KeyStore(
            instance_key_path=tmp_path / "instance.key",
            key_encryption_key=self._kek(),
        )
        seed = b"\x33" * 32
        wrapped = bytearray(store.wrap_agent_seed(seed))
        wrapped[-1] ^= 0x01  # flip a byte in the ciphertext.
        with pytest.raises(KeyStoreError):
            store.unwrap_agent_seed(bytes(wrapped))


class TestGenerateAgentKeyPair:
    def test_generates_fresh_keypair(self, tmp_path: Path) -> None:
        store = KeyStore(instance_key_path=tmp_path / "instance.key")
        kp = store.generate_agent_keypair("agent-123")
        assert kp.agent_id == "agent-123"
        assert len(kp.keypair.private_seed) == 32
