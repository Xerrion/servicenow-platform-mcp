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

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._describe_helpers import (
    _describe_impl,
    _parse_fields_filter,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier


logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = ["describe"]

_VALID_DESCRIBE_ACTIONS: frozenset[str] = frozenset({"list_script_fields", "list_tables"})

# sys_db_object holds thousands of rows; cap list_tables and warn on truncation.
_LIST_TABLES_LIMIT: int = 500
_LIST_TABLES_FIELDS: list[str] = ["name", "label", "super_class", "sys_scope"]


def _coerce_display(value: object) -> str:
    """Flatten a field that may arrive as a display-value dict into a string."""
    if isinstance(value, dict):
        return str(value.get("display_value") or value.get("value") or "")
    return str(value or "")


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
    table: str,
    dictionary: DictionaryRegistry,
    correlation_id: str,
) -> str:
    """Resolve script-bearing fields for ``table`` via ``DictionaryRegistry``.

    Returns the resolved super_class chain alongside the script fields so the
    caller can audit where each field is inherited from.
    """
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


async def _run_list_tables(
    name_filter: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    client_factory: ServiceNowClientProvider,
    correlation_id: str,
) -> str:
    """List tables from ``sys_db_object``, optionally filtered by name/label.

    ``name_filter`` is matched as a substring against both the table ``name``
    and its human-readable ``label``. Results are ordered by name, capped at
    ``_LIST_TABLES_LIMIT``, and accompanied by a truncation warning when the cap
    is reached so the caller knows to narrow the filter.
    """
    builder = ServiceNowQuery()
    if name_filter:
        builder.like("name", name_filter).or_condition("label", "LIKE", name_filter)
    query = builder.order_by("name").build()

    async with client_factory() as client:
        result = await client.query_records(
            table="sys_db_object",
            query=query,
            fields=_LIST_TABLES_FIELDS,
            limit=_LIST_TABLES_LIMIT,
            display_values=True,
        )

    records = result.get("records", [])
    tables = [{name: _coerce_display(row.get(name)) for name in _LIST_TABLES_FIELDS} for row in records]

    warnings: list[str] = []
    if len(tables) >= _LIST_TABLES_LIMIT:
        warnings.append(f"Result truncated at {_LIST_TABLES_LIMIT} tables; narrow name_filter to see more.")

    return format_response(
        data={"tables": tables, "count": len(tables)},
        correlation_id=correlation_id,
        warnings=warnings or None,
    )


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``describe`` tool on the MCP server.

    Mirrors the unified-tool registration signature used by ``server.py`` for
    ``unified.*`` modules. ``choices`` is unused by ``describe``; ``dictionary``
    powers ``action='list_script_fields'``.
    """
    del choices  # unused; signature retained for loader parity

    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
    dict_registry = dictionary

    @mcp.tool()
    @tool_handler
    async def describe(
        table: str = "",
        fields: str = "",
        verbose: bool = False,
        include_docs: bool = False,
        action: str = "",
        name_filter: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Return slim field metadata for a table, or list tables / script fields.

        Args:
            table: ServiceNow table name. Required for the default flow and for
                ``action='list_script_fields'``. Ignored by
                ``action='list_tables'``.
            fields: Comma-separated list of fields to include. Empty = all fields.
            verbose: When True, return the full sys_dictionary row per field
                minus a fixed deny-list of high-noise keys. Default False.
            include_docs: When True, attach the matching sys_documentation entry
                (label/help/hint/url) per field. Default False.
            action: When ``'list_script_fields'``, return the dictionary-driven
                script-bearing fields for ``table`` with its resolved super_class
                chain. When ``'list_tables'``, list tables from sys_db_object
                (optionally filtered by ``name_filter``). Empty (default) runs
                the standard table-describe flow.
            name_filter: Substring matched against table name and label when
                ``action='list_tables'``. Empty returns all tables (capped).
        """
        if action:
            if action not in _VALID_DESCRIBE_ACTIONS:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Unknown describe action {action!r}. Valid actions: {sorted(_VALID_DESCRIBE_ACTIONS)}.",
                )
            if action == "list_tables":
                return await _run_list_tables(name_filter, settings, auth_provider, client_factory, correlation_id)
            return await _run_list_script_fields(table, dict_registry, correlation_id)

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

        data, warnings = await _describe_impl(
            table,
            verbose=verbose,
            include_docs=include_docs,
            requested_fields=requested_fields,
            settings=settings,
            auth_provider=auth_provider,
            client_factory=client_factory,
        )

        return format_response(
            data=data,
            correlation_id=correlation_id,
            warnings=warnings or None,
        )
