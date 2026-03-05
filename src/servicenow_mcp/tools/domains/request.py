"""Request Management domain tools."""

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register Request Management tools with MCP server.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """

    @mcp.tool()
    @tool_handler
    async def request_list(
        state: str = "",
        requested_for: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,requested_for,assignment_group,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """List requests with optional filters.

        Args:
            state: Request state filter
            requested_for: sys_id of requested_for user
            assignment_group: sys_id or name of assignment group
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access("sc_request")

        q = ServiceNowQuery()
        if state:
            resolved = await choices.resolve("sc_request", "state", state.lower()) if choices else state
            q = q.equals_if("state", resolved, True)
        q = q.equals_if("requested_for", requested_for, bool(requested_for))
        q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))
        query = q.build()
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_request",
                query=query,
                fields=field_list,
                display_values=True,
                limit=limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def request_get(number: str, *, correlation_id: str) -> str:
        """Fetch request by REQ number.

        Args:
            number: Request number (must start with REQ prefix)
        """
        check_table_access("sc_request")

        if not number.upper().startswith("REQ"):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid request number: {number}. Must start with REQ prefix.",
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_request",
                query=ServiceNowQuery().equals("number", number.upper()).build(),
                display_values=True,
                limit=1,
            )
            if not result["records"]:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Request {number} not found.",
                )
            masked = mask_sensitive_fields(result["records"][0])
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def request_items(
        number: str,
        fields: str = "number,short_description,state,assignment_group,assigned_to,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch request items (RITMs) for a request.

        Args:
            number: Request number (must start with REQ prefix)
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access("sc_req_item")

        if not number.upper().startswith("REQ"):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid request number: {number}. Must start with REQ prefix.",
            )

        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_req_item",
                query=ServiceNowQuery().equals("request.number", number.upper()).build(),
                fields=field_list,
                display_values=True,
                limit=limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def request_item_get(number: str, *, correlation_id: str) -> str:
        """Fetch request item by RITM number.

        Args:
            number: Request item number (must start with RITM prefix)
        """
        check_table_access("sc_req_item")

        if not number.upper().startswith("RITM"):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid request item number: {number}. Must start with RITM prefix.",
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_req_item",
                query=ServiceNowQuery().equals("number", number.upper()).build(),
                display_values=True,
                limit=1,
            )
            if not result["records"]:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Request item {number} not found.",
                )
            masked = mask_sensitive_fields(result["records"][0])
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def request_item_update(
        number: str,
        state: str = "",
        assignment_group: str = "",
        assigned_to: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Update a request item by RITM number.

        Args:
            number: Request item number (must start with RITM prefix)
            state: Request item state
            assignment_group: sys_id or name of assignment group
            assigned_to: sys_id of assigned user
        """
        check_table_access("sc_req_item")

        blocked = write_gate("sc_req_item", settings, correlation_id)
        if blocked:
            return blocked

        if not number.upper().startswith("RITM"):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Invalid request item number: {number}. Must start with RITM prefix.",
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_req_item",
                query=ServiceNowQuery().equals("number", number.upper()).build(),
                limit=1,
            )
            if not result["records"]:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Request item {number} not found.",
                )

            sys_id = result["records"][0]["sys_id"]

            changes = {}
            if state:
                changes["state"] = await choices.resolve("sc_req_item", "state", state.lower()) if choices else state
            if assignment_group:
                changes["assignment_group"] = assignment_group
            if assigned_to:
                changes["assigned_to"] = assigned_to

            if not changes:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="No fields to update provided.",
                )

            updated = await client.update_record("sc_req_item", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)
