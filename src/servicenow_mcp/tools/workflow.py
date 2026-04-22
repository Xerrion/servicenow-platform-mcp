"""Workflow introspection tools for the legacy Workflow Engine."""

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    SCRIPT_BODY_MASK,
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
)
from servicenow_mcp.tools.flow_designer import (
    CODE_AWARE_SCRIPT_VARIABLE_NAMES,
    STRICT_SCRIPT_VARIABLE_NAMES,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_ref_value,
    validate_identifier,
)


logger = logging.getLogger(__name__)


# Derived union of Flow Designer's two script-variable sets. Single source of
# truth lives in ``flow_designer``; any field added there (strict or
# code-aware) propagates here automatically, so legacy workflow activity
# variables stay aligned with flow action variables without manual edits.
SCRIPT_VARIABLE_NAMES: frozenset[str] = STRICT_SCRIPT_VARIABLE_NAMES | CODE_AWARE_SCRIPT_VARIABLE_NAMES


def _mask_variable_value(variable: dict[str, Any], *, include_script_body: bool) -> dict[str, Any]:
    """Return the variable dict with its ``value`` masked when it names a script body."""
    masked = mask_sensitive_fields(variable)
    if include_script_body:
        return masked
    name = str(masked.get("variable", "")).lower()
    is_script_slot = name in SCRIPT_VARIABLE_NAMES or name.endswith("_script")
    if is_script_slot and "value" in masked:
        masked["value"] = SCRIPT_BODY_MASK
    return masked


# ------------------------------------------------------------------
# Module-scope helpers (extracted from register_tools)
# ------------------------------------------------------------------


def _process_gather_results(
    results: list[Any],
    labels: list[str],
) -> tuple[list[Any], list[str]]:
    """Process asyncio.gather results that may contain exceptions.

    Returns ``(unwrapped_results, warnings)`` where failed results are replaced
    with ``None`` and a warning string is appended for each failure.
    """
    unwrapped: list[Any] = []
    warnings: list[str] = []
    for result, label in zip(results, labels, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            warnings.append(f"Could not fetch {label}: {result}")
            unwrapped.append(None)
        else:
            unwrapped.append(result)
    return unwrapped, warnings


async def _fetch_and_attach_variables(
    client: ServiceNowClient,
    activity_records: list[dict[str, Any]],
    settings: Settings,
    *,
    include_script_body: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Bulk-fetch activity variables and group them by activity sys_id.

    Returns ``(vars_by_activity, warnings)``.
    """
    warnings: list[str] = []
    vars_by_activity: dict[str, list[dict[str, Any]]] = {}

    activity_sys_ids = [resolve_ref_value(a["sys_id"]) for a in activity_records]
    if not activity_sys_ids:
        return vars_by_activity, warnings

    try:
        vars_query = (
            ServiceNowQuery().equals("document", "wf_activity").in_list("document_key", activity_sys_ids).build()
        )
        vars_safety = enforce_query_safety("sys_variable_value", vars_query, INTERNAL_QUERY_LIMIT, settings)
        vars_result = await client.query_records(
            "sys_variable_value",
            vars_query,
            fields=["sys_id", "variable", "value", "document_key"],
            limit=vars_safety["limit"],
            display_values=False,
        )
        if len(vars_result["records"]) >= vars_safety["limit"]:
            warnings.append(f"Activity variables may be truncated at {vars_safety['limit']} records")
        for v in vars_result["records"]:
            key = resolve_ref_value(v.get("document_key", ""))
            vars_by_activity.setdefault(key, []).append(
                _mask_variable_value(v, include_script_body=include_script_body)
            )
    except Exception as exc:
        warnings.append(f"Could not fetch activity variables: {exc}")

    return vars_by_activity, warnings


async def _fetch_activity_definition(
    client: ServiceNowClient,
    definition_sys_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Fetch the wf_element_definition for an activity.

    Returns ``(definition_record_or_none, warnings)``.
    """
    warnings: list[str] = []
    if not definition_sys_id:
        return None, warnings

    try:
        validate_identifier(definition_sys_id)
        check_table_access("wf_element_definition")
        record = await client.get_record(
            "wf_element_definition",
            definition_sys_id,
            display_values=True,
        )
        return record, warnings
    except Exception as exc:
        logger.warning("Could not fetch element definition %s: %s", definition_sys_id, exc)
        warnings.append(f"Could not fetch activity definition from wf_element_definition: {exc}")
        return None, warnings


TOOL_NAMES: list[str] = [
    "workflow_contexts",
    "workflow_map",
    "workflow_status",
    "workflow_activity_detail",
    "workflow_version_list",
]


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
        validate_identifier(record_sys_id)
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

        legacy_safety = enforce_query_safety("wf_context", legacy_query, limit, settings)
        flow_safety = enforce_query_safety("sys_flow_context", flow_query, limit, settings)
        effective_limit = min(legacy_safety["limit"], flow_safety["limit"])

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
                    limit=effective_limit,
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
                    limit=effective_limit,
                    display_values=True,
                ),
            )

        legacy_records = [mask_sensitive_fields(r) for r in legacy_result["records"]]
        flow_records = [mask_sensitive_fields(r) for r in flow_result["records"]]

        return format_response(
            data={
                "legacy_workflows": legacy_records,
                "flow_designer": flow_records,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def workflow_map(
        workflow_version_sys_id: str,
        include_script_body: bool = False,
        *,
        correlation_id: str,
    ) -> str:
        """Show the structure of a workflow version: activities, transitions, and activity variables.

        Each activity includes its configured variable values (script body, conditions, etc.)
        from sys_variable_value.

        Args:
            workflow_version_sys_id: The sys_id of the wf_workflow_version record.
            include_script_body: If True, return activity variable values that
                carry script/condition bodies verbatim. Script/markup bodies
                are masked by default. Set True only when you need to inspect
                the code itself; script bodies may contain hardcoded secrets.
        """
        validate_identifier(workflow_version_sys_id)
        check_table_access("wf_workflow_version")
        check_table_access("wf_activity")
        check_table_access("wf_transition")
        check_table_access("sys_variable_value")

        activity_query = ServiceNowQuery().equals("workflow_version", workflow_version_sys_id).order_by("x").build()
        transition_query = ServiceNowQuery().equals("from.workflow_version", workflow_version_sys_id).build()

        act_safety = enforce_query_safety("wf_activity", activity_query, 100, settings)
        trans_safety = enforce_query_safety("wf_transition", transition_query, 200, settings)

        warnings: list[str] = []

        async with ServiceNowClient(settings, auth_provider) as client:
            results = await asyncio.gather(
                client.get_record(
                    "wf_workflow_version",
                    workflow_version_sys_id,
                    display_values=True,
                ),
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
                    limit=act_safety["limit"],
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
                    limit=trans_safety["limit"],
                    display_values=True,
                ),
                return_exceptions=True,
            )

            unwrapped, gather_warnings = _process_gather_results(
                list(results),
                ["workflow version record", "workflow activities", "workflow transitions"],
            )
            warnings.extend(gather_warnings)

            version_record: dict[str, Any] = unwrapped[0] or {}

            # Activities are critical for a useful map
            if unwrapped[1] is None:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Failed to fetch workflow activities: {results[1]}",
                )
            activity_result: dict[str, Any] = unwrapped[1]

            transition_result: dict[str, Any] = unwrapped[2] or {"records": [], "count": 0}

            if len(activity_result["records"]) >= act_safety["limit"]:
                warnings.append(f"Activities may be truncated at {act_safety['limit']} records")
            if len(transition_result["records"]) >= trans_safety["limit"]:
                warnings.append(f"Transitions may be truncated at {trans_safety['limit']} records")

            activity_records = activity_result["records"]
            vars_by_activity, var_warnings = await _fetch_and_attach_variables(
                client,
                activity_records,
                settings,
                include_script_body=include_script_body,
            )
            warnings.extend(var_warnings)

        # Attach variables to each activity
        activities = []
        for a in activity_records:
            masked = mask_sensitive_fields(a)
            masked["variables"] = vars_by_activity.get(resolve_ref_value(a["sys_id"]), [])
            activities.append(masked)

        # wf_transition.condition carries scriptable condition bodies; mask
        # them when the caller has not opted in.
        masked_transitions: list[dict[str, Any]] = []
        for t in transition_result["records"]:
            t_masked = mask_sensitive_fields(t)
            if not include_script_body and "condition" in t_masked and t_masked["condition"]:
                t_masked["condition"] = SCRIPT_BODY_MASK
            masked_transitions.append(t_masked)

        return format_response(
            data={
                "version": mask_sensitive_fields(version_record),
                "activities": activities,
                "transitions": masked_transitions,
            },
            correlation_id=correlation_id,
            warnings=warnings if warnings else None,
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
        validate_identifier(context_sys_id)
        check_table_access("wf_context")
        check_table_access("wf_executing")
        check_table_access("wf_history")

        executing_query = ServiceNowQuery().equals("context", context_sys_id).order_by("started").build()
        history_query = ServiceNowQuery().equals("context", context_sys_id).order_by("started").build()

        exec_safety = enforce_query_safety("wf_executing", executing_query, 50, settings)
        hist_safety = enforce_query_safety("wf_history", history_query, 50, settings)

        async with ServiceNowClient(settings, auth_provider) as client:
            (
                context_record,
                executing_result,
                history_result,
            ) = await asyncio.gather(
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
                    limit=exec_safety["limit"],
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
                    limit=hist_safety["limit"],
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
        include_script_body: bool = False,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch detailed information about a workflow activity, its element definition, and configured variables.

        Includes activity variable values (script body, conditions, etc.) from sys_variable_value.

        Args:
            activity_sys_id: The sys_id of the wf_activity record.
            include_script_body: If True, return activity variable values that
                carry script/condition bodies verbatim. Script/markup bodies
                are masked by default. Set True only when you need to inspect
                the code itself; script bodies may contain hardcoded secrets.
        """
        validate_identifier(activity_sys_id)
        check_table_access("wf_activity")
        check_table_access("sys_variable_value")

        variables_query = (
            ServiceNowQuery().equals("document", "wf_activity").equals("document_key", activity_sys_id).build()
        )
        vars_safety = enforce_query_safety("sys_variable_value", variables_query, 50, settings)

        warnings: list[str] = []

        async with ServiceNowClient(settings, auth_provider) as client:
            # Phase 1: raw activity to extract the activity_definition sys_id
            raw_activity = await client.get_record("wf_activity", activity_sys_id, display_values=False)
            definition_sys_id = resolve_ref_value(raw_activity.get("activity_definition", ""))

            # Phase 2: critical fetches (display activity + variables)
            display_activity, variables_result = await asyncio.gather(
                client.get_record("wf_activity", activity_sys_id, display_values=True),
                client.query_records(
                    "sys_variable_value",
                    variables_query,
                    fields=["sys_id", "variable", "value", "document_key"],
                    limit=vars_safety["limit"],
                    display_values=True,
                ),
            )
            if len(variables_result["records"]) >= vars_safety["limit"]:
                warnings.append(f"Activity variables may be truncated at {vars_safety['limit']} records")

            # Non-critical: element definition (may be inaccessible on some instances)
            definition_record, def_warnings = await _fetch_activity_definition(client, definition_sys_id)
            warnings.extend(def_warnings)

        return format_response(
            data={
                "activity": mask_sensitive_fields(display_activity),
                "definition": mask_sensitive_fields(definition_record) if definition_record else None,
                "variables": [
                    _mask_variable_value(v, include_script_body=include_script_body)
                    for v in variables_result["records"]
                ],
            },
            correlation_id=correlation_id,
            warnings=warnings if warnings else None,
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
        safety = enforce_query_safety("wf_workflow_version", query, limit, settings)
        effective_limit = safety["limit"]

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
                limit=effective_limit,
                display_values=True,
            )

        versions = [mask_sensitive_fields(v) for v in result["records"]]

        return format_response(
            data={"versions": versions},
            correlation_id=correlation_id,
        )
