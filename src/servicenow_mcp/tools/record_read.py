"""Unified ``record_read`` tool: fetch a single record by sys_id or name.

The response includes the resolved record, a masked view, and the
``script_fields`` list discovered at runtime via ``DictionaryRegistry`` so
callers know which fields they can target on a subsequent ``record_write``.
"""

from __future__ import annotations

from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_record
from servicenow_mcp.tools._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.tools._record_helpers import _resolve_record_sys_id
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["record_read"]

_COMPACT_IDENTITY_FIELDS: tuple[str, ...] = (
    "sys_id",
    "name",
    "number",
    "sys_updated_on",
    "sys_updated_by",
    "sys_mod_count",
)


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


def _parse_requested_fields(fields: str, correlation_id: str) -> tuple[list[str] | None, str] | str:
    """Parse the record projection before any ServiceNow request."""
    if fields.strip() == "*":
        return None, "all"
    requested = [name.strip() for name in fields.split(",") if name.strip()]
    if "*" in requested:
        return _err(correlation_id, "fields='*' must be used alone.")
    for name in requested:
        try:
            validate_identifier(name)
        except ValueError as exc:
            return _err(correlation_id, f"Invalid field projection: {exc}")
    return requested, "explicit" if requested else "compact"


def register_tools(
    mcp: MCPServer,
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
        fields: str = "",
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
            fields: Comma-separated field projection. Empty returns compact
                identity/update metadata plus all discovered script-bearing fields.
                ``'*'`` returns the full masked record.
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

        parsed = _parse_requested_fields(fields, correlation_id)
        if isinstance(parsed, str):
            return parsed
        requested_fields, selection_mode = parsed
        if sys_id:
            validate_sys_id(sys_id)

        # --- 4. Discover fields and validate the requested projection ------
        all_fields = await dict_registry.get_all_fields(table)
        known_fields = {field.name for field in all_fields}
        script_fields = await dict_registry.get_script_fields(table)
        if requested_fields is not None:
            unknown_fields = [field for field in requested_fields if field != "sys_id" and field not in known_fields]
            if unknown_fields:
                return _err(
                    correlation_id,
                    f"Unknown field(s) for table {table!r}: {','.join(unknown_fields)}.",
                )

        if selection_mode == "compact":
            identity_fields = [
                field for field in _COMPACT_IDENTITY_FIELDS if field == "sys_id" or field in known_fields
            ]
            projection = list(dict.fromkeys([*identity_fields, *(field.name for field in script_fields)]))
        elif requested_fields is None:
            projection = None
        else:
            projection = list(dict.fromkeys(["sys_id", *requested_fields]))

        # --- 5. Resolve target sys_id and fetch one projected record --------
        async with client_factory() as client:
            resolved_sys_id, err = await _resolve_record_sys_id(client, table, sys_id, name, correlation_id)
            if err:
                return err
            assert resolved_sys_id is not None
            record = await client.get_record(table, resolved_sys_id, fields=projection)

        masked_record = mask_record(table, record)
        returned_fields = list(masked_record)
        selection: dict[str, object] = {
            "mode": selection_mode,
            "requested_fields": "*" if selection_mode == "all" else (requested_fields or None),
            "returned_fields": returned_fields,
            "omitted": [] if selection_mode == "all" else "all fields outside the projection",
            "sys_id_added": selection_mode == "explicit"
            and requested_fields is not None
            and "sys_id" not in requested_fields,
        }

        return format_response(
            data={
                "table": table,
                "sys_id": resolved_sys_id,
                "record": masked_record,
                "script_fields": _script_field_summary(script_fields),
            },
            correlation_id=correlation_id,
            selection=selection,
        )
