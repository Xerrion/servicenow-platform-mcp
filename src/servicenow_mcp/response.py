"""MCP response formatting and serialization."""

import json
import logging
from typing import Any

from servicenow_mcp.sentry import capture_exception as sentry_capture


logger = logging.getLogger(__name__)


def serialize(data: Any) -> str:
    """Serialize *data* to a JSON string suitable for MCP tool output."""
    try:
        return json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        logger.warning("JSON serialization failed", exc_info=True)
        sentry_capture(e)
        envelope: dict[str, Any] = {"status": "error", "error": {"message": "Serialization failed"}}
        return json.dumps(envelope)


def format_response(
    data: Any,
    status: str = "success",
    error: str | dict[str, str] | None = None,
    pagination: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    selection: dict[str, Any] | None = None,
) -> str:
    """Build and serialize a standardized response envelope.

    The *error* field accepts a plain string or a structured dict. Empty
    warning lists are omitted. Supplied data and metadata are preserved.
    """
    response: dict[str, Any] = {
        "status": status,
        "data": data,
    }
    if error is not None:
        response["error"] = {"message": error} if isinstance(error, str) else error
    if pagination is not None:
        response["pagination"] = pagination
    if warnings:
        response["warnings"] = warnings
    if selection is not None:
        response["selection"] = selection

    return serialize(response)
