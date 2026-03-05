"""Knowledge Management domain tools."""

import re

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
from servicenow_mcp.tools.domains._helpers import parse_field_list, validate_no_empty_changes, validate_required_string
from servicenow_mcp.utils import ServiceNowQuery, format_response


_SYS_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-f0-9]{32}$")


def _collect_non_empty(**fields: str) -> dict[str, str]:
    """Collect non-empty string values into a dict."""
    return {k: v for k, v in fields.items() if v}


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register Knowledge Management tools with MCP server.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """
    _ = choices  # Accepted for interface conformance with domain tool convention

    async def _resolve_article_sys_id(
        client: ServiceNowClient,
        number_or_sys_id: str,
        correlation_id: str,
        display_values: bool = False,
    ) -> tuple[str, dict[str, str] | None, str | None]:
        """Resolve a KB number or sys_id to (sys_id, record_or_None, error_or_None).

        When display_values=True, returns the full record (for knowledge_get).
        Otherwise returns just the sys_id (for update/feedback).
        """
        is_sys_id = bool(_SYS_ID_PATTERN.match(number_or_sys_id.lower()))

        result = await client.query_records(
            table="kb_knowledge",
            query=ServiceNowQuery().equals("number", number_or_sys_id.upper()).build(),
            display_values=display_values,
            limit=1,
        )

        if not result["records"] and is_sys_id:
            result = await client.query_records(
                table="kb_knowledge",
                query=ServiceNowQuery().equals("sys_id", number_or_sys_id).build(),
                display_values=display_values,
                limit=1,
            )

        if not result["records"]:
            return (
                "",
                None,
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Knowledge article '{number_or_sys_id}' not found.",
                ),
            )

        record = result["records"][0]
        return record["sys_id"], record, None

    @mcp.tool()
    @tool_handler
    async def knowledge_search(
        query: str,
        workflow_state: str = "published",
        fields: str = "number,short_description,workflow_state,kb_knowledge_base,sys_created_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Search knowledge articles with fuzzy text matching.

        Args:
            query: Search text to match in short_description or text fields
            workflow_state: Filter by workflow state (default "published")
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum number of results to return (default 20)
        """
        check_table_access("kb_knowledge")

        # Build LIKE query for fuzzy search in short_description and text fields
        search_query = (
            ServiceNowQuery()
            .like("short_description", query)
            .or_condition("text", "LIKE", query)
            .equals("workflow_state", workflow_state)
            .build()
        )
        field_list = parse_field_list(fields)

        safety = enforce_query_safety("kb_knowledge", search_query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="kb_knowledge",
                query=search_query,
                fields=field_list,
                display_values=True,
                limit=effective_limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def knowledge_get(
        number_or_sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch a knowledge article by KB number or sys_id.

        Args:
            number_or_sys_id: KB number (e.g. KB0010001) or sys_id (32-char hex)
        """
        check_table_access("kb_knowledge")

        async with ServiceNowClient(settings, auth_provider) as client:
            _sys_id, record, err = await _resolve_article_sys_id(
                client, number_or_sys_id, correlation_id, display_values=True
            )
            if err:
                return err

            assert record is not None  # guaranteed when err is None
            masked = mask_sensitive_fields(record)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def knowledge_create(
        short_description: str,
        text: str,
        kb_knowledge_base: str = "",
        kb_category: str = "",
        workflow_state: str = "draft",
        *,
        correlation_id: str,
    ) -> str:
        """Create a new knowledge article.

        Args:
            short_description: Article title (required)
            text: Article content (required)
            kb_knowledge_base: Knowledge base sys_id (optional)
            kb_category: Category sys_id (optional)
            workflow_state: Workflow state (default "draft")
        """
        # Validate required fields
        err = validate_required_string(short_description, "short_description", correlation_id)
        if err:
            return err

        err = validate_required_string(text, "text", correlation_id)
        if err:
            return err

        check_table_access("kb_knowledge")

        # Check write gate
        gate_error = write_gate("kb_knowledge", settings, correlation_id)
        if gate_error:
            return gate_error

        # Build data dict with provided fields
        data = {
            "short_description": short_description,
            "text": text,
            "workflow_state": workflow_state,
        }
        if kb_knowledge_base:
            data["kb_knowledge_base"] = kb_knowledge_base
        if kb_category:
            data["kb_category"] = kb_category

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.create_record(table="kb_knowledge", data=data)
            masked = mask_sensitive_fields(result)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def knowledge_update(
        number_or_sys_id: str,
        short_description: str = "",
        text: str = "",
        workflow_state: str = "",
        kb_knowledge_base: str = "",
        kb_category: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Update a knowledge article by KB number or sys_id.

        Args:
            number_or_sys_id: KB number (e.g. KB0010001) or sys_id (32-char hex)
            short_description: Updated article title (optional)
            text: Updated article content (optional)
            workflow_state: Updated workflow state (optional)
            kb_knowledge_base: Updated knowledge base sys_id (optional)
            kb_category: Updated category sys_id (optional)
        """
        check_table_access("kb_knowledge")

        # Check write gate
        gate_error = write_gate("kb_knowledge", settings, correlation_id)
        if gate_error:
            return gate_error

        async with ServiceNowClient(settings, auth_provider) as client:
            sys_id, _, err = await _resolve_article_sys_id(client, number_or_sys_id, correlation_id)
            if err:
                return err

            changes = _collect_non_empty(
                short_description=short_description,
                text=text,
                workflow_state=workflow_state,
                kb_knowledge_base=kb_knowledge_base,
                kb_category=kb_category,
            )

            err = validate_no_empty_changes(changes, correlation_id)
            if err:
                return err

            update_result = await client.update_record(table="kb_knowledge", sys_id=sys_id, data=changes)
            masked = mask_sensitive_fields(update_result)
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def knowledge_feedback(
        number_or_sys_id: str,
        rating: int | None = None,
        comment: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Submit feedback (rating or comment) for a knowledge article.

        Creates a feedback record in the kb_feedback table linked to the article.

        Args:
            number_or_sys_id: KB number (e.g. KB0010001) or sys_id (32-char hex)
            rating: Rating value from 1 to 5 (optional, None means not provided)
            comment: Feedback comment (optional)
        """
        # Validate at least one feedback type provided
        if rating is None and not comment.strip():
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Must provide rating or comment.",
            )

        # Validate rating range if provided
        if rating is not None and (rating < 1 or rating > 5):
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Rating must be between 1 and 5.",
            )

        check_table_access("kb_knowledge")
        check_table_access("kb_feedback")

        gate_error = write_gate("kb_feedback", settings, correlation_id)
        if gate_error:
            return gate_error

        async with ServiceNowClient(settings, auth_provider) as client:
            article_sys_id, _, err = await _resolve_article_sys_id(client, number_or_sys_id, correlation_id)
            if err:
                return err

            feedback_data: dict[str, str] = {"article": article_sys_id}
            if rating is not None:
                feedback_data["rating"] = str(rating)
            if comment.strip():
                feedback_data["comments"] = comment

            created = await client.create_record(
                table="kb_feedback",
                data=feedback_data,
            )
            masked = mask_sensitive_fields(created)
            return format_response(data=masked, correlation_id=correlation_id)
