"""Base58 (Bitcoin alphabet) encode/decode.

Tiny pure-Python implementation so the identity module has no runtime
dependency on ``base58`` the PyPI package. The output is byte-identical to
Bitcoin base58 for the same inputs.
"""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ALPHABET_MAP = {c: i for i, c in enumerate(_ALPHABET)}


def b58encode(data: bytes) -> str:
    """Encode bytes to a base58btc string."""
    if not data:
        return ""
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break

    num = int.from_bytes(data, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _ALPHABET[rem] + out

    return (_ALPHABET[0] * leading_zeros) + out


def b58decode(encoded: str) -> bytes:
    """Decode a base58btc string to bytes."""
    if not encoded:
        return b""

    leading_ones = 0
    for ch in encoded:
        if ch == _ALPHABET[0]:
            leading_ones += 1
        else:
            break

    num = 0
    for ch in encoded:
        if ch not in _ALPHABET_MAP:
            raise ValueError(f"invalid base58 character: {ch!r}")
        num = num * 58 + _ALPHABET_MAP[ch]

    body = b"" if num == 0 else num.to_bytes((num.bit_length() + 7) // 8, "big")
    return b"\x00" * leading_ones + body
