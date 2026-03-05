"""Problem Management domain tools for ServiceNow MCP server."""

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
from servicenow_mcp.tools.domains._helpers import (
    fetch_record_by_number,
    lookup_record_by_number,
    parse_field_list,
    resolve_state,
    validate_int_range,
    validate_no_empty_changes,
    validate_number_prefix,
    validate_required_string,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register Problem Management domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """

    @mcp.tool()
    @tool_handler
    async def problem_list(
        state: str = "",
        priority: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,priority,problem_state,assignment_group,assigned_to,sys_created_on,sys_updated_on",
        limit: int = 20,
        *,
        correlation_id: str,
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
        check_table_access("problem")

        q = ServiceNowQuery()
        if state and state != "all":
            resolved = await resolve_state("problem", state, choices)
            q = q.equals_if("state", resolved, True)
        q = q.equals_if("priority", priority, bool(priority))
        q = q.equals_if("assigned_to", assigned_to, bool(assigned_to))
        q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))
        query = q.build()
        field_list = parse_field_list(fields)

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

    @mcp.tool()
    @tool_handler
    async def problem_get(number: str, *, correlation_id: str) -> str:
        """Fetch problem by PRB number.

        Args:
            number: Problem number (must start with PRB prefix)
        """
        check_table_access("problem")

        err = validate_number_prefix(number, "PRB", "problem", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            return await fetch_record_by_number(client, "problem", number, "Problem", correlation_id)

    @mcp.tool()
    @tool_handler
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
        *,
        correlation_id: str,
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
        check_table_access("problem")

        blocked = write_gate("problem", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_required_string(short_description, "short_description", correlation_id)
        if err:
            return err

        err = validate_int_range(urgency, "urgency", 1, 4, correlation_id)
        if err:
            return err

        err = validate_int_range(impact, "impact", 1, 4, correlation_id)
        if err:
            return err

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

    @mcp.tool()
    @tool_handler
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
        *,
        correlation_id: str,
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
        check_table_access("problem")

        blocked = write_gate("problem", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "PRB", "problem", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "problem", number, "Problem", correlation_id)
            if err:
                return err

            changes = {}
            if short_description:
                changes["short_description"] = short_description
            if urgency > 0:
                changes["urgency"] = str(urgency)
            if impact > 0:
                changes["impact"] = str(impact)
            if priority > 0:
                changes["priority"] = str(priority)
            if state:
                changes["state"] = await resolve_state("problem", state, choices)
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

            err = validate_no_empty_changes(changes, correlation_id)
            if err:
                return err

            updated = await client.update_record("problem", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def problem_root_cause(
        number: str,
        cause_notes: str,
        fix_notes: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Document root cause analysis for a problem.

        Args:
            number: Problem number (must start with PRB prefix)
            cause_notes: Root cause analysis notes (required)
            fix_notes: Fix notes (optional)
        """
        check_table_access("problem")

        blocked = write_gate("problem", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "PRB", "problem", correlation_id)
        if err:
            return err

        err = validate_required_string(cause_notes, "cause_notes", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "problem", number, "Problem", correlation_id)
            if err:
                return err

            changes = {"cause_notes": cause_notes}
            if fix_notes and fix_notes.strip():
                changes["fix_notes"] = fix_notes

            updated = await client.update_record("problem", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)
