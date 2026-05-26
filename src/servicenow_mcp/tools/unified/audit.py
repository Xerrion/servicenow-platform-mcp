"""Unified ``audit`` tool: inspect ServiceNow audit posture and history.

Five actions dispatched off ``action``:

* ``check_field``  - resolve the audit verdict for one ``(table, field)`` pair.
* ``check_fields`` - batch variant; one verdict per field plus a shared
  table positive-control count.
* ``check_table``  - table-level default plus the list of fields whose
  resolved audit flag differs from that default.
* ``history``      - masked audit trail for a record, date-bounded.
* ``describe``     - return the action registry without platform I/O.

Configuration (chain-walked dictionary and table flags) is resolved by
:class:`AuditRegistry`, which composes :class:`DictionaryRegistry`. Live
``sys_audit`` row counts are NEVER cached and are fetched via the Stats
API for every call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.policy import check_table_access, mask_audit_entry
from servicenow_mcp.tools._audit import AuditRegistry, FieldAudit
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["audit"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"check_field", "check_fields", "check_table", "history", "describe"})

_DEFAULT_WINDOW_DAYS: Final[int] = 90
_MAX_FIELDS_PER_BATCH: Final[int] = 50

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "check_field": {
        "description": "Resolve the combined audit verdict for one (table, field) pair.",
        "params": {"table": "str", "field": "str", "window_days": "int (optional)"},
    },
    "check_fields": {
        "description": "Batch ``check_field`` for a comma-separated list of fields on one table.",
        "params": {
            "table": "str",
            "fields_csv": f"comma-separated field names (1..{_MAX_FIELDS_PER_BATCH})",
            "window_days": "int (optional)",
        },
    },
    "check_table": {
        "description": "Return the table-level audit default plus the fields whose resolved flag differs from it.",
        "params": {"table": "str"},
    },
    "history": {
        "description": "Return masked, date-bounded audit-trail entries for one record.",
        "params": {
            "table": "str",
            "sys_id": "str (32-char)",
            "since": "YYYY-MM-DD (optional, overrides window_days)",
            "window_days": "int (optional, default 90)",
            "limit": "int (optional)",
        },
    },
    "describe": {
        "description": "Return this action registry without making any platform calls.",
        "params": {},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _window_days_or_default(window_days: int) -> int:
    """Coerce a non-positive ``window_days`` to the default 90."""
    return window_days if window_days and window_days > 0 else _DEFAULT_WINDOW_DAYS


def _since_for_window(window_days: int) -> str:
    """Return the ``sys_created_on>=`` date string for *window_days* ago."""
    cutoff = datetime.now(UTC).date() - timedelta(days=window_days)
    return cutoff.isoformat()


def _window_note(*, window_days: int, explicit_since: str | None) -> str:
    """Return the human-readable window note surfaced in responses."""
    if explicit_since:
        return f"Explicit since={explicit_since} (overrides window_days); sys_audit is a large table."
    if window_days == _DEFAULT_WINDOW_DAYS:
        return "Default 90-day window used (sys_audit is large; widen with care)."
    return f"Non-default window of {window_days} days used (sys_audit is large; default is 90)."


async def _stats_count(
    client: ServiceNowClient,
    *,
    table: str,
    field: str | None,
    since: str,
) -> int:
    """Return the ``sys_audit`` row count for the given filters via Stats API."""
    builder = ServiceNowQuery().equals("tablename", table)
    if field:
        builder = builder.equals("fieldname", field)
    builder = builder.greater_or_equal("sys_created_on", since)
    query = builder.build()
    result = await client.aggregate("sys_audit", query)
    if not isinstance(result, dict) or "stats" not in result or not isinstance(result["stats"], dict):
        raise ServiceNowMCPError(
            f"sys_audit Stats API returned a malformed response (missing 'stats' object) "
            f"for table={table!r} field={field!r}; cannot determine positive-control count."
        )
    stats = result["stats"]
    if "count" not in stats:
        raise ServiceNowMCPError(
            f"sys_audit Stats API response is missing 'stats.count' for table={table!r} field={field!r}; "
            f"cannot determine positive-control count."
        )
    raw = stats["count"]
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ServiceNowMCPError(
            f"sys_audit Stats API returned non-integer 'stats.count'={raw!r} for "
            f"table={table!r} field={field!r}; cannot determine positive-control count."
        ) from exc


def _resolve_verdict(
    *,
    table_audit: bool | None,
    fa: FieldAudit,
    field_count: int,
    table_count: int,
) -> tuple[str, str | None, str]:
    """Return ``(verdict, reason, explanation)`` for a single field."""
    if table_audit is False:
        return "not_audited_table_flag", None, "Table-level audit flag is off."
    if not fa.has_row:
        return (
            "inconclusive",
            None,
            "Field row not found in the table's super_class chain.",
        )
    if fa.no_audit_attribute:
        return (
            "not_audited_field_flag",
            "no_audit_attribute",
            "Field carries no_audit=true in its attributes blob (absolute veto).",
        )
    if fa.raw_field_audit is False:
        return (
            "not_audited_field_flag",
            "audit_flag",
            "Field-level audit flag is off in sys_dictionary.",
        )
    if field_count > 0:
        return "audited", None, "Audit activity confirmed within the window."
    if table_count > 0:
        return (
            "audited_but_inactive",
            None,
            "Field is configured for audit but had no activity in the window.",
        )
    return (
        "inconclusive",
        None,
        "No audit activity recorded at the table level in the window; cannot disambiguate.",
    )


def _build_field_attributes(fa: FieldAudit) -> dict[str, Any]:
    """Surface the resolved row's ``attributes`` blob in a stable shape."""
    return {"no_audit": fa.no_audit_attribute, "raw": fa.attributes_raw}


def _check_field_payload(
    *,
    table: str,
    field: str,
    table_audit: bool | None,
    fa: FieldAudit,
    field_count: int,
    table_count: int,
    window_days: int,
    since: str,
    window_note: str,
    chain: list[str],
) -> dict[str, Any]:
    """Assemble the ``check_field`` response payload."""
    verdict, reason, explanation = _resolve_verdict(
        table_audit=table_audit,
        fa=fa,
        field_count=field_count,
        table_count=table_count,
    )
    payload: dict[str, Any] = {
        "table": table,
        "field": field,
        "super_class_chain": chain,
        "verdict": verdict,
        "table_audit": table_audit,
        "field_audit": fa.field_audit,
        "raw_field_audit": fa.raw_field_audit,
        "inherited_from": fa.inherited_from,
        "field_attributes": _build_field_attributes(fa),
        "explanation": explanation,
        "window_note": window_note,
        "recent_activity": {
            "window_days": window_days,
            "since": since,
            "field_change_count": field_count,
            "table_change_count": table_count,
            "positive_control_passed": table_count > 0,
        },
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _action_check_field(
    *,
    table: str,
    field: str,
    window_days: int,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    registry: AuditRegistry,
    correlation_id: str,
) -> str:
    validate_identifier(table)
    check_table_access(table)
    validate_identifier(field)

    effective_window = _window_days_or_default(window_days)
    since = _since_for_window(effective_window)
    note = _window_note(window_days=effective_window, explicit_since=None)

    chain = await registry.get_chain(table)
    table_audit = await registry.get_table_audit(table)
    fa = await registry.get_field_audit(table, field)

    field_count = 0
    table_count = 0
    if table_audit is not False:
        async with ServiceNowClient(settings, auth_provider) as client:
            table_count = await _stats_count(client, table=table, field=None, since=since)
            if fa.has_row:
                field_count = await _stats_count(client, table=table, field=field, since=since)

    payload = _check_field_payload(
        table=table,
        field=field,
        table_audit=table_audit,
        fa=fa,
        field_count=field_count,
        table_count=table_count,
        window_days=effective_window,
        since=since,
        window_note=note,
        chain=chain,
    )
    return format_response(data=payload, correlation_id=correlation_id)


async def _action_check_fields(
    *,
    table: str,
    fields_csv: str,
    window_days: int,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    registry: AuditRegistry,
    correlation_id: str,
) -> str:
    validate_identifier(table)
    check_table_access(table)

    fields = [item.strip() for item in fields_csv.split(",") if item.strip()]
    if not fields:
        return _error(correlation_id, "fields_csv must list at least one field.")
    if len(fields) > _MAX_FIELDS_PER_BATCH:
        return _error(
            correlation_id,
            f"At most {_MAX_FIELDS_PER_BATCH} fields per check_fields call (got {len(fields)}).",
        )
    for name in fields:
        validate_identifier(name)

    effective_window = _window_days_or_default(window_days)
    since = _since_for_window(effective_window)
    note = _window_note(window_days=effective_window, explicit_since=None)

    chain = await registry.get_chain(table)
    table_audit = await registry.get_table_audit(table)

    field_audits: dict[str, FieldAudit] = {}
    for name in fields:
        field_audits[name] = await registry.get_field_audit(table, name)

    table_count = 0
    field_counts: dict[str, int] = dict.fromkeys(fields, 0)
    if table_audit is not False:
        async with ServiceNowClient(settings, auth_provider) as client:
            table_count = await _stats_count(client, table=table, field=None, since=since)
            for name, fa in field_audits.items():
                if fa.has_row:
                    field_counts[name] = await _stats_count(client, table=table, field=name, since=since)

    results: list[dict[str, Any]] = []
    for name in fields:
        fa = field_audits[name]
        verdict, reason, explanation = _resolve_verdict(
            table_audit=table_audit,
            fa=fa,
            field_count=field_counts[name],
            table_count=table_count,
        )
        entry: dict[str, Any] = {
            "field": name,
            "verdict": verdict,
            "field_audit": fa.field_audit,
            "raw_field_audit": fa.raw_field_audit,
            "inherited_from": fa.inherited_from,
            "field_attributes": _build_field_attributes(fa),
            "field_change_count": field_counts[name],
            "explanation": explanation,
        }
        if reason is not None:
            entry["reason"] = reason
        results.append(entry)

    payload: dict[str, Any] = {
        "table": table,
        "super_class_chain": chain,
        "table_audit": table_audit,
        "table_change_count": table_count,
        "positive_control_passed": table_count > 0,
        "window_note": note,
        "recent_activity": {
            "window_days": effective_window,
            "since": since,
        },
        "results": results,
    }
    return format_response(data=payload, correlation_id=correlation_id)


async def _action_check_table(
    *,
    table: str,
    registry: AuditRegistry,
    correlation_id: str,
) -> str:
    validate_identifier(table)
    check_table_access(table)

    chain = await registry.get_chain(table)
    table_audit = await registry.get_table_audit(table)
    rows = await registry.get_table_field_rows(table)

    seen: set[str] = set()
    unique_fields: list[str] = []
    for row in rows:
        element = str(row.get("element") or "").strip()
        if not element or element in seen:
            continue
        seen.add(element)
        unique_fields.append(element)

    overrides: list[dict[str, Any]] = []
    for name in unique_fields:
        fa = await registry.get_field_audit(table, name)
        if not fa.has_row:
            continue
        if fa.field_audit == table_audit:
            continue
        reason = "no_audit_attribute" if fa.no_audit_attribute else "audit_flag"
        overrides.append(
            {
                "field": name,
                "field_audit": fa.field_audit,
                "raw_field_audit": fa.raw_field_audit,
                "inherited_from": fa.inherited_from,
                "reason": reason,
                "field_attributes": _build_field_attributes(fa),
            }
        )

    payload: dict[str, Any] = {
        "table": table,
        "super_class_chain": chain,
        "table_audit": table_audit,
        "field_overrides": overrides,
    }
    return format_response(data=payload, correlation_id=correlation_id)


async def _action_history(
    *,
    table: str,
    sys_id: str,
    since: str,
    window_days: int,
    limit: int,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    validate_identifier(table)
    check_table_access(table)
    if not sys_id:
        return _error(correlation_id, "sys_id is required for action='history'.")
    validate_sys_id(sys_id)

    explicit_since = since.strip() if since else ""
    if explicit_since:
        effective_window = 0
        cutoff = explicit_since
        note = _window_note(window_days=effective_window, explicit_since=explicit_since)
    else:
        effective_window = _window_days_or_default(window_days)
        cutoff = _since_for_window(effective_window)
        note = _window_note(window_days=effective_window, explicit_since=None)

    effective_limit = limit if limit and limit > 0 else settings.max_row_limit
    effective_limit = max(1, min(effective_limit, settings.max_row_limit))

    query = (
        ServiceNowQuery()
        .equals("tablename", table)
        .equals("documentkey", sys_id)
        .greater_or_equal("sys_created_on", cutoff)
        .order_by("sys_created_on", descending=True)
        .build()
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.query_records(
            table="sys_audit",
            query=query,
            limit=effective_limit,
        )

    records: Any = result.get("records") if isinstance(result, dict) else None
    rows: list[dict[str, Any]] = list(records) if isinstance(records, list) else []
    masked = [mask_audit_entry(entry) for entry in rows]

    payload: dict[str, Any] = {
        "table": table,
        "sys_id": sys_id,
        "window": {
            "since": cutoff,
            "window_days": effective_window,
            "explicit_since": bool(explicit_since),
        },
        "window_note": note,
        "entry_count": len(masked),
        "entries": masked,
    }
    return format_response(data=payload, correlation_id=correlation_id)


def _action_describe(correlation_id: str) -> str:
    """Return the action registry without making any platform calls."""
    return format_response(
        data={"actions": _ACTION_REGISTRY},
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``audit`` tool on the MCP server.

    The ``audit`` group owns its :class:`AuditRegistry` (and falls back to
    creating a :class:`DictionaryRegistry` when one is not provided), so the
    chain-walked configuration caches survive across calls. ``choices`` is
    accepted only to honour the uniform loader signature.
    """
    del choices  # unused; signature retained for loader parity

    dictionary_registry = dictionary or DictionaryRegistry(settings, auth_provider)
    audit_registry = AuditRegistry(settings, auth_provider, dictionary_registry)

    @mcp.tool()
    @tool_handler
    async def audit(
        action: str,
        table: str = "",
        field: str = "",
        fields_csv: str = "",
        sys_id: str = "",
        since: str = "",
        window_days: int = 0,
        limit: int = 0,
        *,
        correlation_id: str = "",
    ) -> str:
        """Inspect ServiceNow audit posture (table/field config) and audit trail.

        IMPORTANT: ``sys_audit`` is one of the largest tables on the platform.
        Every action keeps a default 90-day window for that reason. Override
        ``window_days`` (or ``since`` on ``history``) only when you genuinely
        need older rows - wider windows cause slow queries and can time out.

        Args:
            action: 'check_field' | 'check_fields' | 'check_table' | 'history' | 'describe'.
            table: ServiceNow table name (required for all actions except 'describe').
            field: Field name (required for 'check_field').
            fields_csv: Comma-separated field names (required for 'check_fields', 1..50).
            sys_id: 32-char record sys_id (required for 'history').
            since: YYYY-MM-DD cutoff (history only; overrides window_days).
            window_days: Audit-trail/positive-control window (defaults to 90).
            limit: Row cap for 'history' (defaults to settings.max_row_limit).
        """
        if action not in _VALID_ACTIONS:
            return _error(
                correlation_id,
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "describe":
            return _action_describe(correlation_id)

        if action == "check_field":
            return await _action_check_field(
                table=table,
                field=field,
                window_days=window_days,
                settings=settings,
                auth_provider=auth_provider,
                registry=audit_registry,
                correlation_id=correlation_id,
            )

        if action == "check_fields":
            return await _action_check_fields(
                table=table,
                fields_csv=fields_csv,
                window_days=window_days,
                settings=settings,
                auth_provider=auth_provider,
                registry=audit_registry,
                correlation_id=correlation_id,
            )

        if action == "check_table":
            return await _action_check_table(
                table=table,
                registry=audit_registry,
                correlation_id=correlation_id,
            )

        return await _action_history(
            table=table,
            sys_id=sys_id,
            since=since,
            window_days=window_days,
            limit=limit,
            settings=settings,
            auth_provider=auth_provider,
            correlation_id=correlation_id,
        )
