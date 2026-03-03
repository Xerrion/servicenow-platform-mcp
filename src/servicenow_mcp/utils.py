"""Utility functions for correlation IDs, response formatting and query building."""

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from toon_format import encode as toon_encode

from servicenow_mcp.errors import ForbiddenError

if TYPE_CHECKING:
    from servicenow_mcp.state import QueryTokenStore

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")

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
        "ENDSWITH",
        "NOT LIKE",
        "BETWEEN",
        "ANYTHING",
        "EMPTYSTRING",
        "GT_FIELD",
        "LT_FIELD",
        "GT_OR_EQUALS_FIELD",
        "LT_OR_EQUALS_FIELD",
        "SAMEAS",
        "NSAMEAS",
        "ON",
        "NOTON",
        "RELATIVEGT",
        "RELATIVELT",
        "MORETHAN",
        "DATEPART",
        "DYNAMIC",
        "IN_HIERARCHY",
        "VALCHANGES",
        "CHANGESFROM",
        "CHANGESTO",
    }
)


def validate_identifier(name: str) -> None:
    """Raise ValueError if *name* is not a valid ServiceNow identifier.

    ServiceNow field names consist of lowercase alphanumerics and
    underscores (``[a-z0-9_]+``).  Dot-walked references such as
    ``change_request.number`` or ``child.sys_id`` are also accepted
    (one or more segments separated by a single dot).
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid identifier: {name!r}. "
            "Only lowercase alphanumeric characters, underscores, and dot-walked segments are allowed."
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


def serialize(data: Any) -> str:
    """Serialize *data* to TOON format for LLM-friendly output.

    Falls back to JSON if TOON encoding fails.
    """
    try:
        return toon_encode(data)
    except Exception:
        logger.warning("TOON encoding failed, falling back to JSON", exc_info=True)
        return json.dumps(data, indent=2)


def format_response(
    data: Any,
    correlation_id: str,
    status: str = "success",
    error: str | None = None,
    pagination: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Build and serialize a standardized response envelope."""
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
    return serialize(response)


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

    def equals_if(self, field: str, value: str, condition: bool) -> "ServiceNowQuery":
        """Conditionally add an equals filter.

        Appends ``field=value`` only when *condition* is truthy,
        allowing fluent one-liner filters without external ``if`` guards.

        Args:
            field: The field name.
            value: The comparison value.
            condition: When truthy the filter is added; otherwise this is a no-op.
        """
        if condition:
            return self.equals(field, value)
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

    def ends_with(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldENDSWITHvalue`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}ENDSWITH{value}")
        return self

    def not_like(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldNOT LIKEvalue`` (does not contain) condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}NOT LIKE{value}")
        return self

    def does_not_contain(self, field: str, value: str) -> "ServiceNowQuery":
        """Alias for :meth:`not_like` -- ``fieldNOT LIKEvalue``."""
        return self.not_like(field, value)

    def between(self, field: str, start: str, end: str) -> "ServiceNowQuery":
        """Add ``fieldBETWEENstart@end`` condition.

        Used for date ranges and numeric ranges in ServiceNow.

        Args:
            field: The field name.
            start: Range start value (e.g. ``"2026-01-01"``).
            end: Range end value (e.g. ``"2026-12-31"``).
        """
        validate_identifier(field)
        start = sanitize_query_value(start)
        end = sanitize_query_value(end)
        self._parts.append(f"{field}BETWEEN{start}@{end}")
        return self

    def anything(self, field: str) -> "ServiceNowQuery":
        """Add ``fieldANYTHING`` condition (matches any value).

        Primarily used in notification filter conditions.
        """
        validate_identifier(field)
        self._parts.append(f"{field}ANYTHING")
        return self

    def empty_string(self, field: str) -> "ServiceNowQuery":
        """Add ``fieldEMPTYSTRING`` condition (matches empty string specifically).

        Unlike :meth:`is_empty` which matches NULL/missing values,
        this matches fields that contain an empty string ``""``.
        """
        validate_identifier(field)
        self._parts.append(f"{field}EMPTYSTRING")
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

    # --- Date/time operators ---

    def on(self, field: str, date_value: str) -> "ServiceNowQuery":
        """Add ``fieldONdate_value`` condition (exact date match).

        Args:
            field: A date/datetime field name.
            date_value: Date string (e.g. ``"2026-01-15"``).
        """
        validate_identifier(field)
        date_value = sanitize_query_value(date_value)
        self._parts.append(f"{field}ON{date_value}")
        return self

    def not_on(self, field: str, date_value: str) -> "ServiceNowQuery":
        """Add ``fieldNOTONdate_value`` condition (not on a specific date).

        Args:
            field: A date/datetime field name.
            date_value: Date string (e.g. ``"2026-01-15"``).
        """
        validate_identifier(field)
        date_value = sanitize_query_value(date_value)
        self._parts.append(f"{field}NOTON{date_value}")
        return self

    def relative_gt(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldRELATIVEGTvalue`` condition.

        Matches records where *field* is greater than a relative date.
        Value uses ServiceNow relative date syntax (e.g. ``"@year@ago@1"``).

        Args:
            field: A date/datetime field name.
            value: Relative date expression.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}RELATIVEGT{value}")
        return self

    def relative_lt(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldRELATIVELTvalue`` condition.

        Matches records where *field* is less than a relative date.
        Value uses ServiceNow relative date syntax (e.g. ``"@year@ago@1"``).

        Args:
            field: A date/datetime field name.
            value: Relative date expression.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}RELATIVELT{value}")
        return self

    def more_than(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldMORETHANvalue`` condition.

        Used for "more than X ago" date conditions.
        Value uses ServiceNow syntax (e.g. ``"@hour@ago@3"`` for "more than 3 hours ago").

        Args:
            field: A date/datetime field name.
            value: Time specification string.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}MORETHAN{value}")
        return self

    def datepart(self, field: str, part: str, operator: str, value: str) -> "ServiceNowQuery":
        """Add a DATEPART condition to query by a component of a date field.

        Generates: ``fieldDATEPARTpart@operator@value``

        Args:
            field: A date/datetime field name.
            part: Date part (e.g. ``"dayofweek"``, ``"month"``, ``"year"``, ``"quarter"``).
            operator: Comparison operator (e.g. ``"="``, ``">"``, ``"<"``).
            value: The comparison value (e.g. ``"1"`` for Monday).
        """
        validate_identifier(field)
        part = sanitize_query_value(part)
        operator = sanitize_query_value(operator)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}DATEPART{part}@{operator}@{value}")
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

    # -- Field comparison --------------------------------------------------------

    def gt_field(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldGT_FIELDother_field`` condition (field > other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}GT_FIELD{other_field}")
        return self

    def lt_field(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldLT_FIELDother_field`` condition (field < other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}LT_FIELD{other_field}")
        return self

    def gt_or_equals_field(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldGT_OR_EQUALS_FIELDother_field`` condition (field >= other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}GT_OR_EQUALS_FIELD{other_field}")
        return self

    def lt_or_equals_field(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldLT_OR_EQUALS_FIELDother_field`` condition (field <= other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}LT_OR_EQUALS_FIELD{other_field}")
        return self

    def same_as(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldSAMEASother_field`` condition (field equals other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}SAMEAS{other_field}")
        return self

    def not_same_as(self, field: str, other_field: str) -> "ServiceNowQuery":
        """Add ``fieldNSAMEASother_field`` condition (field != other field)."""
        validate_identifier(field)
        validate_identifier(other_field)
        self._parts.append(f"{field}NSAMEAS{other_field}")
        return self

    # -- Reference / hierarchy ---------------------------------------------------

    def dynamic(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldDYNAMICvalue`` condition (dynamic reference qualifier).

        Args:
            field: A reference field name.
            value: The dynamic qualifier value (e.g. a reference qualifier script name).
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}DYNAMIC{value}")
        return self

    def in_hierarchy(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldIN_HIERARCHYvalue`` condition.

        Matches records where *field* references a CI within the given hierarchy.

        Args:
            field: A reference field name pointing to a CMDB CI.
            value: The sys_id of the parent CI in the hierarchy.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}IN_HIERARCHY{value}")
        return self

    # -- Change detection --------------------------------------------------------

    def val_changes(self, field: str) -> "ServiceNowQuery":
        """Add ``fieldVALCHANGES`` condition (field value changed).

        Used primarily in notification/business rule conditions to detect
        when a field's value has changed.
        """
        validate_identifier(field)
        self._parts.append(f"{field}VALCHANGES")
        return self

    def changes_from(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldCHANGESFROMvalue`` condition.

        Matches when *field* changes from a specific value.

        Args:
            field: The field name.
            value: The previous value to match against.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}CHANGESFROM{value}")
        return self

    def changes_to(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldCHANGESTOvalue`` condition.

        Matches when *field* changes to a specific value.

        Args:
            field: The field name.
            value: The new value to match against.
        """
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}CHANGESTO{value}")
        return self

    # -- Logical -----------------------------------------------------------------

    def new_query(self) -> "ServiceNowQuery":
        """Append ``^NQ`` to start a new OR-filter group.

        ServiceNow's ``^NQ`` operator acts as a top-level OR between
        independent filter groups, unlike ``^OR`` which operates within
        a single filter group.
        """
        self._parts.append("NQ")
        return self

    # -- OR conditions ---------------------------------------------------------

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
        self._parts.append(f"OR{field}{operator}{value}")
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


def resolve_query_token(query_token: str, query_store: "QueryTokenStore", correlation_id: str) -> str:
    """Resolve a query token to the encoded query string it represents.

    Args:
        query_token: The token from build_query, or empty string for no filter.
        query_store: The shared QueryTokenStore instance.
        correlation_id: Request correlation ID for error formatting.

    Returns the encoded query string. Raises ValueError if the token is invalid or expired.
    """
    if not query_token:
        return ""
    payload = query_store.get(query_token)
    if payload is None:
        raise ValueError("Invalid or expired query token. Use the build_query tool to create a query first.")
    return payload["query"]


async def safe_tool_call(
    fn: Callable[[], Awaitable[str]],
    correlation_id: str,
) -> str:
    """Wrap an MCP tool body with standard error handling.

    Catches ForbiddenError (ACL denial) and generic exceptions,
    returning consistent JSON error envelopes via format_response.
    """
    try:
        return await fn()
    except ForbiddenError as e:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Access denied by ServiceNow ACL: {e}",
        )
    except Exception as e:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=str(e),
        )
