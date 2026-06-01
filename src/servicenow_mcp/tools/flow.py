"""Unified ``flow`` tool: read-only Flow Designer inspection.

Six actions:

* ``inspect``        - assemble one flow by sys_id or name: header, triggers,
  inputs/outputs/variables, decoded V2 nodes (with resolved datapill refs),
  canvas tree, snapshot drift.
* ``summary``        - compact projection of the same data: single trigger,
  flat ordered steps, branch-only tree, global datapill graph, counts.
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

import re
from collections.abc import Iterator
from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._flow_values import decode_values, looks_compressed
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["flow"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {"inspect", "summary", "find_by_table", "decode_values", "list_triggers", "describe"}
)

_DEFAULT_TRIGGER_LIMIT: Final[int] = 100

# Datapills in Flow Designer ``values`` payloads are stored as
# ``{{<ui_uuid>.<dotted.field.path>}}`` where ui_uuid is the producer step's
# canvas UUID (lowercase hex with dashes, 36 chars). The field part may
# contain dots, brackets, and underscores; we capture lazily up to the
# closing braces.
_DATAPILL_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{\{([0-9a-fA-F-]{36})\.([^}]+)\}\}")

# Verbatim "Add your code here" stub Flow Designer drops into every new
# calculated-field input/output/variable. Stripping it keeps real custom
# calculations visible while removing pure boilerplate from payloads.
_CALCULATION_BOILERPLATE: Final[str] = (
    "(function calculatedFieldValue(current) {\n\n\t// Add your code here\n\treturn '';  // return the calculated value\n\n})(current);"
)

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "inspect": {
        "description": "Assemble one flow by sys_id or name: header, triggers, inputs/outputs/variables, decoded V2 nodes with resolved datapill refs, canvas tree, snapshot drift.",
        "params": {"sys_id": "str (32-char)", "name": "str"},
    },
    "summary": {
        "description": "Compact projection of inspect: single resolved trigger, flat ordered steps, branch-only tree, global datapill graph, and counts.",
        "params": {"sys_id": "str (32-char)", "name": "str"},
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


def _safe_int(value: str) -> int:
    """Best-effort int conversion for sorting; non-numeric sorts last."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**9


def _flatten_record(row: dict[str, Any]) -> dict[str, Any]:
    """Collapse a display_value=all row to its raw ``value`` per field, dropping boilerplate calculation."""
    flat: dict[str, Any] = {key: _v(val) for key, val in row.items()}
    if flat.get("calculation") == _CALCULATION_BOILERPLATE:
        flat.pop("calculation", None)
    return flat


def _walk_strings(value: Any) -> Iterator[str]:
    """Yield every string leaf in a nested dict/list/scalar."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_strings(v)


# ---------------------------------------------------------------------------
# inspect / summary - canvas node and trigger builders
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


# ---------------------------------------------------------------------------
# Datapill resolution
# ---------------------------------------------------------------------------


def _build_canvas_map(flat_nodes: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Map each canvas node's ``ui_uuid`` to its identifying metadata.

    Built from the flat node list (pre-nesting) so all nodes are included
    regardless of branch depth.
    """
    canvas_map: dict[str, dict[str, str]] = {}
    for node in flat_nodes:
        uuid = node["ui_uuid"]
        if not uuid:
            continue
        action_type = node.get("action_type")
        action_type_name = action_type.get("name", "") if isinstance(action_type, dict) else ""
        canvas_map[uuid.lower()] = {
            "sys_id": node["sys_id"],
            "name": node["label"] or node["name"],
            "action_type_name": action_type_name,
        }
    return canvas_map


def _find_datapill_refs_in(decoded: Any) -> list[tuple[str, str, str]]:
    """Find every ``{{uuid.field}}`` ref in a decoded payload.

    Returns ``[(raw_ref, producer_ui_uuid, field), ...]`` preserving order
    of first appearance, deduplicated by ``(producer_ui_uuid, field)``.
    """
    refs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for s in _walk_strings(decoded):
        for match in _DATAPILL_PATTERN.finditer(s):
            key = (match.group(1), match.group(2))
            if key in seen:
                continue
            seen.add(key)
            refs.append((match.group(0), match.group(1), match.group(2)))
    return refs


def _resolve_refs_for_node(
    node: dict[str, Any],
    canvas_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Annotate one node's datapill refs with producer metadata."""
    decoded = node.get("values_decoded")
    if decoded is None:
        return []
    resolved: list[dict[str, Any]] = []
    for raw, producer_uuid, field in _find_datapill_refs_in(decoded):
        producer = canvas_map.get(producer_uuid.lower())
        resolved.append(
            {
                "ref": raw,
                "field": field,
                "producer_ui_uuid": producer_uuid,
                "producer_sys_id": producer["sys_id"] if producer else "",
                "producer_name": producer["name"] if producer else "",
                "resolved": producer is not None,
            }
        )
    return resolved


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def _detect_order_gaps(roots: list[dict[str, Any]]) -> bool:
    """Return True if any sibling group on the canvas has a discontinuous order sequence.

    A "gap" is detected at any nesting level with at least three sibling
    nodes when the consecutive order deltas are not uniform (max delta
    exceeds min delta). This catches both classic Flow spacing skips
    (100 -> 200 -> 400) and tight sequences (...9 -> 11...).
    """
    if not roots:
        return False

    def _level_has_gap(siblings: list[dict[str, Any]]) -> bool:
        orders = sorted({_safe_int(n["order"]) for n in siblings})
        if len(orders) >= 3:
            deltas = [orders[i + 1] - orders[i] for i in range(len(orders) - 1)]
            if min(deltas) > 0 and max(deltas) > min(deltas):
                return True
        return any(_level_has_gap(n.get("children", [])) for n in siblings)

    return _level_has_gap(roots)


def _build_warnings(
    *,
    flow_active: bool,
    drift: bool,
    master: str,
    latest: str,
    actions_v1: list[dict[str, Any]],
    logic_v1: list[dict[str, Any]],
    triggers_v1: list[dict[str, Any]],
    actions_v2: list[dict[str, Any]],
    logic_v2: list[dict[str, Any]],
    triggers_v2_entries: list[dict[str, Any]],
    action_type_lookup: dict[str, dict[str, Any]],
    canvas: list[dict[str, Any]],
    unresolved_refs: dict[str, str],
    decode_failure_count: int,
) -> list[str]:
    """Assemble the full warning list shared by ``inspect`` and ``summary``."""
    warnings: list[str] = []

    if drift:
        warnings.append(
            f"snapshot drift: master_snapshot={master!r} differs from latest_snapshot={latest!r}; "
            "the runtime engine and the latest design may not agree."
        )

    if (actions_v1 or logic_v1 or triggers_v1) and (actions_v2 or logic_v2 or triggers_v2_entries):
        warnings.append("V1 and V2 Flow Designer artifacts coexist on this flow; canvas omits V1 nodes.")

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

    if flow_active and triggers_v2_entries and not any(t["active"] for t in triggers_v2_entries):
        warnings.append("flow_active_with_inactive_trigger: flow.active=true but no V2 trigger is active.")

    for entry in triggers_v2_entries:
        if entry["remote_trigger_id"] and not entry["condition"]:
            warnings.append(
                f"missing_record_trigger_condition: V2 trigger {entry['sys_id']!r} has remote_trigger_id "
                f"{entry['remote_trigger_id']!r} but the stitched condition is empty."
            )

    if _detect_order_gaps(canvas):
        warnings.append("canvas_order_nonuniform: sibling step orders are not uniformly spaced on at least one branch.")

    for producer_uuid, consumer_sys_id in unresolved_refs.items():
        warnings.append(
            f"unresolved_datapill_ref: producer_ui_uuid={producer_uuid!r} is referenced by step "
            f"{consumer_sys_id!r} but is not present on the canvas."
        )

    if decode_failure_count:
        warnings.append(
            f"step_decode_failure: {decode_failure_count} canvas node(s) failed to decode their values blob."
        )

    return warnings


# ---------------------------------------------------------------------------
# Bundle loader (shared by inspect and summary)
# ---------------------------------------------------------------------------


async def _resolve_inspect_sys_id(
    client: ServiceNowClient,
    sys_id: str,
    name: str,
    correlation_id: str,
) -> tuple[str, str | None]:
    """Resolve the flow ``sys_id`` for ``inspect``/``summary``.

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


async def _load_flow_bundle(
    client: ServiceNowClient,
    resolved_sys_id: str,
) -> dict[str, Any] | None:
    """Fetch and assemble every artifact needed by ``inspect`` and ``summary``.

    Returns a bundle with raw rows, the assembled canvas (with datapill
    refs attached to every node), trigger entries, and lookup tables.
    Returns ``None`` if the flow header is missing.
    """
    header = await client.get_flow_by_sys_id(resolved_sys_id)
    if header is None:
        return None

    inputs = await client.list_flow_inputs(resolved_sys_id)
    outputs = await client.list_flow_outputs(resolved_sys_id)
    variables = await client.list_flow_variables(resolved_sys_id)
    actions_v2 = await client.list_action_instances_v2(resolved_sys_id)
    actions_v1 = await client.list_action_instances_v1(resolved_sys_id)
    logic_v2 = await client.list_logic_instances_v2(resolved_sys_id)
    logic_v1 = await client.list_logic_instances_v1(resolved_sys_id)
    triggers_v2 = await client.list_trigger_instances_v2(resolved_sys_id)
    triggers_v1 = await client.list_trigger_instances_v1(resolved_sys_id)

    remote_ids = sorted({_v(t.get("remote_trigger_id")) for t in triggers_v2 if _v(t.get("remote_trigger_id"))})
    record_triggers = await client.list_record_triggers(remote_ids) if remote_ids else []

    action_type_ids = sorted({_v(a.get("action_type")) for a in actions_v2 if _v(a.get("action_type"))})
    action_type_rows = await client.get_action_type_definitions(action_type_ids) if action_type_ids else []

    v1_action_ids = sorted({_v(a.get("sys_id")) for a in actions_v1 if _v(a.get("sys_id"))})
    v1_variable_values = await client.list_v1_variable_values(v1_action_ids) if v1_action_ids else []

    action_type_lookup = _index_by_sys_id(action_type_rows)
    condition_lookup: dict[str, str] = {
        _v(row.get("sys_id")): _v(row.get("condition")) for row in record_triggers if _v(row.get("sys_id"))
    }

    flat_nodes: list[dict[str, Any]] = [
        _build_v2_node(row, kind="action", action_type_lookup=action_type_lookup) for row in actions_v2
    ]
    flat_nodes.extend(_build_v2_node(row, kind="logic") for row in logic_v2)
    flat_nodes.sort(key=lambda n: _safe_int(n["order"]))

    canvas_map = _build_canvas_map(flat_nodes)

    # Attach datapill refs before nesting so we can walk the flat list and
    # collect the global unresolved-producer set in one pass.
    unresolved_refs: dict[str, str] = {}
    decode_failure_count = 0
    for node in flat_nodes:
        if "decode_error" in node:
            decode_failure_count += 1
        refs = _resolve_refs_for_node(node, canvas_map)
        if refs:
            node["datapill_refs"] = refs
            for ref in refs:
                if not ref["resolved"] and ref["producer_ui_uuid"] not in unresolved_refs:
                    unresolved_refs[ref["producer_ui_uuid"]] = node["sys_id"]

    canvas = _assemble_canvas(flat_nodes)

    triggers_v2_entries: list[dict[str, Any]] = [
        _v2_trigger_entry(row, condition_lookup=condition_lookup) for row in triggers_v2
    ]
    triggers_v1_entries: list[dict[str, Any]] = [_v1_trigger_entry(row) for row in triggers_v1]

    return {
        "header": header,
        "inputs": inputs,
        "outputs": outputs,
        "variables": variables,
        "actions_v1": actions_v1,
        "actions_v2": actions_v2,
        "logic_v1": logic_v1,
        "logic_v2": logic_v2,
        "triggers_v1_rows": triggers_v1,
        "triggers_v2_rows": triggers_v2,
        "v1_variable_values": v1_variable_values,
        "action_type_lookup": action_type_lookup,
        "flat_nodes": flat_nodes,
        "canvas_map": canvas_map,
        "canvas": canvas,
        "triggers_v2_entries": triggers_v2_entries,
        "triggers_v1_entries": triggers_v1_entries,
        "unresolved_refs": unresolved_refs,
        "decode_failure_count": decode_failure_count,
    }


def _flow_header_payload(header: dict[str, Any]) -> dict[str, Any]:
    """Build the shared ``flow`` header sub-object used by inspect and summary."""
    return {
        "sys_id": _v(header.get("sys_id")),
        "name": _d(header.get("name")),
        "internal_name": _v(header.get("internal_name")),
        "type": _v(header.get("type")),
        "active": _v(header.get("active")) == "true",
        "description": _v(header.get("description")),
        "sys_scope": _d(header.get("sys_scope")),
    }


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


async def _action_inspect(
    *,
    sys_id: str,
    name: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    if sys_id and name:
        return _error(correlation_id, "Provide exactly one of sys_id or name (not both).")
    if not sys_id and not name:
        return _error(correlation_id, "Either sys_id or name is required.")
    if sys_id:
        validate_sys_id(sys_id)

    async with ServiceNowClient(settings, auth_provider) as client:
        resolved_sys_id, err = await _resolve_inspect_sys_id(client, sys_id, name, correlation_id)
        if err is not None:
            return err
        bundle = await _load_flow_bundle(client, resolved_sys_id)
        if bundle is None:
            return _error(correlation_id, f"Flow {resolved_sys_id} not found.")

    header = bundle["header"]
    master = _v(header.get("master_snapshot"))
    latest = _v(header.get("latest_snapshot"))
    drift = bool(master and latest and master != latest)
    flow_payload = _flow_header_payload(header)

    triggers: list[dict[str, Any]] = list(bundle["triggers_v2_entries"])
    triggers.extend(bundle["triggers_v1_entries"])

    warnings = _build_warnings(
        flow_active=flow_payload["active"],
        drift=drift,
        master=master,
        latest=latest,
        actions_v1=bundle["actions_v1"],
        logic_v1=bundle["logic_v1"],
        triggers_v1=bundle["triggers_v1_rows"],
        actions_v2=bundle["actions_v2"],
        logic_v2=bundle["logic_v2"],
        triggers_v2_entries=bundle["triggers_v2_entries"],
        action_type_lookup=bundle["action_type_lookup"],
        canvas=bundle["canvas"],
        unresolved_refs=bundle["unresolved_refs"],
        decode_failure_count=bundle["decode_failure_count"],
    )

    payload: dict[str, Any] = {
        "flow": flow_payload,
        "published_state": {
            "master_snapshot": master,
            "latest_snapshot": latest,
            "drift": drift,
        },
        "inputs": [_flatten_record(row) for row in bundle["inputs"]],
        "outputs": [_flatten_record(row) for row in bundle["outputs"]],
        "variables": [_flatten_record(row) for row in bundle["variables"]],
        "triggers": triggers,
        "canvas": bundle["canvas"],
        "warnings": warnings,
    }

    # v1_actions / v1_variable_values are noise on the modern (V2-only)
    # flows that dominate real installs; surface them only when populated.
    if bundle["actions_v1"]:
        payload["v1_actions"] = [_flatten_record(row) for row in bundle["actions_v1"]]
    if bundle["v1_variable_values"]:
        payload["v1_variable_values"] = [_flatten_record(row) for row in bundle["v1_variable_values"]]

    return format_response(data=payload, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def _summary_trigger(triggers_v2_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten triggers to a single, leaner object - first V2 trigger wins.

    Real flows have one trigger; the array shape on ``inspect`` exists to
    cover edge cases. ``summary`` always returns the same flat object,
    using sentinel values when no V2 trigger exists.
    """
    if not triggers_v2_entries:
        return {"type": "", "table": "", "active": False, "condition": ""}
    first = triggers_v2_entries[0]
    return {
        "type": first["type"],
        "table": first["table"],
        "active": first["active"],
        "condition": first["condition"],
    }


def _summary_steps(
    flat_nodes: list[dict[str, Any]],
    parent_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project the flat node list down to summary step rows.

    Each step carries enough identity (``sys_id``, ``ui_uuid``, ``order``,
    ``kind``, ``name``, ``label``, ``comment``) and branch context
    (``branch_parent_ui_uuid``, ``branch_label``) to be useful on its own
    without the heavy ``values_decoded`` payload.
    """
    steps: list[dict[str, Any]] = []
    for node in flat_nodes:
        action_type = node.get("action_type") or {}
        resolved_name = action_type.get("name", "") if isinstance(action_type, dict) else ""
        parent_id = node["parent_ui_id"]
        parent = parent_lookup.get(parent_id)
        steps.append(
            {
                "order": _safe_int(node["order"]),
                "sys_id": node["sys_id"],
                "ui_uuid": node["ui_uuid"],
                "kind": node["kind"],
                "name": resolved_name or node["name"],
                "label": node["label"],
                "comment": node["comment"],
                "branch_parent_ui_uuid": parent_id,
                "branch_label": parent["label"] if parent else "",
            }
        )
    return steps


def _summary_branches(canvas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip decoded values + datapill refs from the canvas tree, keep structure only."""
    return [
        {
            "ui_uuid": node["ui_uuid"],
            "sys_id": node["sys_id"],
            "order": _safe_int(node["order"]),
            "name": node["label"] or node["name"],
            "children": _summary_branches(node.get("children", [])),
        }
        for node in canvas
    ]


def _summary_datapill_graph(
    flat_nodes: list[dict[str, Any]],
    canvas_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Flatten every node's datapill refs into a single graph of consumer->producer edges."""
    graph: list[dict[str, Any]] = []
    for node in flat_nodes:
        for ref in node.get("datapill_refs", []):
            producer = canvas_map.get(ref["producer_ui_uuid"].lower())
            graph.append(
                {
                    "consumer_step_sys_id": node["sys_id"],
                    "consumer_field": ref["field"],
                    "producer_ui_uuid": ref["producer_ui_uuid"],
                    "producer_sys_id_if_resolved": producer["sys_id"] if producer else "",
                    "producer_name_if_resolved": producer["name"] if producer else "",
                    "raw_reference": ref["ref"],
                }
            )
    return graph


async def _action_summary(
    *,
    sys_id: str,
    name: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    if sys_id and name:
        return _error(correlation_id, "Provide exactly one of sys_id or name (not both).")
    if not sys_id and not name:
        return _error(correlation_id, "Either sys_id or name is required.")
    if sys_id:
        validate_sys_id(sys_id)

    async with ServiceNowClient(settings, auth_provider) as client:
        resolved_sys_id, err = await _resolve_inspect_sys_id(client, sys_id, name, correlation_id)
        if err is not None:
            return err
        bundle = await _load_flow_bundle(client, resolved_sys_id)
        if bundle is None:
            return _error(correlation_id, f"Flow {resolved_sys_id} not found.")

    header = bundle["header"]
    master = _v(header.get("master_snapshot"))
    latest = _v(header.get("latest_snapshot"))
    drift = bool(master and latest and master != latest)
    flow_payload = _flow_header_payload(header)

    parent_lookup: dict[str, dict[str, Any]] = {
        node["ui_uuid"]: node for node in bundle["flat_nodes"] if node["ui_uuid"]
    }

    actions_v2 = bundle["actions_v2"]
    logic_v2 = bundle["logic_v2"]
    warnings = _build_warnings(
        flow_active=flow_payload["active"],
        drift=drift,
        master=master,
        latest=latest,
        actions_v1=bundle["actions_v1"],
        logic_v1=bundle["logic_v1"],
        triggers_v1=bundle["triggers_v1_rows"],
        actions_v2=actions_v2,
        logic_v2=logic_v2,
        triggers_v2_entries=bundle["triggers_v2_entries"],
        action_type_lookup=bundle["action_type_lookup"],
        canvas=bundle["canvas"],
        unresolved_refs=bundle["unresolved_refs"],
        decode_failure_count=bundle["decode_failure_count"],
    )

    payload: dict[str, Any] = {
        "flow": flow_payload,
        "trigger": _summary_trigger(bundle["triggers_v2_entries"]),
        "steps": _summary_steps(bundle["flat_nodes"], parent_lookup),
        "branches": _summary_branches(bundle["canvas"]),
        "datapill_graph": _summary_datapill_graph(bundle["flat_nodes"], bundle["canvas_map"]),
        "warnings": warnings,
        "counts": {
            "steps": len(bundle["flat_nodes"]),
            "actions": len(actions_v2),
            "logic": len(logic_v2),
            "triggers": len(bundle["triggers_v2_entries"]) + len(bundle["triggers_v1_entries"]),
            "inputs": len(bundle["inputs"]),
            "outputs": len(bundle["outputs"]),
            "variables": len(bundle["variables"]),
        },
    }
    return format_response(data=payload, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# find_by_table
# ---------------------------------------------------------------------------


async def _action_find_by_table(
    *,
    table: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    if not table:
        return _error(correlation_id, "'table' is required for action='find_by_table'.")
    validate_identifier(table)

    async with ServiceNowClient(settings, auth_provider) as client:
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

    async with ServiceNowClient(settings, auth_provider) as client:
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
) -> None:
    """Register the unified ``flow`` tool on the MCP server.

    ``flow`` is read-only and does not consume the shared registries; both
    keyword arguments are accepted only to keep the loader contract uniform
    across tool groups.
    """
    del choices, dictionary  # unused; signature retained for loader parity

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
        *,
        correlation_id: str = "",
    ) -> str:
        """Inspect Flow Designer flows, triggers, and value blobs (read-only).

        Args:
            action: 'inspect' | 'summary' | 'find_by_table' | 'decode_values' | 'list_triggers' | 'describe'.
            sys_id: Flow sys_id (inspect/summary; mutually exclusive with name).
            name: Flow name (inspect/summary; mutually exclusive with sys_id).
            value: gzip+base64+JSON blob to decode (decode_values).
            table: Target table (find_by_table; optional filter for list_triggers).
            trigger_type: Trigger type filter (list_triggers, e.g. 'record_update').
            active: 'true' | 'false' filter (list_triggers).
            limit: Row cap for list_triggers (default 100).
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

        if action == "inspect":
            return await _action_inspect(
                sys_id=sys_id,
                name=name,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        if action == "summary":
            return await _action_summary(
                sys_id=sys_id,
                name=name,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        if action == "find_by_table":
            return await _action_find_by_table(
                table=table,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        return await _action_list_triggers(
            table=table,
            trigger_type=trigger_type,
            active=active,
            limit=limit,
            settings=settings,
            auth_provider=auth_provider,
            correlation_id=correlation_id,
        )
