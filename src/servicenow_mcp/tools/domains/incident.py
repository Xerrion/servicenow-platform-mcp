"""Incident Management domain tools for ServiceNow MCP server."""

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
    """Register Incident Management domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """

    @mcp.tool()
    @tool_handler
    async def incident_list(
        state: str = "",
        priority: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,priority,urgency,impact,assignment_group,assigned_to,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """List incidents with optional filters.

        Args:
            state: Incident state (open, in_progress, on_hold, resolved, closed, canceled, all)
            priority: Priority level (1-5)
            assigned_to: sys_id of assigned user
            assignment_group: sys_id or name of assignment group
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access("incident")

        q = ServiceNowQuery()
        if state and state != "all":
            resolved = await resolve_state("incident", state, choices)
            q = q.equals_if("state", resolved, True)
        q = q.equals_if("priority", priority, bool(priority))
        q = q.equals_if("assigned_to", assigned_to, bool(assigned_to))
        q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))

        query = q.build()
        field_list = parse_field_list(fields)

        safety = enforce_query_safety("incident", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="incident",
                query=query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def incident_get(number: str, *, correlation_id: str) -> str:
        """Fetch incident by INC number.

        Args:
            number: Incident number (must start with INC prefix)
        """
        check_table_access("incident")

        err = validate_number_prefix(number, "INC", "incident", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            return await fetch_record_by_number(client, "incident", number, "Incident", correlation_id)

    @mcp.tool()
    @tool_handler
    async def incident_create(
        short_description: str,
        urgency: int = 3,
        impact: int = 3,
        priority: int = 3,
        description: str = "",
        caller_id: str = "",
        assignment_group: str = "",
        assigned_to: str = "",
        category: str = "",
        subcategory: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Create a new incident.

        Args:
            short_description: Brief description (required)
            urgency: Urgency level (1-4, default 3)
            impact: Impact level (1-4, default 3)
            priority: Priority level (1-5, default 3)
            description: Detailed description
            caller_id: sys_id of caller
            assignment_group: sys_id or name of assignment group
            assigned_to: sys_id of assigned user
            category: Category
            subcategory: Subcategory
        """
        check_table_access("incident")

        blocked = write_gate("incident", settings, correlation_id)
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

        err = validate_int_range(priority, "priority", 1, 5, correlation_id)
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
        if caller_id:
            record_data["caller_id"] = caller_id
        if assignment_group:
            record_data["assignment_group"] = assignment_group
        if assigned_to:
            record_data["assigned_to"] = assigned_to
        if category:
            record_data["category"] = category
        if subcategory:
            record_data["subcategory"] = subcategory

        async with ServiceNowClient(settings, auth_provider) as client:
            created = await client.create_record("incident", record_data)
            masked = mask_sensitive_fields(created)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def incident_update(
        number: str,
        short_description: str = "",
        urgency: int = 0,
        impact: int = 0,
        priority: int = 0,
        state: str = "",
        description: str = "",
        assignment_group: str = "",
        assigned_to: str = "",
        category: str = "",
        subcategory: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Update an existing incident by INC number.

        Args:
            number: Incident number (must start with INC prefix)
            short_description: Brief description
            urgency: Urgency level (1-4)
            impact: Impact level (1-4)
            priority: Priority level (1-5)
            state: State (open, in_progress, on_hold, resolved, closed, canceled)
            description: Detailed description
            assignment_group: sys_id or name of assignment group
            assigned_to: sys_id of assigned user
            category: Category
            subcategory: Subcategory
        """
        check_table_access("incident")

        blocked = write_gate("incident", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "INC", "incident", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "incident", number, "Incident", correlation_id)
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
                changes["state"] = await resolve_state("incident", state, choices)
            if description:
                changes["description"] = description
            if assignment_group:
                changes["assignment_group"] = assignment_group
            if assigned_to:
                changes["assigned_to"] = assigned_to
            if category:
                changes["category"] = category
            if subcategory:
                changes["subcategory"] = subcategory

            err = validate_no_empty_changes(changes, correlation_id)
            if err:
                return err

            updated = await client.update_record("incident", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def incident_resolve(
        number: str,
        close_code: str,
        close_notes: str,
        *,
        correlation_id: str,
    ) -> str:
        """Resolve an incident.

        Args:
            number: Incident number (must start with INC prefix)
            close_code: Resolution code (required)
            close_notes: Resolution notes (required)
        """
        check_table_access("incident")

        blocked = write_gate("incident", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "INC", "incident", correlation_id)
        if err:
            return err

        err = validate_required_string(close_code, "close_code", correlation_id)
        if err:
            return err

        err = validate_required_string(close_notes, "close_notes", correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "incident", number, "Incident", correlation_id)
            if err:
                return err

            resolved_state = await choices.resolve("incident", "state", "resolved") if choices else "6"
            changes = {
                "state": resolved_state,
                "close_code": close_code,
                "close_notes": close_notes,
            }

            updated = await client.update_record("incident", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def incident_add_comment(
        number: str,
        comment: str = "",
        work_note: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Add comment or work note to an incident.

        Args:
            number: Incident number (must start with INC prefix)
            comment: Customer-visible comment
            work_note: Internal work note
        """
        check_table_access("incident")

        blocked = write_gate("incident", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "INC", "incident", correlation_id)
        if err:
            return err

        if not comment and not work_note:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="At least one of comment or work_note must be provided.",
            )

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(client, "incident", number, "Incident", correlation_id)
            if err:
                return err

            changes = {}
            if comment:
                changes["comments"] = comment
            if work_note:
                changes["work_notes"] = work_note

            updated = await client.update_record("incident", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)
