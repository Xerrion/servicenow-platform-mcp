"""Native code_search adapter."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.code_search.code_search import CodeSearchCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.platform.client import ServiceNowClientProvider


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = CodeSearchCapability(settings=settings, auth_provider=auth_provider, client_factory=client_factory)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
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
        """Search ServiceNow code or inspect Code Search table coverage.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: One of 'search', 'list_tables', or 'describe'.
            term: Search term for action='search'.
            table: Optional table filter for action='search' (e.g. 'sys_script_include').
            search_group: ServiceNow Code Search group; empty uses sn_codesearch.Default Search Group.
            limit: Max search results for action='search'. Default 20.
            extended_matching: Include additional Code Search context fields. Default false.
                Set true when the extra context is needed."""
        return await capability.code_search(
            action=action,
            term=term,
            table=table,
            search_group=search_group,
            limit=limit,
            extended_matching=extended_matching,
        )
