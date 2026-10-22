"""Unified ``record_read`` tool: fetch a single record by sys_id or name.

The response includes the resolved record, a masked view, and the
``script_fields`` list discovered at runtime via ``DictionaryRegistry`` so
callers know which fields they can target on a subsequent ``record_preview``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NoReturn

from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.capabilities.records._record_helpers import _resolve_record_sys_id
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.policy import check_table_access, mask_record
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


_COMPACT_IDENTITY_FIELDS: tuple[str, ...] = (
    "sys_id",
    "name",
    "number",
    "sys_updated_on",
    "sys_updated_by",
    "sys_mod_count",
)


def _err(message: str) -> NoReturn:
    """Return a application error with the given message."""
    raise CapabilityError(message)


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


def _parse_requested_fields(
    fields: list[str] | Literal["*"],
) -> tuple[list[str] | None, str]:
    """Parse the record projection before any ServiceNow request."""
    if fields == "*":
        return None, "all"
    requested = fields or []
    if "*" in requested:
        return _err("fields='*' must be used alone.")
    for name in requested:
        try:
            validate_identifier(name)
        except ValueError as exc:
            return _err(f"Invalid field projection: {exc}")
    return requested, "explicit" if requested else "compact"


class RecordReadCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        dictionary: DictionaryRegistry | None = None,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (
            lambda: ServiceNowClient(settings, auth_provider)
        )
        if dictionary is None:
            dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
        dict_registry = dictionary

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.dict_registry = dict_registry
        self.dictionary = dictionary
        self.settings = settings

    async def record_read(
        self,
        table: str,
        sys_id: str | None = None,
        name: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
    ) -> CapabilityResult:
        """Execute the record_read capability with native typed inputs."""
        sys_id = sys_id or ""
        name = name or ""
        fields = fields or []

        # --- 1. Validate table identifier ----------------------------------
        if not table:
            return _err("table is required.")
        validate_identifier(table)

        # --- 2. Require exactly one of sys_id/name -------------------------
        if sys_id and name:
            return _err("Provide exactly one of sys_id or name, not both.")
        if not sys_id and not name:
            return _err("Provide exactly one of sys_id or name.")

        # --- 3. Policy gate ------------------------------------------------
        check_table_access(table)

        parsed = _parse_requested_fields(fields)
        requested_fields, selection_mode = parsed
        if sys_id:
            validate_sys_id(sys_id)

        # --- 4. Discover fields and validate the requested projection ------
        all_fields = await self.dict_registry.get_all_fields(table)
        known_fields = {field.name for field in all_fields}
        script_fields = await self.dict_registry.get_script_fields(table)
        if requested_fields is not None:
            unknown_fields = [
                field
                for field in requested_fields
                if field != "sys_id" and field not in known_fields
            ]
            if unknown_fields:
                return _err(
                    f"Unknown field(s) for table {table!r}: {','.join(unknown_fields)}.",
                )

        if selection_mode == "compact":
            identity_fields = [
                field
                for field in _COMPACT_IDENTITY_FIELDS
                if field == "sys_id" or field in known_fields
            ]
            projection = list(
                dict.fromkeys(
                    [*identity_fields, *(field.name for field in script_fields)]
                )
            )
        elif requested_fields is None:
            projection = None
        else:
            projection = list(dict.fromkeys(["sys_id", *requested_fields]))

        # --- 5. Resolve target sys_id and fetch one projected record --------
        async with self.client_factory() as client:
            resolved_sys_id = await _resolve_record_sys_id(client, table, sys_id, name)
            record = await client.get_record(table, resolved_sys_id, fields=projection)

        masked_record = mask_record(table, record)

        return operation_result(
            data={
                "table": table,
                "sys_id": resolved_sys_id,
                "record": masked_record,
                "script_fields": _script_field_summary(script_fields),
            },
        )
