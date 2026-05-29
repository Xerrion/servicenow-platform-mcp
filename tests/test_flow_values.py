"""Unit tests for the pure ``_flow_values`` decoder."""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any

import pytest

from servicenow_mcp.tools._flow_values import decode_values, looks_compressed


def _encode(payload: Any) -> str:
    """Helper: gzip+base64-encode *payload* the way ServiceNow does."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "x", "value": "y"}],
        [{"a": 1, "b": [1, 2, 3]}],
        {"top": "level", "nested": {"k": [1, 2]}},
    ],
)
def test_round_trip(payload: Any) -> None:
    """Encoding then decoding returns the original structure."""
    encoded = _encode(payload)
    assert decode_values(encoded) == payload


# ---------------------------------------------------------------------------
# looks_compressed
# ---------------------------------------------------------------------------


def test_looks_compressed_recognises_h4sia_prefix() -> None:
    assert looks_compressed("H4sIAAAAA...") is True


def test_looks_compressed_recognises_h4sia_after_leading_whitespace() -> None:
    """``lstrip``-then-prefix-check: leading spaces are tolerated."""
    assert looks_compressed("   H4sIAAAA") is True


def test_looks_compressed_returns_false_for_plain_string() -> None:
    assert looks_compressed("just a value") is False


def test_looks_compressed_returns_false_for_h4sia_in_middle() -> None:
    """Magic bytes only matter at the start; mid-string doesn't count."""
    assert looks_compressed("prefix_H4sIA") is False


def test_looks_compressed_returns_false_for_empty_string() -> None:
    assert looks_compressed("") is False


def test_looks_compressed_returns_false_for_non_string() -> None:
    # The function is typed for str but defends against non-string input.
    # Sentinels are typed ``Any`` so the type-checker is satisfied; Sonar's
    # S5655 still tracks the original ``str`` annotation, so we suppress per
    # call below to keep this deliberate misuse exercise green.
    none_sentinel: Any = None
    int_sentinel: Any = 123
    assert looks_compressed(none_sentinel) is False  # NOSONAR(S5655) deliberate non-str input
    assert looks_compressed(int_sentinel) is False  # NOSONAR(S5655) deliberate non-str input


# ---------------------------------------------------------------------------
# decode_values: prefix slicing
# ---------------------------------------------------------------------------


def test_decode_values_slices_from_h4sia_when_prefixed() -> None:
    """A short internal header before the base64 body is sliced off."""
    encoded = _encode([{"k": "v"}])
    prefixed = "someprefix" + encoded
    assert decode_values(prefixed) == [{"k": "v"}]


# ---------------------------------------------------------------------------
# decode_values: error normalization
# ---------------------------------------------------------------------------


def test_decode_values_malformed_base64_raises_value_error() -> None:
    """Non-base64 input surfaces as a ValueError mentioning base64."""
    # Starts with H4sIA so prefix-slice keeps it; but the body is not valid b64.
    bad = "H4sIA!!!not-base64!!!"
    with pytest.raises(ValueError, match="not valid base64"):
        decode_values(bad)


def test_decode_values_valid_base64_but_not_gzip_raises_value_error() -> None:
    """Valid base64 carrying a non-gzip payload is reported as 'not valid gzip data'."""
    # Plain base64 of non-gzip bytes; no gzip magic, so prefix-slice is a no-op
    # and the buffer goes straight to base64 -> gzip and fails at gzip.
    blob = base64.b64encode(b"this is not gzip at all, just bytes").decode("ascii")
    with pytest.raises(ValueError, match="not valid gzip data"):
        decode_values(blob)


def test_decode_values_valid_gzip_but_malformed_json_raises_value_error() -> None:
    """A gzip payload carrying bytes that are not JSON is rejected."""
    encoded = base64.b64encode(gzip.compress(b"this is not json {{{")).decode("ascii")
    with pytest.raises(ValueError, match="not valid JSON"):
        decode_values(encoded)


def test_decode_values_empty_string_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty values blob"):
        decode_values("")


def test_decode_values_whitespace_only_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty values blob"):
        decode_values("   \n\t  ")


def test_decode_values_no_h4sia_substring_still_attempted() -> None:
    """A string without the gzip magic prefix is still attempted - and fails cleanly.

    The decoder does not gate on the magic; it just slices when it appears
    past position 0. Inputs without it fall through to base64+gzip and
    surface a ValueError. The contract is 'never raises anything but
    ValueError on bad input'.
    """
    with pytest.raises(ValueError, match="not valid"):
        decode_values("definitely not encoded at all !!!")


def test_decode_values_decoded_scalar_rejected() -> None:
    """A decompressed JSON scalar (not list/dict) is rejected."""
    encoded = base64.b64encode(gzip.compress(b'"a string"')).decode("ascii")
    with pytest.raises(ValueError, match="not a list or dict"):
        decode_values(encoded)


def test_decode_values_gzip_with_non_utf8_bytes_raises_value_error() -> None:
    """Non-UTF-8 decompressed bytes are normalized to ValueError via the JSON-path."""
    # gzip output of arbitrary bytes naturally base64-encodes to a string
    # starting with the H4sIA magic, so no slicing is needed.
    encoded = base64.b64encode(gzip.compress(b"\xff\xfe\xfd")).decode("ascii")
    assert encoded.startswith("H4sIA")  # sanity: prefix scan is a no-op
    with pytest.raises(ValueError, match="not valid JSON"):
        decode_values(encoded)
