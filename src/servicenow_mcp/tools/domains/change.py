"""Change Management domain tools for ServiceNow MCP server."""

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
    validate_required_string,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response


_CHANGE_ENTITY_LABEL = "change request"
_CHANGE_ENTITY_TITLE = "Change request"

TOOL_NAMES: list[str] = [
    "change_list",
    "change_get",
    "change_create",
    "change_update",
    "change_tasks",
    "change_add_comment",
]


# ------------------------------------------------------------------
# Module-scope helpers (extracted from register_tools closure)
# ------------------------------------------------------------------

_VALID_CHANGE_TYPES = ("standard", "normal", "emergency")

_OPTIONAL_CREATE_FIELDS = (
    "description",
    "risk",
    "assignment_group",
    "start_date",
    "end_date",
)


def _build_change_create_body(
    short_description: str,
    change_type: str,
    **optional_fields: str,
) -> dict[str, str]:
    """Build the record data dict for change request creation.

    Always includes the required fields. Optional string fields are included
    only when non-empty.
    """
    body: dict[str, str] = {
        "short_description": short_description,
        "type": change_type,
    }
    for key in _OPTIONAL_CREATE_FIELDS:
        value = optional_fields.get(key, "")
        if value:
            body[key] = value
    return body


async def _build_change_update_changes(
    short_description: str,
    description: str,
    change_type: str,
    risk: str,
    assignment_group: str,
    state: str,
    choices: ChoiceRegistry | None,
) -> dict[str, str]:
    """Build the changes dict for change request update.

    String fields are included only when non-empty.
    State is resolved via choices registry.
    """
    changes: dict[str, str] = {}
    for key, value in (
        ("short_description", short_description),
        ("description", description),
        ("type", change_type),
        ("risk", risk),
        ("assignment_group", assignment_group),
    ):
        if value:
            changes[key] = value
    if state:
        changes["state"] = await resolve_state("change_request", state, choices)
    return changes


async def _build_change_list_query(
    state: str,
    change_type: str,
    risk: str,
    assignment_group: str,
    choices: ChoiceRegistry | None,
) -> str:
    """Build the encoded query string for change request listing."""
    q = ServiceNowQuery()
    if state:
        resolved = await resolve_state("change_request", state, choices)
        q = q.equals_if("state", resolved, True)
    q = q.equals_if("type", change_type, bool(change_type))
    q = q.equals_if("risk", risk, bool(risk))
    q = q.equals_if("assignment_group", assignment_group, bool(assignment_group))
    return q.build()


def _build_comment_changes(comment: str, work_note: str) -> dict[str, str]:
    """Build changes dict for adding comments/work notes."""
    changes: dict[str, str] = {}
    if comment:
        changes["comments"] = comment
    if work_note:
        changes["work_notes"] = work_note
    return changes


# ------------------------------------------------------------------
# Tool registration
# ------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register Change Management domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """

    @mcp.tool()
    @tool_handler
    async def change_list(
        state: str = "",
        type: str = "",
        risk: str = "",
        assignment_group: str = "",
        fields: str = "number,short_description,state,type,risk,assignment_group,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """List change requests with optional filters.

        Preferred over `table_query` for the `change_request` table - resolves state/type labels, returns display values, applies sensitivity masking, and uses change-relevant default fields.

        Args:
            state: Change state (new, assess, authorize, scheduled, implement, review, closed, canceled)
            type: Change type (standard, normal, emergency)
            risk: Risk level
            assignment_group: sys_id or name of assignment group
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access("change_request")

        query = await _build_change_list_query(state, type, risk, assignment_group, choices)
        field_list = parse_field_list(fields)

        safety = enforce_query_safety("change_request", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="change_request",
                query=query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def change_get(number: str, *, correlation_id: str) -> str:
        """Fetch change request by CHG number.

        Preferred over `record_get` / `table_query` when you have a CHG number - resolves the number to a sys_id automatically.

        Args:
            number: Change request number (must start with CHG prefix)
        """
        check_table_access("change_request")

        err = validate_number_prefix(number, "CHG", _CHANGE_ENTITY_LABEL, correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            return await fetch_record_by_number(client, "change_request", number, _CHANGE_ENTITY_TITLE, correlation_id)

    @mcp.tool()
    @tool_handler
    async def change_create(
        short_description: str,
        description: str = "",
        type: str = "normal",
        risk: str = "",
        assignment_group: str = "",
        start_date: str = "",
        end_date: str = "",
        *,
        correlation_id: str,
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
        check_table_access("change_request")

        blocked = write_gate("change_request", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_required_string(short_description, "short_description", correlation_id)
        if err:
            return err

        if type and type not in _VALID_CHANGE_TYPES:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"type must be one of {list(_VALID_CHANGE_TYPES)}, got '{type}'.",
            )

        record_data = _build_change_create_body(
            short_description,
            type,
            description=description,
            risk=risk,
            assignment_group=assignment_group,
            start_date=start_date,
            end_date=end_date,
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            created = await client.create_record("change_request", record_data)
            masked = mask_sensitive_fields(created)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def change_update(
        number: str,
        short_description: str = "",
        description: str = "",
        type: str = "",
        risk: str = "",
        assignment_group: str = "",
        state: str = "",
        *,
        correlation_id: str,
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
        check_table_access("change_request")

        blocked = write_gate("change_request", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "CHG", _CHANGE_ENTITY_LABEL, correlation_id)
        if err:
            return err

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, err = await lookup_record_by_number(
                client, "change_request", number, _CHANGE_ENTITY_TITLE, correlation_id
            )
            if err:
                return err

            changes = await _build_change_update_changes(
                short_description, description, type, risk, assignment_group, state, choices
            )

            err = validate_no_empty_changes(changes, correlation_id)
            if err:
                return err

            updated = await client.update_record("change_request", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def change_tasks(
        number: str,
        fields: str = "number,short_description,state,assignment_group,assigned_to,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Get change tasks for a change request.

        Preferred over `table_query` on the `change_task` table - scopes results to a single CHG and applies sensible defaults.

        Args:
            number: Change request number (must start with CHG prefix)
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access("change_task")

        err = validate_number_prefix(number, "CHG", _CHANGE_ENTITY_LABEL, correlation_id)
        if err:
            return err

        field_list = parse_field_list(fields)

        query = ServiceNowQuery().equals("change_request.number", number.upper()).build()
        safety = enforce_query_safety("change_task", query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="change_task",
                query=query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def change_add_comment(
        number: str,
        comment: str = "",
        work_note: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Add comment or work note to a change request.

        Args:
            number: Change request number (must start with CHG prefix)
            comment: Customer-visible comment
            work_note: Internal work note
        """
        check_table_access("change_request")

        blocked = write_gate("change_request", settings, correlation_id)
        if blocked:
            return blocked

        err = validate_number_prefix(number, "CHG", _CHANGE_ENTITY_LABEL, correlation_id)
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
            sys_id, err = await lookup_record_by_number(
                client, "change_request", number, _CHANGE_ENTITY_TITLE, correlation_id
            )
            if err:
                return err

            changes = _build_comment_changes(comment, work_note)

            updated = await client.update_record("change_request", sys_id, changes)
            masked = mask_sensitive_fields(updated)
            return format_response(data=masked, correlation_id=correlation_id)
