"""Investigation dispatcher tools — investigate_run and investigate_explain."""

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.investigations import INVESTIGATION_REGISTRY
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = [
    "investigate_run",
    "investigate_explain",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register investigation dispatcher tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def investigate_run(
        investigation: str,
        params: str = "{}",
        *,
        correlation_id: str,
    ) -> str:
        """Run a named investigation and return findings.

        Available investigations: stale_automations, deprecated_apis, table_health,
        acl_conflicts, error_analysis, slow_transactions, performance_bottlenecks.

        Args:
            investigation: The investigation name to run.
            params: JSON string of parameters for the investigation.
        """
        module = INVESTIGATION_REGISTRY.get(investigation)
        if module is None:
            available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Unknown investigation '{investigation}'. Available: {available}",
            )

        if params:
            parsed = parse_payload_json(params, field_name="params", correlation_id=correlation_id, validate_keys=False)
            if isinstance(parsed, str):
                return parsed
            params_dict = parsed
        else:
            params_dict = {}

        table = params_dict.get("table")
        if table:
            validate_identifier(table)
            check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await module.run(client, params_dict)

        return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def investigate_explain(
        investigation: str,
        element_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Get a detailed explanation for a specific finding from an investigation.

        Args:
            investigation: The investigation name the finding came from.
            element_id: The element identifier (e.g. "flow_context:fc001" or a table name).
        """
        module = INVESTIGATION_REGISTRY.get(investigation)
        if module is None:
            available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Unknown investigation '{investigation}'. Available: {available}",
            )

        # Validate element_id components to prevent injection
        if ":" in element_id:
            table_part, sys_id_part = element_id.split(":", 1)
            validate_identifier(table_part)
            validate_identifier(sys_id_part)
        else:
            validate_identifier(element_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await module.explain(client, element_id)

        return format_response(data=result, correlation_id=correlation_id)
