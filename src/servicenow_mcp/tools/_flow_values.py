"""Pure decoder for Flow Designer ``values`` blobs.

Action instances, logic instances, and triggers in the V2 flow tables
(``sys_hub_action_instance_v2``, ``sys_hub_flow_logic_instance_v2``,
``sys_hub_trigger_instance_v2``) store their input bindings in a single
``values`` column as a gzip-compressed, base64-encoded JSON document.

This module provides a stateless decoder. It performs no I/O and holds no
state. Callers decide when (and whether) to decode.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any


# Base64 prefix produced by the gzip magic bytes (``1f 8b 08 ...``). Cheap
# heuristic to distinguish plain string values from compressed payloads.
_GZIP_BASE64_MAGIC: str = "H4sIA"

# Decompression-bomb guards. Legitimate Flow Designer ``values`` blobs are
# typically a few KB; these caps are deliberately generous but bounded so a
# malicious row (or attacker-supplied ``decode_values`` argument) cannot OOM
# the server. The base64-decoded wire payload is rejected before allocating
# the decompressor; the decompressed output is bounded via ``decompressobj``.
MAX_COMPRESSED_BYTES: int = 1 * 1024 * 1024
MAX_DECOMPRESSED_BYTES: int = 4 * 1024 * 1024

# ``zlib.MAX_WBITS | 16`` selects gzip framing (RFC 1952) inside zlib.
_GZIP_WBITS: int = zlib.MAX_WBITS | 16


def looks_compressed(value: str) -> bool:
    """Return True if *value* looks like a gzip+base64 payload.

    Pure prefix check, no decoding. Used to avoid attempting to decode
    plain-string ``values`` columns (e.g. ones that hold a raw scalar).
    """
    if not isinstance(value, str):
        return False
    stripped = value.lstrip()
    return stripped.startswith(_GZIP_BASE64_MAGIC)


def decode_values(compressed: str) -> list[Any] | dict[str, Any]:
    """Decode a gzip+base64+JSON ``values`` blob.

    Pipeline: locate the base64 payload (skipping any leading whitespace or
    short internal header) -> ``base64.b64decode`` -> ``gzip.decompress`` ->
    ``json.loads``.

    Raises:
        ValueError: When *compressed* is empty, not valid base64, not valid
            gzip data, or does not decompress to valid JSON. The message
            includes the underlying cause for diagnostics.
    """
    if not compressed or not compressed.strip():
        raise ValueError("empty values blob")

    payload = compressed.lstrip()

    # Some rows store a short internal header before the base64 body. The
    # gzip magic always renders as ``H4sIA`` in standard base64; if we see
    # it past position 0, slice to that point. Otherwise decode as-is and
    # let base64 surface the error.
    magic_at = payload.find(_GZIP_BASE64_MAGIC)
    if magic_at > 0:
        payload = payload[magic_at:]

    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        # binascii.Error is a subclass of ValueError; ValueError alone covers it.
        raise ValueError(f"not valid base64: {exc}") from exc

    if len(raw) > MAX_COMPRESSED_BYTES:
        raise ValueError(
            f"compressed payload exceeds maximum allowed size ({len(raw)} > {MAX_COMPRESSED_BYTES} bytes)",
        )

    decompressor = zlib.decompressobj(wbits=_GZIP_WBITS)
    try:
        decompressed = decompressor.decompress(raw, MAX_DECOMPRESSED_BYTES + 1)
    except zlib.error as exc:
        raise ValueError(f"not valid gzip data: {exc}") from exc

    if len(decompressed) > MAX_DECOMPRESSED_BYTES or decompressor.unconsumed_tail:
        raise ValueError(
            f"decompressed payload exceeds maximum allowed size (> {MAX_DECOMPRESSED_BYTES} bytes)",
        )

    try:
        decoded = json.loads(decompressed.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"decompressed payload is not valid JSON: {exc}") from exc

    if not isinstance(decoded, list | dict):
        raise ValueError(f"decoded payload is not a list or dict (got {type(decoded).__name__})")

    return decoded
