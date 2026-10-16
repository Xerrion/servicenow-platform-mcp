"""Unified ``flow`` tool: read-only Flow Designer inspection.

Six actions:

* ``contract``       - declared fields, configured stages, ordered V2 actions/logic/subflows, and bindings.
* ``inspect``        - assemble one flow by sys_id or name: header, triggers,
  inputs/outputs/variables, configured stages, decoded V2 nodes, canvas tree, snapshot drift.
* ``find_by_table``  - find flows with record triggers on a given table
  (merges V1 + V2).
* ``decode_values``  - stateless decode of a gzip+base64+JSON ``values`` blob.
* ``list_triggers``  - list V1 and V2 trigger rows with optional filters.
* ``describe``       - return the action registry without platform I/O.

The decoder lives in :mod:`servicenow_mcp.capabilities.flows._flow_values`; canvas-tree
assembly stays inline. We deliberately do not touch ``/api/now/processflow/*``
endpoints (undocumented) or ``sys_hub_flow_snapshot`` (opaque cache).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, NoReturn

from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.capabilities.flows import lifecycle, semantics, support
from servicenow_mcp.capabilities.flows._flow_models import (
    ActionTypeRef,
    ContractBinding,
    ContractTrigger,
    FlowNode,
    LogicDefinitionRef,
    StageDefinition,
    V1Trigger,
    V2Trigger,
)
from servicenow_mcp.capabilities.flows._flow_values import (
    decode_values,
    looks_compressed,
)
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "contract",
        "inspect",
        "find_by_table",
        "decode_values",
        "list_triggers",
        "describe",
        "validate",
        "status",
    },
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
    "subflows_v2": ("sys_hub_sub_flow_instance_v2", "flow"),
    "logic_v2": ("sys_hub_flow_logic_instance_v2", "flow"),
    "logic_v1": ("sys_hub_flow_logic", "flow"),
    "triggers_v2": ("sys_hub_trigger_instance_v2", "flow"),
    "triggers_v1": ("sys_hub_trigger_instance", "flow"),
}

_STRUCTURAL_DATASETS: Final[frozenset[str]] = frozenset(
    {
        "actions_v2",
        "actions_v1",
        "subflows_v2",
        "logic_v2",
        "logic_v1",
        "triggers_v2",
        "triggers_v1",
    },
)
_SECTION_DEPENDENCIES: Final[dict[str, frozenset[str]]] = {
    "flow": frozenset(),
    "published_state": frozenset(),
    "structural_summary": _STRUCTURAL_DATASETS,
    "inputs": frozenset({"inputs"}),
    "outputs": frozenset({"outputs"}),
    "variables": frozenset({"variables"}),
    "stages": frozenset(),
    "triggers": frozenset({"triggers_v2", "triggers_v1"}),
    "canvas": frozenset({"actions_v2", "logic_v2", "subflows_v2"}),
    "v1_actions": frozenset({"actions_v1"}),
    "v1_variable_values": frozenset({"actions_v1", "v1_variable_values"}),
    "steps": frozenset({"actions_v2", "logic_v2", "subflows_v2"}),
    "warnings": _STRUCTURAL_DATASETS,
}
_INSPECT_SECTIONS: Final[tuple[str, ...]] = (
    "flow",
    "published_state",
    "structural_summary",
    "inputs",
    "outputs",
    "variables",
    "stages",
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
    "stages",
    "triggers",
    "steps",
    "warnings",
)
_DEFAULT_FLOW_SECTIONS: Final[tuple[str, ...]] = (
    "flow",
    "published_state",
    "structural_summary",
    "warnings",
)

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "validate": {
        "description": (
            "Read a saved draft and validate supported semantics locally. No remote compile, save, or execution."
        ),
        "params": {"sys_id": "str (32-char)"},
    },
    "status": {
        "description": (
            "Read lifecycle fields and optionally one known execution context. Unknown state never permits replay."
        ),
        "params": {
            "sys_id": "str (32-char)",
            "execution_sys_id": "str (optional 32-char)",
        },
    },
    "contract": {
        "description": (
            "Return progressive contract sections for one flow or subflow. The compact default returns identity, "
            "publication state, a bounded structural summary, and warnings."
        ),
        "params": {
            "sys_id": "str (32-char)",
            "name": "str",
            "sections": (
                "native array containing: "
                "flow,published_state,structural_summary,inputs,outputs,variables,stages,triggers,steps,warnings; "
                "'*' selects all"
            ),
            "section_limit": "int (default 100; shared row/node cap)",
        },
    },
    "inspect": {
        "description": (
            "Return progressive inspection sections for one flow. The compact default avoids optional detail reads."
        ),
        "params": {
            "sys_id": "str (32-char)",
            "name": "str",
            "sections": (
                "native array containing: "
                "flow,published_state,structural_summary,inputs,outputs,variables,stages,triggers,canvas,"
                "v1_actions,v1_variable_values,warnings; '*' selects all"
            ),
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


def _error(message: str) -> NoReturn:
    """Reject an invalid operation."""
    raise CapabilityError(message)


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


def _stage_definition(row: dict[str, Any]) -> dict[str, Any]:
    """Project a stored Flow Designer stage into its configured lifecycle fields."""
    return StageDefinition(
        stage_id=_v(row.get("stage_id")),
        label=_v(row.get("label")),
        value=_v(row.get("value")),
        states=_v(row.get("states")),
        type=_v(row.get("type")),
        order=_v(row.get("order")),
        component_indexes=_v(row.get("component_indexes")),
        ancestor_component_id=_v(row.get("ancestor_component_id")),
        ancestor_stage_id=_v(row.get("ancestor_stage_id")),
        ancestral_if_else_logic=_v(row.get("ancestral_if_else_logic")),
        always_show=_v(row.get("always_show")).lower() == "true",
    ).to_payload()


async def _flow_stages(
    client: ServiceNowClient,
    *,
    root_flow_sys_id: str,
    published_snapshot_sys_id: str,
    section_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read configured stages with root-flow and published-snapshot provenance."""
    source_roles = {root_flow_sys_id: ["root_flow"]}
    if published_snapshot_sys_id:
        source_roles.setdefault(published_snapshot_sys_id, []).append(
            "published_snapshot"
        )
    source_ids = list(source_roles)
    source_rows = await asyncio.gather(
        *(
            client.list_flow_stages(source_id, section_limit + 1)
            for source_id in source_ids
        ),
    )
    sources: list[dict[str, Any]] = []
    truncated_sources: list[dict[str, Any]] = []
    for source_id, rows in zip(source_ids, source_rows, strict=True):
        definitions = [_stage_definition(row) for row in rows[:section_limit]]
        sources.append(
            {
                "flow_sys_id": source_id,
                "roles": source_roles[source_id],
                "definitions": definitions,
            },
        )
        if len(rows) > section_limit:
            truncated_sources.append(
                {
                    "flow_sys_id": source_id,
                    "roles": source_roles[source_id],
                    "returned": len(definitions),
                    "observed_at_least": len(rows),
                    "omitted_at_least": len(rows) - len(definitions),
                },
            )

    return (
        {
            "semantics": (
                "Configured Flow Designer lifecycle stages. These are not executed steps or evidence of record field "
                "writes."
            ),
            "sources": sources,
        },
        truncated_sources,
    )


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _build_v2_node(
    row: dict[str, Any],
    *,
    kind: str,
    action_type_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a canvas node for a persisted V2 action, logic, or subflow instance."""
    decoded, decode_error = _maybe_decode(
        row.get("subflow_inputs" if kind == "subflow" else "values")
    )
    action_type: ActionTypeRef | None = None
    if kind == "action":
        action_type_id = _v(row.get("action_type"))
        action_type = ActionTypeRef(
            sys_id=action_type_id, name=_d(row.get("action_type"))
        )
        if action_type_lookup and action_type_id in action_type_lookup:
            meta = action_type_lookup[action_type_id]
            action_type.internal_name = _v(meta.get("internal_name"))
            action_type.sys_scope = _d(meta.get("sys_scope"))
            action_type.category = _d(meta.get("category"))
    logic_definition = None
    if kind == "logic":
        logic_definition = LogicDefinitionRef(
            sys_id=_v(row.get("logic_definition")),
            name=_d(row.get("logic_definition")),
        )
    subflow = None
    wait = None
    if kind == "subflow":
        subflow = LogicDefinitionRef(
            sys_id=_v(row.get("subflow")), name=_d(row.get("subflow"))
        )
        raw_wait = _v(row.get("wait_for_completion"))
        wait = {"true": True, "false": False}.get(raw_wait)
        if decoded is None and decode_error is None:
            decode_error = (
                "Subflow input values are missing or use an unsupported encoding."
            )
        if not isinstance(decoded, list | dict) and decode_error is None:
            decode_error = "Subflow input values have an invalid structure."
    node = FlowNode(
        kind=kind,
        version="v2",
        sys_id=_v(row.get("sys_id")),
        ui_uuid=_v(row.get("ui_id", row.get("ui_uuid"))),
        parent_ui_id=_v(row.get("parent_ui_id", row.get("parent_ui_uuid"))),
        order=_v(row.get("order")),
        label=_d(row.get("subflow" if kind == "subflow" else "label")),
        name=_v(row.get("name")),
        comment=_v(row.get("comment")),
        values_decoded=decoded,
        children=[],
        action_type=action_type,
        logic_definition=logic_definition,
        subflow=subflow,
        wait_for_completion=wait,
        decode_error=decode_error,
    ).to_payload()
    try:
        reference = action_type or logic_definition or subflow
        semantics.table_step(
            kind="flowlogic" if kind == "logic" else kind,
            identity=node["sys_id"],
            ui_id=node["ui_uuid"],
            order=node["order"],
            parent=node["parent_ui_id"],
            definition=reference.sys_id if reference else "",
            inputs=decoded,
            settings={"wait_for_completion": wait} if kind == "subflow" else {},
        )
        if (
            kind == "subflow"
            and not {
                "sys_id",
                "ui_id",
                "parent_ui_id",
                "order",
                "subflow",
                "wait_for_completion",
                "subflow_inputs",
            }
            <= row.keys()
        ):
            raise CapabilityError("Incomplete persisted subflow fields.")
    except CapabilityError:
        node["incomplete_semantics"] = True
    return node


def _assemble_canvas(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a nested canvas tree by linking each node to its parent UI identity.

    Roots (``parent_ui_id == ""``) are returned at the top level; children
    are attached to their parent's ``children`` list in the order they
    appear in *nodes* (already sorted by ``order`` upstream).
    """
    by_uuid: dict[str, dict[str, Any]] = {}
    for node in nodes:
        uuid = node["ui_uuid"]
        if uuid:
            by_uuid[uuid] = node

    # A cycle must not disappear from the returned roots or recurse during serialization.
    cyclic: set[str] = set()
    for node in nodes:
        seen: set[str] = set()
        parent = node["ui_uuid"]
        while parent and parent in by_uuid:
            if parent in seen:
                cyclic.update(seen)
                break
            seen.add(parent)
            parent = by_uuid[parent]["parent_ui_id"]

    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent = node["parent_ui_id"]
        if parent and parent in by_uuid and node["ui_uuid"] not in cyclic:
            by_uuid[parent]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _v2_trigger_type(row: dict[str, Any]) -> str:
    return _v(row.get("trigger_type" if "trigger_type" in row else "type"))


def _v2_remote_id(row: dict[str, Any]) -> str:
    return _v(
        row.get("remote_sys_id" if "remote_sys_id" in row else "remote_trigger_id")
    )


def _uses_record_trigger(row: dict[str, Any]) -> bool:
    trigger_type = _v2_trigger_type(row)
    return trigger_type in {
        "record_create",
        "record_update",
        "record_create_or_update",
        "record_delete",
    } or (not trigger_type and bool(_v(row.get("remote_trigger_id"))))


def _trigger_schema_field(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Project catalog or stored parameter metadata without supplying absent defaults."""
    parameter = spec.get("parameter")
    parameter = parameter if isinstance(parameter, dict) else spec
    field = {
        "name": spec.get("name"),
        "label": spec.get("label", parameter.get("label")),
        "type": spec.get("type", parameter.get("type")),
        "required": spec.get("mandatory", parameter.get("mandatory")),
    }
    issues = []
    if any(
        not isinstance(field[key], str) or not field[key]
        for key in ("name", "label", "type")
    ):
        issues.append(
            "Trigger definition field identity or type metadata is unreadable."
        )
    if not isinstance(field["required"], bool):
        field["required"] = None
        issues.append("Trigger definition mandatory metadata is unreadable.")
    if "default_value" in spec or "defaultValue" in parameter:
        field["default"] = spec.get("default_value", parameter.get("defaultValue"))
        if field["default"] is None:
            issues.append("Trigger definition default metadata is unreadable.")
    for source, target in (
        ("reference", "reference_table"),
        ("reference_display", "reference_display"),
    ):
        if isinstance(parameter.get(source), str) and parameter[source]:
            field[target] = parameter[source]
    if field["type"] == "reference" and "reference_table" not in field:
        field["reference_table"] = None
        issues.append("Trigger definition reference table metadata is unreadable.")
    if field["type"] == "choice" or "choices" in parameter:
        choices = parameter.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or len(choices) > INTERNAL_QUERY_LIMIT
            or any(
                not isinstance(choice, dict) or not isinstance(choice.get("value"), str)
                for choice in choices
            )
        ):
            field["choices"] = None
            issues.append("Trigger definition choice metadata is unreadable.")
        else:
            field["choices"] = [
                {"value": choice["value"], "label": choice.get("label")}
                for choice in choices
            ]
            if any(not isinstance(choice.get("label"), str) for choice in choices):
                issues.append("Trigger definition choice labels are unreadable.")
        if "choiceOption" in parameter:
            field["choice_option"] = parameter["choiceOption"]
    if parameter.get("children"):
        issues.append("Nested trigger definition schemas are not resolved.")
    dependent_on = spec.get("dependent_on", parameter.get("dependent_on"))
    if dependent_on:
        field["dependent_on"] = dependent_on
        issues.append("Dependent trigger definition schemas are not resolved.")
    return field, issues


def _trigger_definition(
    definition: dict[str, Any], category: str, limit: int
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sys_id": definition.get("id"),
        "type": definition.get("type"),
        "label": definition.get("label"),
        "category": category,
        "source": "instance_trigger_catalog",
        "semantics": "Declared schemas only. Outputs are not execution results.",
    }
    warnings: list[str] = []
    for key in ("type", "label"):
        if not isinstance(result[key], str) or not result[key]:
            warnings.append("Trigger definition identity metadata is unreadable.")
    if not category:
        warnings.append("Trigger definition category metadata is unreadable.")
    for side in ("inputs", "outputs"):
        rows = definition.get(side)
        if not isinstance(rows, list):
            result[side] = None
            warnings.append(f"Trigger definition {side} metadata is unreadable.")
            continue
        fields = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                warnings.append(f"Trigger definition {side} contains malformed fields.")
                continue
            field, issues = _trigger_schema_field(row)
            fields.append(field)
            warnings.extend(issues)
            if side == "inputs" and "default" not in field:
                warnings.append(
                    "Trigger definition input default metadata is unreadable."
                )
        result[side] = fields
        names = [field["name"] for field in fields if isinstance(field["name"], str)]
        if len(set(names)) != len(names):
            warnings.append(f"Trigger definition {side} contains duplicate fields.")
        if len(rows) > limit:
            result.setdefault("truncation", {})[side] = {
                "returned": len(fields),
                "observed": len(rows),
                "limit": limit,
            }
            warnings.append(
                f"Trigger definition {side} exceeds the metadata field limit."
            )
    if definition.get("has_dynamic_outputs") is not False:
        warnings.append("Dynamic trigger output schemas are unknown.")
    result["warnings"] = list(dict.fromkeys(warnings))
    result["metadata_complete"] = not warnings
    return result


async def _load_trigger_definitions(
    client: ServiceNowClient,
    rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, dict[str, Any]]:
    """Read the observed instance catalog once and join only bounded, exact definition IDs."""
    ids = sorted(
        {
            _v(row.get("trigger_definition"))
            for row in rows
            if _v(row.get("trigger_definition"))
        }
    )
    if not ids:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    requested = set(ids[: min(limit, INTERNAL_QUERY_LIMIT)])
    for identity in ids:
        lookup[identity] = {
            "sys_id": identity,
            "inputs": None,
            "outputs": None,
            "metadata_complete": False,
            "warnings": ["Trigger definition metadata is missing or unreadable."],
        }
        if re.fullmatch(r"[0-9a-f]{32}", identity) is None:
            requested.discard(identity)
            lookup[identity]["warnings"] = ["trigger_definition identity is invalid."]
        elif identity not in requested:
            lookup[identity]["warnings"] = [
                "Trigger definition lookup exceeds the metadata identity limit."
            ]
    if not requested:
        return lookup
    try:
        categories = await client.studio_definitions("triggers")
    except Exception as exc:  # noqa: BLE001 — preserve safe fallback behavior for unexpected failures
        status = exc.status_code if isinstance(exc, ServiceNowMCPError) else None
        for identity in requested:
            lookup[identity]["warnings"] = [
                "Trigger definition catalog is unavailable. Check instance and metadata access.",
            ]
            lookup[identity]["read_status"] = (
                f"http_{status}" if status is not None else "unavailable"
            )
        return lookup
    try:
        if not isinstance(categories, list) or len(categories) > INTERNAL_QUERY_LIMIT:
            raise ValueError("Malformed trigger catalog")
        seen: set[str] = set()
        count = 0
        for category in categories:
            if not isinstance(category, dict) or not isinstance(
                category.get("triggers"), dict
            ):
                raise TypeError("Malformed trigger category")
            for definition in category["triggers"].values():
                count += 1
                if count > INTERNAL_QUERY_LIMIT:
                    raise ValueError(
                        "Trigger catalog exceeds the metadata definition limit"
                    )
                if not isinstance(definition, dict):
                    raise TypeError("Malformed trigger definition")
                identity = definition.get("id")
                if not isinstance(identity, str) or identity not in requested:
                    continue
                if identity in seen:
                    raise ValueError("Duplicate trigger definition identity")
                seen.add(identity)
                name = category.get("name")
                lookup[identity] = _trigger_definition(
                    definition, name if isinstance(name, str) else "", limit
                )
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        for identity in requested:
            lookup[identity].update(
                inputs=None,
                outputs=None,
                metadata_complete=False,
                read_status="malformed_or_capped",
                warnings=[
                    "Trigger definition catalog was malformed or exceeded its metadata limit."
                ],
            )
    return lookup


def _trigger_input_issues(
    bindings: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    *,
    complete: bool = True,
) -> list[str]:
    warnings: list[str] = []
    by_name = {
        binding.get("name"): binding
        for binding in bindings
        if isinstance(binding.get("name"), str)
    }
    if len(by_name) != len(bindings):
        warnings.append("Trigger inputs have missing or duplicate binding names.")
    names = {field["name"] for field in fields if isinstance(field.get("name"), str)}
    if complete and set(by_name) - names:
        warnings.append(
            "Trigger inputs contain fields absent from the declared definition."
        )
    for field in fields:
        name = field.get("name")
        if not isinstance(name, str):
            continue
        binding = by_name.get(name)
        if binding is None:
            if field.get("required") is True:
                warnings.append("Trigger inputs omit a required definition field.")
            continue
        value = binding.get("value")
        if value is None:
            warnings.append("Trigger input value is null or unreadable.")
        elif value == "":
            if field.get("required") is True:
                warnings.append("Trigger inputs contain an empty required value.")
            elif field.get("required") is not False:
                warnings.append("Empty trigger input has unknown mandatory metadata.")
        elif isinstance(field.get("choices"), list) and value not in [
            c["value"] for c in field["choices"]
        ]:
            warnings.append(
                "Trigger inputs contain a value outside the declared choice metadata."
            )
    return warnings


def _v2_trigger_entry(
    row: dict[str, Any],
    *,
    record_lookup: dict[str, dict[str, Any]],
    definition_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a trigger entry from a V2 ``sys_hub_trigger_instance_v2`` row."""
    remote_id = _v2_remote_id(row)
    trigger_type = _v2_trigger_type(row)
    record = record_lookup.get(remote_id, {}) if _uses_record_trigger(row) else {}
    warnings: list[str] = []
    definition = definition_lookup.get(_v(row.get("trigger_definition")))
    known_types = {
        "record_create",
        "record_update",
        "record_create_or_update",
        "record_delete",
        "service_catalog",
        "inbound_email",
        "scheduled",
        "daily",
        "weekly",
        "monthly",
        "repeat",
        "run_once",
    }
    if trigger_type not in known_types and (
        not definition or definition.get("type") != trigger_type
    ):
        warnings.append(
            "trigger_type is missing or unrecognized. Trigger family metadata is unknown."
        )
    modern = "trigger_type" in row or "trigger_inputs" in row
    if modern and not _v(row.get("trigger_definition")):
        warnings.append("trigger_definition metadata is missing or unreadable.")
    elif definition:
        warnings.extend(definition["warnings"])
        if definition.get("type") not in (None, trigger_type):
            warnings.append(
                "trigger_definition type differs from the stored trigger_type."
            )

    active: bool | None = None
    active_source: str | None = None
    active_row = row if "active" in row else record
    active_value = _v(active_row.get("active"))
    if active_value in {"true", "false"}:
        active = active_value == "true"
        active_source = (
            "trigger.active" if active_row is row else "record_trigger.active"
        )
    else:
        warnings.append(
            "Trigger active status is missing or invalid. The flow header does not establish trigger activation.",
        )

    table = _v(row.get("table")) or _v(record.get("table"))
    condition = _v(record.get("condition"))
    if _uses_record_trigger(row):
        if not record:
            warnings.append("Record trigger metadata is missing or unreadable.")
        if not table:
            warnings.append("Record trigger table metadata is missing or unreadable.")
        if "condition" not in record:
            warnings.append(
                "Record trigger condition metadata is missing or unreadable."
            )

    input_column = (
        "trigger_inputs"
        if "trigger_inputs" in row or (modern and "values" not in row)
        else "values"
    )
    decoded_fields: dict[str, Any] = {}
    errors: list[str] = []
    for column in (input_column, "trigger_outputs"):
        raw = _v(row.get(column))
        decoded: Any = None
        error = None
        if column == "values":
            decoded, error = _maybe_decode(row.get(column))
        elif (
            column not in row
            or row[column] is None
            or (isinstance(row[column], dict) and row[column].get("value") is None)
        ):
            if modern:
                warnings.append(f"{column} metadata is missing or unreadable.")
        elif raw:
            try:
                decoded = decode_values(raw)
            except ValueError as exc:
                error = str(exc)
        if error:
            errors.append(f"{column}: {error}")
            warnings.append(f"{column} could not be decoded.")
        if decoded is not None:
            bindings, _ = _contract_node_bindings(decoded)
            if not bindings and decoded not in ([], {}):
                warnings.append(f"{column} has an unsupported binding structure.")
            if isinstance(decoded, list) and len(bindings) != len(decoded):
                warnings.append(f"{column} contains malformed bindings.")
            for binding in bindings:
                if not isinstance(binding.get("name"), str) or not binding["name"]:
                    warnings.append(f"{column} has a malformed binding name.")
                if column == input_column and "value" not in binding:
                    warnings.append(f"{column} has an unreadable binding value.")
                parameter = binding.get("parameter")
                if (
                    column == input_column
                    and isinstance(parameter, dict)
                    and not (
                        definition
                        and definition.get("type") == trigger_type
                        and isinstance(definition.get("inputs"), list)
                    )
                ):
                    field, _ = _trigger_schema_field(
                        {**parameter, "name": binding.get("name")}
                    )
                    warnings.extend(_trigger_input_issues([binding], [field]))
        decoded_fields[column] = decoded
    inputs_readable = decoded_fields[input_column] is not None or (
        input_column in row
        and _v(row[input_column]) == ""
        and row[input_column] is not None
        and not (
            isinstance(row[input_column], dict)
            and row[input_column].get("value") is None
        )
    )
    if (
        inputs_readable
        and definition
        and definition.get("type") == trigger_type
        and isinstance(definition.get("inputs"), list)
    ):
        bindings, _ = _contract_node_bindings(decoded_fields[input_column])
        warnings.extend(
            _trigger_input_issues(
                bindings, definition["inputs"], complete=definition["metadata_complete"]
            )
        )
    warnings = list(dict.fromkeys(warnings))
    return V2Trigger(
        sys_id=_v(row.get("sys_id")),
        type=trigger_type,
        active=active,
        table=table,
        remote_trigger_id=remote_id,
        condition=condition,
        values_decoded=decoded_fields[input_column],
        outputs_decoded=decoded_fields["trigger_outputs"],
        decode_error=". ".join(errors) or None,
        active_source=active_source,
        metadata_complete=False
        if warnings
        else True
        if definition and definition["metadata_complete"]
        else None,
        warnings=warnings,
        definition=definition,
    ).to_payload()


def _v1_trigger_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Build a trigger entry from a V1 ``sys_hub_trigger_instance`` row."""
    return V1Trigger(
        sys_id=_v(row.get("sys_id")),
        type=_v(row.get("type")),
        active=_v(row.get("active")) == "true",
        table=_v(row.get("table")),
        condition=_v(row.get("condition")),
    ).to_payload()


async def _resolve_inspect_sys_id(
    client: ServiceNowClient,
    sys_id: str,
    name: str,
) -> str:
    """Resolve one flow identity and reject missing or ambiguous matches."""
    if sys_id:
        return sys_id
    matches = await client.find_flows_by_name(name)
    if len(matches) == 0:
        _error(f"No flow found with name {name!r}.")
    if len(matches) > 1:
        _error(
            f"Name {name!r} is ambiguous ({len(matches)} flows match); pass sys_id instead.",
        )
    resolved = _v(matches[0].get("sys_id"))
    if not resolved:
        _error(f"Resolved flow for {name!r} has no sys_id.")
    return resolved


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
    subflows_v2: list[dict[str, Any]],
) -> list[str]:
    """Assemble the ``inspect`` warning list (drift, V1/V2 coexistence, spokes)."""
    warnings: list[str] = []
    if drift:
        warnings.append(
            f"snapshot drift: master_snapshot={master!r} differs from latest_snapshot={latest!r}; "
            "the runtime engine and the latest design may not agree.",
        )
    if (actions_v1 or logic_v1 or triggers_v1) and (
        actions_v2 or logic_v2 or triggers_v2 or subflows_v2
    ):
        warnings.append(
            "V1 and V2 Flow Designer artifacts coexist on this flow; canvas omits V1 nodes."
        )
    if actions_v1:
        warnings.append(
            f"{len(actions_v1)} V1 action instance(s) present; "
            "their configured bindings are not represented as contract steps.",
        )
    if logic_v1:
        warnings.append(
            f"{len(logic_v1)} V1 logic instance(s) present; their semantics are not reflected in the canvas tree.",
        )
    spoke_actions = [
        meta
        for meta in action_type_lookup.values()
        if "spoke" in _v(meta.get("category")).lower()
        or "spoke" in _v(meta.get("sys_scope")).lower()
    ]
    if spoke_actions:
        warnings.append(
            f"{len(spoke_actions)} spoke action type(s) referenced; their decoded values may depend on scoped types.",
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


def _action_definition_field(
    row: dict[str, Any], *, has_default: bool
) -> dict[str, Any]:
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
            indexed.setdefault(action_type_id, []).append(
                _action_definition_field(row, has_default=has_default)
            )
    return indexed


def _contract_binding(value: dict[str, Any]) -> dict[str, Any]:
    """Project one decoded Flow Designer input or output assignment.

    ``data_pills`` contains only literal references present in the stored
    binding. It intentionally does not resolve or interpret them.
    """
    parameter = value.get("parameter")
    parameter_data = parameter if isinstance(parameter, dict) else {}
    raw_value = value.get("value", "")
    data_pills = (
        _DATA_PILL_PATTERN.findall(raw_value) if isinstance(raw_value, str) else []
    )
    return ContractBinding(
        name=str(value.get("name", "")),
        label=str(parameter_data.get("label", "") or value.get("name", "")),
        type=str(parameter_data.get("type", "")),
        required=bool(parameter_data.get("mandatory", False)),
        value=raw_value,
        data_pills=data_pills or None,
    ).to_payload()


def _contract_node_bindings(
    decoded: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
            elif node["kind"] == "subflow":
                step["subflow"] = node["subflow"]
                step["version"] = node["version"]
                step["sys_id"] = node["sys_id"]
                step["ui_uuid"] = node["ui_uuid"]
                step["parent_ui_id"] = node["parent_ui_id"]
                step["wait_for_completion"] = node.get("wait_for_completion")
                step["inputs"] = [_contract_binding(value) for value in inputs]
            else:
                definition = node["logic_definition"]
                step["logic"] = {"name": definition["name"]}
                step["conditions"] = [_contract_binding(value) for value in inputs]
            if assignments:
                step["output_assignments"] = [
                    _contract_binding(value) for value in assignments
                ]
            if "decode_error" in node:
                step["decode_error"] = node["decode_error"]
            if node.get("incomplete_semantics"):
                step["incomplete_semantics"] = True
            steps.append(step)
            visit(node["children"], step_id)

    visit(canvas)
    return steps


def _contract_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    """Project a trigger into contract fields without raw decoded metadata."""
    configuration, _ = _contract_node_bindings(trigger.get("values_decoded"))
    outputs, _ = _contract_node_bindings(trigger.get("outputs_decoded"))
    result = ContractTrigger(
        version=trigger["version"],
        type=trigger["type"],
        active=trigger["active"],
        table=trigger["table"],
        condition=trigger["condition"],
        configuration=[_contract_binding(value) for value in configuration] or None,
        output_configuration=[_contract_binding(value) for value in outputs] or None,
        decode_error=trigger.get("decode_error"),
    )
    payload = result.to_payload()
    for key in ("active_source", "metadata_complete", "warnings", "definition"):
        if key in trigger:
            payload[key] = trigger[key]
    for binding, output in zip(
        outputs, payload.get("output_configuration", []), strict=True
    ):
        parameter = binding.get("parameter")
        if (
            isinstance(parameter, dict)
            and isinstance(parameter.get("reference"), str)
            and parameter["reference"]
        ):
            output["reference_table"] = parameter["reference"]
    return payload


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
        "stages": payload["stages"],
        "triggers": [_contract_trigger(trigger) for trigger in payload["triggers"]],
        "steps": _contract_steps(
            payload["canvas"],
            action_input_definitions,
            action_output_definitions,
            schema_limitations,
        ),
        "warnings": [*payload["warnings"], *schema_limitations],
    }


def _parse_sections(
    sections: list[str] | Literal["*"], *, is_contract: bool
) -> tuple[list[str], str | None]:
    available = _CONTRACT_SECTIONS if is_contract else _INSPECT_SECTIONS
    if not sections:
        return list(_DEFAULT_FLOW_SECTIONS), None
    if sections == "*":
        return list(available), None

    requested = list(dict.fromkeys(sections))
    unknown = [section for section in requested if section not in available]
    if unknown:
        return (
            [],
            f"Unknown section(s): {', '.join(unknown)}. Available: {', '.join(available)}, *.",
        )
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


def _warning_continuation(
    *, section_limit: int, max_row_limit: int, flow_sys_id: str
) -> str:
    if section_limit < max_row_limit:
        return f"Re-run with section_limit greater than {section_limit}."
    return (
        f"The configured MAX_ROW_LIMIT of {max_row_limit} has been reached; no further continuation is available "
        "through flow. To complete warning analysis, use query with pagination and these explicit projections: "
        f"sys_hub_action_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id,action_type); "
        f"sys_hub_action_instance (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_flow_logic_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_flow_logic (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_sub_flow_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_trigger_instance_v2 (encoded_query=flow={flow_sys_id}, fields=sys_id); "
        f"sys_hub_trigger_instance (encoded_query=flow={flow_sys_id}, fields=sys_id). Then collect every action_type "
        "sys_id from the V2 actions and query sys_hub_action_type_base "
        "(encoded_query=sys_idIN<action_type_sys_ids>, fields=sys_id,category,sys_scope) for spoke detection. "
        "Use limit and offset to continue each read. Query safety and row limits still apply."
    )


def _stage_continuation(
    *, section_limit: int, max_row_limit: int, flow_sys_id: str
) -> str:
    if section_limit < max_row_limit:
        return f"Re-run with section_limit greater than {section_limit}."
    return (
        f"The configured MAX_ROW_LIMIT of {max_row_limit} has been reached; no further continuation is available "
        "through flow. Use query with an explicit fields projection and pagination on sys_hub_flow_stage "
        f"(encoded_query=flow={flow_sys_id}^ORDERBYorder). Query safety and row limits still apply."
    )


def _v1_variable_value_continuation(
    *, section_limit: int, max_row_limit: int, flow_sys_id: str
) -> str:
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
        if requested_limits.get(name, 0) > 0
        and len(datasets.get(name, [])) >= requested_limits[name]
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


def _structural_summary(
    datasets: dict[str, list[dict[str, Any]]], section_limit: int
) -> dict[str, Any]:
    counts = {
        name: min(len(datasets.get(name, [])), section_limit)
        for name in sorted(_STRUCTURAL_DATASETS)
    }
    total_nodes = (
        counts["actions_v2"]
        + counts["actions_v1"]
        + counts["logic_v2"]
        + counts["logic_v1"]
        + counts["subflows_v2"]
    )
    return {
        "counts": counts,
        "count_semantics": "exact unless truncated=true; truncated dataset counts are lower bounds",
        "node_count": total_nodes,
        "trigger_count": counts["triggers_v2"] + counts["triggers_v1"],
        "versions": [
            version
            for version in ("v1", "v2")
            if any(
                counts.get(f"{kind}_{version}", 0)
                for kind in ("actions", "logic", "triggers", "subflows")
            )
        ],
        "truncated": any(
            len(datasets.get(name, [])) > section_limit for name in _STRUCTURAL_DATASETS
        ),
        "per_dataset_limit": section_limit,
    }


async def _fetch_flow_datasets(
    client: ServiceNowClient,
    resolved_sys_id: str,
    required: set[str],
    section_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], list[str]]:
    fetchers = {
        "inputs": client.list_flow_inputs,
        "outputs": client.list_flow_outputs,
        "variables": client.list_flow_variables,
        "actions_v2": client.list_action_instances_v2,
        "actions_v1": client.list_action_instances_v1,
        "subflows_v2": client.list_subflow_instances_v2,
        "logic_v2": client.list_logic_instances_v2,
        "logic_v1": client.list_logic_instances_v1,
        "triggers_v2": client.list_trigger_instances_v2,
        "triggers_v1": client.list_trigger_instances_v1,
    }
    names = sorted(required)
    direct_names = [name for name in names if name != "v1_variable_values"]
    rows = await asyncio.gather(
        *(fetchers[name](resolved_sys_id, section_limit + 1) for name in direct_names),
        return_exceptions=True,
    )
    datasets: dict[str, list[dict[str, Any]]] = {}
    incomplete: list[str] = []
    for name, result in zip(direct_names, rows, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if (
            isinstance(result, BaseException)
            or not isinstance(result, list)
            or any(not isinstance(row, dict) for row in result)
        ):
            incomplete.append(name)
            datasets[name] = []
        else:
            datasets[name] = result
    requested_limits = dict.fromkeys(direct_names, section_limit + 1)
    if "v1_variable_values" in required:
        v1_action_ids = sorted(
            {
                _v(action.get("sys_id"))
                for action in datasets.get("actions_v1", [])
                if _v(action.get("sys_id"))
            },
        )
        datasets["v1_variable_values"] = (
            await client.list_v1_variable_values(v1_action_ids) if v1_action_ids else []
        )
        requested_limits["v1_variable_values"] = (
            min(len(v1_action_ids) * 10, 5000) if v1_action_ids else 0
        )
    return datasets, requested_limits, incomplete


@dataclass(frozen=True)
class InspectionSnapshot:
    """Loaded rows, dependency completeness, and bounded section selections."""

    resolved_sys_id: str
    header: dict[str, Any]
    master: str
    latest: str
    drift: bool
    datasets: dict[str, list[dict[str, Any]]]
    requested_limits: dict[str, int]
    truncation: dict[str, dict[str, Any]]
    stages: dict[str, Any]
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    node_actions_v2: list[dict[str, Any]]
    node_logic_v2: list[dict[str, Any]]
    node_subflows_v2: list[dict[str, Any]]
    returned_triggers_v2: list[dict[str, Any]]
    returned_triggers_v1: list[dict[str, Any]]
    record_triggers: list[dict[str, Any]]
    trigger_definitions: dict[str, dict[str, Any]]
    action_type_rows: list[dict[str, Any]]
    action_input_rows: list[dict[str, Any]]
    action_output_rows: list[dict[str, Any]]
    schema_limitations: list[str]
    v1_variable_values: list[dict[str, Any]]
    effective_limit: int
    selected_sections: list[str]
    is_contract: bool


async def _load_inspection(
    *,
    sys_id: str,
    name: str,
    settings: Settings,
    client_factory: ServiceNowClientProvider,
    sections: list[str] | Literal["*"],
    section_limit: int,
    is_contract: bool = False,
) -> InspectionSnapshot:
    if sys_id and name:
        return _error("Provide exactly one of sys_id or name (not both).")
    if not sys_id and not name:
        return _error("Either sys_id or name is required.")
    if sys_id:
        validate_sys_id(sys_id)
    selected_sections, section_error = _parse_sections(
        sections, is_contract=is_contract
    )
    if section_error is not None:
        return _error(section_error)
    effective_limit = max(
        1,
        min(
            section_limit if section_limit > 0 else _DEFAULT_SECTION_LIMIT,
            settings.max_row_limit,
        ),
    )
    required = _required_datasets(selected_sections)

    async with client_factory() as client:
        resolved_sys_id = await _resolve_inspect_sys_id(client, sys_id, name)

        header = await client.get_flow_by_sys_id(resolved_sys_id)
        if header is None:
            return _error(f"Flow {resolved_sys_id} not found.")

        master = _v(header.get("master_snapshot"))
        latest = _v(header.get("latest_snapshot"))
        drift = bool(master and latest and master != latest)
        datasets, requested_limits, incomplete = await _fetch_flow_datasets(
            client,
            resolved_sys_id,
            required,
            effective_limit,
        )
        truncation: dict[str, dict[str, Any]] = {}
        for section in selected_sections:
            missing = sorted(set(incomplete) & _SECTION_DEPENDENCIES[section])
            if missing:
                truncation[section] = {
                    "incomplete_datasets": missing,
                    "limitation": "The read failed or returned malformed rows. Counts for these datasets are unknown.",
                    "continuation": (
                        "Retry the read with the required table and field access. Do not infer absent steps from "
                        "this result."
                    ),
                }
        stages: dict[str, Any] = {"semantics": "", "sources": []}
        if "stages" in selected_sections:
            stages, truncated_stage_sources = await _flow_stages(
                client,
                root_flow_sys_id=resolved_sys_id,
                published_snapshot_sys_id=master,
                section_limit=effective_limit,
            )
            requested_limits["stages_per_source"] = effective_limit + 1
            if truncated_stage_sources:
                truncation["stages"] = {
                    "sources": [
                        {
                            **source,
                            "continuation": _stage_continuation(
                                section_limit=effective_limit,
                                max_row_limit=settings.max_row_limit,
                                flow_sys_id=source["flow_sys_id"],
                            ),
                        }
                        for source in truncated_stage_sources
                    ],
                }
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
        logic_v2 = datasets.get("logic_v2", [])
        subflows_v2 = datasets.get("subflows_v2", [])
        triggers_v2 = datasets.get("triggers_v2", [])
        triggers_v1 = datasets.get("triggers_v1", [])
        node_actions_v2 = actions_v2
        node_logic_v2 = logic_v2
        node_subflows_v2 = subflows_v2
        returned_triggers_v2 = triggers_v2
        returned_triggers_v1 = triggers_v1
        if "structural_summary" in selected_sections:
            truncated_datasets = _saturated_datasets(
                datasets, requested_limits, _STRUCTURAL_DATASETS
            )
            if truncated_datasets:
                truncation.setdefault("structural_summary", {}).update(
                    {
                        "datasets": truncated_datasets,
                        "returned_per_dataset": effective_limit,
                        "continuation": _continuation(
                            section_limit=effective_limit,
                            max_row_limit=settings.max_row_limit,
                            flow_sys_id=resolved_sys_id,
                            source_datasets=tuple(truncated_datasets),
                        ),
                    },
                )

        if "warnings" in selected_sections:
            saturated_dependencies = _saturated_datasets(
                datasets, requested_limits, _STRUCTURAL_DATASETS
            )
            if saturated_dependencies:
                truncation.setdefault("warnings", {}).update(
                    {
                        "datasets": saturated_dependencies,
                        "limitation": "Warning analysis covers only the probed rows for the saturated dependencies.",
                        "continuation": _warning_continuation(
                            section_limit=effective_limit,
                            max_row_limit=settings.max_row_limit,
                            flow_sys_id=resolved_sys_id,
                        ),
                    },
                )

        selected_node_section = "steps" if is_contract else "canvas"
        if selected_node_section in selected_sections:
            combined_nodes = sorted(
                [("action", row) for row in actions_v2]
                + [("logic", row) for row in logic_v2]
                + [("subflow", row) for row in subflows_v2],
                key=lambda item: _safe_int(_v(item[1].get("order"))),
            )
            bounded_nodes = combined_nodes[:effective_limit]
            if len(combined_nodes) > effective_limit:
                truncation.setdefault(selected_node_section, {}).update(
                    {
                        "returned": len(bounded_nodes),
                        "observed_at_least": len(combined_nodes),
                        "omitted_at_least": len(combined_nodes) - len(bounded_nodes),
                        "continuation": _continuation(
                            section_limit=effective_limit,
                            max_row_limit=settings.max_row_limit,
                            flow_sys_id=resolved_sys_id,
                            source_datasets=("actions_v2", "logic_v2", "subflows_v2"),
                        ),
                    },
                )
            node_actions_v2 = [row for kind, row in bounded_nodes if kind == "action"]
            node_logic_v2 = [row for kind, row in bounded_nodes if kind == "logic"]
            node_subflows_v2 = [row for kind, row in bounded_nodes if kind == "subflow"]

        if "triggers" in selected_sections:
            all_triggers = [("v2", row) for row in triggers_v2] + [
                ("v1", row) for row in triggers_v1
            ]
            bounded_triggers = all_triggers[:effective_limit]
            if len(all_triggers) > effective_limit:
                truncation.setdefault("triggers", {}).update(
                    {
                        "returned": len(bounded_triggers),
                        "observed_at_least": len(all_triggers),
                        "omitted_at_least": len(all_triggers) - len(bounded_triggers),
                        "continuation": _continuation(
                            section_limit=effective_limit,
                            max_row_limit=settings.max_row_limit,
                            flow_sys_id=resolved_sys_id,
                            source_datasets=("triggers_v2", "triggers_v1"),
                        ),
                    },
                )
            returned_triggers_v2 = [
                row for version, row in bounded_triggers if version == "v2"
            ]
            returned_triggers_v1 = [
                row for version, row in bounded_triggers if version == "v1"
            ]

        trigger_definitions = (
            await _load_trigger_definitions(
                client, returned_triggers_v2, effective_limit
            )
            if "triggers" in selected_sections
            else {}
        )
        remote_ids = sorted(
            {
                _v2_remote_id(t)
                for t in returned_triggers_v2
                if _uses_record_trigger(t) and _v2_remote_id(t)
            },
        )
        record_triggers = []
        if "triggers" in selected_sections and remote_ids:
            try:
                record_triggers = await client.list_record_triggers(remote_ids)
                if not isinstance(record_triggers, list) or any(
                    not isinstance(row, dict) for row in record_triggers
                ):
                    raise ValueError("Malformed record trigger metadata")
            except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
                record_triggers = []
                truncation.setdefault("triggers", {}).setdefault(
                    "incomplete_datasets", []
                ).append(
                    "sys_flow_record_trigger",
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

        node_action_type_ids = {
            _v(a.get("action_type"))
            for a in node_actions_v2
            if _v(a.get("action_type"))
        }
        warning_action_type_ids = {
            _v(a.get("action_type")) for a in actions_v2 if _v(a.get("action_type"))
        }
        required_action_type_ids: set[str] = set()
        if selected_node_section in selected_sections:
            required_action_type_ids.update(node_action_type_ids)
        if "warnings" in selected_sections:
            required_action_type_ids.update(warning_action_type_ids)
        action_type_ids = sorted(required_action_type_ids)
        action_type_rows = (
            await client.get_action_type_definitions(action_type_ids)
            if action_type_ids
            else []
        )
        returned_action_type_ids = {
            _v(row.get("sys_id")) for row in action_type_rows if _v(row.get("sys_id"))
        }
        section_action_type_ids = {
            selected_node_section: node_action_type_ids,
            "warnings": warning_action_type_ids,
        }
        for section, section_type_ids in section_action_type_ids.items():
            missing_action_type_ids = sorted(
                section_type_ids - returned_action_type_ids
            )
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
            section_truncation["missing_action_type_metadata"] = len(
                missing_action_type_ids
            )
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
                action_input_rows = await client.list_action_input_definitions(
                    node_action_type_id_list
                )
            except Exception as exc:  # noqa: BLE001 — preserve safe fallback behavior for unexpected failures
                schema_limitations.append(
                    f"Action input definitions are unavailable: {exc}"
                )
            try:
                action_output_rows = await client.list_action_output_definitions(
                    node_action_type_id_list
                )
            except Exception as exc:  # noqa: BLE001 — preserve safe fallback behavior for unexpected failures
                schema_limitations.append(
                    f"Action output definitions are unavailable: {exc}"
                )

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
            datasets,
            requested_limits,
            frozenset({"actions_v1"}),
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

    return InspectionSnapshot(
        resolved_sys_id=resolved_sys_id,
        header=header,
        master=master,
        latest=latest,
        drift=drift,
        datasets=datasets,
        requested_limits=requested_limits,
        truncation=truncation,
        stages=stages,
        inputs=inputs,
        outputs=outputs,
        variables=variables,
        node_actions_v2=node_actions_v2,
        node_logic_v2=node_logic_v2,
        node_subflows_v2=node_subflows_v2,
        returned_triggers_v2=returned_triggers_v2,
        returned_triggers_v1=returned_triggers_v1,
        record_triggers=record_triggers,
        trigger_definitions=trigger_definitions,
        action_type_rows=action_type_rows,
        action_input_rows=action_input_rows,
        action_output_rows=action_output_rows,
        schema_limitations=schema_limitations,
        v1_variable_values=v1_variable_values,
        effective_limit=effective_limit,
        selected_sections=selected_sections,
        is_contract=is_contract,
    )


def assemble_inspection(
    snapshot: InspectionSnapshot, *, max_row_limit: int
) -> CapabilityResult:
    """Project a loaded Flow snapshot without platform I/O."""
    resolved_sys_id = snapshot.resolved_sys_id
    header = snapshot.header
    master = snapshot.master
    latest = snapshot.latest
    drift = snapshot.drift
    datasets = snapshot.datasets
    requested_limits = snapshot.requested_limits
    truncation = snapshot.truncation
    stages = snapshot.stages
    inputs = snapshot.inputs
    outputs = snapshot.outputs
    variables = snapshot.variables
    node_actions_v2 = snapshot.node_actions_v2
    node_logic_v2 = snapshot.node_logic_v2
    returned_triggers_v2 = snapshot.returned_triggers_v2
    returned_triggers_v1 = snapshot.returned_triggers_v1
    record_triggers = snapshot.record_triggers
    action_type_rows = snapshot.action_type_rows
    action_input_rows = snapshot.action_input_rows
    action_output_rows = snapshot.action_output_rows
    schema_limitations = snapshot.schema_limitations
    v1_variable_values = snapshot.v1_variable_values
    effective_limit = snapshot.effective_limit
    selected_sections = snapshot.selected_sections
    is_contract = snapshot.is_contract
    selected_node_section = "steps" if is_contract else "canvas"
    actions_v1 = datasets.get("actions_v1", [])
    actions_v2 = datasets.get("actions_v2", [])
    logic_v1 = datasets.get("logic_v1", [])
    logic_v2 = datasets.get("logic_v2", [])
    triggers_v1 = datasets.get("triggers_v1", [])
    triggers_v2 = datasets.get("triggers_v2", [])

    # Build lookups.
    action_type_lookup = _index_by_sys_id(action_type_rows)
    record_lookup = _index_by_sys_id(record_triggers)

    # Build V2 canvas nodes in global persisted order.
    canvas: list[dict[str, Any]] = []
    if selected_node_section in selected_sections:
        nodes: list[dict[str, Any]] = [
            _build_v2_node(row, kind="action", action_type_lookup=action_type_lookup)
            for row in node_actions_v2
        ]
        nodes.extend(_build_v2_node(row, kind="logic") for row in node_logic_v2)
        nodes.extend(
            _build_v2_node(row, kind="subflow") for row in snapshot.node_subflows_v2
        )
        nodes.sort(key=lambda n: _safe_int(n["order"]))
        issues = _canvas_issues(nodes)
        if issues:
            truncation.setdefault(selected_node_section, {}).update(
                {"incomplete_semantics": issues}
            )
        canvas = _assemble_canvas(nodes)

    # Triggers - V2 first (stitched), then V1.
    triggers: list[dict[str, Any]] = []
    if "triggers" in selected_sections:
        triggers = [
            _v2_trigger_entry(
                row,
                record_lookup=record_lookup,
                definition_lookup=snapshot.trigger_definitions,
            )
            for row in returned_triggers_v2
        ]
        triggers.extend(_v1_trigger_entry(row) for row in returned_triggers_v1)
        trigger_issues = list(
            dict.fromkeys(
                issue for trigger in triggers for issue in trigger.get("warnings", [])
            )
        )
        if trigger_issues:
            trigger_truncation = truncation.setdefault("triggers", {})
            trigger_truncation["incomplete_metadata"] = trigger_issues
            trigger_truncation.setdefault(
                "continuation",
                "Read the trigger source fields and definition metadata with the required field access. Unknown "
                "metadata does not establish inactivity.",
            )

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
        subflows_v2=datasets.get("subflows_v2", []),
    )
    warnings.extend(
        dict.fromkeys(
            issue for trigger in triggers for issue in trigger.get("warnings", [])
        )
    )
    if "triggers" in truncation:
        warnings.append(
            "Trigger metadata is incomplete. Check truncation.triggers before interpreting unknown fields."
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
        "stages": stages,
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
                        max_row_limit=max_row_limit,
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
    action_input_definitions = _index_action_definition_fields(
        action_input_rows, has_default=True
    )
    action_output_definitions = _index_action_definition_fields(
        action_output_rows, has_default=False
    )
    if schema_limitations and "steps" in selected_sections:
        truncation.setdefault("steps", {})["incomplete_definitions"] = True
    assembled = (
        _build_flow_contract(
            full_payload,
            action_input_definitions,
            action_output_definitions,
            schema_limitations,
        )
        if is_contract
        else full_payload
    )
    if "v1_variable_values" in selected_sections and requested_limits.get(
        "v1_variable_values"
    ) == len(
        v1_variable_values,
    ):
        truncation.setdefault(
            "v1_variable_values",
            {
                "returned": len(v1_variable_values),
                "possible_more": True,
                "continuation": _v1_variable_value_continuation(
                    section_limit=effective_limit,
                    max_row_limit=max_row_limit,
                    flow_sys_id=resolved_sys_id,
                ),
            },
        )
    assembled["structural_summary"] = _structural_summary(datasets, effective_limit)
    assembled["structural_summary"]["complete"] = "structural_summary" not in truncation
    metadata_sections = [
        section
        for section in selected_sections
        if section not in {"flow", "published_state", "structural_summary", "warnings"}
    ]
    assembled["structural_summary"].update(
        {
            "completeness_scope": "structural_counts_only",
            "metadata_sections": metadata_sections,
            "metadata_complete": False
            if any(section in truncation for section in metadata_sections)
            else None,
        },
    )
    if truncation.get("structural_summary", {}).get("incomplete_datasets"):
        assembled["structural_summary"]["count_semantics"] = (
            "Counts exclude unreadable datasets and are incomplete."
        )
        missing = truncation["structural_summary"]["incomplete_datasets"]
        for dataset in missing:
            assembled["structural_summary"]["counts"][dataset] = None
        if any(
            dataset.startswith(("actions", "logic", "subflows")) for dataset in missing
        ):
            assembled["structural_summary"]["node_count"] = None
        if any(dataset.startswith("triggers") for dataset in missing):
            assembled["structural_summary"]["trigger_count"] = None
    data = {section: assembled[section] for section in selected_sections}
    return operation_result(data=data, truncation=truncation)


def _canvas_issues(nodes: list[dict[str, Any]]) -> list[str]:
    """Report partial fields and malformed topology without discarding returned nodes."""
    issues: list[str] = []
    by_ui = {node["ui_uuid"]: node for node in nodes if node["ui_uuid"]}
    if len(by_ui) != len(nodes):
        issues.append(
            "Missing or duplicate UI identities prevent complete topology readback."
        )
    identities = [node["sys_id"] for node in nodes]
    if any(not value for value in identities) or len(set(identities)) != len(
        identities
    ):
        issues.append("Missing or duplicate persisted step identities.")
    orders = [node["order"] for node in nodes]
    if any(_safe_int(value) == 10**9 for value in orders) or len(set(orders)) != len(
        orders
    ):
        issues.append("Invalid or duplicate persisted step orders.")
    for node in nodes:
        if node.get("incomplete_semantics"):
            issues.append(
                "Some step fields or input bindings are unreadable or malformed."
            )
        if node.get("decode_error"):
            issues.append("Some persisted input values could not be decoded.")
        if node["kind"] == "subflow" and (
            not node["subflow"]["sys_id"] or "wait_for_completion" not in node
        ):
            issues.append("A subflow definition or wait setting is unreadable.")
        seen = {node["ui_uuid"]}
        parent = node["parent_ui_id"]
        while parent:
            if (
                parent in seen
                or parent not in by_ui
                or by_ui[parent]["kind"] != "logic"
            ):
                issues.append(
                    "A parent is missing, cyclic, or not flow logic. Returned roots do not establish topology.",
                )
                break
            seen.add(parent)
            parent = by_ui[parent]["parent_ui_id"]
    return list(dict.fromkeys(issues))


async def _action_inspect(
    *,
    sys_id: str,
    name: str,
    settings: Settings,
    client_factory: ServiceNowClientProvider,
    sections: list[str] | Literal["*"],
    section_limit: int,
    is_contract: bool = False,
) -> CapabilityResult:
    snapshot = await _load_inspection(
        sys_id=sys_id,
        name=name,
        settings=settings,
        client_factory=client_factory,
        sections=sections,
        section_limit=section_limit,
        is_contract=is_contract,
    )
    return assemble_inspection(snapshot, max_row_limit=settings.max_row_limit)


def _safe_int(value: str) -> int:
    """Best-effort int conversion for sorting; non-numeric sorts last."""
    try:
        return int(value)
    except TypeError, ValueError:
        return 10**9


def _flatten_record(row: dict[str, Any]) -> dict[str, Any]:
    """Collapse a display_value=all row to its raw ``value`` per field."""
    return {key: _v(val) for key, val in row.items()}


# ---------------------------------------------------------------------------
# find_by_table
# ---------------------------------------------------------------------------


def _index_flow_headers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index flow headers by canonical identity and current snapshot aliases."""
    indexed = _index_by_sys_id(rows)
    for row in rows:
        for field in ("master_snapshot", "latest_snapshot"):
            snapshot_id = _v(row.get(field))
            if snapshot_id:
                indexed.setdefault(snapshot_id, row)
    return indexed


async def _action_find_by_table(
    *,
    table: str,
    client_factory: ServiceNowClientProvider,
) -> CapabilityResult:
    if not table:
        return _error("'table' is required for action='find_by_table'.")
    validate_identifier(table)

    async with client_factory() as client:
        record_triggers = await client.find_record_triggers_by_table(table)
        remote_ids = sorted(
            {_v(r.get("sys_id")) for r in record_triggers if _v(r.get("sys_id"))}
        )
        v2_triggers = (
            await client.list_v2_triggers_by_remote_ids(remote_ids)
            if remote_ids
            else []
        )
        v1_triggers = await client.list_v1_triggers_by_table(table)

        flow_versions = _collect_flow_versions(v2_triggers, v1_triggers)
        flow_sys_ids = sorted(flow_versions.keys())
        flows_meta = await client.get_flows_bulk(flow_sys_ids) if flow_sys_ids else []

    meta_by_id = _index_flow_headers(flows_meta)
    canonical_versions: dict[str, set[str]] = {}
    canonical_meta: dict[str, dict[str, Any]] = {}
    unresolved_ids: list[str] = []
    for reference_id, versions in flow_versions.items():
        meta = meta_by_id.get(reference_id, {})
        canonical_id = _v(meta.get("sys_id")) or reference_id
        canonical_versions.setdefault(canonical_id, set()).update(versions)
        canonical_meta[canonical_id] = meta
        if not meta:
            unresolved_ids.append(reference_id)
    flow_versions = canonical_versions
    v1_unique = {fid for fid, versions in flow_versions.items() if "v1" in versions}
    v2_unique = {fid for fid, versions in flow_versions.items() if "v2" in versions}
    flows = [
        _find_by_table_entry(fid, versions, canonical_meta[fid])
        for fid, versions in flow_versions.items()
    ]
    warnings = []
    if unresolved_ids:
        warnings.append(
            "Some trigger flow references could not be resolved to current flow headers. "
            "They may refer to older snapshots or inaccessible records; active state is unknown.",
        )
    if any(
        len(rows) >= INTERNAL_QUERY_LIMIT
        for rows in (record_triggers, v1_triggers, v2_triggers)
    ):
        warnings.append(
            "Trigger discovery reached its internal row limit; results may be incomplete. "
            "Use query with explicit fields and offset pagination on the trigger tables.",
        )

    payload: dict[str, Any] = {
        "table": table,
        "v1_count": len(v1_unique),
        "v2_count": len(v2_unique),
        "total": len(flow_versions),
        "flows": flows,
        "unresolved_flow_ids": unresolved_ids,
    }
    return operation_result(data=payload, warnings=warnings or None)


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
        "active": (
            _v(meta.get("active")) == "true" if meta and "active" in meta else None
        ),
        "metadata_resolved": bool(meta),
        "sys_scope": _d(meta.get("sys_scope")) if meta else "",
        "version": "+".join(sorted(versions)),
    }


# ---------------------------------------------------------------------------
# decode_values
# ---------------------------------------------------------------------------


def _action_decode_values(*, value: str) -> CapabilityResult:
    if not value:
        return _error("'value' is required for action='decode_values'.")
    try:
        decoded = decode_values(value)
    except ValueError as exc:
        return _error(str(exc))
    return operation_result(
        data={"decoded": decoded, "encoding": "gzip+base64+json"},
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
    client_factory: ServiceNowClientProvider,
) -> CapabilityResult:
    if active and active not in {"true", "false"}:
        return _error(
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

        flow_ids = {
            _v(row.get("flow")) for row in v2_rows + v1_rows if _v(row.get("flow"))
        }
        flows_meta = await client.get_flows_bulk(sorted(flow_ids)) if flow_ids else []

    meta_by_id = _index_flow_headers(flows_meta)

    triggers: list[dict[str, Any]] = [
        _trigger_with_flow(row, "v2", meta_by_id) for row in v2_rows
    ]
    triggers.extend(_trigger_with_flow(row, "v1", meta_by_id) for row in v1_rows)

    payload: dict[str, Any] = {
        "is_complete": not filtered.get("truncation"),
        "v1_count": len(v1_rows),
        "v2_count": len(v2_rows),
        "triggers": triggers,
    }
    return operation_result(
        data=payload,
        pagination={"limit": effective_limit, "offset": 0, "total": len(triggers)},
        warnings=(
            [
                (
                    "Trigger search is incomplete. Counts describe returned matches only; zero does not prove absence. "
                    "Follow truncation for source queries and continuation."
                ),
            ]
            if filtered.get("truncation")
            else None
        ),
        truncation=filtered.get("truncation"),
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
        "type": _v(row.get("trigger_type" if version == "v1" else "type")),
        "active": _v(row.get("active")) == "true" if "active" in row else None,
        "table": _v(row.get("table")),
        "flow": {
            "sys_id": _v(meta.get("sys_id")) or flow_id,
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


def _action_describe() -> CapabilityResult:
    """Return the action registry without making any platform calls."""
    return operation_result(
        data={"actions": _ACTION_REGISTRY, "authoring": support.ledger()},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class FlowCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (
            lambda: ServiceNowClient(settings, auth_provider)
        )

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.settings = settings

    async def flow(
        self,
        action: str,
        sys_id: str | None = None,
        name: str | None = None,
        value: str | None = None,
        table: str | None = None,
        trigger_type: str | None = None,
        active: str | None = None,
        limit: int | None = None,
        sections: list[str] | Literal["*"] | None = None,
        section_limit: int | None = None,
        execution_sys_id: str | None = None,
    ) -> CapabilityResult:
        """Execute the flow capability with native typed inputs."""
        sys_id = "" if sys_id is None else sys_id
        name = "" if name is None else name
        value = "" if value is None else value
        table = "" if table is None else table
        trigger_type = "" if trigger_type is None else trigger_type
        active = "" if active is None else active
        limit = 0 if limit is None else limit
        sections = [] if sections is None else sections
        section_limit = 0 if section_limit is None else section_limit

        if action not in _VALID_ACTIONS:
            return _error(
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "describe":
            return _action_describe()

        if action == "decode_values":
            return _action_decode_values(value=value)

        if action in {"validate", "status"}:
            if not sys_id:
                raise CapabilityError("validate and status require sys_id.")
            async with self.client_factory() as client:
                return (
                    await lifecycle.validate(sys_id, client)
                    if action == "validate"
                    else await lifecycle.status(sys_id, execution_sys_id, client)
                )

        if action in {"contract", "inspect"}:
            return await _action_inspect(
                sys_id=sys_id,
                name=name,
                settings=self.settings,
                client_factory=self.client_factory,
                sections=sections,
                section_limit=section_limit,
                is_contract=action == "contract",
            )

        if action == "find_by_table":
            return await _action_find_by_table(
                table=table,
                client_factory=self.client_factory,
            )

        return await _action_list_triggers(
            table=table,
            trigger_type=trigger_type,
            active=active,
            limit=limit,
            settings=self.settings,
            client_factory=self.client_factory,
        )
