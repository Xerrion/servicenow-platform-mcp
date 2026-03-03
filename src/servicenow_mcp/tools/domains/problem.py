"""Problem Management domain tools for ServiceNow MCP server."""

import uuid

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields, write_gate
from servicenow_mcp.utils import ServiceNowQuery, format_response, safe_tool_call

PROBLEM_STATE_MAP: dict[str, str] = {
    "new": "1",
    "in_progress": "2",
    "known_error": "3",
    "root_cause_analysis": "4",
    "fix_in_progress": "5",
    "resolved": "6",
    "closed": "7",
}


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register Problem Management domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
    """

    @mcp.tool()
    async def problem_list(
        state: str = "",
        priority: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,priority,problem_state,assignment_group,assigned_to,sys_created_on,sys_updated_on",
        limit: int = 20,
    ) -> str:
        """List problems with optional filters.

        Args:
            state: Problem state (new, in_progress, known_error, root_cause_analysis, fix_in_progress, resolved, closed, all)
            priority: Priority level (1-5)
            assigned_to: sys_id of assigned user
            assignment_group: sys_id or name of assignment group
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("problem")

            q = ServiceNowQuery()
            if state and state != "all" and state.lower() in PROBLEM_STATE_MAP:
                q = q.equals("state", PROBLEM_STATE_MAP[state.lower()])
            q = q.equals_if("priority", priority, bool(priority))
            q = q.equals_if("assigned_to", assigned_to, bool(assigned_to))
            q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))
            query = q.build()
            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="problem",
                    query=query,
                    fields=field_list,
                    display_values=True,
                    limit=limit,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def problem_get(number: str) -> str:
        """Fetch problem by PRB number.

        Args:
            number: Problem number (must start with PRB prefix)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("problem")

            if not number.upper().startswith("PRB"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid problem number: {number}. Must start with PRB prefix.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="problem",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    display_values=True,
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Problem {number} not found.",
                    )
                masked = mask_sensitive_fields(result["records"][0])
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def problem_create(
        short_description: str,
        urgency: int = 3,
        impact: int = 3,
        priority: int = 3,
        description: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        category: str = "",
        subcategory: str = "",
    ) -> str:
        """Create a new problem.

        Args:
            short_description: Brief description (required)
            urgency: Urgency level (1-4, default 3)
            impact: Impact level (1-4, default 3)
            priority: Priority level (1-5, default 3)
            description: Detailed description
            assigned_to: sys_id of assigned user
            assignment_group: sys_id or name of assignment group
            category: Category
            subcategory: Subcategory
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("problem")

            blocked = write_gate("problem", settings, correlation_id)
            if blocked:
                return blocked

            if not short_description or not short_description.strip():
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="short_description is required and cannot be empty.",
                )

            if urgency < 1 or urgency > 4:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"urgency must be between 1 and 4, got {urgency}.",
                )

            if impact < 1 or impact > 4:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"impact must be between 1 and 4, got {impact}.",
                )

            record_data = {
                "short_description": short_description,
                "urgency": str(urgency),
                "impact": str(impact),
                "priority": str(priority),
            }

            if description:
                record_data["description"] = description
            if assigned_to:
                record_data["assigned_to"] = assigned_to
            if assignment_group:
                record_data["assignment_group"] = assignment_group
            if category:
                record_data["category"] = category
            if subcategory:
                record_data["subcategory"] = subcategory

            async with ServiceNowClient(settings, auth_provider) as client:
                created = await client.create_record("problem", record_data)
                masked = mask_sensitive_fields(created)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def problem_update(
        number: str,
        short_description: str = "",
        urgency: int = 0,
        impact: int = 0,
        priority: int = 0,
        state: str = "",
        description: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        category: str = "",
        subcategory: str = "",
    ) -> str:
        """Update an existing problem by PRB number.

        Args:
            number: Problem number (must start with PRB prefix)
            short_description: Brief description
            urgency: Urgency level (1-4)
            impact: Impact level (1-4)
            priority: Priority level (1-5)
            state: State (new, in_progress, known_error, root_cause_analysis, fix_in_progress, resolved, closed)
            description: Detailed description
            assigned_to: sys_id of assigned user
            assignment_group: sys_id or name of assignment group
            category: Category
            subcategory: Subcategory
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("problem")

            blocked = write_gate("problem", settings, correlation_id)
            if blocked:
                return blocked

            if not number.upper().startswith("PRB"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid problem number: {number}. Must start with PRB prefix.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="problem",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Problem {number} not found.",
                    )

                sys_id = result["records"][0]["sys_id"]

                changes = {}
                if short_description:
                    changes["short_description"] = short_description
                if urgency > 0:
                    changes["urgency"] = str(urgency)
                if impact > 0:
                    changes["impact"] = str(impact)
                if priority > 0:
                    changes["priority"] = str(priority)
                if state and state.lower() in PROBLEM_STATE_MAP:
                    changes["state"] = PROBLEM_STATE_MAP[state.lower()]
                if description:
                    changes["description"] = description
                if assigned_to:
                    changes["assigned_to"] = assigned_to
                if assignment_group:
                    changes["assignment_group"] = assignment_group
                if category:
                    changes["category"] = category
                if subcategory:
                    changes["subcategory"] = subcategory

                if not changes:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="No fields to update provided.",
                    )

                updated = await client.update_record("problem", sys_id, changes)
                masked = mask_sensitive_fields(updated)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def problem_root_cause(
        number: str,
        cause_notes: str,
        fix_notes: str = "",
    ) -> str:
        """Document root cause analysis for a problem.

        Args:
            number: Problem number (must start with PRB prefix)
            cause_notes: Root cause analysis notes (required)
            fix_notes: Fix notes (optional)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("problem")

            blocked = write_gate("problem", settings, correlation_id)
            if blocked:
                return blocked

            if not number.upper().startswith("PRB"):
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid problem number: {number}. Must start with PRB prefix.",
                )

            if not cause_notes or not cause_notes.strip():
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="cause_notes is required and cannot be empty.",
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="problem",
                    query=ServiceNowQuery().equals("number", number.upper()).build(),
                    limit=1,
                )
                if not result["records"]:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Problem {number} not found.",
                    )

                sys_id = result["records"][0]["sys_id"]

                changes = {"cause_notes": cause_notes}
                if fix_notes and fix_notes.strip():
                    changes["fix_notes"] = fix_notes

                updated = await client.update_record("problem", sys_id, changes)
                masked = mask_sensitive_fields(updated)
                return format_response(data=masked, correlation_id=correlation_id)

        return await safe_tool_call(_run, correlation_id)
