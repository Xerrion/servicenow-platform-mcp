"""Payload parsing and XML validation for unified record writes."""

from __future__ import annotations

from typing import Any

from servicenow_mcp.response import format_response
from servicenow_mcp.tools._artifact import validate_ui_macro_xml
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.tools._record_write_validation import WriteRequest


def _error(message: str) -> str:
    return format_response(data=None, status="error", error=message)


async def prepare_write_payload(
    request: WriteRequest,
    dictionary: DictionaryRegistry,
) -> dict[str, Any] | str:
    """Parse a bounded field map and reject invalid XML before a write."""
    if request.action == "delete":
        return {}

    parsed = parse_payload_json(request.data, field_name="data")
    if isinstance(parsed, str):
        return parsed

    fields = await dictionary.get_fields(request.table, list(parsed))
    for field in fields:
        if field.internal_type != "xml":
            continue
        content = parsed[field.name]
        if not isinstance(content, str):
            return _error(f"XML field {field.name!r} must be a string.")
        xml_error = validate_ui_macro_xml(content)
        if xml_error:
            return _error(f"Field {field.name!r}: {xml_error}")
    return parsed
