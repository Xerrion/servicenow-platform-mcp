"""Unit tests for the pure ``_flow_values`` decoder."""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any

import pytest

from servicenow_mcp.capabilities.flows import _flow_values
from servicenow_mcp.capabilities.flows._flow_values import (
    decode_values,
    looks_compressed,
)


def _encode(payload: Any) -> str:
    """Helper: gzip+base64-encode *payload* the way ServiceNow does."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


# ---------------------------------------------------------------------------
# looks_compressed
# ---------------------------------------------------------------------------


def test_looks_compressed_returns_false_for_non_string() -> None:
    # The function is typed for str but defends against non-string input.
    # Sentinels are typed ``Any`` so the type-checker is satisfied; Sonar's
    # S5655 still tracks the original ``str`` annotation, so we suppress per
    # call below to keep this deliberate misuse exercise green.
    none_sentinel: Any = None
    int_sentinel: Any = 123
    assert (
        looks_compressed(none_sentinel) is False
    )  # NOSONAR(S5655) deliberate non-str input
    assert (
        looks_compressed(int_sentinel) is False
    )  # NOSONAR(S5655) deliberate non-str input


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


def test_decode_values_valid_gzip_but_malformed_json_raises_value_error() -> None:
    """A gzip payload carrying bytes that are not JSON is rejected."""
    encoded = base64.b64encode(gzip.compress(b"this is not json {{{")).decode("ascii")
    with pytest.raises(ValueError, match="not valid JSON"):
        decode_values(encoded)


def test_decode_values_empty_string_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty values blob"):
        decode_values("")


def test_decode_values_decoded_scalar_rejected() -> None:
    """A decompressed JSON scalar (not list/dict) is rejected."""
    encoded = base64.b64encode(gzip.compress(b'"a string"')).decode("ascii")
    with pytest.raises(ValueError, match="not a list or dict"):
        decode_values(encoded)


# ---------------------------------------------------------------------------
# decode_values: decompression-bomb guards
# ---------------------------------------------------------------------------


def test_decode_values_rejects_oversize_compressed_payload() -> None:
    """A base64-decoded wire payload over MAX_COMPRESSED_BYTES is rejected.

    The reject path runs before any decompressor is allocated, so even a
    legitimate-looking gzip blob is refused if its compressed size exceeds
    the wire cap.
    """
    # Random bytes don't compress well; pad to exceed the 1 MiB compressed cap.
    big_blob = b"\x00" * (_flow_values.MAX_COMPRESSED_BYTES + 1024)
    encoded = base64.b64encode(big_blob).decode("ascii")
    with pytest.raises(ValueError, match="compressed payload exceeds maximum"):
        decode_values(encoded)


def test_decode_values_rejects_gzip_bomb() -> None:
    """A gzip payload that decompresses past MAX_DECOMPRESSED_BYTES is rejected.

    ``b"A" * (MAX_DECOMPRESSED_BYTES + 1024)`` compresses to a tiny base64
    blob but would expand past the cap; the decoder must reject without
    materializing the full output.
    """
    bomb_plain = b"A" * (_flow_values.MAX_DECOMPRESSED_BYTES + 1024)
    encoded = base64.b64encode(gzip.compress(bomb_plain)).decode("ascii")
    # Sanity: the compressed wire form is well under the wire cap, so the
    # reject path is the *decompression* cap, not the compressed-size cap.
    assert len(base64.b64decode(encoded)) < _flow_values.MAX_COMPRESSED_BYTES
    with pytest.raises(ValueError, match="decompressed payload exceeds maximum"):
        decode_values(encoded)
