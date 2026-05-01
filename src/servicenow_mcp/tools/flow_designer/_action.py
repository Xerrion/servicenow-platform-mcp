"""Flow action introspection tool (sys_hub_action_instance + type definition + steps)."""

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


def register(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register the flow_action_detail tool."""

    @mcp.tool()
    @tool_handler
    async def flow_action_detail(
        action_instance_sys_id: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Fetch detailed information about a flow action instance, its type definition, and steps.

        Args:
            action_instance_sys_id: The sys_id of the sys_hub_action_instance record.
        """
        validate_identifier(action_instance_sys_id)
        check_table_access("sys_hub_action_instance")
        check_table_access("sys_hub_action_type_definition")
        check_table_access("sys_hub_step_instance")

        async with ServiceNowClient(settings, auth_provider) as client:
            # Phase 1: raw instance to extract the action_type sys_id
            raw_instance = await client.get_record(
                "sys_hub_action_instance",
                action_instance_sys_id,
                display_values=False,
            )
            action_type_sys_id = resolve_ref_value(raw_instance.get("action_type", ""))

            # Phase 2: parallel fetch of display instance, type definition (if linked), and steps
            if not action_type_sys_id:
                display_instance = await client.get_record(
                    "sys_hub_action_instance",
                    action_instance_sys_id,
                    display_values=True,
                )
                type_definition = None
                step_records: list[dict[str, Any]] = []
            else:
                validate_identifier(action_type_sys_id)

                steps_query = ServiceNowQuery().equals("action", action_type_sys_id).order_by("order").build()
                steps_safety = enforce_query_safety("sys_hub_step_instance", steps_query, 100, settings)

                display_instance, type_definition, steps_result = await asyncio.gather(
                    client.get_record(
                        "sys_hub_action_instance",
                        action_instance_sys_id,
                        display_values=True,
                    ),
                    client.get_record(
                        "sys_hub_action_type_definition",
                        action_type_sys_id,
                        display_values=True,
                    ),
                    client.query_records(
                        "sys_hub_step_instance",
                        steps_query,
                        fields=[
                            "sys_id",
                            "name",
                            "step_type",
                            "order",
                            "sys_created_on",
                        ],
                        limit=steps_safety["limit"],
                        display_values=True,
                    ),
                )
                step_records = steps_result["records"]

        return format_response(
            data={
                "instance": mask_sensitive_fields(display_instance),
                "type_definition": mask_sensitive_fields(type_definition) if type_definition else None,
                "steps": [mask_sensitive_fields(s) for s in step_records],
            },
            correlation_id=correlation_id,
        )
