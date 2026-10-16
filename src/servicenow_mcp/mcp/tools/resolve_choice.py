"""Native resolve_choice adapter."""

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.metadata.resolve_choice import ResolveChoiceCapability
from servicenow_mcp.mcp.handlers import tool_handler

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
    @tool_handler
    async def resolve_choice(
        table: str, field: str, label: str | None = None
    ) -> CapabilityResult:
        """Resolve a choice label to its underlying value via ChoiceRegistry.

        Omit unused arguments; null uses defaults.

        Args:
            table: ServiceNow table name.
            field: Field name on that table.
            label: Choice label to resolve. When empty, returns the full {label: value}
                mapping for the field."""
        return await capability.resolve_choice(table=table, field=field, label=label)
