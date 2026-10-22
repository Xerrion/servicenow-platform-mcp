"""Native describe adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.metadata.describe import DescribeCapability
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
    capability = DescribeCapability(
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
    async def describe(
        table: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
        verbose: bool | None = False,
        include_docs: bool | None = False,
        action: Literal["", "list_script_fields", "list_tables"] | None = None,
        name_filter: str | None = None,
        field_offset: int | None = 0,
        field_limit: int | None = 25,
    ) -> CapabilityResult:
        """Return slim field metadata for a table, or list tables / script fields.

        Omit unused optional arguments; null uses their defaults.

        Args:
            table: ServiceNow table name. Required for the default flow and for
                ``action='list_script_fields'``. Ignored by
                ``action='list_tables'``.
            fields: Native array of fields to include. Empty returns a bounded
                compact page. ``'*'`` explicitly returns all fields.
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
            field_offset: Zero-based field offset for compact default pages.
            field_limit: Field count for compact default pages (1-100)."""
        return await capability.describe(
            table=table,
            fields=fields,
            verbose=verbose,
            include_docs=include_docs,
            action=action,
            name_filter=name_filter,
            field_offset=field_offset,
            field_limit=field_limit,
        )
