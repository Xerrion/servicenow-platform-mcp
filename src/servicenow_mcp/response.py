"""MCP response formatting and serialization."""

import logging
from typing import Any

from servicenow_mcp._json import JSON
from servicenow_mcp.sentry import capture_exception as sentry_capture


logger = logging.getLogger(__name__)

# Exact legacy bytes: clients may match this failure envelope literally.
SERIALIZATION_FAILED = '{"status": "error", "error": {"message": "Serialization failed"}}'


def serialize(data: Any) -> str:
    """Serialize *data* to a compact UTF-8 JSON string suitable for MCP tool output.

    Pydantic native types (datetime, date, UUID, Decimal) use ISO/JSON forms.
    Types with no JSON form fall back to ``str()``.
    """
    try:
        return JSON.dump_json(data, fallback=str).decode("utf-8")
    except (TypeError, ValueError) as e:
        logger.warning("JSON serialization failed", exc_info=True)
        sentry_capture(e)
        return SERIALIZATION_FAILED


def format_response(
    data: Any,
    status: str = "success",
    error: str | dict[str, str] | None = None,
    pagination: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    truncation: dict[str, Any] | None = None,
) -> str:
    """Build and serialize a standardized response envelope.

    The *error* field accepts a plain string or a structured dict. Empty
    warning lists and empty truncation details are omitted. Supplied data
    and pagination are preserved.
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
    if truncation:
        response["truncation"] = truncation

    return serialize(response)
