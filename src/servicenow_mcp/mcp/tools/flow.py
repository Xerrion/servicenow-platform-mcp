"""Native flow adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.flows.flow import FlowCapability
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
        """Inspect Flow Designer flows, triggers, and value blobs (read-only).

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'contract' | 'inspect' | 'find_by_table' | 'decode_values' | 'list_triggers' |
                'describe' | 'validate' | 'status'.
            validate: requires sys_id. Local semantic validation only.
                It never compiles, saves, activates, or runs a flow.
            status: requires sys_id. execution_sys_id selects one exact existing execution context;
                no execution is started.
            sys_id: Flow sys_id (contract/inspect; mutually exclusive with name).
            name: Flow name (contract/inspect; mutually exclusive with sys_id).
            value: gzip+base64+JSON blob to decode (decode_values).
            table: Target table (find_by_table; optional filter for list_triggers).
            trigger_type: Trigger type filter (list_triggers, e.g. 'record_update').
            active: 'true' | 'false' filter (list_triggers).
            limit: Row cap for list_triggers (default 100).
            sections: Native array of inspect/contract sections. Empty uses the compact default; '*' returns all.
            section_limit: Shared cap for selected flow rows/nodes (default 100, max MAX_ROW_LIMIT)."""
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
