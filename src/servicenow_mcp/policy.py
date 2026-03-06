"""Policy engine for query safety, deny lists, field masking and write gating."""

import logging
import re
from typing import Any

from servicenow_mcp.config import Settings
from servicenow_mcp.errors import PolicyError, QuerySafetyError


logger = logging.getLogger(__name__)

# Tables that must never be accessed via the MCP server
DENIED_TABLES: set[str] = {
    "sys_user_has_password",
    "oauth_credential",
    "oauth_entity",
    "sys_certificate",
    "sys_ssh_key",
    "sys_credentials",
    "discovery_credentials",
    "sys_user_token",
}

# Field name patterns that trigger masking
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"api_key", re.IGNORECASE),
    re.compile(r"private_key", re.IGNORECASE),
]

MASK_VALUE = "***MASKED***"

# Standard limit for internal/metadata queries (not user-facing)
INTERNAL_QUERY_LIMIT: int = 1000

# Date field patterns used to detect date-bounded filters
_DATE_FIELD_PATTERNS: list[str] = [
    "sys_created_on",
    "sys_updated_on",
    "opened_at",
    "closed_at",
    "sys_recorded_at",
]

# Regex to match: a date field name following a condition separator, with a comparison operator
_DATE_CONSTRAINT_RE: re.Pattern[str] = re.compile(
    r"(?:^|\^(?:OR)?)"  # start of string or ^ or ^OR separator  # noqa: ISC003
    + r"(" + "|".join(re.escape(f) for f in _DATE_FIELD_PATTERNS) + r")"  # date field name
    + r"(>=?|<=?|BETWEEN|javascript:gs\.\w+Ago)",  # comparison operator
)


def check_table_access(table: str) -> None:
    """Raise PolicyError if the table is on the deny list.

    Returns None if access is allowed.
    """
    if table.lower() in DENIED_TABLES:
        raise PolicyError(f"Access to table '{table}' is denied by policy")


def is_sensitive_field(field_name: str) -> bool:
    """Check if a field name matches sensitive patterns."""
    return any(pattern.search(field_name) for pattern in _SENSITIVE_PATTERNS)


def mask_sensitive_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the record with sensitive fields masked."""
    masked = dict(record)
    for key in masked:
        if is_sensitive_field(key):
            masked[key] = MASK_VALUE
    return masked


def mask_audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an audit-style entry with values masked when the field name is sensitive.

    In sys_audit rows the actual field name is stored in a metadata key
    (``fieldname`` or ``field``) while the data lives in ``oldvalue``/``newvalue``
    (or ``old_value``/``new_value``).  Standard ``mask_sensitive_fields`` would
    not catch these because the *dict keys* are generic.  This helper inspects
    the field-name value and, if it matches a sensitive pattern, masks the
    corresponding value keys.
    """
    masked = dict(entry)

    # Determine the field name from whichever key is present
    field_name = masked.get("fieldname") or masked.get("field") or ""

    if is_sensitive_field(field_name):
        for key in ("oldvalue", "newvalue", "old_value", "new_value"):
            if key in masked:
                masked[key] = MASK_VALUE

    return masked


def _has_date_filter(query: str) -> bool:
    """Check if the query contains a structural date-bounded filter.

    Verifies that a recognized date field appears with a comparison operator
    (>, >=, <, <=, BETWEEN, or gs.*Ago functions), not merely as a substring.
    """
    return bool(_DATE_CONSTRAINT_RE.search(query))


def enforce_query_safety(
    table: str,
    query: str,
    limit: int | None,
    settings: Settings,
) -> dict[str, Any]:
    """Validate and enforce query safety constraints.

    Returns a dict with the validated 'limit' value.
    Raises QuerySafetyError if constraints are violated.
    """
    check_table_access(table)

    # Cap limit at max_row_limit
    effective_limit = settings.max_row_limit if limit is None or limit > settings.max_row_limit else limit

    # Floor limit at 1 to prevent zero or negative values
    effective_limit = max(1, effective_limit)

    # Large tables require date-bounded filters
    if table in settings.large_table_names and not _has_date_filter(query):
        raise QuerySafetyError(
            (  # noqa: UP034
                f"Table '{table}' is large and requires a date-bounded filter "
                f"(e.g., sys_created_on>=YYYY-MM-DD). "
                f"Add a date field constraint to your query."
            )
        )

    return {"limit": effective_limit}


def write_gate(table: str, settings: Settings, correlation_id: str) -> str | None:
    """Check write access and return a JSON error envelope if blocked, or None if allowed.

    This helper is used by tool functions to gate write operations early.
    If writes are blocked, returns a formatted JSON error response. Otherwise returns None.

    Args:
        table: The table name being accessed.
        settings: The application settings (used to check production environment).
        correlation_id: The correlation ID for the operation.

    Returns:
        A JSON error envelope if writes are blocked, or None if allowed.
    """
    # Import here to avoid circular dependency (format_response imports from policy)
    from servicenow_mcp.utils import format_response

    reason = write_blocked_reason(table, settings)
    if reason:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=reason,
        )
    return None


def can_write(
    table: str,
    settings: Settings,
    override: bool = False,
) -> bool:
    """Check if write operations are allowed for the given table and environment."""
    if override:
        return True
    reason = write_blocked_reason(table, settings)
    if reason is not None:
        logger.warning("Write blocked: %s", reason)
        return False
    return True


def write_blocked_reason(table: str, settings: Settings) -> str | None:
    """Pre-flight local policy check for write operations.

    Checks local policy only (denied tables, production env).
    ServiceNow-side ACL denials will surface as ForbiddenError
    from the HTTP layer and should be handled by callers.

    Returns a reason string if writes are blocked, or None if allowed.
    """
    if table.lower() in DENIED_TABLES:
        return f"Write operations are blocked for table '{table}' (restricted table)"
    if settings.is_production:
        return "Write operations are blocked in production environments"
    return None
