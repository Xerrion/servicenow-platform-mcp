"""Shared payload-parsing helpers for tool input JSON."""

from __future__ import annotations

import json
import logging
from typing import Any

from servicenow_mcp.utils import format_response, validate_identifier


logger = logging.getLogger(__name__)

MAX_JSON_PAYLOAD_BYTES = 256 * 1024
MAX_JSON_DEPTH = 32


def parse_payload_json(
    raw: str,
    *,
    field_name: str,
    correlation_id: str,
    max_bytes: int = MAX_JSON_PAYLOAD_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    validate_keys: bool = True,
) -> dict[str, Any] | str:
    """Parse a caller-supplied JSON object payload.

    Returns the parsed dict on success, or a serialized error envelope (str) on failure.
    Callers should check ``isinstance(result, str)`` and return it directly to MCP.

    Args:
        raw: The raw JSON string from the caller.
        field_name: The parameter name for error messages (e.g. "data", "changes").
        correlation_id: The tool's correlation_id for the error envelope.
        max_bytes: Max byte length of ``raw`` (defaults to 256 KiB).
        max_depth: Max nesting depth of the parsed structure.
        validate_keys: If True, every top-level key must satisfy ``validate_identifier``.
    """
    if len(raw) > max_bytes:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"{field_name} exceeds maximum size of {max_bytes} bytes",
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"{field_name} is not valid JSON: {e.msg}",
        )
    if not isinstance(parsed, dict):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"{field_name} must be a JSON object",
        )
    if _depth(parsed, max_depth=max_depth) > max_depth:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"{field_name} exceeds maximum nesting depth of {max_depth}",
        )
    if validate_keys:
        for key in parsed:
            try:
                validate_identifier(key)
            except ValueError as e:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid key in {field_name}: {e}",
                )
    return parsed


def _depth(obj: Any, current: int = 1, max_depth: int = MAX_JSON_DEPTH) -> int:
    """Compute structural depth, short-circuiting once max_depth is exceeded.

    Short-circuiting is critical: an adversarial payload with thousands of
    nesting levels (still under the byte cap) would otherwise hit Python's
    recursion limit and raise RecursionError before the depth guard runs.
    """
    if current > max_depth:
        return current
    if isinstance(obj, dict):
        if not obj:
            return current
        return max(_depth(v, current + 1, max_depth) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return current
        return max(_depth(v, current + 1, max_depth) for v in obj)
    return current
