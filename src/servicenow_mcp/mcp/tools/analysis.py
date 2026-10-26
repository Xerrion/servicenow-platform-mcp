"""Native analysis adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.catalog.analysis import AnalysisCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = AnalysisCapability(
        settings=settings,
        auth_provider=auth_provider,
        dictionary=dictionary,
        client_factory=client_factory,
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
            "action": "Select ritm_variables, journal_history, or describe.",
            "table": "Target table for journal_history, such as incident.",
            "sys_id": "Record sys_id; use a requested-item ID for ritm_variables.",
            "fields": "Journal fields: comments, work_notes, or close_notes.",
            "since": {
                "description": "Journal cutoff date; overrides window_days.",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "window_days": "Recent journal window in days; default is 90.",
            "limit": "Maximum journal rows to return.",
            "offset": "Zero-based journal result offset.",
        }
    )
    @tool_handler
    async def analysis(
        action: Literal["ritm_variables", "journal_history", "describe"],
        table: str | None = None,
        sys_id: str | None = None,
        fields: list[str] | None = None,
        since: str | None = None,
        window_days: int | None = None,
        limit: int | None = None,
        offset: int | None = 0,
    ) -> CapabilityResult:
        """Read requested-item variables or record journal history. Use 'describe' to discover actions and 'audit' for sys_audit history."""
        return await capability.analysis(
            action=action,
            table=table,
            sys_id=sys_id,
            fields=fields,
            since=since,
            window_days=window_days,
            limit=limit,
            offset=offset,
        )
