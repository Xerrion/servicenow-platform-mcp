"""Unified ``record_read`` tool: fetch a single artifact record by sys_id or name.

Complements ``record_write`` by giving agents a discoverable, masked read path
for platform artifacts (Business Rules, Script Includes, UI Policies, etc.).
The response includes the resolved table, the masked record, and the
``script_fields`` list from ``SCRIPT_FIELD_MAP`` so callers know which fields
they can target on a subsequent write.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_record
from servicenow_mcp.tools._artifact import (
    SCRIPT_FIELD_MAP,
    _resolve_writable_artifact_table,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_sys_id


TOOL_NAMES: list[str] = ["record_read"]


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register the unified ``record_read`` tool on the MCP server."""
    del choices  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def record_read(
        artifact_type: str,
        sys_id: str = "",
        name: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch a platform artifact record by sys_id or name.

        Exactly one of ``sys_id`` or ``name`` must be supplied. Sensitive fields
        are masked. The response includes ``script_fields`` (the list from
        ``SCRIPT_FIELD_MAP[artifact_type]``) so callers can discover which
        script-bearing fields are writable on a subsequent ``record_write``.

        Args:
            artifact_type: One of the writable artifact types. Use
                ``describe(action='list_artifact_types')`` to discover all
                valid types.
            sys_id: Mutually exclusive with ``name``. Direct lookup by sys_id.
            name: Mutually exclusive with ``sys_id``. Resolves via
                ``name=<value>`` query; ambiguous matches return an error.
        """
        # --- 1. Validate artifact_type --------------------------------------
        try:
            table = _resolve_writable_artifact_table(artifact_type)
        except ValueError as exc:
            return _err(correlation_id, str(exc))

        # --- 2. Require exactly one of sys_id/name --------------------------
        if sys_id and name:
            return _err(correlation_id, "Provide exactly one of sys_id or name, not both.")
        if not sys_id and not name:
            return _err(correlation_id, "Provide exactly one of sys_id or name.")

        # --- 3. Policy gate -------------------------------------------------
        check_table_access(table)

        # --- 4. Resolve target sys_id --------------------------------------
        async with ServiceNowClient(settings, auth_provider) as client:
            if sys_id:
                validate_sys_id(sys_id)
                resolved_sys_id = sys_id
            else:
                lookup = await client.query_records(
                    table,
                    ServiceNowQuery().equals("name", name).build(),
                    limit=2,
                )
                records = lookup.get("records", [])
                if not records:
                    return _err(
                        correlation_id,
                        f"No artifact found with name={name!r} on table {table!r}.",
                    )
                if len(records) > 1:
                    return _err(
                        correlation_id,
                        f"Ambiguous name={name!r} on table {table!r}: multiple records match.",
                    )
                resolved_sys_id = records[0]["sys_id"]

            record = await client.get_record(table, resolved_sys_id)

        return format_response(
            data={
                "artifact_type": artifact_type,
                "table": table,
                "sys_id": resolved_sys_id,
                "record": mask_record(table, record),
                "script_fields": list(SCRIPT_FIELD_MAP[artifact_type]),
            },
            correlation_id=correlation_id,
        )
