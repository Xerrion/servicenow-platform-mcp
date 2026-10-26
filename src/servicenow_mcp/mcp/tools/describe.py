"""Native describe adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.metadata.describe import DescribeCapability
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

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
    @describe_tool_parameters(
        {
            "table": "Table to describe or inspect for script fields.",
            "fields": "Optional field-name array; use * for all fields.",
            "verbose": "Return fuller sys_dictionary rows when true.",
            "include_docs": "Include field labels, help, hints, and documentation URLs.",
            "action": "Choose default table details, list_tables, or list_script_fields.",
            "name_filter": "Substring filter for table names and labels in list_tables.",
            "field_offset": "Zero-based offset for paging table fields.",
            "field_limit": "Fields per page, from 1 to 100.",
        }
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
        """Discover ServiceNow tables and field metadata before querying or editing. Use list_tables for unknown table names and list_script_fields to locate script-capable fields."""
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
