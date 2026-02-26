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


class ServiceNowQuery:
    """Fluent builder for ServiceNow encoded query strings."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    # --- Comparison operators ---

    def equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field=value condition."""
        self._parts.append(f"{field}={value}")
        return self

    def not_equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field!=value condition."""
        self._parts.append(f"{field}!={value}")
        return self

    def greater_than(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field>value condition."""
        self._parts.append(f"{field}>{value}")
        return self

    def greater_or_equal(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field>=value condition."""
        self._parts.append(f"{field}>={value}")
        return self

    def less_than(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field<value condition."""
        self._parts.append(f"{field}<{value}")
        return self

    def less_or_equal(self, field: str, value: str) -> "ServiceNowQuery":
        """Add field<=value condition."""
        self._parts.append(f"{field}<={value}")
        return self

    # --- String operators ---

    def contains(self, field: str, value: str) -> "ServiceNowQuery":
        """Add fieldCONTAINSvalue condition."""
        self._parts.append(f"{field}CONTAINS{value}")
        return self

    def starts_with(self, field: str, value: str) -> "ServiceNowQuery":
        """Add fieldSTARTSWITHvalue condition."""
        self._parts.append(f"{field}STARTSWITH{value}")
        return self

    def like(self, field: str, value: str) -> "ServiceNowQuery":
        """Add fieldLIKEvalue condition."""
        self._parts.append(f"{field}LIKE{value}")
        return self

    # --- Null operators ---

    def is_empty(self, field: str) -> "ServiceNowQuery":
        """Add fieldISEMPTY condition."""
        self._parts.append(f"{field}ISEMPTY")
        return self

    def is_not_empty(self, field: str) -> "ServiceNowQuery":
        """Add fieldISNOTEMPTY condition."""
        self._parts.append(f"{field}ISNOTEMPTY")
        return self

    # --- GlideSystem time filters (server-side, timezone-correct) ---

    def hours_ago(self, field: str, hours: int) -> "ServiceNowQuery":
        """Add field>=javascript:gs.hoursAgoStart(hours) condition."""
        self._parts.append(f"{field}>=javascript:gs.hoursAgoStart({hours})")
        return self

    def minutes_ago(self, field: str, minutes: int) -> "ServiceNowQuery":
        """Add field>=javascript:gs.minutesAgoStart(minutes) condition."""
        self._parts.append(f"{field}>=javascript:gs.minutesAgoStart({minutes})")
        return self

    def days_ago(self, field: str, days: int) -> "ServiceNowQuery":
        """Add field>=javascript:gs.daysAgoStart(days) condition."""
        self._parts.append(f"{field}>=javascript:gs.daysAgoStart({days})")
        return self

    def older_than_days(self, field: str, days: int) -> "ServiceNowQuery":
        """Add field<=javascript:gs.daysAgoEnd(days) condition for records before the cutoff."""
        self._parts.append(f"{field}<=javascript:gs.daysAgoEnd({days})")
        return self

    # --- Raw fragment ---

    def raw(self, fragment: str) -> "ServiceNowQuery":
        """Append a raw encoded query fragment."""
        if fragment:
            self._parts.append(fragment)
        return self

    # --- Build ---

    def build(self) -> str:
        """Return the joined encoded query string."""
        return "^".join(self._parts)

    def __str__(self) -> str:
        """Return the built query string."""
        return self.build()
