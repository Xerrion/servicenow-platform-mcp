"""Native investigate adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations
from pydantic import JsonValue

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.investigations.investigate import InvestigateCapability
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
    capability = InvestigateCapability(
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
            "action": "Choose run, explain, or describe.",
            "name": "Investigation name for run; optional family filter for describe/explain.",
            "params": "Native JSON object of run parameters from describe.",
            "element_id": "Finding ID returned by run, usually table:sys_id.",
        }
    )
    @tool_handler
    async def investigate(
        action: Literal["run", "explain", "describe"],
        name: str | None = None,
        params: dict[str, JsonValue] | None = None,
        element_id: str | None = None,
    ) -> CapabilityResult:
        """Run a named diagnostic or explain a finding. Use action=describe to find available diagnostics and their inputs."""
        return await capability.investigate(
            action=action, name=name, params=params, element_id=element_id
        )
