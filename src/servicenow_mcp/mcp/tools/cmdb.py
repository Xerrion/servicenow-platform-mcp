"""Native cmdb adapter."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.cmdb.cmdb import CmdbCapability
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
    capability = CmdbCapability(settings=settings, auth_provider=auth_provider, client_factory=client_factory)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    @tool_handler
    async def cmdb(
        action: Literal["query", "get", "meta", "describe"],
        class_name: str | None = None,
        sys_id: str | None = None,
        encoded_query: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
    ) -> CapabilityResult:
        """Inspect configuration items, relationships, or CMDB class metadata.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'query', 'get', 'meta', or 'describe'.
            class_name: CMDB table, such as 'cmdb_ci_server'. Required except for describe.
            sys_id: CI sys_id, required for get.
            encoded_query: Optional ServiceNow encoded filter for query.
            limit: Maximum CIs per query page (default 20, capped by MAX_ROW_LIMIT).
            offset: Starting query row (default 0). Advance by limit to fetch the next page."""
        return await capability.cmdb(
            action=action, class_name=class_name, sys_id=sys_id, encoded_query=encoded_query, limit=limit, offset=offset
        )
