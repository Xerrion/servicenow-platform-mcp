"""Native record_read adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records.record_read import RecordReadCapability
from servicenow_mcp.mcp.handlers import tool_handler

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = RecordReadCapability(
        settings=settings,
        auth_provider=auth_provider,
        dictionary=dictionary,
        client_factory=client_factory,
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    @tool_handler
    async def record_read(
        table: str,
        sys_id: str | None = None,
        name: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
    ) -> CapabilityResult:
        """Fetch a record by sys_id or name from any table.

        Exactly one of ``sys_id`` or ``name`` must be supplied. Sensitive fields
        are masked. The response includes a ``script_fields`` list (resolved
        dynamically via ``sys_dictionary`` plus the table's super_class chain)
        so callers can discover which script-bearing fields are writable on a
        subsequent ``record_preview``.
        Omit unused arguments; null uses defaults. Exactly one record selector
        is still required.

        Args:
            table: ServiceNow table name (e.g. ``sys_script``,
                ``catalog_script_client``, ``incident``). Tables with zero
                script fields return ``script_fields: []`` and succeed.
            sys_id: Mutually exclusive with ``name``. Direct lookup by sys_id.
            name: Mutually exclusive with ``sys_id``. Resolves via
                ``name=<value>`` query; ambiguous matches return an error.
            fields: Native array of field projection. Empty returns compact
                identity/update metadata plus all discovered script-bearing fields.
                ``'*'`` returns the full masked record."""
        return await capability.record_read(
            table=table, sys_id=sys_id, name=name, fields=fields
        )
