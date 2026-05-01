"""Flow execution introspection tools (sys_flow_context, sys_flow_log)."""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    validate_identifier,
)


def register(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register flow execution tools (flow_execution_list, flow_execution_detail)."""

    @mcp.tool()
    @tool_handler
    async def flow_execution_list(
        flow_sys_id: str = "",
        source_record: str = "",
        state: str = "",
        limit: int = 20,
        *,
        correlation_id: str = "",
    ) -> str:
        """List Flow Designer execution contexts with optional filters.

        Args:
            flow_sys_id: Filter by the sys_id of the flow definition.
            source_record: Filter by the sys_id of the source record that triggered the flow.
            state: Filter by execution state (e.g. 'IN_PROGRESS', 'COMPLETE', 'ERROR', 'CANCELLED').
            limit: Maximum number of execution contexts to return (default 20).
        """
        if not flow_sys_id and not source_record:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="At least one of flow_sys_id or source_record is required",
            )

        if flow_sys_id:
            validate_identifier(flow_sys_id)
        if source_record:
            validate_identifier(source_record)
        check_table_access("sys_flow_context")

        query = (
            ServiceNowQuery()
            .equals_if("flow", flow_sys_id, bool(flow_sys_id))
            .equals_if("source_record", source_record, bool(source_record))
            .equals_if("state", state, bool(state))
            .build()
        )
        safety = enforce_query_safety("sys_flow_context", query, limit, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "sys_flow_context",
                query,
                fields=[
                    "sys_id",
                    "name",
                    "flow",
                    "source_record",
                    "source_table",
                    "state",
                    "started",
                    "ended",
                    "sys_created_on",
                ],
                limit=safety["limit"],
                display_values=True,
            )

        executions = [mask_sensitive_fields(e) for e in result["records"]]

        return format_response(
            data={"executions": executions},
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def flow_execution_detail(
        context_id: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch detailed execution information for a Flow Designer context including ordered log entries.

        Provides richer detail than debug_flow_execution, including operation types, log levels,
        and duration per step.

        Args:
            context_id: The sys_id of the sys_flow_context record.
        """
        validate_identifier(context_id)
        check_table_access("sys_flow_context")
        check_table_access("sys_flow_log")

        log_query = ServiceNowQuery().equals("context", context_id).order_by("order").build()
        log_safety = enforce_query_safety("sys_flow_log", log_query, 500, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            context_record, log_result = await asyncio.gather(
                client.get_record("sys_flow_context", context_id, display_values=True),
                client.query_records(
                    "sys_flow_log",
                    log_query,
                    fields=[
                        "sys_id",
                        "action",
                        "operation",
                        "level",
                        "message",
                        "order",
                        "sys_created_on",
                        "output_data",
                        "error_message",
                        "duration",
                    ],
                    limit=log_safety["limit"],
                    display_values=True,
                ),
            )

        logs = [mask_sensitive_fields(entry) for entry in log_result["records"]]

        return format_response(
            data={
                "context": mask_sensitive_fields(context_record),
                "log_count": len(logs),
                "logs": logs,
            },
            correlation_id=correlation_id,
        )
