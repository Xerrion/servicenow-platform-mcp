"""Utility functions for correlation IDs, response formatting and query building."""

import re
import uuid
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+$")


def validate_identifier(name: str) -> None:
    """Raise ValueError if *name* is not a valid ServiceNow identifier.

    ServiceNow table and field names consist only of lowercase
    alphanumerics and underscores (``[a-z0-9_]+``).
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid identifier: {name!r}. Only lowercase alphanumeric characters and underscores are allowed."
        )


def sanitize_query_value(value: str) -> str:
    """Escape special encoded-query delimiters in a user-supplied value.

    ServiceNow uses ``^`` as the condition separator in encoded queries.
    A literal caret inside a *value* is represented as ``^^``.
    """
    return value.replace("^", "^^")


def generate_correlation_id() -> str:
    """Generate a unique correlation ID for request tracing."""
    return str(uuid.uuid4())


def format_response(
    data: Any,
    correlation_id: str,
    status: str = "success",
    error: str | None = None,
    pagination: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a standardized response envelope."""
    response: dict[str, Any] = {
        "correlation_id": correlation_id,
        "status": status,
        "data": data,
    }
    if error is not None:
        response["error"] = error
    if pagination is not None:
        response["pagination"] = pagination
    if warnings is not None:
        response["warnings"] = warnings
    return response


def build_encoded_query(conditions: dict[str, str] | str) -> str:
    """Convert a dict of conditions to a ServiceNow encoded query string.

    If a string is passed, it is returned unchanged.
    """
    if isinstance(conditions, str):
        return conditions
    if not conditions:
        return ""
    return "^".join(f"{key}={value}" for key, value in conditions.items())
