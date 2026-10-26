"""Native audit adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.audit.audit import AuditCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
    from servicenow_mcp.telemetry import HttpTelemetry


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
    telemetry: HttpTelemetry | None = None,
) -> None:
    capability = AuditCapability(
        settings=settings,
        auth_provider=auth_provider,
        dictionary=dictionary,
        client_factory=client_factory,
        telemetry=telemetry,
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
            "action": "Choose check_field, check_fields, check_table, history, or describe.",
            "table": "Target ServiceNow table; required except for describe.",
            "field": "Field to inspect for check_field.",
            "fields": "1-50 field names for check_fields.",
            "sys_id": "Record sys_id for history.",
            "since": {
                "description": "History cutoff date; overrides window_days.",
                "format": "date",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "window_days": "Recent history window in days; default is 90.",
            "limit": "Maximum audit rows returned.",
        }
    )
    @tool_handler
    async def audit(
        action: Literal[
            "check_field", "check_fields", "check_table", "history", "describe"
        ],
        table: str | None = None,
        field: str | None = None,
        fields: list[str] | None = None,
        sys_id: str | None = None,
        since: str | None = None,
        window_days: int | None = None,
        limit: int | None = None,
    ) -> CapabilityResult:
        """Inspect audit settings for a table or field, or read a record's change history. History defaults to 90 days because sys_audit is very large."""
        return await capability.audit(
            action=action,
            table=table,
            field=field,
            fields=fields,
            sys_id=sys_id,
            since=since,
            window_days=window_days,
            limit=limit,
        )
