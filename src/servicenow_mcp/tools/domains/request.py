"""Request Management domain tools."""

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.tools.domains._helpers import (
    fetch_record_by_number,
    lookup_record_by_number,
    parse_field_list,
    resolve_state,
    validate_no_empty_changes,
    validate_number_prefix,
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
            resolved = await resolve_state("sc_request", state, choices)
            q = q.equals_if("state", resolved, True)
        q = q.equals_if("requested_for", requested_for, bool(requested_for))
        q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))
        query = q.build()
        field_list = parse_field_list(fields)

        safety = enforce_query_safety("sc_request", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_request",
                query=query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
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

        err = validate_number_prefix(number, "REQ", "request", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            return await fetch_record_by_number(client, "sc_request", number, "Request", correlation_id)

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

        err = validate_number_prefix(number, "REQ", "request", correlation_id)
        if err:
            return err

        field_list = parse_field_list(fields)
        query = ServiceNowQuery().equals("request.number", number.upper()).build()

        safety = enforce_query_safety("sc_req_item", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="sc_req_item",
                query=query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
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

        err = validate_number_prefix(number, "RITM", "request item", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            return await fetch_record_by_number(client, "sc_req_item", number, "Request item", correlation_id)

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

        err = validate_number_prefix(number, "RITM", "request item", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "sc_req_item", number, "Request item", correlation_id)
            if err:
                return err

            changes = {}
            if state:
                changes["state"] = await resolve_state("sc_req_item", state, choices)
            if assignment_group:
                changes["assignment_group"] = assignment_group
            if assigned_to:
                changes["assigned_to"] = assigned_to

            err = validate_no_empty_changes(changes, correlation_id)
            if err:
                return err

            updated = await client.update_record("sc_req_item", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)
