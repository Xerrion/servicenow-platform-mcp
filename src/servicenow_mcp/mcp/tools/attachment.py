"""Native attachment adapter."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.attachments.attachment import AttachmentCapability
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
    capability = AttachmentCapability(settings=settings, auth_provider=auth_provider, client_factory=client_factory)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    @tool_handler
    async def attachment(
        action: Literal["list", "get", "download", "download_by_name"],
        sys_id: str | None = None,
        table: str | None = None,
        table_sys_id: str | None = None,
        file_name: str | None = None,
    ) -> CapabilityResult:
        """Read attachments. action: 'list' | 'get' | 'download' | 'download_by_name'.

        Omit unused arguments; null uses defaults. Action-specific required
        arguments are still validated.

        Args:
            action: One of: list, get, download, download_by_name.
            sys_id: Attachment sys_id (for get, download).
            table: Parent table (for list, download_by_name).
            table_sys_id: Parent record sys_id (for list, download_by_name).
            file_name: File name (for download_by_name)."""
        return await capability.attachment(
            action=action, sys_id=sys_id, table=table, table_sys_id=table_sys_id, file_name=file_name
        )
