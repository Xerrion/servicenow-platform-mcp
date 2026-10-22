"""Native audit adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.audit.audit import AuditCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler

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
        """Inspect ServiceNow audit posture (table/field config) and audit trail.

        IMPORTANT: ``sys_audit`` is one of the largest tables on the platform.
        Every action keeps a default 90-day window for that reason. Override
        ``window_days`` (or ``since`` on ``history``) only when you genuinely
        need older rows - wider windows cause slow queries and can time out.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'check_field' | 'check_fields' | 'check_table' | 'history' | 'describe'.
            table: ServiceNow table name (required for all actions except 'describe').
            field: Field name (required for 'check_field').
            fields: Native array of field names (required for 'check_fields', 1..50).
            sys_id: 32-char record sys_id (required for 'history').
            since: YYYY-MM-DD cutoff (history only; overrides window_days).
            window_days: Audit-trail/positive-control window (defaults to 90).
            limit: Row cap for 'history' (defaults to settings.max_row_limit)."""
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
