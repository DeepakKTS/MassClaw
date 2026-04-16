"""Unit tests for deterministic JSON canonicalisation."""

from __future__ import annotations

import math

import pytest

from app.identity.canonicalize import CanonicalizationError, canonicalize


class TestCanonicalPrimitives:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, b"null"),
            (True, b"true"),
            (False, b"false"),
            (0, b"0"),
            (-1, b"-1"),
            (42, b"42"),
            (2**63, b"9223372036854775808"),
        ],
    )
    def test_primitives(self, value: object, expected: bytes) -> None:
        assert canonicalize(value) == expected

    def test_floats_that_are_integers_emit_integer_form(self) -> None:
        assert canonicalize(1.0) == b"1"

    def test_non_integer_floats_round_trip(self) -> None:
        out = canonicalize(1.5)
        assert out == b"1.5"

    def test_nan_is_rejected(self) -> None:
        with pytest.raises(CanonicalizationError):
            canonicalize(math.nan)

    def test_infinity_is_rejected(self) -> None:
        with pytest.raises(CanonicalizationError):
            canonicalize(math.inf)


class TestStringEscaping:
    def test_plain_string(self) -> None:
        assert canonicalize("hi") == b'"hi"'

    def test_double_quote_escaped(self) -> None:
        assert canonicalize('a"b') == b'"a\\"b"'

    def test_backslash_escaped(self) -> None:
        assert canonicalize("a\\b") == b'"a\\\\b"'

    def test_control_characters(self) -> None:
        assert canonicalize("\n") == b'"\\n"'
        assert canonicalize("\t") == b'"\\t"'
        assert canonicalize("\r") == b'"\\r"'
        assert canonicalize("\x01") == b'"\\u0001"'

    def test_unicode_passthrough(self) -> None:
        assert canonicalize("héllo") == '"héllo"'.encode()


class TestArrayAndObject:
    def test_array(self) -> None:
        assert canonicalize([1, 2, 3]) == b"[1,2,3]"

    def test_nested(self) -> None:
        assert canonicalize({"a": [1, {"b": True}]}) == b'{"a":[1,{"b":true}]}'

    def test_object_keys_are_sorted(self) -> None:
        assert canonicalize({"b": 1, "a": 2}) == b'{"a":2,"b":1}'

    def test_key_ordering_is_deterministic_across_calls(self) -> None:
        d = {"delta": 4, "alpha": 1, "charlie": 3, "bravo": 2}
        a = canonicalize(d)
        b = canonicalize({"alpha": 1, "bravo": 2, "charlie": 3, "delta": 4})
        assert a == b

    def test_non_string_keys_rejected(self) -> None:
        with pytest.raises(CanonicalizationError):
            canonicalize({1: "a"})

    def test_tuples_treated_as_arrays(self) -> None:
        assert canonicalize((1, 2)) == b"[1,2]"

    def test_unsupported_type_rejected(self) -> None:
        with pytest.raises(CanonicalizationError):
            canonicalize(object())
