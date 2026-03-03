"""Knowledge Management domain tools."""

import json
import re
import uuid

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields, write_gate
from servicenow_mcp.utils import format_response, safe_tool_call


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register Knowledge Management tools with MCP server.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
    """

    @mcp.tool()
    async def knowledge_search(
        query: str,
        workflow_state: str = "published",
        limit: int = 20,
    ) -> str:
        """Search knowledge articles with fuzzy text matching.

        Args:
            query: Search text to match in short_description or text fields
            workflow_state: Filter by workflow state (default "published")
            limit: Maximum number of results to return (default 20)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("kb_knowledge")

            # Build LIKE query for fuzzy search in short_description and text fields
            search_query = f"short_descriptionLIKE{query}^ORtextLIKE{query}^workflow_state={workflow_state}"

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table="kb_knowledge",
                    query=search_query,
                    display_values=True,
                    limit=limit,
                )
                masked = [mask_sensitive_fields(r) for r in result["records"]]
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def knowledge_get(
        number_or_sys_id: str,
    ) -> str:
        """Fetch a knowledge article by KB number or sys_id.

        Args:
            number_or_sys_id: KB number (e.g. KB0010001) or sys_id (32-char hex)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("kb_knowledge")

            # Detect if input is sys_id (32-char lowercase hex string)
            is_sys_id = bool(re.match(r"^[a-z0-9]{32}$", number_or_sys_id.lower()))

            async with ServiceNowClient(settings, auth_provider) as client:
                # Try lookup by number first
                result = await client.query_records(
                    table="kb_knowledge",
                    query=f"number={number_or_sys_id.upper()}",
                    display_values=True,
                    limit=1,
                )

                # If not found and looks like sys_id, try sys_id lookup
                if not result["records"] and is_sys_id:
                    result = await client.query_records(
                        table="kb_knowledge",
                        query=f"sys_id={number_or_sys_id}",
                        display_values=True,
                        limit=1,
                    )

                if not result["records"]:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Knowledge article '{number_or_sys_id}' not found.",
                        )
                    )

                masked = mask_sensitive_fields(result["records"][0])
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def knowledge_create(
        short_description: str,
        text: str,
        kb_knowledge_base: str = "",
        kb_category: str = "",
        workflow_state: str = "draft",
    ) -> str:
        """Create a new knowledge article.

        Args:
            short_description: Article title (required)
            text: Article content (required)
            kb_knowledge_base: Knowledge base sys_id (optional)
            kb_category: Category sys_id (optional)
            workflow_state: Workflow state (default "draft")
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            # Validate required fields
            if not short_description.strip():
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="short_description is required and cannot be empty.",
                    )
                )

            if not text.strip():
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="text is required and cannot be empty.",
                    )
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
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def knowledge_update(
        number_or_sys_id: str,
        short_description: str = "",
        text: str = "",
        workflow_state: str = "",
        kb_knowledge_base: str = "",
        kb_category: str = "",
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
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            check_table_access("kb_knowledge")

            # Check write gate
            gate_error = write_gate("kb_knowledge", settings, correlation_id)
            if gate_error:
                return gate_error

            # Detect if input is sys_id (32-char lowercase hex string)
            is_sys_id = bool(re.match(r"^[a-z0-9]{32}$", number_or_sys_id.lower()))

            async with ServiceNowClient(settings, auth_provider) as client:
                # Try lookup by number first
                result = await client.query_records(
                    table="kb_knowledge",
                    query=f"number={number_or_sys_id.upper()}",
                    limit=1,
                )

                # If not found and looks like sys_id, try sys_id lookup
                if not result["records"] and is_sys_id:
                    result = await client.query_records(
                        table="kb_knowledge",
                        query=f"sys_id={number_or_sys_id}",
                        limit=1,
                    )

                if not result["records"]:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Knowledge article '{number_or_sys_id}' not found.",
                        )
                    )

                sys_id = result["records"][0]["sys_id"]

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

                update_result = await client.update_record(
                    table="kb_knowledge",
                    sys_id=sys_id,
                    data=changes,
                )
                masked = mask_sensitive_fields(update_result)
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def knowledge_feedback(
        number_or_sys_id: str,
        rating: int | None = None,
        comment: str = "",
    ) -> str:
        """Submit feedback (rating or comment) for a knowledge article.

        Args:
            number_or_sys_id: KB number (e.g. KB0010001) or sys_id (32-char hex)
            rating: Rating value from 1 to 5 (optional, None means not provided)
            comment: Feedback comment (optional)
        """
        correlation_id = str(uuid.uuid4())

        async def _run() -> str:
            # Validate at least one feedback type provided
            if rating is None and not comment.strip():
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Must provide rating or comment.",
                    )
                )

            # Validate rating range if provided
            if rating is not None and (rating < 1 or rating > 5):
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Rating must be between 1 and 5.",
                    )
                )

            check_table_access("kb_knowledge")

            # Check write gate
            gate_error = write_gate("kb_knowledge", settings, correlation_id)
            if gate_error:
                return gate_error

            # Detect if input is sys_id (32-char lowercase hex string)
            is_sys_id = bool(re.match(r"^[a-z0-9]{32}$", number_or_sys_id.lower()))

            async with ServiceNowClient(settings, auth_provider) as client:
                # Try lookup by number first
                result = await client.query_records(
                    table="kb_knowledge",
                    query=f"number={number_or_sys_id.upper()}",
                    limit=1,
                )

                # If not found and looks like sys_id, try sys_id lookup
                if not result["records"] and is_sys_id:
                    result = await client.query_records(
                        table="kb_knowledge",
                        query=f"sys_id={number_or_sys_id}",
                        limit=1,
                    )

                if not result["records"]:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Knowledge article '{number_or_sys_id}' not found.",
                        )
                    )

                sys_id = result["records"][0]["sys_id"]

                # Build changes dict based on what was provided
                changes: dict[str, str] = {}
                if rating is not None:
                    changes["rating"] = str(rating)
                if comment.strip():
                    changes["feedback_comments"] = comment

                update_result = await client.update_record(
                    table="kb_knowledge",
                    sys_id=sys_id,
                    data=changes,
                )
                masked = mask_sensitive_fields(update_result)
                return json.dumps(format_response(data=masked, correlation_id=correlation_id))

        return await safe_tool_call(_run, correlation_id)
