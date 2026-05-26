"""Unified ``flow`` tool: read-only Flow Designer inspection.

Five actions:

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
    {"inspect", "find_by_table", "decode_values", "list_triggers", "describe"}
)

_DEFAULT_TRIGGER_LIMIT: Final[int] = 100

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "inspect": {
        "description": "Assemble one flow by sys_id or name: header, triggers, inputs/outputs/variables, decoded V2 nodes, canvas tree, snapshot drift.",
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
    raw = field.get("value", "") if isinstance(field, dict) else (field if field is not None else "")
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
        resolved_sys_id = sys_id
        if not resolved_sys_id:
            matches = await client.find_flows_by_name(name)
            if len(matches) == 0:
                return _error(correlation_id, f"No flow found with name {name!r}.")
            if len(matches) > 1:
                return _error(
                    correlation_id,
                    f"Name {name!r} is ambiguous ({len(matches)} flows match); pass sys_id instead.",
                )
            resolved_sys_id = _v(matches[0].get("sys_id"))
            if not resolved_sys_id:
                return _error(correlation_id, f"Resolved flow for {name!r} has no sys_id.")

        header = await client.get_flow_by_sys_id(resolved_sys_id)
        if header is None:
            return _error(correlation_id, f"Flow {resolved_sys_id} not found.")

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

    # Build lookups.
    action_type_lookup: dict[str, dict[str, Any]] = {}
    for row in action_type_rows:
        key = _v(row.get("sys_id"))
        if key:
            action_type_lookup[key] = row
    condition_lookup: dict[str, str] = {}
    for row in record_triggers:
        key = _v(row.get("sys_id"))
        if key:
            condition_lookup[key] = _v(row.get("condition"))

    # Build canvas nodes (actions + logic, both V2), sorted by ``order``.
    nodes: list[dict[str, Any]] = []
    for row in actions_v2:
        nodes.append(_build_v2_node(row, kind="action", action_type_lookup=action_type_lookup))
    for row in logic_v2:
        nodes.append(_build_v2_node(row, kind="logic"))
    nodes.sort(key=lambda n: _safe_int(n["order"]))
    canvas = _assemble_canvas(nodes)

    # Triggers - V2 first (stitched), then V1.
    triggers: list[dict[str, Any]] = [_v2_trigger_entry(row, condition_lookup=condition_lookup) for row in triggers_v2]
    triggers.extend(_v1_trigger_entry(row) for row in triggers_v1)

    master = _v(header.get("master_snapshot"))
    latest = _v(header.get("latest_snapshot"))
    drift = bool(master and latest and master != latest)

    warnings: list[str] = []
    if drift:
        warnings.append(
            f"snapshot drift: master_snapshot={master!r} differs from latest_snapshot={latest!r}; "
            "the runtime engine and the latest design may not agree."
        )
    if (actions_v1 or logic_v1 or triggers_v1) and (actions_v2 or logic_v2 or triggers_v2):
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

    payload: dict[str, Any] = {
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
        "v1_actions": [_flatten_record(row) for row in actions_v1],
        "v1_variable_values": [_flatten_record(row) for row in v1_variable_values],
        "warnings": warnings,
    }
    return format_response(data=payload, correlation_id=correlation_id)


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

        flow_versions: dict[str, set[str]] = {}
        for row in v2_triggers:
            flow_id = _v(row.get("flow"))
            if flow_id:
                flow_versions.setdefault(flow_id, set()).add("v2")
        for row in v1_triggers:
            flow_id = _v(row.get("flow"))
            if flow_id:
                flow_versions.setdefault(flow_id, set()).add("v1")

        flow_sys_ids = sorted(flow_versions.keys())
        flows_meta = await client.get_flows_bulk(flow_sys_ids) if flow_sys_ids else []

    meta_by_id: dict[str, dict[str, Any]] = {}
    for row in flows_meta:
        key = _v(row.get("sys_id"))
        if key:
            meta_by_id[key] = row

    flows: list[dict[str, Any]] = []
    v1_unique: set[str] = set()
    v2_unique: set[str] = set()
    for flow_id, versions in flow_versions.items():
        if "v1" in versions:
            v1_unique.add(flow_id)
        if "v2" in versions:
            v2_unique.add(flow_id)
        version_tag = "+".join(sorted(versions))
        meta = meta_by_id.get(flow_id, {})
        flows.append(
            {
                "sys_id": flow_id,
                "name": _d(meta.get("name")) if meta else "",
                "internal_name": _v(meta.get("internal_name")) if meta else "",
                "type": _v(meta.get("type")) if meta else "",
                "active": _v(meta.get("active")) == "true" if meta else False,
                "sys_scope": _d(meta.get("sys_scope")) if meta else "",
                "version": version_tag,
            }
        )

    payload: dict[str, Any] = {
        "table": table,
        "v1_count": len(v1_unique),
        "v2_count": len(v2_unique),
        "total": len(flow_versions),
        "flows": flows,
    }
    return format_response(data=payload, correlation_id=correlation_id)


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

    meta_by_id: dict[str, dict[str, Any]] = {}
    for row in flows_meta:
        key = _v(row.get("sys_id"))
        if key:
            meta_by_id[key] = row

    def _trigger_with_flow(row: dict[str, Any], version: str) -> dict[str, Any]:
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

    triggers: list[dict[str, Any]] = [_trigger_with_flow(row, "v2") for row in v2_rows]
    triggers.extend(_trigger_with_flow(row, "v1") for row in v1_rows)

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
            action: 'inspect' | 'find_by_table' | 'decode_values' | 'list_triggers' | 'describe'.
            sys_id: Flow sys_id (inspect; mutually exclusive with name).
            name: Flow name (inspect; mutually exclusive with sys_id).
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
