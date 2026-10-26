"""Native cmdb adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.cmdb.cmdb import CmdbCapability
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
    capability = CmdbCapability(
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
            "action": "Choose query, get, meta, or describe.",
            "class_name": "CMDB class, such as cmdb_ci_server; required except for describe.",
            "sys_id": "CI sys_id for get.",
            "encoded_query": "ServiceNow encoded filter for query.",
            "limit": "Maximum CIs per page; default is 20.",
            "offset": "Zero-based query offset; add limit for the next page.",
        }
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
        """Read configuration items, their relationships, or CMDB class metadata. Use query for other tables."""
        return await capability.cmdb(
            action=action,
            class_name=class_name,
            sys_id=sys_id,
            encoded_query=encoded_query,
            limit=limit,
            offset=offset,
        )
