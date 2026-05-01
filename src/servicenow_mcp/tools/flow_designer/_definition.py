"""Flow definition introspection tools (sys_hub_flow*, snapshots, action instances, logic blocks)."""

from __future__ import annotations

import asyncio
from typing import Any

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
    resolve_ref_value,
    validate_identifier,
)


def _resolve_flow_map_target(flow_record: dict[str, Any], original_flow_sys_id: str) -> str:
    """Resolve the child-link target used by Flow Designer map tables."""
    latest_snapshot_sys_id = resolve_ref_value(flow_record.get("latest_snapshot", ""))
    if latest_snapshot_sys_id:
        return latest_snapshot_sys_id

    master_snapshot_sys_id = resolve_ref_value(flow_record.get("master_snapshot", ""))
    if master_snapshot_sys_id:
        return master_snapshot_sys_id

    return original_flow_sys_id


def register_core(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register the core flow definition tools: flow_list, flow_get, flow_map.

    ``flow_snapshot_list`` is registered separately via ``register_snapshot_list``
    because the canonical registration order interleaves action and execution tools
    between the core definition tools and snapshot listing.
    """

    @mcp.tool()
    @tool_handler
    async def flow_list(
        table: str = "",
        flow_type: str = "",
        status: str = "",
        active_only: bool = True,
        limit: int = 20,
        *,
        correlation_id: str = "",
    ) -> str:
        """List Flow Designer flows and subflows with optional filters.

        Args:
            table: Filter by the table the flow operates on (e.g. 'incident').
            flow_type: Filter by flow type (e.g. 'flow', 'subflow', 'action').
            status: Filter by flow status (e.g. 'draft', 'published').
            active_only: Only return active flows (default True).
            limit: Maximum number of flows to return (default 20).
        """
        if table:
            validate_identifier(table)
        check_table_access("sys_hub_flow")

        query = (
            ServiceNowQuery()
            .equals_if("table", table, bool(table))
            .equals_if("type", flow_type, bool(flow_type))
            .equals_if("status", status, bool(status))
            .equals_if("active", "true", active_only)
            .build()
        )
        safety = enforce_query_safety("sys_hub_flow", query, limit, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "sys_hub_flow",
                query,
                fields=[
                    "sys_id",
                    "name",
                    "status",
                    "type",
                    "table",
                    "active",
                    "description",
                    "sys_updated_on",
                    "sys_created_on",
                ],
                limit=safety["limit"],
                display_values=True,
            )

        flows = [mask_sensitive_fields(f) for f in result["records"]]

        return format_response(
            data={"flows": flows},
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def flow_get(
        flow_sys_id: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch a Flow Designer flow definition with its input and output variables.

        Args:
            flow_sys_id: The sys_id of the sys_hub_flow record.
        """
        validate_identifier(flow_sys_id)
        check_table_access("sys_hub_flow")
        check_table_access("sys_hub_flow_variable")

        var_query = ServiceNowQuery().equals("flow", flow_sys_id).build()
        var_safety = enforce_query_safety("sys_hub_flow_variable", var_query, 100, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            flow_record, var_result = await asyncio.gather(
                client.get_record("sys_hub_flow", flow_sys_id, display_values=True),
                client.query_records(
                    "sys_hub_flow_variable",
                    var_query,
                    fields=[
                        "sys_id",
                        "name",
                        "type",
                        "mandatory",
                        "default_value",
                    ],
                    limit=var_safety["limit"],
                    display_values=True,
                ),
            )

        return format_response(
            data={
                "flow": mask_sensitive_fields(flow_record),
                "variables": [mask_sensitive_fields(v) for v in var_result["records"]],
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def flow_map(
        flow_sys_id: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Map the structure of a Flow Designer flow: action instances and logic blocks.

        Args:
            flow_sys_id: The sys_id of the sys_hub_flow record.
        """
        validate_identifier(flow_sys_id)
        check_table_access("sys_hub_flow")
        check_table_access("sys_hub_action_instance")
        check_table_access("sys_hub_flow_logic")

        async with ServiceNowClient(settings, auth_provider) as client:
            # display_values=False is intentional so latest_snapshot/master_snapshot stay raw sys_ids.
            flow_record = await client.get_record("sys_hub_flow", flow_sys_id, display_values=False)
            flow_map_target = _resolve_flow_map_target(flow_record, flow_sys_id)

            action_query = ServiceNowQuery().equals("flow", flow_map_target).order_by("position").build()
            logic_query = ServiceNowQuery().equals("flow", flow_map_target).order_by("position").build()

            action_safety = enforce_query_safety("sys_hub_action_instance", action_query, 100, settings)
            logic_safety = enforce_query_safety("sys_hub_flow_logic", logic_query, 100, settings)

            action_result, logic_result = await asyncio.gather(
                client.query_records(
                    "sys_hub_action_instance",
                    action_query,
                    fields=[
                        "sys_id",
                        "name",
                        "action_type",
                        "order",
                        "position",
                        "sys_created_on",
                    ],
                    limit=action_safety["limit"],
                    display_values=True,
                ),
                client.query_records(
                    "sys_hub_flow_logic",
                    logic_query,
                    fields=[
                        "sys_id",
                        "name",
                        "type",
                        "order",
                        "position",
                        "sys_created_on",
                    ],
                    limit=logic_safety["limit"],
                    display_values=True,
                ),
            )

        return format_response(
            data={
                "actions": [mask_sensitive_fields(a) for a in action_result["records"]],
                "logic_blocks": [mask_sensitive_fields(lb) for lb in logic_result["records"]],
            },
            correlation_id=correlation_id,
        )


def register_snapshot_list(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register the flow_snapshot_list tool.

    Kept separate from ``register_core`` so the package coordinator can preserve the
    original registration order, which interleaves action and execution tools between
    the core definition tools and snapshot listing.
    """

    @mcp.tool()
    @tool_handler
    async def flow_snapshot_list(
        flow_sys_id: str,
        limit: int = 20,
        *,
        correlation_id: str = "",
    ) -> str:
        """List published snapshots (versions) for a Flow Designer flow.

        Args:
            flow_sys_id: The sys_id of the sys_hub_flow record.
            limit: Maximum number of snapshots to return (default 20).
        """
        validate_identifier(flow_sys_id)
        check_table_access("sys_hub_flow_snapshot")

        query = ServiceNowQuery().equals("parent_flow", flow_sys_id).order_by("sys_created_on", descending=True).build()
        safety = enforce_query_safety("sys_hub_flow_snapshot", query, limit, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "sys_hub_flow_snapshot",
                query,
                fields=[
                    "sys_id",
                    "name",
                    "parent_flow",
                    "version",
                    "status",
                    "sys_created_on",
                    "sys_updated_on",
                ],
                limit=safety["limit"],
                display_values=True,
            )

        snapshots = [mask_sensitive_fields(s) for s in result["records"]]

        return format_response(
            data={"snapshots": snapshots},
            correlation_id=correlation_id,
        )
