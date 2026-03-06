"""Developer utility tools for toggling artifacts and managing system properties."""

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    MASK_VALUE,
    check_table_access,
    is_sensitive_field,
    write_blocked_reason,
)
from servicenow_mcp.tools.metadata import ARTIFACT_TABLES
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    validate_identifier,
)


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register developer utility tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def dev_toggle(artifact_type: str, sys_id: str, active: bool, *, correlation_id: str) -> str:
        """Toggle the active field on a ServiceNow artifact (business rule, script include, etc.).

        Args:
            artifact_type: The type of artifact (e.g. 'business_rule', 'script_include').
            sys_id: The sys_id of the artifact record.
            active: Whether to set the artifact active (true) or inactive (false).
        """
        # Resolve artifact type to table name
        table = ARTIFACT_TABLES.get(artifact_type)
        if table is None:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=(
                    f"Unknown artifact type: '{artifact_type}'. "
                    f"Valid types: {', '.join(sorted(ARTIFACT_TABLES.keys()))}"
                ),
            )

        check_table_access(table)
        validate_identifier(sys_id)

        # Write gate
        reason = write_blocked_reason(table, settings)
        if reason:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=reason,
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            # Read current state
            current = await client.get_record(table, sys_id)
            old_active = current.get("active", "unknown")

            # Update active field
            updated = await client.update_record(table, sys_id, {"active": str(active).lower()})
            new_active = updated.get("active", "unknown")

        return format_response(
            data={
                "sys_id": sys_id,
                "artifact_type": artifact_type,
                "table": table,
                "old_active": old_active,
                "new_active": new_active,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def dev_set_property(name: str, value: str, *, correlation_id: str) -> str:
        """Set a ServiceNow system property value. Returns the old value.

        Args:
            name: The property name (e.g. 'glide.ui.session_timeout').
            value: The new value to set.
        """
        # Write gate
        check_table_access("sys_properties")
        reason = write_blocked_reason("sys_properties", settings)
        if reason:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=reason,
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            # Find the property by name
            result = await client.query_records(
                "sys_properties",
                ServiceNowQuery().equals("name", name).build(),
                limit=1,
            )
            records = result["records"]
            if not records:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Property '{name}' not found",
                )

            prop = records[0]
            prop_sys_id = prop["sys_id"]
            old_value = prop.get("value", "")

            # Update the property value
            updated = await client.update_record("sys_properties", prop_sys_id, {"value": value})
            new_value = updated.get("value", value)

        # Mask values for sensitive property names
        display_old = MASK_VALUE if is_sensitive_field(name) else old_value
        display_new = MASK_VALUE if is_sensitive_field(name) else new_value

        return format_response(
            data={
                "name": name,
                "sys_id": prop_sys_id,
                "old_value": display_old,
                "new_value": display_new,
            },
            correlation_id=correlation_id,
        )
