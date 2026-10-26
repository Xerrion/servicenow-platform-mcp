"""Decode and check persisted Flow table rows without constructing or saving drafts."""

import json
import re
from dataclasses import dataclass
from typing import Any, NoReturn

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows._flow_values import decode_values

TRY = "TOP_LEVEL_TRY"
CATCH = "TOP_LEVEL_CATCH"
TYPES = {TRY, CATCH}


def order_key(value: Any) -> int:
    """Studio sortComponentInstances sorts an arrow label by its right-hand side."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:->[0-9]+)?", value):
        try:
            return int(value.rsplit("->", 1)[-1])
        except ValueError:
            pass
    raise CapabilityError(
        "The step order is not a native integer or Parallel arrow label.", "UNAVAILABLE"
    )


def _json(value: Any) -> str:
    # JSON equality distinguishes false from 0 and true from 1.
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except TypeError, ValueError:
        _incomplete()


class IncompleteSemantics(CapabilityError):
    """A persisted row cannot establish complete inspection data."""

    def __init__(self, path: str, invariant: str) -> None:
        super().__init__(invariant, "UNAVAILABLE")
        self.path = path


def _incomplete(
    path: str = "flow",
    invariant: str = "The persisted flow row has incomplete or malformed fields. Inspect the flow.",
) -> NoReturn:
    raise IncompleteSemantics(path, invariant)


def _bindings(raw: Any, path: str = "flow.inputs") -> str:
    if not isinstance(raw, list):
        _incomplete(
            path,
            "Read the complete binding list. Missing or non-list bindings cannot establish semantics.",
        )
    names: set[str] = set()
    values: dict[str, Any] = {}
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            _incomplete(item_path, "Each binding must be an object.")
        if not isinstance(item.get("name"), str):
            _incomplete(
                item_path + ".name", "Each binding requires a readable string name."
            )
        if "value" not in item:
            _incomplete(
                item_path + ".value",
                "Read the stored binding value. An absent value is not an empty default.",
            )
        name = item["name"]
        if not name or name in names:
            _incomplete(
                item_path + ".name",
                "Binding names must be nonempty and unique within the binding list.",
            )
        names.add(name)
        # Keep nested values and literal pill references without coercion or display substitution.
        values[name] = item
    return _json(values)


@dataclass(frozen=True)
class SemanticStep:
    kind: str
    identity: str
    ui_id: str
    order: int | str
    parent: str
    definition: str
    inputs: str
    settings: str


def table_step(
    *,
    kind: str,
    identity: str,
    ui_id: str,
    order: str,
    parent: str,
    definition: str,
    inputs: Any,
    settings: dict[str, Any],
    handler_main: str | None = None,
) -> SemanticStep:
    """Check identities, order, and bindings in one decoded V2 table row."""
    if (
        not identity
        or not ui_id
        or not definition
        or not isinstance(inputs, list | dict)
    ):
        _incomplete()
    try:
        numeric_order = order_key(order)
    except CapabilityError:
        _incomplete()
    hidden_try = (
        kind == "flowlogic"
        and parent == ""
        and (
            settings.get("logic_type") == "TOP_LEVEL_TRY"
            or settings.get("logic_type") == "TRY"
            and ui_id == handler_main
        )
    )
    if numeric_order < (0 if hidden_try else 1):
        _incomplete()
    if isinstance(inputs, list):
        bindings = _bindings(inputs)
    else:
        # Logic blobs also contain non-binding settings. Preserve them in full.
        for key in (
            "inputs",
            "decisionTableInputs",
            "dynamicInputs",
            "workflowInputs",
            "outputsToAssign",
        ):
            if key in inputs:
                _bindings(inputs[key])
        bindings = _json(inputs)
    return SemanticStep(
        kind,
        identity,
        ui_id,
        order if "->" in order else numeric_order,
        parent,
        definition,
        bindings,
        _json(settings),
    )


def _table_value(row: dict[str, Any], name: str, fallback: str = "") -> str:
    value = row.get(name, row.get(fallback))
    if isinstance(value, dict):
        value = value.get("value")
    return str(value) if value is not None else ""


def table_main(rows: list[tuple[str, dict[str, Any]]]) -> str | None:
    """Bind Rome's hidden main to one complete, metadata-associated table tree.

    Callers must withhold this context for any bounded or unreadable dataset.
    Joined definition types and Catch.connected_to are authoritative; order zero
    by itself never grants a TRY the handler role.
    """
    nodes: dict[str, tuple[str, dict[str, Any], int]] = {}
    orders: set[int] = set()
    identities: set[str] = set()
    for kind, row in rows:
        uid = _table_value(row, "ui_id", "ui_uuid")
        identity = _table_value(row, "sys_id")
        if (
            not uid
            or uid in nodes
            or not identity
            or identity in identities
            or not {"parent_ui_id", "parent_ui_uuid"} & row.keys()
        ):
            return None
        try:
            order = order_key(_table_value(row, "order"))
        except CapabilityError:
            return None
        if order in orders:
            return None
        nodes[uid] = kind, row, order
        orders.add(order)
        identities.add(identity)
    roots = sorted(
        (
            (uid, kind, row, order)
            for uid, (kind, row, order) in nodes.items()
            if not _table_value(row, "parent_ui_id", "parent_ui_uuid")
        ),
        key=lambda item: item[3],
    )
    if len(roots) != 2:
        return None
    main_uid, main_kind, main, main_order = roots[0]
    catch_uid, catch_kind, catch, catch_order = roots[1]
    if (
        main_kind != "logic"
        or catch_kind != "logic"
        or main_order != 0
        or _table_value(main, "logic_definition.type") != "TRY"
        or _table_value(catch, "logic_definition.type") != CATCH
        or not _table_value(main, "logic_definition")
        or not _table_value(catch, "logic_definition")
        or _table_value(main, "logic_definition")
        not in _table_value(catch, "logic_definition.connected_to").split(",")
    ):
        return None
    for uid, (kind, row, order) in nodes.items():
        if (
            uid not in {main_uid, catch_uid}
            and kind == "logic"
            and _table_value(row, "logic_definition.type") in TYPES
        ):
            return None
        seen = {uid}
        owner = uid
        parent = _table_value(row, "parent_ui_id", "parent_ui_uuid")
        while parent:
            if parent in seen or parent not in nodes or nodes[parent][0] != "logic":
                return None
            if nodes[parent][2] >= order:
                return None
            seen.add(parent)
            owner = parent
            parent = _table_value(nodes[parent][1], "parent_ui_id", "parent_ui_uuid")
        if owner not in {main_uid, catch_uid} or (
            uid != catch_uid and (order > catch_order) != (owner == catch_uid)
        ):
            return None
    try:
        main_values = decode_values(_table_value(main, "values"))
        catch_values = decode_values(_table_value(catch, "values"))
    except ValueError:
        return None
    if isinstance(main_values, dict):
        main_values = main_values.get("inputs")
    if isinstance(catch_values, dict):
        if catch_values.get("connectedTo") not in (None, "", main_uid):
            return None
        catch_values = catch_values.get("inputs")
    if main_values != [] or not isinstance(catch_values, list):
        return None
    names = [row.get("name") for row in catch_values if isinstance(row, dict)]
    if (
        len(names) != len(catch_values)
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
        or set(names) not in ({"enabled"}, {"enabled", "__status__"})
    ):
        return None
    try:
        _handler_enabled(catch_values)
    except CapabilityError, KeyError:
        return None
    return main_uid


def _handler_enabled(inputs: list[dict[str, Any]]) -> bool:
    value = next(row["value"] for row in inputs if row["name"] == "enabled")
    if type(value) is bool:
        return value
    if type(value) in {str, int} and value in ("0", "1", 0, 1):
        return str(value) == "1"
    raise CapabilityError(
        "The handler enabled flag is not a native boolean.", "UNAVAILABLE"
    )
