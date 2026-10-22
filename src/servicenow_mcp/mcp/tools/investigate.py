"""Native investigate adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations
from pydantic import JsonValue

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.investigations.investigate import InvestigateCapability
from servicenow_mcp.mcp.handlers import tool_handler

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
    @tool_handler
    async def investigate(
        action: Literal["run", "explain", "describe"],
        name: str | None = None,
        params: dict[str, JsonValue] | None = None,
        element_id: str | None = None,
    ) -> CapabilityResult:
        """Run an investigation or explain a finding.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'run' | 'explain' | 'describe'.
            name: Investigation name (required for 'run'; optional direct selector for 'explain' and filter for
                'describe').
                Available: stale_automations, deprecated_apis, table_health,
                acl_conflicts, error_analysis, slow_transactions, performance_bottlenecks.
            params: Native object of the selected investigation's run parameters.
                Use describe to read its names and types. Unknown keys and invalid types are rejected.
                slow_transactions.categories is a native string array.
            element_id: Finding identifier (explain only). Usually 'table:sys_id'.
                With name='table_health' or heavy automation, use the table name.
                With name='acl_conflicts', an ACL sys_id is also accepted."""
        return await capability.investigate(
            action=action, name=name, params=params, element_id=element_id
        )
