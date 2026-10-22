"""Bounds for complete Studio definitions, separate from caller input limits."""

from typing import Any

from servicenow_mcp.capabilities.contracts import validate_payload

# Complete definitions include embedded schemas and metadata for every step.
# Match the existing decoded flow-value bound, not the caller's 256 KiB bound.
MAX_STUDIO_PAYLOAD_BYTES = 4 * 1024 * 1024


def validate_definition(
    definition: dict[str, Any], *, field_name: str = "flow"
) -> dict[str, Any]:
    return validate_payload(
        definition,
        field_name=field_name,
        max_bytes=MAX_STUDIO_PAYLOAD_BYTES,
        validate_keys=False,
    )
