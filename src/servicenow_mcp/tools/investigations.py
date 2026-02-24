"""Investigation dispatcher tools — investigate_run and investigate_explain."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.investigations import INVESTIGATION_REGISTRY
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.utils import format_response, generate_correlation_id, validate_identifier


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register investigation dispatcher tools on the MCP server."""

    @mcp.tool()
    async def investigate_run(
        investigation: str,
        params: str = "{}",
    ) -> str:
        """Run a named investigation and return findings.

        Available investigations: stale_automations, deprecated_apis, table_health,
        acl_conflicts, error_analysis, slow_transactions, performance_bottlenecks.

        Args:
            investigation: The investigation name to run.
            params: JSON string of parameters for the investigation.
        """
        correlation_id = generate_correlation_id()
        try:
            module = INVESTIGATION_REGISTRY.get(investigation)
            if module is None:
                available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Unknown investigation '{investigation}'. Available: {available}",
                    )
                )

            params_dict: dict[str, Any] = json.loads(params) if params else {}

            table = params_dict.get("table")
            if table:
                validate_identifier(table)
                check_table_access(table)

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await module.run(client, params_dict)

            return json.dumps(format_response(data=result, correlation_id=correlation_id))
        except Exception as exc:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(exc),
                )
            )

    @mcp.tool()
    async def investigate_explain(
        investigation: str,
        element_id: str,
    ) -> str:
        """Get a detailed explanation for a specific finding from an investigation.

        Args:
            investigation: The investigation name the finding came from.
            element_id: The element identifier (e.g. "flow_context:fc001" or a table name).
        """
        correlation_id = generate_correlation_id()
        try:
            module = INVESTIGATION_REGISTRY.get(investigation)
            if module is None:
                available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Unknown investigation '{investigation}'. Available: {available}",
                    )
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await module.explain(client, element_id)

            return json.dumps(format_response(data=result, correlation_id=correlation_id))
        except Exception as exc:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(exc),
                )
            )
