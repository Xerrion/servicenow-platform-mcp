"""Legacy workflow to Flow Designer migration analysis tool and helpers."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_ref_value,
    validate_identifier,
)


# -- Duplicated string constants ---------------------------------------------------

FD_SCRIPT_STEP = "Custom Action (Script Step)"
FD_APPROVAL = "Ask for Approval Action"
FD_CREATE_RECORD = "Create Record Action"
FD_MANUAL_REFACTOR = "No Direct Equivalent (Manual Refactor)"
FD_UNKNOWN = "Unknown (Review Manually)"
FIELD_TO_NAME = "to.name"
FIELD_ACTIVITY_DEF_NAME = "activity_definition.name"

# -- Script detection patterns (split for lower regex complexity) ------------------

# Known variable names that reliably contain script bodies in workflow activities.
STRICT_SCRIPT_VARIABLE_NAMES: frozenset[str] = frozenset({"script", "run_script", "script_body"})
CODE_AWARE_SCRIPT_VARIABLE_NAMES: frozenset[str] = frozenset({"condition"})
_SCRIPT_OBJECT_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:gs|current|workflow|answer|inputs|outputs)\s*\.|answer\s*=",
    re.IGNORECASE,
)
_SCRIPT_KEYWORD_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:function|var|let|const|if|else|for|while|try|catch|return)\b",
    re.IGNORECASE,
)


def _contains_script_code(text: str) -> bool:
    """Return True when *text* contains patterns indicating executable script."""
    return bool(_SCRIPT_OBJECT_PATTERN.search(text) or _SCRIPT_KEYWORD_PATTERN.search(text))


# Maps legacy workflow activity type names to their Flow Designer equivalents.
ACTIVITY_TYPE_MAPPING: dict[str, str] = {
    "run_script": FD_SCRIPT_STEP,
    "run script": FD_SCRIPT_STEP,
    "approval - user": FD_APPROVAL,
    "approval - group": FD_APPROVAL,
    "approval coordinator": FD_APPROVAL,
    "wait for condition": "Wait for Condition Action",
    "timer": "Wait for Duration Action",
    "if": "Flow Logic (If/Else)",
    "branch": "Flow Logic (Parallel)",
    "notification": "Send Email Action",
    "create task": FD_CREATE_RECORD,
    "catalog task": FD_CREATE_RECORD,
    "set values": "Update Record Action",
    "join": "Flow Logic (Parallel - Join)",
    "return value": "Flow Output Variable",
    "end": "Flow End",
    "begin": "Flow Trigger",
    "rollback to": FD_MANUAL_REFACTOR,
    "turnback to": FD_MANUAL_REFACTOR,
}


def _build_transition_indexes(
    transitions: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, int]]:
    """Build outgoing transition details and inbound counts by activity sys_id."""
    outgoing: dict[str, list[dict[str, str]]] = {}
    inbound_counts: dict[str, int] = {}

    for transition in transitions:
        source_id = resolve_ref_value(transition.get("from", ""))
        target_id = resolve_ref_value(transition.get("to", ""))
        if not source_id or not target_id:
            continue

        outgoing.setdefault(source_id, []).append(
            {
                "target_id": target_id,
                "target_name": resolve_ref_value(transition.get(FIELD_TO_NAME, "")) or target_id,
                "condition": resolve_ref_value(transition.get("condition", "")),
            }
        )
        inbound_counts[target_id] = inbound_counts.get(target_id, 0) + 1

    return outgoing, inbound_counts


def _is_script_content(variable_name: str, value: str) -> bool:
    """Return True when a variable value is likely executable script content."""
    normalized_name = variable_name.lower().strip()
    normalized_value = value.strip()
    if not normalized_value:
        return False

    if normalized_name in STRICT_SCRIPT_VARIABLE_NAMES:
        return True

    if normalized_name not in CODE_AWARE_SCRIPT_VARIABLE_NAMES:
        return False

    return _contains_script_code(normalized_value)


def _build_prerequisites(
    workflow_name: str,
    table_name: str,
    workflow_condition: str,
    cycles: list[list[str]],
    migration_blockers: list[dict[str, Any]],
    extracted_scripts: list[dict[str, str]],
) -> list[str]:
    """Build prerequisite checks a user should complete before manual migration."""
    prerequisites = [
        f"Confirm you can create or edit a Flow Designer flow for the {table_name or 'target'} table.",
        f"Open the legacy workflow '{workflow_name}' and review all activity properties before rebuilding it.",
    ]

    if workflow_condition:
        prerequisites.append(f"Capture the legacy workflow trigger condition exactly as written: {workflow_condition}.")
    else:
        prerequisites.append("Identify the legacy workflow trigger condition before building the new flow.")

    if cycles:
        prerequisites.append(
            "Review cyclic paths first - they must be redesigned as Flow Logic loops or subflows before buildout."
        )

    if migration_blockers:
        prerequisites.append(
            "Review all migration blockers and manual refactor items before creating production-ready flow steps."
        )

    if extracted_scripts:
        prerequisites.append(
            "Collect every legacy script body and decide whether each one becomes a custom action, inline script step, or a redesigned no-code action."
        )

    return prerequisites


def _build_build_steps(
    workflow_name: str,
    table_name: str,
    workflow_condition: str,
    activities: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    cycles: list[list[str]],
    extracted_scripts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Build ordered manual build steps for recreating the workflow in Flow Designer."""
    ordered_activity_names = [resolve_ref_value(activity.get("name", "")) for activity in activities]
    transition_summaries = []
    for transition in transitions:
        source_name = resolve_ref_value(transition.get("from.name", "")) or resolve_ref_value(
            transition.get("from", "")
        )
        target_name = resolve_ref_value(transition.get(FIELD_TO_NAME, "")) or resolve_ref_value(
            transition.get("to", "")
        )
        condition = resolve_ref_value(transition.get("condition", ""))
        summary = f"{source_name} -> {target_name}"
        if condition:
            summary = f"{summary} when {condition}"
        transition_summaries.append(summary)

    build_steps = [
        {
            "step": 1,
            "title": "Create the target flow shell",
            "details": (
                f"In Flow Designer, create a new flow named '{workflow_name} - Migrated' and bind it to the "
                f"{table_name or 'same target'} table."
            ),
        },
        {
            "step": 2,
            "title": "Configure the trigger",
            "details": (
                f"Use the legacy workflow trigger on {table_name or 'the source table'} and apply the same condition: "
                f"{workflow_condition or 'review the legacy workflow and manually copy its trigger condition.'}"
            ),
        },
        {
            "step": 3,
            "title": "Lay out the main flow path",
            "details": (
                "Add Flow Logic and actions in this order, then connect them to match the legacy transitions: "
                f"{', '.join(name for name in ordered_activity_names if name) or 'No legacy activities were found.'}"
            ),
            "transition_plan": transition_summaries,
        },
    ]

    next_step = 4
    if cycles:
        build_steps.append(
            {
                "step": next_step,
                "title": "Redesign loopback logic",
                "details": "Convert each cyclic path into a Flow Logic loop, staged state check, or subflow before finalizing the action sequence.",
            }
        )
        next_step += 1

    if extracted_scripts:
        build_steps.append(
            {
                "step": next_step,
                "title": "Reimplement legacy scripts",
                "details": "For every scripted legacy activity, manually recreate the logic in a custom action or supported script step and verify equivalent inputs and outputs.",
            }
        )
        next_step += 1

    build_steps.append(
        {
            "step": next_step,
            "title": "Validate and publish",
            "details": "Test the migrated flow with representative records, confirm each branch executes correctly, and only then publish the new flow.",
        }
    )

    return build_steps


_INSTRUCTION_MAP: dict[str, str] = {
    "Flow End": (
        "Let the flow end after the previous branch completes."
        " Add outputs only if the legacy workflow returned values to another process."
    ),
    "Flow Logic (If/Else)": (
        "Add an If branch and copy the legacy decision criteria so each outcome routes to the same downstream activity."
    ),
    "Flow Logic (Parallel)": (
        "Add a parallel branch block and split execution"
        " so each downstream path matches the original workflow branches."
    ),
    "Flow Logic (Parallel - Join)": (
        "Add join logic or redesign the downstream sequence so all required branches complete before continuing."
    ),
    FD_APPROVAL: (
        "Add an Ask for Approval action and mirror the same approver source,"
        " approval outcome branches, and timeout handling."
    ),
    "Wait for Condition Action": (
        "Add a Wait for Condition action and copy the same resume criteria from the legacy activity."
    ),
    "Wait for Duration Action": (
        "Add a Wait for Duration action and reproduce the same timeout or schedule delay from the legacy activity."
    ),
    FD_CREATE_RECORD: (
        "Add a Create Record action and map the same target table and field values that the legacy activity created."
    ),
    "Update Record Action": (
        "Add an Update Record action and set the same field values that the legacy workflow updated at this step."
    ),
    "Send Email Action": (
        "Add a Send Email action and copy the same recipients, template content, and send conditions."
    ),
    "Flow Output Variable": (
        "Create or update a flow output so downstream consumers receive the same value as the legacy return activity."
    ),
    FD_SCRIPT_STEP: (
        "Rebuild this scripted step as a custom action or reviewed script step,"
        " then wire its outputs into the next flow step."
    ),
}


def _build_activity_instruction(
    mapping: dict[str, Any],
    outgoing_transitions: list[dict[str, str]],
    workflow_condition: str,
) -> str:
    """Build a human-facing instruction for one legacy activity."""
    equivalent = mapping["flow_designer_equivalent"]
    activity_name = mapping["activity_name"] or mapping["legacy_type"]

    # Flow Trigger is special - it needs the workflow condition context.
    if equivalent == "Flow Trigger":
        return (
            f"Use this legacy begin activity as the flow trigger and apply the original trigger condition "
            f"{workflow_condition or 'from the legacy workflow definition'}."
        )

    # Check the static instruction map for a direct match.
    instruction = _INSTRUCTION_MAP.get(equivalent)
    if instruction:
        return instruction

    # Unmapped or unknown activities need manual redesign guidance.
    transition_targets = ", ".join(transition["target_name"] for transition in outgoing_transitions)
    if equivalent in {FD_UNKNOWN, FD_MANUAL_REFACTOR}:
        return (
            f"This activity has no safe direct Flow Designer equivalent. Redesign the business outcome of '{activity_name}' manually"
            f" and reconnect it to: {transition_targets or 'the next required step'}."
        )

    return f"Add the closest Flow Designer action for '{activity_name}' and reconnect it to the same downstream logic."


def _build_activity_translation_steps(
    activity_mapping: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    workflow_condition: str,
) -> list[dict[str, Any]]:
    """Translate each legacy activity into a manual Flow Designer build instruction."""
    outgoing_index, _ = _build_transition_indexes(transitions)

    translation_steps: list[dict[str, Any]] = []
    for index, mapping in enumerate(activity_mapping, start=1):
        outgoing_transitions = outgoing_index.get(mapping["activity_sys_id"], [])
        transition_notes = []
        for transition in outgoing_transitions:
            target_name = transition["target_name"]
            condition = transition["condition"]
            if condition:
                transition_notes.append(f"Route to '{target_name}' when condition '{condition}' is true.")
                continue
            transition_notes.append(f"Continue to '{target_name}'.")

        translation_steps.append(
            {
                "step": index,
                "activity_name": mapping["activity_name"],
                "legacy_type": mapping["legacy_type"],
                "flow_designer_equivalent": mapping["flow_designer_equivalent"],
                "manual_instruction": _build_activity_instruction(mapping, outgoing_transitions, workflow_condition),
                "transition_notes": transition_notes,
                "has_script": mapping["has_script"],
            }
        )

    return translation_steps


def _build_script_migration_notes(extracted_scripts: list[dict[str, str]]) -> list[dict[str, str]]:
    """Build actionable notes for each extracted legacy script."""
    script_notes: list[dict[str, str]] = []
    for script in extracted_scripts:
        script_notes.append(
            {
                "activity_name": script["activity_name"],
                "variable_name": script["variable_name"],
                "instruction": (
                    f"Review the {script['variable_name']} script from '{script['activity_name']}' and manually reimplement it in a "
                    "Flow Designer custom action or approved script step."
                ),
            }
        )
    return script_notes


def _build_validation_checklist(
    workflow_condition: str,
    transitions: list[dict[str, Any]],
    extracted_scripts: list[dict[str, str]],
    migration_blockers: list[dict[str, Any]],
) -> list[str]:
    """Build a deterministic post-build validation checklist."""
    checklist = [
        "Confirm the migrated flow uses the same table and trigger timing as the legacy workflow.",
        f"Verify the trigger condition matches the legacy definition: {workflow_condition or 'No condition captured - validate manually.'}",
        f"Test every legacy transition path. Expected path count: {len(transitions)}.",
    ]

    if extracted_scripts:
        checklist.append(
            "Run test cases that execute each reimplemented script path and compare outputs to the legacy workflow."
        )

    if migration_blockers:
        checklist.append(
            "Validate that every manual redesign item and blocker has a documented Flow Designer replacement."
        )

    checklist.append(
        "Publish only after a representative record completes the migrated flow end-to-end without manual intervention."
    )
    return checklist


def _build_known_manual_work(
    migration_blockers: list[dict[str, Any]],
    extracted_scripts: list[dict[str, str]],
) -> list[str]:
    """Build an ordered list of manual migration work items."""
    manual_work = [blocker["description"] for blocker in migration_blockers]

    for script in extracted_scripts:
        manual_work.append(
            f"Manually review and rebuild the script from '{script['activity_name']}' ({script['variable_name']})."
        )

    if not manual_work:
        return [
            "No explicit blockers were detected, but the rebuilt flow still needs manual validation in Flow Designer."
        ]

    return manual_work


def _build_manual_migration_instructions(
    workflow_name: str,
    table_name: str,
    workflow_condition: str,
    activities: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    cycles: list[list[str]],
    migration_blockers: list[dict[str, Any]],
    activity_mapping: list[dict[str, Any]],
    extracted_scripts: list[dict[str, str]],
) -> dict[str, Any]:
    """Build structured manual migration guidance for Flow Designer users."""
    return {
        "summary": (
            f"Manually build a Flow Designer flow for '{workflow_name}' on the {table_name or 'same'} table, recreating "
            f"{len(activities)} activities and {len(transitions)} transitions from the legacy workflow."
        ),
        "prerequisites": _build_prerequisites(
            workflow_name,
            table_name,
            workflow_condition,
            cycles,
            migration_blockers,
            extracted_scripts,
        ),
        "build_steps": _build_build_steps(
            workflow_name,
            table_name,
            workflow_condition,
            activities,
            transitions,
            cycles,
            extracted_scripts,
        ),
        "activity_translation_steps": _build_activity_translation_steps(
            activity_mapping,
            transitions,
            workflow_condition,
        ),
        "script_migration_notes": _build_script_migration_notes(extracted_scripts),
        "validation_checklist": _build_validation_checklist(
            workflow_condition,
            transitions,
            extracted_scripts,
            migration_blockers,
        ),
        "known_manual_work": _build_known_manual_work(migration_blockers, extracted_scripts),
    }


# -- DFS color constants for cycle detection -----------------------------------------

_WHITE, _GRAY, _BLACK = 0, 1, 2


def _process_neighbor(
    neighbor: str,
    color: dict[str, int],
    path: list[str],
    stack: list[tuple[str, int]],
    cycles: list[list[str]],
) -> None:
    """Handle a single neighbor during DFS: detect back-edges or push for exploration."""
    if neighbor not in color:
        return
    if color[neighbor] == _GRAY:
        cycle_start = path.index(neighbor)
        cycles.append(path[cycle_start:])
    elif color[neighbor] == _WHITE:
        color[neighbor] = _GRAY
        path.append(neighbor)
        stack.append((neighbor, 0))


async def _fetch_topology(
    workflow_version_sys_id: str,
    client: ServiceNowClient,
    settings: Settings,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fetch the workflow version record, activities, and transitions from ServiceNow.

    Validates table access and enforces query safety before issuing the three
    parallel API calls.

    Args:
        workflow_version_sys_id: The sys_id of the wf_workflow_version record.
        client: An already-opened ServiceNowClient.
        settings: Server settings for query safety limits.

    Returns:
        A tuple of (version_record, activities, transitions, warnings).
    """
    check_table_access("wf_workflow_version")
    check_table_access("wf_activity")
    check_table_access("wf_transition")

    act_query = ServiceNowQuery().equals("workflow_version", workflow_version_sys_id).order_by("x").build()
    trans_query = ServiceNowQuery().equals("from.workflow_version", workflow_version_sys_id).build()

    act_safety = enforce_query_safety("wf_activity", act_query, 200, settings)
    trans_safety = enforce_query_safety("wf_transition", trans_query, 500, settings)

    version_record, activity_result, transition_result = await asyncio.gather(
        client.get_record("wf_workflow_version", workflow_version_sys_id, display_values=True),
        client.query_records(
            "wf_activity",
            act_query,
            fields=[
                "sys_id",
                "name",
                "activity_definition",
                FIELD_ACTIVITY_DEF_NAME,
                "activity_definition.category",
                "x",
                "y",
                "timeout",
                "notes",
            ],
            limit=act_safety["limit"],
            display_values=True,
        ),
        client.query_records(
            "wf_transition",
            trans_query,
            fields=[
                "sys_id",
                "from",
                "from.name",
                "to",
                FIELD_TO_NAME,
                "condition",
            ],
            limit=trans_safety["limit"],
            display_values=True,
        ),
    )

    warnings: list[str] = []
    if len(activity_result["records"]) >= act_safety["limit"]:
        warnings.append(f"Activities may be truncated at {act_safety['limit']} records")
    if len(transition_result["records"]) >= trans_safety["limit"]:
        warnings.append(f"Transitions may be truncated at {trans_safety['limit']} records")

    return version_record, activity_result["records"], transition_result["records"], warnings


def _detect_cycles(
    activities: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> tuple[list[list[str]], dict[str, str]]:
    """Detect cycles in a workflow graph and build the activity name lookup.

    Uses iterative DFS with 3-color marking to find back-edges.

    Returns:
        A tuple of (cycles, activity_name_lookup) where activity_name_lookup
        maps each activity sys_id to its display name.
    """
    activity_name_lookup: dict[str, str] = {
        resolve_ref_value(a["sys_id"]): resolve_ref_value(a.get("name", "")) for a in activities
    }

    adjacency: dict[str, list[str]] = {}
    for t in transitions:
        src = resolve_ref_value(t.get("from", ""))
        dst = resolve_ref_value(t.get("to", ""))
        if src and dst:
            adjacency.setdefault(src, []).append(dst)

    color: dict[str, int] = {resolve_ref_value(a["sys_id"]): _WHITE for a in activities}
    cycles: list[list[str]] = []
    path: list[str] = []

    for activity in activities:
        start = resolve_ref_value(activity["sys_id"])
        if color.get(start) != _WHITE:
            continue
        color[start] = _GRAY
        path.append(start)
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, idx = stack[-1]
            neighbors = adjacency.get(node, [])
            if idx < len(neighbors):
                stack[-1] = (node, idx + 1)
                _process_neighbor(neighbors[idx], color, path, stack, cycles)
            else:
                stack.pop()
                color[node] = _BLACK
                if path and path[-1] == node:
                    path.pop()

    return cycles, activity_name_lookup


async def _extract_activity_scripts(
    activity_sys_ids: list[str],
    activities: list[dict[str, Any]],
    client: ServiceNowClient,
    settings: Settings,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]], list[str]]:
    """Fetch activity variables and extract embedded script bodies.

    Combines the variable fetch from sys_variable_value with script body
    extraction into a single helper.

    Args:
        activity_sys_ids: sys_ids of the activities to fetch variables for.
        activities: The full activity records (needed for name resolution in extracted scripts).
        client: An already-opened ServiceNowClient.
        settings: Server settings for query safety limits.

    Returns:
        A tuple of (vars_by_activity, extracted_scripts, warnings) where vars_by_activity
        groups variable records by activity sys_id, extracted_scripts contains
        script bodies found in those variables, and warnings lists any truncation notices.
    """
    check_table_access("sys_variable_value")

    vars_by_activity: dict[str, list[dict[str, Any]]] = {}
    if not activity_sys_ids:
        return vars_by_activity, [], []

    vars_query = ServiceNowQuery().equals("document", "wf_activity").in_list("document_key", activity_sys_ids).build()
    vars_safety = enforce_query_safety("sys_variable_value", vars_query, INTERNAL_QUERY_LIMIT, settings)
    vars_result = await client.query_records(
        "sys_variable_value",
        vars_query,
        fields=["sys_id", "variable", "value", "document_key"],
        limit=vars_safety["limit"],
        display_values=False,
    )
    warnings: list[str] = []
    if len(vars_result["records"]) >= vars_safety["limit"]:
        warnings.append(f"Activity variables may be truncated at {vars_safety['limit']} records")
    for v in vars_result["records"]:
        key = resolve_ref_value(v.get("document_key", ""))
        vars_by_activity.setdefault(key, []).append(mask_sensitive_fields(v))

    # Extract embedded script bodies from the fetched variables.
    extracted_scripts: list[dict[str, str]] = []
    for a in activities:
        for var in vars_by_activity.get(resolve_ref_value(a["sys_id"]), []):
            val = resolve_ref_value(var.get("value", ""))
            var_name = resolve_ref_value(var.get("variable", "")).lower().strip()
            if _is_script_content(var_name, val):
                extracted_scripts.append(
                    mask_sensitive_fields(
                        {
                            "activity_name": resolve_ref_value(a.get("name", "")),
                            "activity_sys_id": resolve_ref_value(a["sys_id"]),
                            "variable_name": resolve_ref_value(var.get("variable", "")),
                            "script_body": val,
                        }
                    )
                )

    return vars_by_activity, extracted_scripts, warnings


def _build_activity_mapping(
    activities: list[dict[str, Any]],
    vars_by_activity: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Map legacy workflow activities to their Flow Designer equivalents."""
    mapping: list[dict[str, Any]] = []
    for a in activities:
        definition_name = resolve_ref_value(a.get(FIELD_ACTIVITY_DEF_NAME, "")).lower().strip()
        mapped = ACTIVITY_TYPE_MAPPING.get(definition_name, FD_UNKNOWN)

        act_vars = vars_by_activity.get(resolve_ref_value(a["sys_id"]), [])
        max_script_lines = 0
        has_script = False
        for var in act_vars:
            val = resolve_ref_value(var.get("value", ""))
            var_name = resolve_ref_value(var.get("variable", "")).lower().strip()
            if _is_script_content(var_name, val):
                line_count = len(val.strip().splitlines())
                if line_count > max_script_lines:
                    max_script_lines = line_count
                has_script = True

        mapping.append(
            {
                "activity_name": resolve_ref_value(a.get("name", "")),
                "activity_sys_id": resolve_ref_value(a["sys_id"]),
                "legacy_type": resolve_ref_value(a.get(FIELD_ACTIVITY_DEF_NAME, "")),
                "flow_designer_equivalent": mapped,
                "has_script": has_script,
                "script_line_count": max_script_lines,
            }
        )
    return mapping


def _build_migration_blockers(
    cycles: list[list[str]],
    activity_name_lookup: dict[str, str],
    activity_mapping: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Identify migration blockers from cycles and unmapped activities."""
    blockers: list[dict[str, Any]] = []
    for cycle in cycles:
        cycle_names = [activity_name_lookup.get(sid, sid) for sid in cycle]
        blockers.append(
            {
                "type": "cycle",
                "description": f"Cyclic path detected: {' -> '.join(cycle_names)}",
                "activities_involved": cycle,
            }
        )
    for entry in activity_mapping:
        if entry["flow_designer_equivalent"] in {FD_MANUAL_REFACTOR, FD_UNKNOWN}:
            blockers.append(
                {
                    "type": "unmapped_activity",
                    "description": (
                        f"Activity '{entry['activity_name']}' ({entry['legacy_type']})"
                        " has no direct Flow Designer equivalent"
                    ),
                    "activity_name": entry["activity_name"],
                }
            )
    return blockers


def _build_recommendations(
    cycles: list[list[str]],
    script_penalty: int,
    unmapped_names: list[str],
) -> list[str]:
    """Build migration recommendations based on complexity analysis."""
    recommendations: list[str] = []
    if cycles:
        recommendations.append("Refactor cyclic paths into Do Until loops or Subflows")
    if script_penalty > 0:
        recommendations.append("Extract Run Script activities into reusable Custom Actions")
    if unmapped_names:
        recommendations.append(
            f"Activities {', '.join(unmapped_names)} have no direct Flow Designer equivalent - manual redesign required"
        )
    recommendations.append("Test migrated flow with same trigger conditions as original workflow")
    return recommendations


def _assemble_migration_response(
    activities: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    cycles: list[list[str]],
    activity_name_lookup: dict[str, str],
    vars_by_activity: dict[str, list[dict[str, Any]]],
    extracted_scripts: list[dict[str, str]],
    version_record: dict[str, Any],
) -> dict[str, Any]:
    """Build the complete migration analysis response from pre-fetched data.

    Maps activities, computes complexity metrics, identifies migration blockers,
    generates recommendations, and assembles manual migration instructions.

    Returns:
        The full response dict suitable for format_response(data=...).
    """
    activity_mapping = _build_activity_mapping(activities, vars_by_activity)

    # Derive complexity metrics.
    unmapped_names = [
        m["activity_name"] or m["legacy_type"]
        for m in activity_mapping
        if m["flow_designer_equivalent"] in {FD_UNKNOWN, FD_MANUAL_REFACTOR}
    ]
    script_penalty = sum(1 for m in activity_mapping if m["has_script"] and m["script_line_count"] > 10)
    complexity_score = len(activities) + (len(cycles) * 2) + script_penalty + len(unmapped_names)

    migration_blockers = _build_migration_blockers(cycles, activity_name_lookup, activity_mapping)
    recommendations = _build_recommendations(cycles, script_penalty, unmapped_names)

    workflow_name = resolve_ref_value(version_record.get("name", ""))
    workflow_table = resolve_ref_value(version_record.get("table", ""))
    workflow_condition = resolve_ref_value(version_record.get("condition", ""))

    return {
        "workflow": {
            "name": workflow_name,
            "table": workflow_table,
            "condition": workflow_condition,
            "activity_count": len(activities),
            "transition_count": len(transitions),
        },
        "topology": {
            "activities": [mask_sensitive_fields(a) for a in activities],
            "transitions": [mask_sensitive_fields(t) for t in transitions],
            "cycles": cycles,
        },
        "migration_blockers": migration_blockers,
        "activity_mapping": activity_mapping,
        "extracted_scripts": extracted_scripts,
        "complexity": {
            "score": complexity_score,
            "breakdown": {
                "base_activities": len(activities),
                "cycle_penalty": len(cycles) * 2,
                "script_penalty": script_penalty,
                "unmapped_penalty": len(unmapped_names),
            },
        },
        "recommendations": recommendations,
        "manual_migration_instructions": _build_manual_migration_instructions(
            workflow_name,
            workflow_table,
            workflow_condition,
            activities,
            transitions,
            cycles,
            migration_blockers,
            activity_mapping,
            extracted_scripts,
        ),
    }


def register(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register the workflow_migration_analysis tool."""

    @mcp.tool()
    @tool_handler
    async def workflow_migration_analysis(
        workflow_version_sys_id: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Analyze a legacy workflow version for Flow Designer migration readiness.

        Inspects the workflow's topology, detects cycles, maps activity types to Flow Designer
        equivalents, extracts embedded scripts, and computes a complexity score with actionable
        migration recommendations.

        Args:
            workflow_version_sys_id: The sys_id of the wf_workflow_version record to analyze.
        """
        validate_identifier(workflow_version_sys_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            version_record, activities, transitions, topo_warnings = await _fetch_topology(
                workflow_version_sys_id, client, settings
            )
            cycles, activity_name_lookup = _detect_cycles(activities, transitions)
            activity_sys_ids = [resolve_ref_value(a["sys_id"]) for a in activities if a.get("sys_id")]
            vars_by_activity, extracted_scripts, vars_warnings = await _extract_activity_scripts(
                activity_sys_ids, activities, client, settings
            )

        all_warnings = topo_warnings + vars_warnings

        result = _assemble_migration_response(
            activities, transitions, cycles, activity_name_lookup, vars_by_activity, extracted_scripts, version_record
        )

        return format_response(
            data=result,
            correlation_id=correlation_id,
            warnings=all_warnings or None,
        )
