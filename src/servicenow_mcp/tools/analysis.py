"""Read-only composed analysis for requested-item variables and journal history."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import NotFoundError
from servicenow_mcp.policy import MASK_VALUE, check_table_access, is_sensitive_field
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_ref_value,
    validate_identifier,
    validate_sys_id,
)


TOOL_NAMES: list[str] = ["analysis"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"ritm_variables", "journal_history", "describe"})
_DEFAULT_WINDOW_DAYS: Final[int] = 90
_JOURNAL_FIELDS: Final[frozenset[str]] = frozenset({"comments", "work_notes", "close_notes"})
_JOURNAL_TYPES: Final[frozenset[str]] = frozenset({"journal", "journal_input", "journal_list"})
_MRVS_TYPES: Final[frozenset[str]] = frozenset({"multi_row_variable_set", "21"})
_SENSITIVE_VARIABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"password|token|secret|credential|api[\s_-]*key|private[\s_-]*key",
    re.IGNORECASE,
)

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "ritm_variables": {
        "description": "Return submitted variable answers for one sc_req_item record.",
        "params": {"sys_id": "str (32-char)", "limit": "int (optional)", "offset": "int (optional)"},
    },
    "journal_history": {
        "description": "Return date-bounded sys_journal_field entries; audit.history remains field-change history.",
        "params": {
            "table": "str",
            "sys_id": "str (32-char)",
            "fields_csv": "comments,work_notes,close_notes (optional)",
            "since": "YYYY-MM-DD (optional, overrides window_days)",
            "window_days": "int (optional, default 90)",
            "limit": "int (optional)",
            "offset": "int (optional)",
        },
    },
    "describe": {"description": "Return this action registry without platform I/O.", "params": {}},
}


def _error(correlation_id: str, message: str) -> str:
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _records(result: dict[str, Any]) -> list[dict[str, Any]]:
    records = result.get("records")
    return list(records) if isinstance(records, list) else []


def _effective_page(limit: int, offset: int, settings: Settings) -> tuple[int, int]:
    if offset < 0:
        raise ValueError("offset must be zero or greater.")
    if limit < 0:
        raise ValueError("limit must be zero or greater.")
    effective_limit = limit or settings.max_row_limit
    return min(effective_limit, settings.max_row_limit), offset


def _parse_since(since: str, window_days: int) -> tuple[str, int, bool]:
    """Return an ISO cutoff and its effective window metadata."""
    explicit_since = since.strip()
    if explicit_since:
        try:
            datetime.strptime(explicit_since, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError("since must use YYYY-MM-DD format.") from exc
        return explicit_since, 0, True
    if window_days < 0:
        raise ValueError("window_days must be zero or greater.")
    effective_window = window_days or _DEFAULT_WINDOW_DAYS
    return (datetime.now(UTC).date() - timedelta(days=effective_window)).isoformat(), effective_window, False


def _is_sensitive_definition(definition: dict[str, Any]) -> bool:
    return any(
        is_sensitive_field(value) or bool(_SENSITIVE_VARIABLE_RE.search(value))
        for key in ("name", "question_text")
        if (value := resolve_ref_value(definition.get(key)))
    )


async def _ritm_variables(
    client: ServiceNowClient,
    *,
    sys_id: str,
    limit: int,
    offset: int,
    settings: Settings,
    correlation_id: str,
) -> str:
    for table in ("sc_req_item", "sc_item_option_mtom", "sc_item_option", "item_option_new"):
        check_table_access(table)
    validate_sys_id(sys_id)
    effective_limit, effective_offset = _effective_page(limit, offset, settings)
    try:
        target = await client.get_record("sc_req_item", sys_id, fields=["sys_id"])
    except NotFoundError:
        return _error(correlation_id, "Requested item was not found.")

    links_result = await client.query_records(
        "sc_item_option_mtom",
        ServiceNowQuery().equals("request_item", sys_id).order_by("sys_id").build(),
        fields=["sys_id", "sc_item_option"],
        limit=effective_limit,
        offset=effective_offset,
    )
    links = _records(links_result)
    option_ids = [
        value for value in dict.fromkeys(resolve_ref_value(row.get("sc_item_option")) for row in links) if value
    ]
    options = (
        _records(
            await client.query_records(
                "sc_item_option",
                ServiceNowQuery().in_list("sys_id", option_ids).build(),
                fields=["sys_id", "item_option_new", "value"],
                limit=len(option_ids),
            )
        )
        if option_ids
        else []
    )
    options_by_id = {resolve_ref_value(row.get("sys_id")): row for row in options}
    definition_ids = [
        value for value in dict.fromkeys(resolve_ref_value(row.get("item_option_new")) for row in options) if value
    ]
    definitions = (
        _records(
            await client.query_records(
                "item_option_new",
                ServiceNowQuery().in_list("sys_id", definition_ids).build(),
                fields=["sys_id", "name", "question_text", "type", "reference", "variable_set"],
                limit=len(definition_ids),
                display_values=True,
            )
        )
        if definition_ids
        else []
    )
    definitions_by_id = {resolve_ref_value(row.get("sys_id")): row for row in definitions}
    definition_counts: dict[str, int] = {}
    for option_row in options:
        definition_id = resolve_ref_value(option_row.get("item_option_new"))
        definition_counts[definition_id] = definition_counts.get(definition_id, 0) + 1

    warnings: list[str] = []
    entries: list[dict[str, Any]] = []
    seen_options: set[str] = set()
    for link in links:
        option_id = resolve_ref_value(link.get("sc_item_option"))
        submitted_option = options_by_id.get(option_id)
        if submitted_option is None:
            warnings.append("A submitted-answer link referenced an inaccessible or missing option.")
            entries.append({"answer_sys_id": option_id or None, "status": "orphaned_option"})
            continue
        definition_id = resolve_ref_value(submitted_option.get("item_option_new"))
        definition: dict[str, Any] | None = definitions_by_id.get(definition_id)
        if definition is None:
            warnings.append("A submitted answer referenced an inaccessible or missing variable definition.")
            entries.append(
                {
                    "answer_sys_id": option_id,
                    "definition_sys_id": definition_id or None,
                    "raw_value": MASK_VALUE,
                    "display_value": MASK_VALUE,
                    "status": "inaccessible_definition",
                }
            )
            continue
        raw_value = resolve_ref_value(submitted_option.get("value"))
        variable_type = resolve_ref_value(definition.get("type"))
        reference_target = resolve_ref_value(definition.get("reference"))
        is_masked = _is_sensitive_definition(definition)
        is_mrvs = variable_type.lower() in _MRVS_TYPES
        if is_mrvs:
            warnings.append(
                "Multi-row variable-set answers are not decoded because no stable Table API representation is guaranteed."
            )
        if reference_target and raw_value:
            warnings.append(
                "Reference answers retain raw sys_ids because a generic display field cannot be resolved reliably."
            )
        if option_id in seen_options:
            warnings.append("Duplicate submitted-answer links were preserved.")
        seen_options.add(option_id)
        entries.append(
            {
                "answer_sys_id": option_id,
                "definition_sys_id": definition_id,
                "name": resolve_ref_value(definition.get("name")),
                "label": resolve_ref_value(definition.get("question_text")),
                "type": variable_type,
                "raw_value": MASK_VALUE if is_masked else raw_value,
                "display_value": MASK_VALUE if is_masked else (None if reference_target else raw_value),
                "reference_target": reference_target or None,
                "variable_set": resolve_ref_value(definition.get("variable_set")) or None,
                "multi_value": definition_counts.get(definition_id, 0) > 1,
                "masked": is_masked,
                "status": "unsupported_mrvs" if is_mrvs else "resolved",
            }
        )

    total = int(links_result.get("count", len(links)))
    next_offset = effective_offset + len(links)
    return format_response(
        data={
            "table": "sc_req_item",
            "sys_id": resolve_ref_value(target.get("sys_id")) or sys_id,
            "entry_count": len(entries),
            "entries": entries,
        },
        correlation_id=correlation_id,
        pagination={"offset": effective_offset, "limit": effective_limit, "total": total},
        selection={
            "mode": "submitted_answers",
            "truncated": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
        },
        warnings=list(dict.fromkeys(warnings)) or None,
    )


def _journal_fields(fields_csv: str) -> list[str]:
    requested = (
        [item.strip() for item in fields_csv.split(",") if item.strip()] if fields_csv else ["comments", "work_notes"]
    )
    fields = list(dict.fromkeys(requested))
    if not fields:
        raise ValueError("fields_csv must contain at least one journal field.")
    for field in fields:
        validate_identifier(field)
        if field not in _JOURNAL_FIELDS:
            raise ValueError(f"Unsupported journal field {field!r}. Allowed fields: {sorted(_JOURNAL_FIELDS)}.")
    return fields


async def _journal_history(
    client: ServiceNowClient,
    *,
    table: str,
    sys_id: str,
    fields_csv: str,
    since: str,
    window_days: int,
    limit: int,
    offset: int,
    settings: Settings,
    dictionary: DictionaryRegistry,
    correlation_id: str,
) -> str:
    validate_identifier(table)
    check_table_access(table)
    check_table_access("sys_journal_field")
    validate_sys_id(sys_id)
    effective_limit, effective_offset = _effective_page(limit, offset, settings)
    requested_fields = _journal_fields(fields_csv)
    cutoff, effective_window, is_explicit_since = _parse_since(since, window_days)
    dictionary_fields = {field.name: field for field in await dictionary.get_all_fields(table)}
    invalid = [
        name
        for name in requested_fields
        if name not in dictionary_fields or dictionary_fields[name].internal_type not in _JOURNAL_TYPES
    ]
    if invalid:
        return _error(correlation_id, f"Field(s) are not journal-compatible on table {table!r}: {','.join(invalid)}.")

    target = await client.query_records(
        table, ServiceNowQuery().equals("sys_id", sys_id).build(), fields=["sys_id"], limit=1
    )
    if not _records(target):
        return _error(correlation_id, "Target record was not found.")
    query = (
        ServiceNowQuery()
        .equals("name", table)
        .equals("element_id", sys_id)
        .in_list("element", requested_fields)
        .greater_or_equal("sys_created_on", cutoff)
        .order_by("sys_created_on")
        .order_by("sys_id")
        .build()
    )
    result = await client.query_records(
        "sys_journal_field",
        query,
        fields=["sys_id", "element", "element_id", "sys_created_on", "sys_created_by", "value"],
        limit=effective_limit,
        offset=effective_offset,
    )
    entries = _records(result)
    for entry in entries:
        if is_sensitive_field(resolve_ref_value(entry.get("element"))):
            entry["value"] = MASK_VALUE
    total = int(result.get("count", len(entries)))
    next_offset = effective_offset + len(entries)
    return format_response(
        data={
            "table": table,
            "sys_id": sys_id,
            "fields": requested_fields,
            "window": {"since": cutoff, "window_days": effective_window, "explicit_since": is_explicit_since},
            "entry_count": len(entries),
            "entries": entries,
        },
        correlation_id=correlation_id,
        pagination={"offset": effective_offset, "limit": effective_limit, "total": total},
        selection={
            "mode": "journal_fields",
            "requested_fields": requested_fields,
            "truncated": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
        },
        warnings=["ServiceNow row and field ACLs and journal retention govern completeness."],
    )


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register composed read-only analysis actions."""
    del choices
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
    dictionary_registry = dictionary or DictionaryRegistry(settings, auth_provider, client_factory)

    @mcp.tool()
    @tool_handler
    async def analysis(
        action: str,
        table: str = "",
        sys_id: str = "",
        fields_csv: str = "",
        since: str = "",
        window_days: int = 0,
        limit: int = 0,
        offset: int = 0,
        *,
        correlation_id: str = "",
    ) -> str:
        """Run bounded, read-only analysis over catalog answers or journals.

        Args:
            action: 'ritm_variables' | 'journal_history' | 'describe'.
            table: Target table for journal_history.
            sys_id: Target record sys_id for ritm_variables or journal_history.
            fields_csv: Allowed journal fields: comments, work_notes, close_notes.
            since: ISO date floor for journal_history; overrides window_days.
            window_days: Journal window; defaults to 90 days.
            limit: Row cap; defaults to MAX_ROW_LIMIT and is capped by it.
            offset: Zero-based row offset.
        """
        if action not in _VALID_ACTIONS:
            return _error(correlation_id, f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.")
        if action == "describe":
            return format_response(data={"actions": _ACTION_REGISTRY}, correlation_id=correlation_id)
        if not sys_id:
            return _error(correlation_id, f"sys_id is required for action={action!r}.")
        async with client_factory() as client:
            if action == "ritm_variables":
                return await _ritm_variables(
                    client,
                    sys_id=sys_id,
                    limit=limit,
                    offset=offset,
                    settings=settings,
                    correlation_id=correlation_id,
                )
            if not table:
                return _error(correlation_id, "table is required for action='journal_history'.")
            return await _journal_history(
                client,
                table=table,
                sys_id=sys_id,
                fields_csv=fields_csv,
                since=since,
                window_days=window_days,
                limit=limit,
                offset=offset,
                settings=settings,
                dictionary=dictionary_registry,
                correlation_id=correlation_id,
            )
