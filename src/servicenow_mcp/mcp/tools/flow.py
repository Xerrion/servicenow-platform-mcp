"""Native flow adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.flows.flow import FlowCapability
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
    capability = FlowCapability(
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
            "action": "Choose contract, inspect, find_by_table, decode_values, list_triggers, describe, validate, or status.",
            "sys_id": "Canonical sys_hub_flow sys_id for a flow or subflow.",
            "name": "Flow name or internal_name for contract/inspect.",
            "value": "Gzip+base64 encoded JSON blob for decode_values.",
            "table": "Target table for find_by_table or a list_triggers filter.",
            "trigger_type": "Optional trigger type filter for list_triggers.",
            "active": "Optional true/false trigger filter.",
            "limit": "Maximum triggers returned; default is 100.",
            "sections": "Inspection sections array; use * for all sections.",
            "section_limit": "Shared row/node cap for selected sections.",
            "execution_sys_id": "Existing execution context sys_id for status; does not start execution.",
        }
    )
    @tool_handler
    async def flow(
        action: Literal[
            "contract",
            "inspect",
            "find_by_table",
            "decode_values",
            "list_triggers",
            "describe",
            "validate",
            "status",
        ],
        sys_id: str | None = None,
        name: str | None = None,
        value: str | None = None,
        table: str | None = None,
        trigger_type: str | None = None,
        active: str | None = None,
        limit: int | None = None,
        sections: list[str] | Literal["*"] | None = None,
        section_limit: int | None = None,
        execution_sys_id: str | None = None,
    ) -> CapabilityResult:
        """Inspect Flow Designer definitions, triggers, local validation, or existing execution status. Use flow_preview to prepare changes; this tool is read-only."""
        return await capability.flow(
            action=action,
            sys_id=sys_id,
            name=name,
            value=value,
            table=table,
            trigger_type=trigger_type,
            active=active,
            limit=limit,
            sections=sections,
            section_limit=section_limit,
            execution_sys_id=execution_sys_id,
        )
