"""Native analysis adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.catalog.analysis import AnalysisCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler

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
        """Run bounded, read-only analysis over catalog answers or journals.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'ritm_variables' | 'journal_history' | 'describe'.
            table: Target table for journal_history.
            sys_id: Target record sys_id for ritm_variables or journal_history.
            fields: Allowed journal fields: comments, work_notes, close_notes.
            since: ISO date floor for journal_history; overrides window_days.
            window_days: Journal window; defaults to 90 days.
            limit: Row cap; defaults to MAX_ROW_LIMIT and is capped by it.
            offset: Zero-based row offset."""
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
