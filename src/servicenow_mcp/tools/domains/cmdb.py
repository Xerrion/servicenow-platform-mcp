"""CMDB domain tools for ServiceNow MCP server."""

import json
import re
import uuid

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import format_response, safe_tool_call


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register CMDB domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
    """

    @mcp.tool()
    async def cmdb_list(
        ci_class: str = "cmdb_ci",
        operational_status: str = "",
        limit: int = 20,
    ) -> str:
        """List Configuration Items from CMDB.

        Args:
            ci_class: CMDB table/class to query (default "cmdb_ci")
            operational_status: Filter by operational status (operational=1, non_operational=2, etc.)
            limit: Maximum results to return (default 20)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access(ci_class)

            query_parts = []
            if operational_status:
                # Map human-readable status to numeric codes
                status_map = {
                    "operational": "1",
                    "non_operational": "2",
                    "repair_in_progress": "3",
                    "dr_standby": "4",
                    "ready": "5",
                    "retired": "6",
                }
                status_value = status_map.get(operational_status.lower(), operational_status)
                query_parts.append(f"operational_status={status_value}")

            query = "^".join(query_parts) if query_parts else ""

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table=ci_class,
                    query=query,
                    display_values=True,
                    limit=limit,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def cmdb_get(
        name_or_sys_id: str,
        ci_class: str = "cmdb_ci",
    ) -> str:
        """Fetch a Configuration Item by name or sys_id.

        Args:
            name_or_sys_id: CI name or sys_id (32-char hex string)
            ci_class: CMDB table/class to query (default "cmdb_ci")
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access(ci_class)

            # Detect if input is sys_id (32-char hex string)
            is_sys_id = bool(re.match(r"^[a-f0-9]{32}$", name_or_sys_id.lower()))

            query = f"sys_id={name_or_sys_id}" if is_sys_id else f"name={name_or_sys_id}"

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table=ci_class,
                    query=query,
                    display_values=True,
                    limit=1,
                )
                if not result["records"]:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"CI '{name_or_sys_id}' not found in {ci_class}.",
                        )
                    )
                masked = mask_sensitive_fields(result["records"][0])
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def cmdb_relationships(
        name_or_sys_id: str,
        direction: str = "both",
        ci_class: str = "cmdb_ci",
    ) -> str:
        """Fetch CMDB relationships for a Configuration Item.

        Args:
            name_or_sys_id: CI name or sys_id (32-char hex string)
            direction: Relationship direction - "parent", "child", or "both" (default "both")
            ci_class: CMDB table/class to query for name resolution (default "cmdb_ci")
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access(ci_class)
            check_table_access("cmdb_rel_ci")

            # Detect if input is sys_id (32-char hex string)
            is_sys_id = bool(re.match(r"^[a-f0-9]{32}$", name_or_sys_id.lower()))

            async with ServiceNowClient(settings, auth_provider) as client:
                # If name provided, resolve to sys_id first
                if not is_sys_id:
                    lookup_result = await client.query_records(
                        table=ci_class,
                        query=f"name={name_or_sys_id}",
                        limit=1,
                    )
                    if not lookup_result["records"]:
                        return json.dumps(
                            format_response(
                                data=None,
                                correlation_id=correlation_id,
                                status="error",
                                error=f"CI '{name_or_sys_id}' not found in {ci_class}.",
                            )
                        )
                    ci_sys_id = lookup_result["records"][0]["sys_id"]
                else:
                    ci_sys_id = name_or_sys_id

                # Build relationship query based on direction
                if direction == "parent":
                    query = f"child.sys_id={ci_sys_id}"
                elif direction == "child":
                    query = f"parent.sys_id={ci_sys_id}"
                elif direction == "both":
                    query = f"child.sys_id={ci_sys_id}^ORparent.sys_id={ci_sys_id}"
                else:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Invalid direction: {direction}. Must be 'parent', 'child', or 'both'.",
                        )
                    )

                result = await client.query_records(
                    table="cmdb_rel_ci",
                    query=query,
                    display_values=True,
                    limit=100,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def cmdb_classes(
        limit: int = 50,
    ) -> str:
        """List unique CI classes in CMDB using aggregate API.

        Args:
            limit: Maximum number of classes to return (default 50)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("cmdb_ci")

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.aggregate(
                    table="cmdb_ci",
                    query="",
                    group_by="sys_class_name",
                )
                return json.dumps(format_response(data=result, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def cmdb_health(
        ci_class: str = "cmdb_ci",
    ) -> str:
        """Check CMDB health by aggregating operational status.

        Args:
            ci_class: CMDB table/class to check (default "cmdb_ci")
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access(ci_class)

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.aggregate(
                    table=ci_class,
                    query="",
                    group_by="operational_status",
                )
                return json.dumps(format_response(data=result, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)
