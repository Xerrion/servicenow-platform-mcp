"""Unified ``flow`` tool: read-only Flow Designer inspection.

Six actions:

* ``contract``       - concise declared fields, ordered V2 checks/actions, and bindings.
* ``inspect``        - assemble one flow by sys_id or name: header, triggers,
  inputs/outputs/variables, decoded V2 nodes, canvas tree, snapshot drift.
* ``find_by_table``  - find flows with record triggers on a given table
  (merges V1 + V2).
* ``decode_values``  - stateless decode of a gzip+base64+JSON ``values`` blob.
* ``list_triggers``  - list V1 and V2 trigger rows with optional filters.
* ``describe``       - return the action registry without platform I/O.

The decoder lives in :mod:`servicenow_mcp.tools._flow_values`; canvas-tree
assembly stays inline. We deliberately do not touch ``/api/now/processflow/*``
endpoints (undocumented) or ``sys_hub_flow_snapshot`` (opaque cache).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._flow_values import decode_values, looks_compressed
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["flow"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {"contract", "inspect", "find_by_table", "decode_values", "list_triggers", "describe"}
)

_DEFAULT_TRIGGER_LIMIT: Final[int] = 100
_DEFAULT_SECTION_LIMIT: Final[int] = 100
_DATA_PILL_PATTERN: Final[re.Pattern[str]] = re.compile(r"{{([^{}]+)}}")

_DATASET_QUERY_PATHS: Final[dict[str, tuple[str, str]]] = {
    "inputs": ("sys_hub_flow_input", "model"),
    "outputs": ("sys_hub_flow_output", "model"),
    "variables": ("sys_hub_flow_variable", "model"),
    "actions_v2": ("sys_hub_action_instance_v2", "flow"),
    "actions_v1": ("sys_hub_action_instance", "flow"),
    "logic_v2": ("sys_hub_flow_logic_instance_v2", "flow"),
    "logic_v1": ("sys_hub_flow_logic", "flow"),
    "triggers_v2": ("sys_hub_trigger_instance_v2", "flow"),
    "triggers_v1": ("sys_hub_trigger_instance", "flow"),
}

_STRUCTURAL_DATASETS: Final[frozenset[str]] = frozenset(
    {"actions_v2", "actions_v1", "logic_v2", "logic_v1", "triggers_v2", "triggers_v1"}
)
_SECTION_DEPENDENCIES: Final[dict[str, frozenset[str]]] = {
    "flow": frozenset(),
    "published_state": frozenset(),
    "structural_summary": _STRUCTURAL_DATASETS,
    "inputs": frozenset({"inputs"}),
    "outputs": frozenset({"outputs"}),
    "variables": frozenset({"variables"}),
    "triggers": frozenset({"triggers_v2", "triggers_v1"}),
    "canvas": frozenset({"actions_v2", "logic_v2"}),
    "v1_actions": frozenset({"actions_v1"}),
    "v1_variable_values": frozenset({"actions_v1", "v1_variable_values"}),
    "steps": frozenset({"actions_v2", "logic_v2"}),
    "warnings": _STRUCTURAL_DATASETS,
}
_INSPECT_SECTIONS: Final[tuple[str, ...]] = (
    "flow",
    "published_state",
    "structural_summary",
    "inputs",
    "outputs",
    "variables",
    "triggers",
    "canvas",
    "v1_actions",
    "v1_variable_values",
    "warnings",
)
_CONTRACT_SECTIONS: Final[tuple[str, ...]] = (
    "flow",
    "published_state",
    "structural_summary",
    "inputs",
    "outputs",
    "variables",
    "triggers",
    "steps",
    "warnings",
)
_DEFAULT_FLOW_SECTIONS: Final[tuple[str, ...]] = ("flow", "published_state", "structural_summary", "warnings")

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "contract": {
        "description": "Return progressive contract sections for one flow or subflow. The compact default returns identity, publication state, a bounded structural summary, and warnings.",
        "params": {
            "sys_id": "str (32-char)",
            "name": "str",
            "sections": "comma-separated: flow,published_state,structural_summary,inputs,outputs,variables,triggers,steps,warnings; '*' selects all",
            "section_limit": "int (default 100; shared row/node cap)",
        },
    },
    "inspect": {
        "description": "Return progressive inspection sections for one flow. The compact default avoids optional detail reads.",
        "params": {
            "sys_id": "str (32-char)",
            "name": "str",
            "sections": "comma-separated: flow,published_state,structural_summary,inputs,outputs,variables,triggers,canvas,v1_actions,v1_variable_values,warnings; '*' selects all",
            "section_limit": "int (default 100; shared row/node cap)",
        },
    },
    "find_by_table": {
        "description": "Find V1 and V2 flows with record triggers on the given table.",
        "params": {"table": "str"},
    },
    "decode_values": {
        "description": "Stateless decode of a gzip+base64+JSON ``values`` blob.",
        "params": {"value": "str"},
    },
    "list_triggers": {
        "description": "List V1 and V2 trigger rows with optional table/trigger_type/active filters.",
        "params": {
            "table": "str (optional)",
            "trigger_type": "str (optional)",
            "active": "'true' | 'false' (optional)",
            "limit": "int (default 100)",
        },
    },
    "describe": {
        "description": "Return this action registry without making any platform calls.",
        "params": {},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _v(field: Any) -> str:
    """Return the underlying value of a display_value=all reference field.

    Accepts either a ``{"value": x, "display_value": y}`` dict or a scalar.
    Always returns a string (empty string for None).
    """
    if isinstance(field, dict):
        raw = field.get("value", "")
    elif field is None:
        raw = ""
    else:
        raw = field
    return str(raw) if raw is not None else ""


def _d(field: Any) -> str:
    """Return the display value of a reference field, falling back to the value."""
    if isinstance(field, dict):
        display = field.get("display_value")
        if display:
            return str(display)
        return _v(field)
    return _v(field)


def _maybe_decode(values_field: Any) -> tuple[Any, str | None]:
    """Decode a V2 ``values`` blob when it looks compressed.

    Returns ``(decoded_or_none, error_message_or_none)``. Empty / plain
    string values yield ``(None, None)`` - nothing to decode, no failure.
    """
    raw = _v(values_field)
    if not raw or not looks_compressed(raw):
        return None, None
    try:
        return decode_values(raw), None
    except ValueError as exc:
        return None, str(exc)


def _index_by_sys_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index ``rows`` by their ``sys_id`` field (display_value=all-aware)."""
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _v(row.get("sys_id"))
        if key:
            indexed[key] = row
    return indexed


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _build_v2_node(
    row: dict[str, Any],
    *,
    kind: str,
    action_type_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a canvas node for a V2 action or logic instance."""
    decoded, decode_error = _maybe_decode(row.get("values"))
    node: dict[str, Any] = {
        "kind": kind,
        "version": "v2",
        "sys_id": _v(row.get("sys_id")),
        "ui_uuid": _v(row.get("ui_uuid")),
        "parent_ui_id": _v(row.get("parent_ui_uuid")),
        "order": _v(row.get("order")),
        "label": _d(row.get("label")),
        "name": _v(row.get("name")),
        "comment": _v(row.get("comment")),
        "values_decoded": decoded,
        "children": [],
    }
    if kind == "action":
        action_type_id = _v(row.get("action_type"))
        node["action_type"] = {
            "sys_id": action_type_id,
            "name": _d(row.get("action_type")),
        }
        if action_type_lookup and action_type_id in action_type_lookup:
            meta = action_type_lookup[action_type_id]
            node["action_type"].update(
                {
                    "internal_name": _v(meta.get("internal_name")),
                    "sys_scope": _d(meta.get("sys_scope")),
                    "category": _d(meta.get("category")),
                }
            )
    if kind == "logic":
        node["logic_definition"] = {
            "sys_id": _v(row.get("logic_definition")),
            "name": _d(row.get("logic_definition")),
        }
    if decode_error is not None:
        node["decode_error"] = decode_error
    return node


def _assemble_canvas(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a nested canvas tree by linking each node to its parent_ui_uuid.

    Roots (``parent_ui_id == ""``) are returned at the top level; children
    are attached to their parent's ``children`` list in the order they
    appear in *nodes* (already sorted by ``order`` upstream).
    """
    by_uuid: dict[str, dict[str, Any]] = {}
    for node in nodes:
        uuid = node["ui_uuid"]
        if uuid:
            by_uuid[uuid] = node

    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent = node["parent_ui_id"]
        if parent and parent in by_uuid:
            by_uuid[parent]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _v2_trigger_entry(
    row: dict[str, Any],
    *,
    condition_lookup: dict[str, str],
) -> dict[str, Any]:
    """Build a trigger entry from a V2 ``sys_hub_trigger_instance_v2`` row."""
    remote_id = _v(row.get("remote_trigger_id"))
    decoded, decode_error = _maybe_decode(row.get("values"))
    entry: dict[str, Any] = {
        "version": "v2",
        "sys_id": _v(row.get("sys_id")),
        "type": _v(row.get("type")),
        "active": _v(row.get("active")) == "true",
        "table": _v(row.get("table")),
        "remote_trigger_id": remote_id,
        "condition": condition_lookup.get(remote_id, ""),
        "values_decoded": decoded,
    }
    if decode_error is not None:
        entry["decode_error"] = decode_error
    return entry


def _v1_trigger_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Build a trigger entry from a V1 ``sys_hub_trigger_instance`` row."""
    return {
        "version": "v1",
        "sys_id": _v(row.get("sys_id")),
        "type": _v(row.get("type")),
        "active": _v(row.get("active")) == "true",
        "table": _v(row.get("table")),
        "condition": _v(row.get("condition")),
    }


async def _resolve_inspect_sys_id(
    client: ServiceNowClient,
    sys_id: str,
    name: str,
    correlation_id: str,
) -> tuple[str, str | None]:
    """Resolve the flow ``sys_id`` for ``inspect``.

    Returns ``(resolved_sys_id, error_envelope_or_none)``. When ``sys_id``
    is provided it is returned unchanged; otherwise ``name`` is looked up
    and the single match's sys_id is returned. Empty / ambiguous / missing
    cases yield an error envelope in the second slot.
    """
    if sys_id:
        return sys_id, None
    matches = await client.find_flows_by_name(name)
    if len(matches) == 0:
        return "", _error(correlation_id, f"No flow found with name {name!r}.")
    if len(matches) > 1:
        return "", _error(
            correlation_id,
            f"Name {name!r} is ambiguous ({len(matches)} flows match); pass sys_id instead.",
        )
    resolved = _v(matches[0].get("sys_id"))
    if not resolved:
        return "", _error(correlation_id, f"Resolved flow for {name!r} has no sys_id.")
    return resolved, None


def _build_inspect_warnings(
    *,
    drift: bool,
    master: str,
    latest: str,
    actions_v1: list[dict[str, Any]],
    logic_v1: list[dict[str, Any]],
    triggers_v1: list[dict[str, Any]],
    actions_v2: list[dict[str, Any]],
    logic_v2: list[dict[str, Any]],
    triggers_v2: list[dict[str, Any]],
    action_type_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    """Assemble the ``inspect`` warning list (drift, V1/V2 coexistence, spokes)."""
    warnings: list[str] = []
    if drift:
        warnings.append(
            f"snapshot drift: master_snapshot={master!r} differs from latest_snapshot={latest!r}; "
            "the runtime engine and the latest design may not agree."
        )
    if (actions_v1 or logic_v1 or triggers_v1) and (actions_v2 or logic_v2 or triggers_v2):
        warnings.append("V1 and V2 Flow Designer artifacts coexist on this flow; canvas omits V1 nodes.")
    if actions_v1:
        warnings.append(
            f"{len(actions_v1)} V1 action instance(s) present; their configured bindings are not represented as contract steps."
        )
    if logic_v1:
        warnings.append(
            f"{len(logic_v1)} V1 logic instance(s) present; their semantics are not reflected in the canvas tree."
        )
    spoke_actions = [
        meta
        for meta in action_type_lookup.values()
        if "spoke" in _v(meta.get("category")).lower() or "spoke" in _v(meta.get("sys_scope")).lower()
    ]
    if spoke_actions:
        warnings.append(
            f"{len(spoke_actions)} spoke action type(s) referenced; their decoded values may depend on scoped types."
        )
    return warnings


def _contract_field(row: dict[str, Any]) -> dict[str, Any]:
    """Project a declared flow field into the stable contract shape."""
    field: dict[str, Any] = {
        "name": _v(row.get("element")),
        "label": _v(row.get("label")) or _v(row.get("element")),
        "type": _v(row.get("internal_type")),
        "required": _v(row.get("mandatory")) == "true",
    }
    default = _v(row.get("default_value"))
    if default:
        field["default"] = default
    reference = _v(row.get("reference"))
    if reference:
        field["reference_table"] = reference
    return field


def _action_definition_field(row: dict[str, Any], *, has_default: bool) -> dict[str, Any]:
    """Project one declared action field without exposing its source record."""
    name = _v(row.get("name"))
    field: dict[str, Any] = {
        "name": name,
        "label": _v(row.get("label")) or name,
        "required": _v(row.get("mandatory")).lower() == "true",
    }
    prototype = row.get("element_prototype")
    if isinstance(prototype, dict):
        display = str(prototype.get("display_value") or "")
        if display and re.fullmatch(r"[0-9a-f]{32}", display.lower()) is None:
            field["type"] = display
    if has_default:
        default = _v(row.get("default_value"))
        if default:
            field["default"] = default
    reference = _v(row.get("reference"))
    if reference:
        field["reference_table"] = reference
    return field


def _index_action_definition_fields(
    rows: list[dict[str, Any]],
    *,
    has_default: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Group projected action fields by their ``action_type`` relation."""
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        action_type_id = _v(row.get("action_type"))
        if action_type_id:
            indexed.setdefault(action_type_id, []).append(_action_definition_field(row, has_default=has_default))
    return indexed


def _contract_binding(value: dict[str, Any]) -> dict[str, Any]:
    """Project one decoded Flow Designer input or output assignment.

    ``data_pills`` contains only literal references present in the stored
    binding. It intentionally does not resolve or interpret them.
    """
    parameter = value.get("parameter")
    parameter_data = parameter if isinstance(parameter, dict) else {}
    raw_value = value.get("value", "")
    binding: dict[str, Any] = {
        "name": str(value.get("name", "")),
        "label": str(parameter_data.get("label", "") or value.get("name", "")),
        "type": str(parameter_data.get("type", "")),
        "required": bool(parameter_data.get("mandatory", False)),
        "value": raw_value,
    }
    if isinstance(raw_value, str):
        data_pills = _DATA_PILL_PATTERN.findall(raw_value)
        if data_pills:
            binding["data_pills"] = data_pills
    return binding


def _contract_node_bindings(decoded: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract configured inputs and output assignments from decoded node values."""
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)], []
    if not isinstance(decoded, dict):
        return [], []

    inputs: list[dict[str, Any]] = []
    for key in ("inputs", "decisionTableInputs", "dynamicInputs", "workflowInputs"):
        items = decoded.get(key)
        if isinstance(items, list):
            inputs.extend(item for item in items if isinstance(item, dict))
    assignments = decoded.get("outputsToAssign")
    if not isinstance(assignments, list):
        return inputs, []
    return inputs, [item for item in assignments if isinstance(item, dict)]


def _contract_steps(
    canvas: list[dict[str, Any]],
    action_input_definitions: dict[str, list[dict[str, Any]]],
    action_output_definitions: dict[str, list[dict[str, Any]]],
    schema_limitations: list[str],
) -> list[dict[str, Any]]:
    """Flatten the canvas into execution-order contract steps without raw node metadata."""
    steps: list[dict[str, Any]] = []

    def visit(nodes: list[dict[str, Any]], parent_step: str = "") -> None:
        for index, node in enumerate(nodes, start=1):
            step_id = f"{parent_step}.{index}" if parent_step else str(index)
            inputs, assignments = _contract_node_bindings(node.get("values_decoded"))
            step: dict[str, Any] = {
                "step": step_id,
                "kind": node["kind"],
                "order": node["order"],
                "label": node["label"],
            }
            if node["kind"] == "action":
                action_type = node["action_type"]
                step["action"] = {
                    "name": action_type["name"],
                    "internal_name": action_type.get("internal_name", ""),
                    "category": action_type.get("category", ""),
                    "scope": action_type.get("sys_scope", ""),
                }
                action_type_id = action_type["sys_id"]
                action_definition: dict[str, Any] = {
                    "inputs": list(action_input_definitions.get(action_type_id, [])),
                    "outputs": list(action_output_definitions.get(action_type_id, [])),
                }
                if schema_limitations:
                    action_definition["limitations"] = list(schema_limitations)
                step["definition"] = action_definition
                step["inputs"] = [_contract_binding(value) for value in inputs]
            else:
                definition = node["logic_definition"]
                step["logic"] = {"name": definition["name"]}
                step["conditions"] = [_contract_binding(value) for value in inputs]
            if assignments:
                step["output_assignments"] = [_contract_binding(value) for value in assignments]
            if "decode_error" in node:
                step["decode_error"] = node["decode_error"]
            steps.append(step)
            visit(node["children"], step_id)

    visit(canvas)
    return steps


def _contract_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    """Project a trigger into contract fields without raw decoded metadata."""
    result: dict[str, Any] = {
        "version": trigger["version"],
        "type": trigger["type"],
        "active": trigger["active"],
        "table": trigger["table"],
        "condition": trigger["condition"],
    }
    configuration, _ = _contract_node_bindings(trigger.get("values_decoded"))
    if configuration:
        result["configuration"] = [_contract_binding(value) for value in configuration]
    if "decode_error" in trigger:
        result["decode_error"] = trigger["decode_error"]
    return result


def _build_flow_contract(
    payload: dict[str, Any],
    action_input_definitions: dict[str, list[dict[str, Any]]],
    action_output_definitions: dict[str, list[dict[str, Any]]],
    schema_limitations: list[str],
) -> dict[str, Any]:
    """Build a concise contract from the detailed inspect payload.

    The contract retains literal binding values and data pills as stored, so
    consumers can distinguish configured mappings from inferred behavior.
    """
    return {
        "flow": payload["flow"],
        "published_state": payload["published_state"],
        "inputs": [_contract_field(row) for row in payload["inputs"]],
        "outputs": [_contract_field(row) for row in payload["outputs"]],
        "variables": [_contract_field(row) for row in payload["variables"]],
        "triggers": [_contract_trigger(trigger) for trigger in payload["triggers"]],
        "steps": _contract_steps(
            payload["canvas"],
            action_input_definitions,
            action_output_definitions,
            schema_limitations,
        ),
        "warnings": [*payload["warnings"], *schema_limitations],
    }


def _parse_sections(sections: str, *, is_contract: bool) -> tuple[list[str], str | None]:
    available = _CONTRACT_SECTIONS if is_contract else _INSPECT_SECTIONS
    if not sections.strip():
        return list(_DEFAULT_FLOW_SECTIONS), None
    if sections.strip() == "*":
        return list(available), None

    requested = list(dict.fromkeys(part.strip() for part in sections.split(",") if part.strip()))
    unknown = [section for section in requested if section not in available]
    if unknown:
        return [], f"Unknown section(s): {', '.join(unknown)}. Available: {', '.join(available)}, *."
    return requested, None


def _required_datasets(selected_sections: list[str]) -> set[str]:
    datasets: set[str] = set()
    for section in selected_sections:
        datasets.update(_SECTION_DEPENDENCIES[section])
    return datasets


def _continuation(
    *,
    section_limit: int,
    max_row_limit: int,
    flow_sys_id: str,
    source_datasets: tuple[str, ...],
) -> str:
    if section_limit < max_row_limit:
        return f"Re-run with section_limit greater than {section_limit}."
    query_paths = ", ".join(
        f"{table} (encoded_query={field}={flow_sys_id})"
        for table, field in (_DATASET_QUERY_PATHS[name] for name in source_datasets)
    )
    return (
        f"The configured MAX_ROW_LIMIT of {max_row_limit} has been reached; no further continuation is available "
        f"through flow. Use query with an explicit fields projection and a safe encoded query on: {query_paths}. "
        "Query safety and row limits still apply."
    )


def _warning_continuation(*, section_limit: int, max_row_limit: int, flow_sys_id: str) -> str:
    if section_limit < max_row_limit:
        return f"Re-run with section_limit greater than {section_limit}."
    return (
        f"The configured MAX_ROW_LIMIT of {max_row_limit} has been reached; no further continuation is available "
        "through flow. To complete warning analysis, use query with pagination and these explicit projections: "
        f"sys_hub_action_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id,action_type); "
        f"sys_hub_action_instance (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_flow_logic_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_flow_logic (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_trigger_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_trigger_instance (encoded_query=flow={flow_sys_id}, fields=sys_id). Then collect every action_type "
        "sys_id from the V2 actions and query sys_hub_action_type_base "
        "(encoded_query=sys_idIN<action_type_sys_ids>, fields=sys_id,category,sys_scope) for spoke detection. "
        "Use limit and offset to continue each read. Query safety and row limits still apply."
    )


def _v1_variable_value_continuation(*, section_limit: int, max_row_limit: int, flow_sys_id: str) -> str:
    next_step = (
        f"Re-run with section_limit greater than {section_limit}. "
        if section_limit < max_row_limit
        else f"The configured MAX_ROW_LIMIT of {max_row_limit} has been reached. "
    )
    return (
        f"{next_step}For direct recovery, use query with an explicit fields projection and a safe encoded query on "
        f"sys_hub_action_instance (encoded_query=flow={flow_sys_id}, fields=sys_id) to obtain every action sys_id, "
        "then query sys_variable_value "
        "(encoded_query=document=sys_hub_action_instance^document_keyIN<action_sys_ids>, "
        "fields=document,document_key,variable,value). Use limit and offset to continue each read. "
        "Query safety and row limits still apply."
    )


def _saturated_datasets(
    datasets: dict[str, list[dict[str, Any]]],
    requested_limits: dict[str, int],
    names: frozenset[str],
) -> list[str]:
    return sorted(
        name
        for name in names
        if requested_limits.get(name, 0) > 0 and len(datasets.get(name, [])) >= requested_limits[name]
    )


def _bounded_rows(
    rows: list[dict[str, Any]],
    *,
    section: str,
    section_limit: int,
    truncation: dict[str, dict[str, Any]],
    continuation: str,
) -> list[dict[str, Any]]:
    returned = rows[:section_limit]
    if len(rows) > section_limit:
        truncation[section] = {
            "returned": len(returned),
            "observed_at_least": len(rows),
            "omitted_at_least": len(rows) - len(returned),
            "continuation": continuation,
        }
    return returned


def _structural_summary(datasets: dict[str, list[dict[str, Any]]], section_limit: int) -> dict[str, Any]:
    counts = {name: min(len(datasets.get(name, [])), section_limit) for name in sorted(_STRUCTURAL_DATASETS)}
    total_nodes = counts["actions_v2"] + counts["actions_v1"] + counts["logic_v2"] + counts["logic_v1"]
    return {
        "counts": counts,
        "count_semantics": "exact unless truncated=true; truncated dataset counts are lower bounds",
        "node_count": total_nodes,
        "trigger_count": counts["triggers_v2"] + counts["triggers_v1"],
        "versions": [
            version
            for version in ("v1", "v2")
            if any(counts[f"{kind}_{version}"] for kind in ("actions", "logic", "triggers"))
        ],
        "truncated": any(len(datasets.get(name, [])) > section_limit for name in _STRUCTURAL_DATASETS),
        "per_dataset_limit": section_limit,
    }


async def _fetch_flow_datasets(
    client: ServiceNowClient,
    resolved_sys_id: str,
    required: set[str],
    section_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    fetchers = {
        "inputs": client.list_flow_inputs,
        "outputs": client.list_flow_outputs,
        "variables": client.list_flow_variables,
        "actions_v2": client.list_action_instances_v2,
        "actions_v1": client.list_action_instances_v1,
        "logic_v2": client.list_logic_instances_v2,
        "logic_v1": client.list_logic_instances_v1,
        "triggers_v2": client.list_trigger_instances_v2,
        "triggers_v1": client.list_trigger_instances_v1,
    }
    names = sorted(required)
    direct_names = [name for name in names if name != "v1_variable_values"]
    rows = await asyncio.gather(*(fetchers[name](resolved_sys_id, section_limit + 1) for name in direct_names))
    datasets = dict(zip(direct_names, rows, strict=True))
    requested_limits = dict.fromkeys(direct_names, section_limit + 1)
    if "v1_variable_values" in required:
        v1_action_ids = sorted(
            {_v(action.get("sys_id")) for action in datasets.get("actions_v1", []) if _v(action.get("sys_id"))}
        )
        datasets["v1_variable_values"] = await client.list_v1_variable_values(v1_action_ids) if v1_action_ids else []
        requested_limits["v1_variable_values"] = min(len(v1_action_ids) * 10, 5000) if v1_action_ids else 0
    return datasets, requested_limits


async def _action_inspect(
    *,
    sys_id: str,
    name: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    client_factory: ServiceNowClientProvider,
    correlation_id: str,
    sections: str,
    section_limit: int,
    is_contract: bool = False,
) -> str:
    if sys_id and name:
        return _error(correlation_id, "Provide exactly one of sys_id or name (not both).")
    if not sys_id and not name:
        return _error(correlation_id, "Either sys_id or name is required.")
    if sys_id:
        validate_sys_id(sys_id)
    selected_sections, section_error = _parse_sections(sections, is_contract=is_contract)
    if section_error is not None:
        return _error(correlation_id, section_error)
    effective_limit = max(
        1, min(section_limit if section_limit > 0 else _DEFAULT_SECTION_LIMIT, settings.max_row_limit)
    )
    required = _required_datasets(selected_sections)

    async with client_factory() as client:
        resolved_sys_id, err = await _resolve_inspect_sys_id(client, sys_id, name, correlation_id)
        if err is not None:
            return err

        header = await client.get_flow_by_sys_id(resolved_sys_id)
        if header is None:
            return _error(correlation_id, f"Flow {resolved_sys_id} not found.")

        datasets, requested_limits = await _fetch_flow_datasets(client, resolved_sys_id, required, effective_limit)
        truncation: dict[str, dict[str, Any]] = {}
        inputs = _bounded_rows(
            datasets.get("inputs", []),
            section="inputs",
            section_limit=effective_limit,
            truncation=truncation,
            continuation=_continuation(
                section_limit=effective_limit,
                max_row_limit=settings.max_row_limit,
                flow_sys_id=resolved_sys_id,
                source_datasets=("inputs",),
            ),
        )
        outputs = _bounded_rows(
            datasets.get("outputs", []),
            section="outputs",
            section_limit=effective_limit,
            truncation=truncation,
            continuation=_continuation(
                section_limit=effective_limit,
                max_row_limit=settings.max_row_limit,
                flow_sys_id=resolved_sys_id,
                source_datasets=("outputs",),
            ),
        )
        variables = _bounded_rows(
            datasets.get("variables", []),
            section="variables",
            section_limit=effective_limit,
            truncation=truncation,
            continuation=_continuation(
                section_limit=effective_limit,
                max_row_limit=settings.max_row_limit,
                flow_sys_id=resolved_sys_id,
                source_datasets=("variables",),
            ),
        )
        actions_v2 = datasets.get("actions_v2", [])
        actions_v1 = datasets.get("actions_v1", [])
        logic_v2 = datasets.get("logic_v2", [])
        logic_v1 = datasets.get("logic_v1", [])
        triggers_v2 = datasets.get("triggers_v2", [])
        triggers_v1 = datasets.get("triggers_v1", [])
        node_actions_v2 = actions_v2
        node_logic_v2 = logic_v2
        returned_triggers_v2 = triggers_v2
        returned_triggers_v1 = triggers_v1
        if "structural_summary" in selected_sections:
            truncated_datasets = _saturated_datasets(datasets, requested_limits, _STRUCTURAL_DATASETS)
            if truncated_datasets:
                truncation["structural_summary"] = {
                    "datasets": truncated_datasets,
                    "returned_per_dataset": effective_limit,
                    "continuation": _continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                        source_datasets=tuple(truncated_datasets),
                    ),
                }

        if "warnings" in selected_sections:
            saturated_dependencies = _saturated_datasets(datasets, requested_limits, _STRUCTURAL_DATASETS)
            if saturated_dependencies:
                truncation["warnings"] = {
                    "datasets": saturated_dependencies,
                    "limitation": "Warning analysis covers only the probed rows for the saturated dependencies.",
                    "continuation": _warning_continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                    ),
                }

        selected_node_section = "steps" if is_contract else "canvas"
        if selected_node_section in selected_sections:
            combined_nodes = sorted(
                [("action", row) for row in actions_v2] + [("logic", row) for row in logic_v2],
                key=lambda item: _safe_int(_v(item[1].get("order"))),
            )
            bounded_nodes = combined_nodes[:effective_limit]
            if len(combined_nodes) > effective_limit:
                truncation[selected_node_section] = {
                    "returned": len(bounded_nodes),
                    "observed_at_least": len(combined_nodes),
                    "omitted_at_least": len(combined_nodes) - len(bounded_nodes),
                    "continuation": _continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                        source_datasets=("actions_v2", "logic_v2"),
                    ),
                }
            node_actions_v2 = [row for kind, row in bounded_nodes if kind == "action"]
            node_logic_v2 = [row for kind, row in bounded_nodes if kind == "logic"]

        if "triggers" in selected_sections:
            all_triggers = [("v2", row) for row in triggers_v2] + [("v1", row) for row in triggers_v1]
            bounded_triggers = all_triggers[:effective_limit]
            if len(all_triggers) > effective_limit:
                truncation["triggers"] = {
                    "returned": len(bounded_triggers),
                    "observed_at_least": len(all_triggers),
                    "omitted_at_least": len(all_triggers) - len(bounded_triggers),
                    "continuation": _continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                        source_datasets=("triggers_v2", "triggers_v1"),
                    ),
                }
            returned_triggers_v2 = [row for version, row in bounded_triggers if version == "v2"]
            returned_triggers_v1 = [row for version, row in bounded_triggers if version == "v1"]

        remote_ids = sorted(
            {_v(t.get("remote_trigger_id")) for t in returned_triggers_v2 if _v(t.get("remote_trigger_id"))}
        )
        record_triggers = (
            await client.list_record_triggers(remote_ids) if "triggers" in selected_sections and remote_ids else []
        )
        if "triggers" in selected_sections and len(remote_ids) > INTERNAL_QUERY_LIMIT:
            trigger_truncation = truncation.setdefault(
                "triggers",
                {
                    "returned": len(returned_triggers_v2) + len(returned_triggers_v1),
                    "possible_more": True,
                },
            )
            trigger_truncation["dependency_datasets"] = ["sys_flow_record_trigger"]
            trigger_truncation["limitation"] = (
                f"V2 trigger conditions were queried for only {INTERNAL_QUERY_LIMIT} remote trigger ids."
            )
            trigger_truncation["continuation"] = (
                "Batch all remote_trigger_id values from the returned V2 triggers, then use query with an explicit "
                "fields projection on sys_flow_record_trigger (encoded_query=sys_idIN<remote_trigger_ids>, "
                "fields=sys_id,condition). Use limit and offset to continue each batch. Query safety and row limits "
                "still apply."
            )

        node_action_type_ids = {_v(a.get("action_type")) for a in node_actions_v2 if _v(a.get("action_type"))}
        warning_action_type_ids = {_v(a.get("action_type")) for a in actions_v2 if _v(a.get("action_type"))}
        required_action_type_ids: set[str] = set()
        if selected_node_section in selected_sections:
            required_action_type_ids.update(node_action_type_ids)
        if "warnings" in selected_sections:
            required_action_type_ids.update(warning_action_type_ids)
        action_type_ids = sorted(required_action_type_ids)
        action_type_rows = await client.get_action_type_definitions(action_type_ids) if action_type_ids else []
        returned_action_type_ids = {_v(row.get("sys_id")) for row in action_type_rows if _v(row.get("sys_id"))}
        section_action_type_ids = {
            selected_node_section: node_action_type_ids,
            "warnings": warning_action_type_ids,
        }
        for section, section_type_ids in section_action_type_ids.items():
            missing_action_type_ids = sorted(section_type_ids - returned_action_type_ids)
            if section not in selected_sections or not missing_action_type_ids:
                continue
            section_truncation = truncation.setdefault(
                section,
                {
                    "datasets": [],
                    "limitation": f"{section} has incomplete dependencies.",
                    "continuation": _warning_continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                    ),
                },
            )
            section_truncation["missing_action_type_metadata"] = len(missing_action_type_ids)
            section_truncation["limitation"] = (
                f"{section} covers only probed structural rows and returned action-type metadata."
            )
            section_truncation["continuation"] += (
                f" Query sys_hub_action_type_base (encoded_query=sys_idIN{','.join(missing_action_type_ids)}, "
                "fields=sys_id,name,internal_name,sys_scope,category) to recover missing action metadata. Query safety "
                "and row limits still apply."
            )

        action_input_rows: list[dict[str, Any]] = []
        action_output_rows: list[dict[str, Any]] = []
        schema_limitations: list[str] = []
        node_action_type_id_list = sorted(node_action_type_ids)
        if is_contract and "steps" in selected_sections and node_action_type_id_list:
            try:
                action_input_rows = await client.list_action_input_definitions(node_action_type_id_list)
            except Exception as exc:
                schema_limitations.append(f"Action input definitions are unavailable: {exc}")
            try:
                action_output_rows = await client.list_action_output_definitions(node_action_type_id_list)
            except Exception as exc:
                schema_limitations.append(f"Action output definitions are unavailable: {exc}")

        v1_variable_values = datasets.get("v1_variable_values", [])
        v1_variable_values = _bounded_rows(
            v1_variable_values,
            section="v1_variable_values",
            section_limit=effective_limit,
            truncation=truncation,
            continuation=_v1_variable_value_continuation(
                section_limit=effective_limit,
                max_row_limit=settings.max_row_limit,
                flow_sys_id=resolved_sys_id,
            ),
        )

        if "v1_variable_values" in selected_sections and _saturated_datasets(
            datasets, requested_limits, frozenset({"actions_v1"})
        ):
            truncation["v1_variable_values"] = {
                "dependency_datasets": ["actions_v1"],
                "returned": len(v1_variable_values),
                "possible_more": True,
                "limitation": "Variable values were queried only for the probed V1 action instances.",
                "continuation": _v1_variable_value_continuation(
                    section_limit=effective_limit,
                    max_row_limit=settings.max_row_limit,
                    flow_sys_id=resolved_sys_id,
                ),
            }

    # Build lookups.
    action_type_lookup = _index_by_sys_id(action_type_rows)
    condition_lookup: dict[str, str] = {
        _v(row.get("sys_id")): _v(row.get("condition")) for row in record_triggers if _v(row.get("sys_id"))
    }

    # Build canvas nodes (actions + logic, both V2), sorted by ``order``.
    canvas: list[dict[str, Any]] = []
    if selected_node_section in selected_sections:
        nodes: list[dict[str, Any]] = [
            _build_v2_node(row, kind="action", action_type_lookup=action_type_lookup) for row in node_actions_v2
        ]
        nodes.extend(_build_v2_node(row, kind="logic") for row in node_logic_v2)
        nodes.sort(key=lambda n: _safe_int(n["order"]))
        canvas = _assemble_canvas(nodes)

    # Triggers - V2 first (stitched), then V1.
    triggers: list[dict[str, Any]] = []
    if "triggers" in selected_sections:
        triggers = [_v2_trigger_entry(row, condition_lookup=condition_lookup) for row in returned_triggers_v2]
        triggers.extend(_v1_trigger_entry(row) for row in returned_triggers_v1)

    master = _v(header.get("master_snapshot"))
    latest = _v(header.get("latest_snapshot"))
    drift = bool(master and latest and master != latest)

    warnings = _build_inspect_warnings(
        drift=drift,
        master=master,
        latest=latest,
        actions_v1=actions_v1,
        logic_v1=logic_v1,
        triggers_v1=triggers_v1,
        actions_v2=actions_v2,
        logic_v2=logic_v2,
        triggers_v2=triggers_v2,
        action_type_lookup=action_type_lookup,
    )

    full_payload: dict[str, Any] = {
        "flow": {
            "sys_id": _v(header.get("sys_id")),
            "name": _d(header.get("name")),
            "internal_name": _v(header.get("internal_name")),
            "type": _v(header.get("type")),
            "active": _v(header.get("active")) == "true",
            "description": _v(header.get("description")),
            "sys_scope": _d(header.get("sys_scope")),
        },
        "published_state": {
            "master_snapshot": master,
            "latest_snapshot": latest,
            "drift": drift,
        },
        "inputs": [_flatten_record(row) for row in inputs],
        "outputs": [_flatten_record(row) for row in outputs],
        "variables": [_flatten_record(row) for row in variables],
        "triggers": triggers,
        "canvas": canvas,
        "v1_actions": (
            [
                _flatten_record(row)
                for row in _bounded_rows(
                    actions_v1,
                    section="v1_actions",
                    section_limit=effective_limit,
                    truncation=truncation,
                    continuation=_continuation(
                        section_limit=effective_limit,
                        max_row_limit=settings.max_row_limit,
                        flow_sys_id=resolved_sys_id,
                        source_datasets=("actions_v1",),
                    ),
                )
            ]
            if "v1_actions" in selected_sections
            else []
        ),
        "v1_variable_values": [_flatten_record(row) for row in v1_variable_values],
        "warnings": warnings,
    }
    action_input_definitions = _index_action_definition_fields(action_input_rows, has_default=True)
    action_output_definitions = _index_action_definition_fields(action_output_rows, has_default=False)
    assembled = (
        _build_flow_contract(full_payload, action_input_definitions, action_output_definitions, schema_limitations)
        if is_contract
        else full_payload
    )
    if "v1_variable_values" in selected_sections and requested_limits.get("v1_variable_values") == len(
        v1_variable_values
    ):
        truncation.setdefault(
            "v1_variable_values",
            {
                "returned": len(v1_variable_values),
                "possible_more": True,
                "continuation": _v1_variable_value_continuation(
                    section_limit=effective_limit,
                    max_row_limit=settings.max_row_limit,
                    flow_sys_id=resolved_sys_id,
                ),
            },
        )
    assembled["structural_summary"] = _structural_summary(datasets, effective_limit)
    data = {section: assembled[section] for section in selected_sections}
    available_sections = _CONTRACT_SECTIONS if is_contract else _INSPECT_SECTIONS
    mode = "all" if sections.strip() == "*" else "explicit" if sections.strip() else "compact"
    selection = {
        "mode": mode,
        "requested_sections": ["*"] if mode == "all" else selected_sections if mode == "explicit" else None,
        "default_sections": list(_DEFAULT_FLOW_SECTIONS),
        "returned_sections": selected_sections,
        "omitted_sections": [section for section in available_sections if section not in selected_sections],
        "section_limit": effective_limit,
        "truncated": bool(truncation),
        "truncation": truncation,
        "dataset_probe_limits": requested_limits,
    }
    return format_response(data=data, correlation_id=correlation_id, selection=selection)


def _safe_int(value: str) -> int:
    """Best-effort int conversion for sorting; non-numeric sorts last."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**9


def _flatten_record(row: dict[str, Any]) -> dict[str, Any]:
    """Collapse a display_value=all row to its raw ``value`` per field."""
    return {key: _v(val) for key, val in row.items()}


# ---------------------------------------------------------------------------
# find_by_table
# ---------------------------------------------------------------------------


async def _action_find_by_table(
    *,
    table: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    client_factory: ServiceNowClientProvider,
    correlation_id: str,
) -> str:
    if not table:
        return _error(correlation_id, "'table' is required for action='find_by_table'.")
    validate_identifier(table)

    async with client_factory() as client:
        record_triggers = await client.find_record_triggers_by_table(table)
        remote_ids = sorted({_v(r.get("sys_id")) for r in record_triggers if _v(r.get("sys_id"))})
        v2_triggers = await client.list_v2_triggers_by_remote_ids(remote_ids) if remote_ids else []
        v1_triggers = await client.list_v1_triggers_by_table(table)

        flow_versions = _collect_flow_versions(v2_triggers, v1_triggers)
        flow_sys_ids = sorted(flow_versions.keys())
        flows_meta = await client.get_flows_bulk(flow_sys_ids) if flow_sys_ids else []

    meta_by_id = _index_by_sys_id(flows_meta)
    v1_unique = {fid for fid, versions in flow_versions.items() if "v1" in versions}
    v2_unique = {fid for fid, versions in flow_versions.items() if "v2" in versions}
    flows = [_find_by_table_entry(fid, versions, meta_by_id.get(fid, {})) for fid, versions in flow_versions.items()]

    payload: dict[str, Any] = {
        "table": table,
        "v1_count": len(v1_unique),
        "v2_count": len(v2_unique),
        "total": len(flow_versions),
        "flows": flows,
    }
    return format_response(data=payload, correlation_id=correlation_id)


def _collect_flow_versions(
    v2_triggers: list[dict[str, Any]],
    v1_triggers: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Group trigger rows by flow sys_id, tagging each with its version set."""
    flow_versions: dict[str, set[str]] = {}
    for row in v2_triggers:
        flow_id = _v(row.get("flow"))
        if flow_id:
            flow_versions.setdefault(flow_id, set()).add("v2")
    for row in v1_triggers:
        flow_id = _v(row.get("flow"))
        if flow_id:
            flow_versions.setdefault(flow_id, set()).add("v1")
    return flow_versions


def _find_by_table_entry(
    flow_id: str,
    versions: set[str],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build one ``find_by_table`` flow entry from its trigger versions and metadata."""
    return {
        "sys_id": flow_id,
        "name": _d(meta.get("name")) if meta else "",
        "internal_name": _v(meta.get("internal_name")) if meta else "",
        "type": _v(meta.get("type")) if meta else "",
        "active": _v(meta.get("active")) == "true" if meta else False,
        "sys_scope": _d(meta.get("sys_scope")) if meta else "",
        "version": "+".join(sorted(versions)),
    }


# ---------------------------------------------------------------------------
# decode_values
# ---------------------------------------------------------------------------


def _action_decode_values(*, value: str, correlation_id: str) -> str:
    if not value:
        return _error(correlation_id, "'value' is required for action='decode_values'.")
    try:
        decoded = decode_values(value)
    except ValueError as exc:
        return _error(correlation_id, str(exc))
    return format_response(
        data={"decoded": decoded, "encoding": "gzip+base64+json"},
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# list_triggers
# ---------------------------------------------------------------------------


async def _action_list_triggers(
    *,
    table: str,
    trigger_type: str,
    active: str,
    limit: int,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    client_factory: ServiceNowClientProvider,
    correlation_id: str,
) -> str:
    if active and active not in {"true", "false"}:
        return _error(
            correlation_id,
            f"'active' must be 'true', 'false', or '' (got {active!r}).",
        )
    if table:
        validate_identifier(table)
    if trigger_type:
        validate_identifier(trigger_type)

    effective_limit = limit if limit and limit > 0 else _DEFAULT_TRIGGER_LIMIT
    effective_limit = max(1, min(effective_limit, settings.max_row_limit))

    async with client_factory() as client:
        filtered = await client.list_triggers_filtered(
            trigger_type=trigger_type,
            table=table,
            active=active,
            limit=effective_limit,
        )

        v2_rows = list(filtered.get("v2") or [])
        v1_rows = list(filtered.get("v1") or [])

        flow_ids = {_v(row.get("flow")) for row in v2_rows + v1_rows if _v(row.get("flow"))}
        flows_meta = await client.get_flows_bulk(sorted(flow_ids)) if flow_ids else []

    meta_by_id = _index_by_sys_id(flows_meta)

    triggers: list[dict[str, Any]] = [_trigger_with_flow(row, "v2", meta_by_id) for row in v2_rows]
    triggers.extend(_trigger_with_flow(row, "v1", meta_by_id) for row in v1_rows)

    payload: dict[str, Any] = {
        "v1_count": len(v1_rows),
        "v2_count": len(v2_rows),
        "triggers": triggers,
    }
    return format_response(
        data=payload,
        correlation_id=correlation_id,
        pagination={"limit": effective_limit, "offset": 0, "total": len(triggers)},
    )


def _trigger_with_flow(
    row: dict[str, Any],
    version: str,
    meta_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a ``list_triggers`` entry stitched with flow metadata."""
    flow_id = _v(row.get("flow"))
    meta = meta_by_id.get(flow_id, {})
    entry: dict[str, Any] = {
        "version": version,
        "sys_id": _v(row.get("sys_id")),
        "type": _v(row.get("type")),
        "active": _v(row.get("active")) == "true",
        "table": _v(row.get("table")),
        "flow": {
            "sys_id": flow_id,
            "name": _d(meta.get("name")) if meta else _d(row.get("flow")),
        },
    }
    if version == "v1":
        entry["condition"] = _v(row.get("condition"))
    else:
        entry["remote_trigger_id"] = _v(row.get("remote_trigger_id"))
    return entry


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


def _action_describe(correlation_id: str) -> str:
    """Return the action registry without making any platform calls."""
    return format_response(
        data={"actions": _ACTION_REGISTRY},
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``flow`` tool on the MCP server.

    ``flow`` is read-only and does not consume the shared registries; both
    keyword arguments are accepted only to keep the loader contract uniform
    across tool groups.
    """
    del choices, dictionary  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def flow(
        action: str,
        sys_id: str = "",
        name: str = "",
        value: str = "",
        table: str = "",
        trigger_type: str = "",
        active: str = "",
        limit: int = 0,
        sections: str = "",
        section_limit: int = 0,
        *,
        correlation_id: str = "",
    ) -> str:
        """Inspect Flow Designer flows, triggers, and value blobs (read-only).

        Args:
            action: 'contract' | 'inspect' | 'find_by_table' | 'decode_values' | 'list_triggers' | 'describe'.
            sys_id: Flow sys_id (contract/inspect; mutually exclusive with name).
            name: Flow name (contract/inspect; mutually exclusive with sys_id).
            value: gzip+base64+JSON blob to decode (decode_values).
            table: Target table (find_by_table; optional filter for list_triggers).
            trigger_type: Trigger type filter (list_triggers, e.g. 'record_update').
            active: 'true' | 'false' filter (list_triggers).
            limit: Row cap for list_triggers (default 100).
            sections: Comma-separated inspect/contract sections. Empty uses the compact default; '*' returns all.
            section_limit: Shared cap for selected flow rows/nodes (default 100, max MAX_ROW_LIMIT).
        """
        if action not in _VALID_ACTIONS:
            return _error(
                correlation_id,
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "describe":
            return _action_describe(correlation_id)

        if action == "decode_values":
            return _action_decode_values(value=value, correlation_id=correlation_id)

        if action in {"contract", "inspect"}:
            return await _action_inspect(
                sys_id=sys_id,
                name=name,
                settings=settings,
                auth_provider=auth_provider,
                client_factory=client_factory,
                correlation_id=correlation_id,
                sections=sections,
                section_limit=section_limit,
                is_contract=action == "contract",
            )

        if action == "find_by_table":
            return await _action_find_by_table(
                table=table,
                settings=settings,
                auth_provider=auth_provider,
                client_factory=client_factory,
                correlation_id=correlation_id,
            )

        return await _action_list_triggers(
            table=table,
            trigger_type=trigger_type,
            active=active,
            limit=limit,
            settings=settings,
            auth_provider=auth_provider,
            client_factory=client_factory,
            correlation_id=correlation_id,
        )
