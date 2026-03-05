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
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response


_SYS_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-f0-9]{32}$")


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
    ) -> tuple[str | None, str | None]:
        """Resolve a KB article identifier to its sys_id.

        Tries lookup by number first, then falls back to sys_id if the input
        matches the 32-char hex pattern.

        Returns:
            Tuple of (sys_id, None) on success, or (None, error_response) on failure.
        """
        normalized_input = number_or_sys_id.lower()
        is_sys_id = bool(_SYS_ID_PATTERN.match(normalized_input))

        result = await client.query_records(
            table="kb_knowledge",
            query=ServiceNowQuery().equals("number", number_or_sys_id.upper()).build(),
            limit=1,
        )

        if not result["records"] and is_sys_id:
            result = await client.query_records(
                table="kb_knowledge",
                query=ServiceNowQuery().equals("sys_id", normalized_input).build(),
                limit=1,
            )

        if not result["records"]:
            return None, format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Knowledge article '{number_or_sys_id}' not found.",
            )

        return result["records"][0]["sys_id"], None

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
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table="kb_knowledge",
                query=search_query,
                fields=field_list,
                display_values=True,
                limit=limit,
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

        # Detect if input is sys_id (32-char lowercase hex string)
        is_sys_id = bool(_SYS_ID_PATTERN.match(number_or_sys_id.lower()))

        async with ServiceNowClient(settings, auth_provider) as client:
            # Try lookup by number first
            result = await client.query_records(
                table="kb_knowledge",
                query=ServiceNowQuery().equals("number", number_or_sys_id.upper()).build(),
                display_values=True,
                limit=1,
            )

            # If not found and looks like sys_id, try sys_id lookup
            if not result["records"] and is_sys_id:
                result = await client.query_records(
                    table="kb_knowledge",
                    query=ServiceNowQuery().equals("sys_id", number_or_sys_id).build(),
                    display_values=True,
                    limit=1,
                )

            if not result["records"]:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Knowledge article '{number_or_sys_id}' not found.",
                )

            masked = mask_sensitive_fields(result["records"][0])
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
        if not short_description.strip():
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="short_description is required and cannot be empty.",
            )

        if not text.strip():
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="text is required and cannot be empty.",
            )

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
            sys_id, error = await _resolve_article_sys_id(client, number_or_sys_id, correlation_id)
            if error or not sys_id:
                return error or format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Failed to resolve article '{number_or_sys_id}'",
                )

            # Build changes dict (only include non-empty params)
            changes: dict[str, str] = {}
            if short_description:
                changes["short_description"] = short_description
            if text:
                changes["text"] = text
            if workflow_state:
                changes["workflow_state"] = workflow_state
            if kb_knowledge_base:
                changes["kb_knowledge_base"] = kb_knowledge_base
            if kb_category:
                changes["kb_category"] = kb_category

            if not changes:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="No fields to update provided.",
                )

            update_result = await client.update_record(
                table="kb_knowledge",
                sys_id=sys_id,
                data=changes,
            )
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
            article_sys_id, error = await _resolve_article_sys_id(client, number_or_sys_id, correlation_id)
            if error or not article_sys_id:
                return error or format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Failed to resolve article '{number_or_sys_id}'",
                )

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
