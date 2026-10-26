"""Native code_search adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.code_search.code_search import CodeSearchCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = CodeSearchCapability(
        settings=settings, auth_provider=auth_provider, client_factory=client_factory
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
            "action": "Choose search, list_tables, or describe; defaults to search.",
            "term": "Distinctive code symbol or phrase for search.",
            "table": "Optional indexed-table filter for search, such as sys_script_include.",
            "search_group": "Code Search group; omit to use the ServiceNow default.",
            "limit": "Maximum search matches; default is 20.",
            "extended_matching": "Set true to include additional indexed context fields.",
        }
    )
    @tool_handler
    async def code_search(
        action: Literal["search", "list_tables", "describe"] | None = "search",
        term: str | None = None,
        table: str | None = None,
        search_group: str | None = None,
        limit: int | None = 20,
        extended_matching: bool | None = False,
    ) -> CapabilityResult:
        """Search indexed ServiceNow code or list searchable tables. Use query or record_read to read records."""
        return await capability.code_search(
            action=action,
            term=term,
            table=table,
            search_group=search_group,
            limit=limit,
            extended_matching=extended_matching,
        )
