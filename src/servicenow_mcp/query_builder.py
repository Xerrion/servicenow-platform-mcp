"""ServiceNow encoded query construction."""

from typing import override

from servicenow_mcp.validation import sanitize_query_value, validate_identifier


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


class ServiceNowQuery:
    """Fluent builder for ServiceNow encoded query strings."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def equals(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}={value}")
        return self

    def greater_or_equal(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``field>=value`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}>={value}")
        return self

    def like(self, field: str, value: str) -> "ServiceNowQuery":
        """Add ``fieldLIKEvalue`` condition."""
        validate_identifier(field)
        value = sanitize_query_value(value)
        self._parts.append(f"{field}LIKE{value}")
        return self

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

    def new_query(self) -> "ServiceNowQuery":
        """Append ``^NQ`` to start a new OR-filter group.

        ServiceNow's ``^NQ`` operator acts as a top-level OR between
        independent filter groups, unlike ``^OR`` which operates within
        a single filter group.
        """
        self._parts.append("NQ")
        return self

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

    def build(self) -> str:
        """Return the joined encoded query string."""
        return "^".join(self._parts)

    @override
    def __str__(self) -> str:
        """Return the built query string."""
        return self.build()
