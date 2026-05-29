"""Unified ``describe`` tool: slim field metadata for any table.

Phase 3a relocation of ``tools/table.py:table_describe``. The behavior, return
shape, policy gates, and warning strategy are identical; only the tool name
(``describe``) and module location change. The legacy ``table_describe`` stays
registered until Phase 3b flips the package registry over.

Helpers for projecting sys_dictionary rows into the slim/verbose shapes live in
``servicenow_mcp.tools._describe_helpers`` so the legacy and unified tools share
a single source of truth.
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._describe_helpers import (
    _build_slim_field_list,
    _build_verbose_field_list,
    _fetch_choice_counts,
    _fetch_documentation,
    _parse_fields_filter,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier


logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = ["describe"]

_VALID_DESCRIBE_ACTIONS: frozenset[str] = frozenset({"list_script_fields"})


def _apply_fields_filter(
    field_list: list[dict[str, Any]],
    requested_fields: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Restrict ``field_list`` to ``requested_fields`` and warn on unknown names.

    Preserves left-to-right order of ``requested_fields`` when collecting the
    ``unknown`` list so the warning text is stable across runs. The returned
    list keeps the original ``field_list`` ordering for matched fields.
    """
    wanted = set(requested_fields)
    present = {str(f.get("name") or f.get("element") or "") for f in field_list}
    unknown = [name for name in requested_fields if name not in present]
    filtered = [f for f in field_list if str(f.get("name") or f.get("element") or "") in wanted]
    if unknown:
        warnings.append(f"Unknown field(s): {','.join(unknown)}")
    return filtered


def _script_field_summary(fields: list[ScriptField]) -> list[dict[str, object]]:
    """Project ``ScriptField`` instances into a JSON-friendly list."""
    return [
        {
            "name": f.name,
            "internal_type": f.internal_type,
            "inherited_from": f.inherited_from,
            "via_heuristic": f.via_heuristic,
        }
        for f in fields
    ]


async def _run_list_script_fields(
    action: str,
    table: str,
    dictionary: DictionaryRegistry,
    correlation_id: str,
) -> str:
    """Resolve script-bearing fields for ``table`` via ``DictionaryRegistry``.

    Returns the resolved super_class chain alongside the script fields so the
    caller can audit where each field is inherited from.
    """
    if action not in _VALID_DESCRIBE_ACTIONS:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"Unknown describe action {action!r}. Valid actions: {sorted(_VALID_DESCRIBE_ACTIONS)}.",
        )

    if not table:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error="table is required when action='list_script_fields'.",
        )

    validate_identifier(table)
    check_table_access(table)

    chain = await dictionary.get_chain(table)
    script_fields = await dictionary.get_script_fields(table)

    return format_response(
        data={
            "table": table,
            "chain": chain,
            "script_fields": _script_field_summary(script_fields),
            "count": len(script_fields),
        },
        correlation_id=correlation_id,
    )


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``describe`` tool on the MCP server.

    Mirrors the unified-tool registration signature used by ``server.py`` for
    ``unified.*`` modules. ``choices`` is unused by ``describe``; ``dictionary``
    powers ``action='list_script_fields'``.
    """
    del choices  # unused; signature retained for loader parity

    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider)
    dict_registry = dictionary

    @mcp.tool()
    @tool_handler
    async def describe(
        table: str = "",
        fields: str = "",
        verbose: bool = False,
        include_docs: bool = False,
        action: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Return slim field metadata for a table, or list script-bearing fields.

        Args:
            table: ServiceNow table name. Required unless ``action`` is set
                (and even with ``action='list_script_fields'`` ``table`` is
                still required - it names the table to inspect).
            fields: Comma-separated list of fields to include. Empty = all fields.
            verbose: When True, return the full sys_dictionary row per field
                minus a fixed deny-list of high-noise keys. Default False.
            include_docs: When True, attach the matching sys_documentation entry
                (label/help/hint/url) per field. Default False.
            action: When set to ``'list_script_fields'``, return the
                dictionary-driven script-bearing fields for ``table`` along
                with the resolved super_class chain. Empty (default) runs the
                standard table-describe flow.
        """
        if action:
            return await _run_list_script_fields(action, table, dict_registry, correlation_id)

        if not table:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="table is required when action is not set.",
            )

        validate_identifier(table)
        check_table_access(table)

        requested_fields = _parse_fields_filter(fields)
        for name in requested_fields:
            validate_identifier(name)

        warnings: list[str] = []

        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await client.get_metadata(table)

            # Fetch table-level metadata from sys_db_object
            table_meta = await client.query_records(
                "sys_db_object",
                ServiceNowQuery().equals("name", table).build(),
                fields=["label", "super_class", "is_extendable", "number_ref", "sys_id"],
                limit=1,
            )
            table_info = table_meta.get("records", [{}])[0] if table_meta.get("records") else {}

            # Batched sys_choice fetch keyed by field; non-fatal failures so a
            # slim describe still works in restricted instances.
            choice_counts = await _fetch_choice_counts(client, table, warnings)

            # Optional sys_documentation fetch (off by default; help text is huge).
            docs: dict[str, dict[str, Any]] = {}
            if include_docs:
                docs = await _fetch_documentation(client, table, warnings)

        field_list = (
            _build_verbose_field_list(metadata, choice_counts)
            if verbose
            else _build_slim_field_list(metadata, choice_counts)
        )

        if requested_fields:
            field_list = _apply_fields_filter(field_list, requested_fields, warnings)

        data: dict[str, Any] = {
            "table": table_info,
            "fields": field_list,
            "field_count": len(field_list),
        }
        if include_docs:
            data["documentation"] = docs

        return format_response(
            data=data,
            correlation_id=correlation_id,
            warnings=warnings or None,
        )
