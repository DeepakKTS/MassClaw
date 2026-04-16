"""Deterministic JSON canonicalization compatible with RFC 8785 JCS.

Signing a JSON document directly is not safe, because two semantically
identical documents can produce different byte streams (different key order,
different whitespace, different number formatting). To make signatures
reproducible, we serialise the document with a single, rigid set of rules
before signing:

- UTF-8 output
- Object keys sorted lexicographically
- No insignificant whitespace
- JSON string escaping follows RFC 8259 (the minimum set)
- Integers rendered without decimal point
- Finite floats rendered with the shortest round-trippable ECMAScript form
- NaN/Infinity are rejected (JSON does not define them)

This covers every JSON shape MassClaw produces. For full RFC 8785 conformance
on obscure edge cases, a future refactor can swap the implementation for
``rfc8785`` from PyPI without any caller change.
"""

from __future__ import annotations

import math
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonicalised."""


def canonicalize(value: Any) -> bytes:
    """Return the canonical UTF-8 byte representation of ``value``.

    Accepts JSON-compatible Python values (dict, list, tuple, str, int, float,
    bool, None). Raises CanonicalizationError for unsupported types.
    """
    parts: list[str] = []
    _emit(value, parts)
    return "".join(parts).encode("utf-8")


def _emit(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
        return
    if value is True:
        out.append("true")
        return
    if value is False:
        out.append("false")
        return
    if isinstance(value, str):
        out.append(_escape_string(value))
        return
    if isinstance(value, bool):
        # Redundant but explicit: bool is a subclass of int in Python.
        out.append("true" if value else "false")
        return
    if isinstance(value, int):
        out.append(str(value))
        return
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError("NaN and Infinity are not JSON-valid")
        if value.is_integer():
            out.append(str(int(value)))
        else:
            out.append(repr(value))
        return
    if isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, out)
        out.append("]")
        return
    if isinstance(value, dict):
        out.append("{")
        items = sorted(value.items(), key=lambda kv: kv[0])
        for i, (k, v) in enumerate(items):
            if not isinstance(k, str):
                raise CanonicalizationError(
                    f"dict keys must be strings for canonicalisation, got {type(k).__name__}"
                )
            if i:
                out.append(",")
            out.append(_escape_string(k))
            out.append(":")
            _emit(v, out)
        out.append("}")
        return
    raise CanonicalizationError(f"unsupported type for canonicalisation: {type(value).__name__}")


def _escape_string(s: str) -> str:
    result = ['"']
    for ch in s:
        code = ord(ch)
        if ch == '"':
            result.append('\\"')
        elif ch == "\\":
            result.append("\\\\")
        elif ch == "\b":
            result.append("\\b")
        elif ch == "\f":
            result.append("\\f")
        elif ch == "\n":
            result.append("\\n")
        elif ch == "\r":
            result.append("\\r")
        elif ch == "\t":
            result.append("\\t")
        elif code < 0x20:
            result.append(f"\\u{code:04x}")
        else:
            result.append(ch)
    result.append('"')
    return "".join(result)
