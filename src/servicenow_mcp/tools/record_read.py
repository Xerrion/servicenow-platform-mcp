"""Unified ``record_read`` tool: fetch a single record by sys_id or name.

The response includes the resolved record, a masked view, and the
``script_fields`` list discovered at runtime via ``DictionaryRegistry`` so
callers know which fields they can target on a subsequent ``record_write``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_record
from servicenow_mcp.tools._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.tools._record_helpers import _resolve_record_sys_id
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = ["record_read"]


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


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


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``record_read`` tool on the MCP server."""
    del choices  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
    dict_registry = dictionary

    @mcp.tool()
    @tool_handler
    async def record_read(
        table: str,
        sys_id: str = "",
        name: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch a record by sys_id or name from any table.

        Exactly one of ``sys_id`` or ``name`` must be supplied. Sensitive fields
        are masked. The response includes a ``script_fields`` list (resolved
        dynamically via ``sys_dictionary`` plus the table's super_class chain)
        so callers can discover which script-bearing fields are writable on a
        subsequent ``record_write``.

        Args:
            table: ServiceNow table name (e.g. ``sys_script``,
                ``catalog_script_client``, ``incident``). Tables with zero
                script fields return ``script_fields: []`` and succeed.
            sys_id: Mutually exclusive with ``name``. Direct lookup by sys_id.
            name: Mutually exclusive with ``sys_id``. Resolves via
                ``name=<value>`` query; ambiguous matches return an error.
        """
        # --- 1. Validate table identifier ----------------------------------
        if not table:
            return _err(correlation_id, "table is required.")
        validate_identifier(table)

        # --- 2. Require exactly one of sys_id/name -------------------------
        if sys_id and name:
            return _err(correlation_id, "Provide exactly one of sys_id or name, not both.")
        if not sys_id and not name:
            return _err(correlation_id, "Provide exactly one of sys_id or name.")

        # --- 3. Policy gate ------------------------------------------------
        check_table_access(table)

        # --- 4. Resolve target sys_id and fetch record ---------------------
        async with client_factory() as client:
            resolved_sys_id, err = await _resolve_record_sys_id(client, table, sys_id, name, correlation_id)
            if err:
                return err
            # _resolve_record_sys_id returns (sys_id, None) on success.
            assert resolved_sys_id is not None
            record = await client.get_record(table, resolved_sys_id)

        # --- 5. Discover script-bearing fields (dictionary-driven) ---------
        script_fields = await dict_registry.get_script_fields(table)

        return format_response(
            data={
                "table": table,
                "sys_id": resolved_sys_id,
                "record": mask_record(table, record),
                "script_fields": _script_field_summary(script_fields),
            },
            correlation_id=correlation_id,
        )
