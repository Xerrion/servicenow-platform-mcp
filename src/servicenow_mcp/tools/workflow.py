"""Workflow introspection tools for the legacy Workflow Engine."""

import asyncio

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register workflow introspection tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def workflow_contexts(
        record_sys_id: str,
        table: str = "",
        state: str = "",
        limit: int = 10,
        *,
        correlation_id: str,
    ) -> str:
        """List legacy workflow contexts and Flow Designer contexts running on a record.

        Args:
            record_sys_id: The sys_id of the source record.
            table: Filter legacy contexts by table name.
            state: Filter legacy contexts by state. Legacy values: executing, finished, cancelled. Flow Designer values: IN_PROGRESS, COMPLETE, ERROR, CANCELLED.
            limit: Maximum number of records to return per engine (default 10).
        """
        check_table_access("wf_context")
        check_table_access("sys_flow_context")

        if table:
            validate_identifier(table)

        legacy_query = (
            ServiceNowQuery()
            .equals("id", record_sys_id)
            .equals_if("state", state, bool(state))
            .equals_if("table", table, bool(table))
            .build()
        )
        flow_query = ServiceNowQuery().equals("source_record", record_sys_id).build()

        async with ServiceNowClient(settings, auth_provider) as client:
            legacy_result, flow_result = await asyncio.gather(
                client.query_records(
                    "wf_context",
                    legacy_query,
                    fields=[
                        "sys_id",
                        "name",
                        "state",
                        "started",
                        "ended",
                        "workflow_version",
                        "table",
                        "result",
                        "running_duration",
                        "active",
                    ],
                    limit=limit,
                    display_values=True,
                ),
                client.query_records(
                    "sys_flow_context",
                    flow_query,
                    fields=[
                        "sys_id",
                        "name",
                        "state",
                        "started",
                        "ended",
                        "flow_version",
                        "source_table",
                        "source_record",
                    ],
                    limit=limit,
                    display_values=True,
                ),
            )

        legacy_records = [mask_sensitive_fields(r) for r in legacy_result["records"]]
        flow_records = [mask_sensitive_fields(r) for r in flow_result["records"]]

        return format_response(
            data={"legacy_workflows": legacy_records, "flow_designer": flow_records},
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def workflow_map(
        workflow_version_sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Show the structure of a workflow version: activities and transitions between them.

        Args:
            workflow_version_sys_id: The sys_id of the wf_workflow_version record.
        """
        check_table_access("wf_workflow_version")
        check_table_access("wf_activity")
        check_table_access("wf_transition")

        activity_query = ServiceNowQuery().equals("workflow_version", workflow_version_sys_id).order_by("x").build()
        transition_query = ServiceNowQuery().equals("from.workflow_version", workflow_version_sys_id).build()

        async with ServiceNowClient(settings, auth_provider) as client:
            version_record, activity_result, transition_result = await asyncio.gather(
                client.get_record("wf_workflow_version", workflow_version_sys_id, display_values=True),
                client.query_records(
                    "wf_activity",
                    activity_query,
                    fields=[
                        "sys_id",
                        "name",
                        "activity_definition",
                        "activity_definition.name",
                        "activity_definition.category",
                        "x",
                        "y",
                        "timeout",
                        "notes",
                        "out_of_date",
                        "is_parent",
                        "stage",
                    ],
                    limit=100,
                    display_values=True,
                ),
                client.query_records(
                    "wf_transition",
                    transition_query,
                    fields=[
                        "sys_id",
                        "from",
                        "from.name",
                        "to",
                        "to.name",
                        "condition",
                    ],
                    limit=200,
                    display_values=True,
                ),
            )

        return format_response(
            data={
                "version": mask_sensitive_fields(version_record),
                "activities": [mask_sensitive_fields(a) for a in activity_result["records"]],
                "transitions": [mask_sensitive_fields(t) for t in transition_result["records"]],
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def workflow_status(
        context_sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Show execution status of a workflow context: currently executing and completed steps.

        Args:
            context_sys_id: The sys_id of the wf_context record.
        """
        check_table_access("wf_context")
        check_table_access("wf_executing")
        check_table_access("wf_history")

        executing_query = ServiceNowQuery().equals("context", context_sys_id).order_by("started").build()
        history_query = ServiceNowQuery().equals("context", context_sys_id).order_by("started").build()

        async with ServiceNowClient(settings, auth_provider) as client:
            context_record, executing_result, history_result = await asyncio.gather(
                client.get_record("wf_context", context_sys_id, display_values=True),
                client.query_records(
                    "wf_executing",
                    executing_query,
                    fields=[
                        "sys_id",
                        "activity",
                        "activity.name",
                        "activity.activity_definition.name",
                        "state",
                        "started",
                        "due",
                        "result",
                        "fault_description",
                        "activity_index",
                    ],
                    limit=50,
                    display_values=True,
                ),
                client.query_records(
                    "wf_history",
                    history_query,
                    fields=[
                        "sys_id",
                        "activity",
                        "activity.name",
                        "activity.activity_definition.name",
                        "state",
                        "started",
                        "ended",
                        "result",
                        "fault_description",
                        "activity_index",
                    ],
                    limit=50,
                    display_values=True,
                ),
            )

        return format_response(
            data={
                "context": mask_sensitive_fields(context_record),
                "executing": [mask_sensitive_fields(e) for e in executing_result["records"]],
                "history": [mask_sensitive_fields(h) for h in history_result["records"]],
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def workflow_activity_detail(
        activity_sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch detailed information about a workflow activity and its element definition.

        Args:
            activity_sys_id: The sys_id of the wf_activity record.
        """
        check_table_access("wf_activity")
        check_table_access("wf_element_definition")

        async with ServiceNowClient(settings, auth_provider) as client:
            # Fetch raw activity to extract the activity_definition sys_id
            raw_activity = await client.get_record("wf_activity", activity_sys_id, display_values=False)
            definition_sys_id = raw_activity.get("activity_definition", "")

            if not definition_sys_id:
                # No definition linked - return the activity with display values only
                display_activity = await client.get_record("wf_activity", activity_sys_id, display_values=True)
                definition_record = None
            else:
                # Parallel fetch: display-value activity + element definition
                display_activity, definition_record = await asyncio.gather(
                    client.get_record("wf_activity", activity_sys_id, display_values=True),
                    client.get_record("wf_element_definition", definition_sys_id, display_values=True),
                )

        return format_response(
            data={
                "activity": mask_sensitive_fields(display_activity),
                "definition": mask_sensitive_fields(definition_record) if definition_record else None,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def workflow_version_list(
        table: str,
        active_only: bool = True,
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """List workflow versions defined for a specific table.

        Args:
            table: The table name to find workflows for (e.g. 'incident').
            active_only: Only return active workflow versions (default True).
            limit: Maximum number of versions to return (default 20).
        """
        validate_identifier(table)
        check_table_access("wf_workflow_version")

        query = ServiceNowQuery().equals("table", table).equals_if("active", "true", active_only).build()

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "wf_workflow_version",
                query,
                fields=[
                    "sys_id",
                    "name",
                    "table",
                    "description",
                    "active",
                    "published",
                    "checked_out",
                    "checked_out_by",
                    "workflow",
                ],
                limit=limit,
                display_values=True,
            )

        versions = [mask_sensitive_fields(v) for v in result["records"]]

        return format_response(
            data={"versions": versions},
            correlation_id=correlation_id,
        )
