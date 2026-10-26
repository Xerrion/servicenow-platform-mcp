"""Native resolve_choice adapter."""

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.metadata.resolve_choice import ResolveChoiceCapability
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.platform.metadata.choices import ChoiceRegistry


def register_tools(mcp: MCPServer, choices: ChoiceRegistry | None = None) -> None:
    capability = ResolveChoiceCapability(choices=choices)

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
            "table": "Table containing the choice field.",
            "field": "Choice field element name, such as state.",
            "label": "Exact displayed choice label to resolve.",
        }
    )
    @tool_handler
    async def resolve_choice(
        table: str, field: str, label: str | None = None
    ) -> CapabilityResult:
        """Resolve a displayed choice label to its stored value for a table field. Omit label to retrieve the full label-to-value mapping."""
        return await capability.resolve_choice(table=table, field=field, label=label)
