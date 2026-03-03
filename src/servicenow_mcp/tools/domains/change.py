"""Change Management domain tools for ServiceNow MCP server."""

import uuid

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields, write_gate
from servicenow_mcp.utils import ServiceNowQuery, format_response, safe_tool_call

CHANGE_STATE_MAP: dict[str, str] = {
    "new": "-5",
    "assess": "-4",
    "authorize": "-3",
    "scheduled": "-2",
    "implement": "-1",
    "review": "0",
    "closed": "3",
    "canceled": "4",
}


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register Change Management domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
    """

    @mcp.tool()
    async def change_list(
        state: str = "",
        type: str = "",
        risk: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,type,risk,assignment_group,sys_created_on",
        limit: int = 20,
    ) -> str:
        """List change requests with optional filters.

        Args:
            state: Change state (new, assess, authorize, scheduled, implement, review, closed, canceled)
            type: Change type (standard, normal, emergency)
            risk: Risk level
            assignment_group: sys_id or name of assignment group
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_request")

            q = ServiceNowQuery()
            if state and state.lower() in CHANGE_STATE_MAP:
                q = q.equals("state", CHANGE_STATE_MAP[state.lower()])
            q = q.equals_if("type", type, bool(type))
            q = q.equals_if("risk", risk, bool(risk))
            q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))

            query = q.build()
            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="change_request",
                    query=query,
                    fields=field_list,
                    display_values=True,
                    limit=limit,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def change_get(number: str) -> str:
        """Fetch change request by CHG number.

        Args:
            number: Change request number (must start with CHG prefix)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_request")

            if not number.upper().startswith("CHG"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid change request number: {number}. Must start with CHG prefix.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="change_request",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    display_values=True,
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Change request {number} not found.",
                    )
                masked = mask_sensitive_fields(result["records"][0])
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def change_create(
        short_description: str,
        description: str = "",
        type: str = "normal",
        risk: str = "",
        assignment_group: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Create a new change request.

        Args:
            short_description: Brief description (required)
            description: Detailed description
            type: Change type (standard, normal, emergency, default: normal)
            risk: Risk level
            assignment_group: sys_id or name of assignment group
            start_date: Planned start date
            end_date: Planned end date
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_request")

            blocked = write_gate("change_request", settings, correlation_id)
            if blocked:
                return blocked

            if not short_description or not short_description.strip():
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="short_description is required and cannot be empty.",
                )

            valid_types = ["standard", "normal", "emergency"]
            if type and type not in valid_types:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"type must be one of {valid_types}, got '{type}'.",
                )

            record_data = {
                "short_description": short_description,
                "type": type,
            }

            if description:
                record_data["description"] = description
            if risk:
                record_data["risk"] = risk
            if assignment_group:
                record_data["assignment_group"] = assignment_group
            if start_date:
                record_data["start_date"] = start_date
            if end_date:
                record_data["end_date"] = end_date

            async with ServiceNowClient(settings, auth_provider) as client:
                created = await client.create_record("change_request", record_data)
                masked = mask_sensitive_fields(created)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def change_update(
        number: str,
        short_description: str = "",
        description: str = "",
        type: str = "",
        risk: str = "",
        assignment_group: str = "",
        state: str = "",
    ) -> str:
        """Update an existing change request by CHG number.

        Args:
            number: Change request number (must start with CHG prefix)
            short_description: Brief description
            description: Detailed description
            type: Change type (standard, normal, emergency)
            risk: Risk level
            assignment_group: sys_id or name of assignment group
            state: Change state (new, assess, authorize, scheduled, implement, review, closed, canceled)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_request")

            blocked = write_gate("change_request", settings, correlation_id)
            if blocked:
                return blocked

            if not number.upper().startswith("CHG"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid change request number: {number}. Must start with CHG prefix.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="change_request",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Change request {number} not found.",
                    )

                sys_id = result["records"][0]["sys_id"]

                changes = {}
                if short_description:
                    changes["short_description"] = short_description
                if description:
                    changes["description"] = description
                if type:
                    changes["type"] = type
                if risk:
                    changes["risk"] = risk
                if assignment_group:
                    changes["assignment_group"] = assignment_group
                if state and state.lower() in CHANGE_STATE_MAP:
                    changes["state"] = CHANGE_STATE_MAP[state.lower()]

                if not changes:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="No fields to update provided.",
                    )

                updated = await client.update_record("change_request", sys_id, changes)
                masked = mask_sensitive_fields(updated)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def change_tasks(
        number: str,
        fields: str = "number,short_description,state,assignment_group,assigned_to,sys_created_on",
        limit: int = 20,
    ) -> str:
        """Get change tasks for a change request.

        Args:
            number: Change request number (must start with CHG prefix)
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_task")

            if not number.upper().startswith("CHG"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid change request number: {number}. Must start with CHG prefix.",
                )

            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="change_task",
                    query=ServiceNowQuery().equals("change_request.number", number.upper()).build(),
                    fields=field_list,
                    display_values=True,
                    limit=limit,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def change_add_comment(
        number: str,
        comment: str = "",
        work_note: str = "",
    ) -> str:
        """Add comment or work note to a change request.

        Args:
            number: Change request number (must start with CHG prefix)
            comment: Customer-visible comment
            work_note: Internal work note
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("change_request")

            blocked = write_gate("change_request", settings, correlation_id)
            if blocked:
                return blocked

            if not number.upper().startswith("CHG"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid change request number: {number}. Must start with CHG prefix.",
                )

            if not comment and not work_note:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="At least one of comment or work_note must be provided.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="change_request",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Change request {number} not found.",
                    )

                sys_id = result["records"][0]["sys_id"]

                changes = {}
                if comment:
                    changes["comments"] = comment
                if work_note:
                    changes["work_notes"] = work_note

                updated = await client.update_record("change_request", sys_id, changes)
                masked = mask_sensitive_fields(updated)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)
