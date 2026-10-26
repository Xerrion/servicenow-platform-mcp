"""Native record_read adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records.record_read import RecordReadCapability
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
    capability = RecordReadCapability(
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
            "table": "ServiceNow table name.",
            "sys_id": "Record sys_id; mutually exclusive with name.",
            "name": "Exact record name; mutually exclusive with sys_id.",
            "fields": "Field projection array; * returns all masked fields.",
        }
    )
    @tool_handler
    async def record_read(
        table: str,
        sys_id: str | None = None,
        name: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
    ) -> CapabilityResult:
        """Read one record by sys_id or exact name, with sensitive fields masked. Use query for multiple records or encoded filters."""
        return await capability.record_read(
            table=table, sys_id=sys_id, name=name, fields=fields
        )
