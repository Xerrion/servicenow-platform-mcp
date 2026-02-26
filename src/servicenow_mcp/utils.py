"""Utility functions for correlation IDs, response formatting and query building."""

import re
import uuid
import warnings
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+$")

# Operators recognised by ``or_condition()``.
_ALLOWED_OPERATORS: frozenset[str] = frozenset(
    {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "CONTAINS",
        "STARTSWITH",
        "LIKE",
        "ISEMPTY",
        "ISNOTEMPTY",
        "IN",
        "NOT IN",
    }
)


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

    .. deprecated::
        Use :class:`ServiceNowQuery` instead.  This helper performs no
        field-name validation and only basic value sanitization.
    """
    warnings.warn(
        "build_encoded_query() is deprecated — use ServiceNowQuery instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if isinstance(conditions, str):
        return conditions
    if not conditions:
        return ""
    return "^".join(f"{key}={sanitize_query_value(value)}" for key, value in conditions.items())


class ServiceNowQuery:
    """Fluent builder for ServiceNow encoded query strings."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    # --- Comparison operators ---

    def equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}={value}")
        return self

    def not_equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field!=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}!={value}")
        return self

    def greater_than(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field>value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}>{value}")
        return self

    def greater_or_equal(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field>=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}>={value}")
        return self

    def less_than(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field<value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}<{value}")
        return self

    def less_or_equal(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field<=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}<={value}")
        return self

    # --- String operators ---

    def contains(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldCONTAINSvalue`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}CONTAINS{value}")
        return self

    def starts_with(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldSTARTSWITHvalue`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}STARTSWITH{value}")
        return self

    def like(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldLIKEvalue`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}LIKE{value}")
        return self

    # --- Null operators ---

    def is_empty(self, field: str) -> "ServiceNowQuery":
        """Add ``fieldISEMPTY`` condition."""
        validate_identifier(field)
        self._parts.append(f"{field}ISEMPTY")
        return self

    def is_not_empty(self, field: str) -> "ServiceNowQuery":
        """Add ``fieldISNOTEMPTY`` condition."""
        validate_identifier(field)
        self._parts.append(f"{field}ISNOTEMPTY")
        return self

    # --- GlideSystem time filters (server-side, timezone-correct) ---

    def hours_ago(self, field: str, hours: int) -> "ServiceNowQuery":
        """Add ``field>=javascript:gs.hoursAgoStart(hours)`` condition.

        Args:
            field: Field name (validated as identifier).
            hours: Number of hours, 1-8760 (1 year).
        """
        validate_identifier(field)
        hours = int(hours)
        if not (1 <= hours <= 8760):
            raise ValueError(f"hours must be between 1 and 8760, got {hours}")
        self._parts.append(f"{field}>=javascript:gs.hoursAgoStart({hours})")
        return self

    def minutes_ago(self, field: str, minutes: int) -> "ServiceNowQuery":
        """Add ``field>=javascript:gs.minutesAgoStart(minutes)`` condition.

        Args:
            field: Field name (validated as identifier).
            minutes: Number of minutes, 1-525600 (1 year).
        """
        validate_identifier(field)
        minutes = int(minutes)
        if not (1 <= minutes <= 525600):
            raise ValueError(f"minutes must be between 1 and 525600, got {minutes}")
        self._parts.append(f"{field}>=javascript:gs.minutesAgoStart({minutes})")
        return self

    def days_ago(self, field: str, days: int) -> "ServiceNowQuery":
        """Add ``field>=javascript:gs.daysAgoStart(days)`` condition.

        Args:
            field: Field name (validated as identifier).
            days: Number of days, 1-365.
        """
        validate_identifier(field)
        days = int(days)
        if not (1 <= days <= 365):
            raise ValueError(f"days must be between 1 and 365, got {days}")
        self._parts.append(f"{field}>=javascript:gs.daysAgoStart({days})")
        return self

    def older_than_days(self, field: str, days: int) -> "ServiceNowQuery":
        """Add ``field<=javascript:gs.daysAgoEnd(days)`` condition for records before the cutoff.

        Args:
            field: Field name (validated as identifier).
            days: Number of days, 1-3650 (10 years).
        """
        validate_identifier(field)
        days = int(days)
        if not (1 <= days <= 3650):
            raise ValueError(f"days must be between 1 and 3650, got {days}")
        self._parts.append(f"{field}<=javascript:gs.daysAgoEnd({days})")
        return self

    # --- IN / NOT IN operators ---

    def in_list(self, field: str, values: list[str]) -> "ServiceNowQuery":
        """Add ``fieldINvalue1,value2,...`` condition.

        Args:
            field: Field name (validated as identifier).
            values: List of values; each is sanitized individually.
        """
        validate_identifier(field)
        sanitized = ",".join(sanitize_query_value(v) for v in values)
        self._parts.append(f"{field}IN{sanitized}")
        return self

    def not_in_list(self, field: str, values: list[str]) -> "ServiceNowQuery":
        """Add ``fieldNOT INvalue1,value2,...`` condition.

        Args:
            field: Field name (validated as identifier).
            values: List of values; each is sanitized individually.
        """
        validate_identifier(field)
        sanitized = ",".join(sanitize_query_value(v) for v in values)
        self._parts.append(f"{field}NOT IN{sanitized}")
        return self

    # --- OR conditions ---

    def or_condition(self, field: str, operator: str, value: str) -> "ServiceNowQuery":
        """Append an OR condition: ``^ORfield<OPERATOR>value``.

        Args:
            field: Field name (validated as identifier).
            operator: One of the allowed ServiceNow operators (e.g. ``=``, ``!=``, ``CONTAINS``).
            value: Condition value (sanitized).
        """
        validate_identifier(field)
        if operator not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unknown operator: {operator!r}. Allowed: {sorted(_ALLOWED_OPERATORS)}")
        value = sanitize_query_value(value)
        self._parts.append(f"^OR{field}{operator}{value}")
        return self

    def or_equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Convenience: append ``^ORfield=value``.

        Args:
            field: Field name (validated as identifier).
            value: Condition value (sanitized).
        """
        return self.or_condition(field, "=", value)

    def or_starts_with(self, field: str, value: str) -> "ServiceNowQuery":
        """Convenience: append ``^ORfieldSTARTSWITHvalue``.

        Args:
            field: Field name (validated as identifier).
            value: Condition value (sanitized).
        """
        return self.or_condition(field, "STARTSWITH", value)

    # --- Ordering ---

    def order_by(self, field: str, descending: bool = False) -> "ServiceNowQuery":
        """Append an ``ORDERBY`` or ``ORDERBYDESC`` directive.

        Args:
            field: Field name (validated as identifier).
            descending: If ``True`` use ``ORDERBYDESC``, otherwise ``ORDERBY``.
        """
        validate_identifier(field)
        prefix = "ORDERBYDESC" if descending else "ORDERBY"
        self._parts.append(f"{prefix}{field}")
        return self

    # --- Raw fragment ---

    def raw(self, fragment: str) -> "ServiceNowQuery":
        """Append a raw encoded query fragment.

        .. warning::

            This method performs **no** validation or sanitization.
            Only use it for trusted, pre-validated query fragments.
            Prefer the typed builder methods whenever possible to
            ensure field-name validation and value escaping.
        """
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
