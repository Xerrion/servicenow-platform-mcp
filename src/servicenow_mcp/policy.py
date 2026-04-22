"""Policy engine for query safety, deny lists, field masking and write gating."""

import logging
import re
from typing import Any

from servicenow_mcp.config import Settings
from servicenow_mcp.errors import PolicyError, QuerySafetyError


logger = logging.getLogger(__name__)

# Tables that must never be accessed via the MCP server.
#
# Grouping notes:
#   - Credential / token tables: outright secrets (``sys_credentials``,
#     ``oauth_credential``, ``sys_ssh_key`` ...).
#   - PII tables: ``sys_user`` exposes email, phone, manager chain and
#     employee_number in bulk.
#   - Config / secret-bearing tables: ``sys_properties`` values routinely
#     contain API keys and connection strings; ``sys_data_source`` carries
#     connection info; ``sys_variable_value`` catalog variable values may
#     include PII submitted by requesters.
#   - Security-posture tables: ``sys_security_acl`` and ``sys_auth_profile``
#     disclose enforcement structure useful for privilege escalation.
#   - MID server tables: ``ecc_agent`` exposes MID topology;
#     ``ecc_queue`` regularly contains credentials inside probe payloads.
#   - Outbound email: ``sys_script_email`` bodies leak customer content.
#
# Investigation modules that legitimately need to read some of these tables
# (``sys_security_acl`` in particular) do so via
# ``ServiceNowClient.get_records_privileged()`` with an explicit per-call
# allowlist; that method is the ONLY sanctioned bypass of this gate and is
# not reachable from tool code paths.
DENIED_TABLES: set[str] = {
    # Existing entries
    "sys_user_has_password",
    "oauth_credential",
    "oauth_entity",
    "sys_certificate",
    "sys_ssh_key",
    "sys_credentials",
    "discovery_credentials",
    "sys_user_token",
    # Phase 4 additions
    "sys_user",
    "sys_properties",
    "sys_security_acl",
    "sys_auth_profile",
    "sys_data_source",
    "sys_variable_value",
    "sys_script_email",
    "ecc_agent",
    "ecc_queue",
}

# Field name patterns that trigger masking.
#
# Design notes for false-positive avoidance:
#   - Substring-safe tokens (password, token, secret, credential, bearer,
#     authorization, webhook, vault, certificate, x509, pkcs, jsessionid,
#     connection_string, session_id, passphrase, keystore, truststore,
#     cookie, apikey, privatekey) are matched anywhere in the field name.
#   - ``cookie`` is deliberately substring-matched even though it appears in
#     benign names (``cookie_name``); any cookie field is treated as a bearer
#     credential. Callers are expected to accept the false-positive rate.
#   - ``apikey`` / ``privatekey`` are the no-underscore variants that would
#     otherwise slip past the underscore-aware ``api_key`` / ``private_key``
#     tokens when ServiceNow field names are camel-cased.
#   - Short/ambiguous tokens that appear as substrings of common non-sensitive
#     fields are bounded to whole snake_case segments via ``(?:^|_)TOKEN(?:_|$)``
#     rather than ``\b``. The Python regex ``\b`` treats ``_`` as a word
#     character, so ``\bdsn\b`` would *not* reject ``primary_dsn``; the
#     explicit underscore-or-edge boundary is correct.
#       * ``dsn``    -> avoid matching ``address`` / ``description`` / ``dns_name``
#       * ``pwd``    -> whole segment only (but ``user_pwd`` matches)
#       * ``passwd`` -> whole segment only
#       * ``otp``    -> avoid ``option`` / ``adopt``
#       * ``kms``    -> avoid embedded acronyms
#       * ``mfa``    -> avoid embedded acronyms (but ``mfa_secret`` matches)
#       * ``cert``   -> avoid ``certification_level`` / ``concert`` /
#                       ``certified_date``; whole-segment keeps ``ssl_cert``
#                       and bare ``cert`` matching.
#   - Compound ``_key`` / ``_pem`` / ``_header`` tokens are expressed as
#     whole segments so that unrelated fields like ``assignment_group`` or
#     ``company_sys_id`` do not false-positive.
#   - ``authorization`` intentionally matches ``authorization_code`` because
#     an authorization-code grant value is itself a bearer-equivalent secret.
#   - Negative cases documented in ``tests/test_policy.py``:
#     ``address``, ``description``, ``first_name``, ``assignment_group``,
#     ``company_sys_id``, ``dns_name``, ``certification_level``,
#     ``concert_ticket``, ``certified_date``.
_SENSITIVE_SUBSTRING_RE: re.Pattern[str] = re.compile(
    r"password"
    r"|passphrase"
    r"|token"
    r"|secret"
    r"|credential"
    r"|bearer"
    r"|authorization"
    r"|webhook"
    r"|vault"
    r"|certificate"
    r"|x509"
    r"|pkcs"
    r"|jsessionid"
    r"|connection_string"
    r"|session_id"
    r"|auth_header"
    r"|cert_pem"
    r"|api_key"
    r"|apikey"
    r"|private_key"
    r"|privatekey"
    r"|ssh_key"
    r"|rsa_key"
    r"|signing_key"
    r"|hmac_key"
    r"|shared_key"
    r"|master_key"
    r"|encryption_key"
    r"|mfa_secret"
    r"|keystore"
    r"|truststore"
    r"|cookie",
    re.IGNORECASE,
)

# Whole-segment match: the keyword must be either the entire field name or
# bounded by underscores on each relevant side. Prevents e.g. ``dsn`` from
# matching ``description`` while still catching ``primary_dsn``.
_SENSITIVE_SEGMENT_RE: re.Pattern[str] = re.compile(
    r"(?:^|_)(?:pwd|passwd|dsn|otp|kms|mfa|cert)(?:_|$)",
    re.IGNORECASE,
)

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [_SENSITIVE_SUBSTRING_RE, _SENSITIVE_SEGMENT_RE]

MASK_VALUE = "***MASKED***"

# Distinct sentinel for script/markup body omission so callers can tell
# credential masking and script-body masking apart in tool output.
SCRIPT_BODY_MASK = "***SCRIPT_BODY_OMITTED***"

# Tables whose records contain executable script or markup bodies. Values
# are masked by default and returned only when a tool opts in via
# ``include_script_body=True``. Script bodies often embed hardcoded secrets,
# so they are treated as sensitive-by-default even though the field *names*
# themselves (``script``, ``html``, ``xml`` ...) are not credential-like.
TABLE_SCRIPT_FIELDS: dict[str, frozenset[str]] = {
    "sys_script": frozenset({"script"}),
    "sys_script_include": frozenset({"script"}),
    "sys_script_client": frozenset({"script"}),
    "sys_ui_policy": frozenset({"script_true", "script_false"}),
    "sys_ui_action": frozenset({"script"}),
    "sysauto_script": frozenset({"script"}),
    "sys_script_fix": frozenset({"script"}),
    "sp_widget": frozenset({"client_script", "server_script", "template"}),
    "sys_ui_page": frozenset({"html", "client_script", "processing_script"}),
    "sys_ui_macro": frozenset({"xml"}),
    "sys_ui_script": frozenset({"script"}),
    "sys_processor": frozenset({"script"}),
    "sys_ws_operation": frozenset({"operation_script"}),
    "sysevent_email_action": frozenset({"advanced_condition", "body_html"}),
    "sysevent_script_action": frozenset({"script"}),
    "ecc_agent_script_include": frozenset({"script"}),
    "sys_web_service": frozenset({"script"}),
    "sys_security_acl": frozenset({"script", "condition"}),
    # Legacy Workflow Engine and Flow Designer tables. Their script-bearing
    # columns are normally reached via the dedicated ``workflow_*`` / ``flow_*``
    # tools (which already mask or opt-in). Registering them here closes the
    # gap for generic ``record_get`` / ``table_query`` readers that bypass
    # those tool-specific masking paths.
    "wf_transition": frozenset({"condition"}),
    "wf_workflow_version": frozenset({"condition"}),
    "wf_activity": frozenset({"script"}),
    "sys_hub_flow": frozenset({"condition"}),
    "sys_hub_action_instance": frozenset({"condition", "script"}),
}

# Standard limit for internal/metadata queries (not user-facing)
INTERNAL_QUERY_LIMIT: int = 1000

# Module-level dangerous-bypass flag. Set once from server.py bootstrap.
# When True, denied-table, write-gate and query-safety checks are skipped.
# Field masking is NOT bypassed.
_dangerous_bypass_enabled: bool = False
# Sentinel enforcing one-shot assignment of the bypass flag. After the first
# call to set_dangerous_bypass(), further calls raise RuntimeError. Tests that
# need to toggle the flag must reset this module-level state directly.
_bypass_locked: bool = False


def set_dangerous_bypass(enabled: bool) -> None:
    """Enable or disable the dangerous policy bypass (one-shot, bootstrap-only).

    This setter may be called exactly once, from ``server.py`` at startup.
    Subsequent calls raise ``RuntimeError`` so that no tool-call path can
    flip the flag at runtime. Tool functions are never given access to this
    setter, which combined with the one-shot sentinel makes the flag
    effectively immutable for the lifetime of the process.
    """
    global _dangerous_bypass_enabled, _bypass_locked  # noqa: PLW0603
    if _bypass_locked:
        raise RuntimeError(
            "set_dangerous_bypass() may only be called once, at server bootstrap. "
            "Current state cannot be changed from tool call paths."
        )
    _dangerous_bypass_enabled = enabled
    _bypass_locked = True


def is_dangerous_bypass_enabled() -> bool:
    """Return True if the dangerous policy bypass is currently enabled."""
    return _dangerous_bypass_enabled


def is_table_denied(table: str) -> bool:
    """Return True if `table` is on the deny list AND the dangerous bypass is not active.

    Prefer this over direct `DENIED_TABLES` membership checks so bypass semantics
    remain centralized.
    """
    if _dangerous_bypass_enabled:
        return False
    return table.lower() in DENIED_TABLES


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
    r"(?:^|\^(?:OR)?)"  # start of string or ^ or ^OR separator
    r"(" + "|".join(re.escape(f) for f in _DATE_FIELD_PATTERNS) + r")"  # date field name
    r"(>=?|<=?|BETWEEN|javascript:gs\.\w+Ago)",  # comparison operator
)


def check_table_access(table: str) -> None:
    """Raise PolicyError if the table is on the deny list.

    Returns None if access is allowed.
    """
    if _dangerous_bypass_enabled:
        return
    if table.lower() in DENIED_TABLES:
        raise PolicyError(f"Access to table '{table}' is denied by policy")


def is_sensitive_field(field_name: str) -> bool:
    """Check if a field name matches sensitive patterns."""
    return any(pattern.search(field_name) for pattern in _SENSITIVE_PATTERNS)


def mask_sensitive_fields(
    record: dict[str, Any],
    table: str | None = None,
    *,
    include_script_body: bool = False,
) -> dict[str, Any]:
    """Return a copy of ``record`` with sensitive fields masked.

    Two independent masking passes run in order:

    1. Credential masking: any field matching the sensitive-name regex is
       replaced with ``MASK_VALUE``.
    2. Script-body masking: when ``table`` is known to contain executable
       script/markup (see ``TABLE_SCRIPT_FIELDS``) and the caller has not
       opted in via ``include_script_body=True``, the registered fields are
       replaced with ``SCRIPT_BODY_MASK``.

    The credential pass runs first so a field matching both (contrived case)
    reports the stronger ``MASK_VALUE`` sentinel rather than the script-body
    one. Backward compatibility: calling with just ``record`` behaves as
    before - no script-body masking is applied without a table context.
    """
    masked = dict(record)
    for key in masked:
        if is_sensitive_field(key):
            masked[key] = MASK_VALUE

    if table is None or include_script_body:
        return masked

    script_fields = TABLE_SCRIPT_FIELDS.get(table.lower())
    if not script_fields:
        return masked

    for key in script_fields:
        if key in masked and masked[key] != MASK_VALUE:
            masked[key] = SCRIPT_BODY_MASK
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
    if _dangerous_bypass_enabled:
        effective_limit = limit if limit is not None else settings.max_row_limit
        return {"limit": max(1, effective_limit)}

    check_table_access(table)

    # Cap limit at max_row_limit
    effective_limit = settings.max_row_limit if limit is None or limit > settings.max_row_limit else limit

    # Floor limit at 1 to prevent zero or negative values
    effective_limit = max(1, effective_limit)

    # Large tables require date-bounded filters
    if table in settings.large_table_names and not _has_date_filter(query):
        raise QuerySafetyError(
            f"Table '{table}' is large and requires a date-bounded filter "
            f"(e.g., sys_created_on>=YYYY-MM-DD). "
            f"Add a date field constraint to your query."
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
    if _dangerous_bypass_enabled:
        return None
    if table.lower() in DENIED_TABLES:
        return f"Write operations are blocked for table '{table}' (restricted table)"
    if settings.is_production:
        return "Write operations are blocked in production environments"
    return None
