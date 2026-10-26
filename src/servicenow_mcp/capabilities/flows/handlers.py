"""Native top-level handler wrappers. No compile, activation, or execution calls.

Studio 27: errorHandlingToggledSaga:477258-477299; enabled:163491-163498;
save parents:163513-163524; disable/remove empty:204244-204299;
orders/projection:156494-156713; save splitter:130432-130460.
"""

import copy
import uuid
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, tree
from servicenow_mcp.capabilities.flows._flow_values import decode_values

TRY = "TOP_LEVEL_TRY"
CATCH = "TOP_LEVEL_CATCH"
TYPES = {TRY, CATCH}
OPS = {"set_error_handler", "remove_error_handler"}


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
            order = tree.order_key(_table_value(row, "order"))
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
        enabled(tree.Step({"inputs": catch_values}, "flowlogic"))
    except CapabilityError, KeyError:
        return None
    return main_uid


def pair(roots: list[tree.Step]) -> tuple[tree.Step, tree.Step] | None:
    wrappers = [s for s in tree.walk(roots) if s.logic_type in TYPES]
    if not wrappers:
        return None
    if (
        len(roots) != 2
        or [s.logic_type for s in roots] != [TRY, CATCH]
        or len(wrappers) != 2
    ):
        raise CapabilityError(
            "The top-level handler requires one root Try/Catch pair.", "UNAVAILABLE"
        )
    return roots[0], roots[1]


def enabled(catch: tree.Step) -> bool:
    value = data.named(catch.raw["inputs"], "enabled")["value"]
    if type(value) is bool:
        return value
    if type(value) in {str, int} and value in ("0", "1", 0, 1):
        return str(value) == "1"
    raise CapabilityError(
        "The handler enabled flag is not a native boolean.", "UNAVAILABLE"
    )


def validate(flow: dict[str, Any], roots: list[tree.Step]) -> None:
    wrappers = pair(roots)
    indexes = [
        flow[key]
        for key in (
            "startOfErrorHandlingInstances",
            "startingIndexOfErrorHandlingInstances",
        )
        if key in flow
    ]
    if not wrappers:
        if any(type(value) is not int or value != -1 for value in indexes) or any(
            s.raw.get("errorHandlingInstance") for s in tree.walk(roots)
        ):
            raise CapabilityError(
                "Handler flags require a complete native wrapper pair.", "UNAVAILABLE"
            )
        return
    main, catch = wrappers
    if main.handler_try:
        definitions(
            [main.raw["flowLogicDefinition"], catch.raw["flowLogicDefinition"]],
            legacy=True,
        )
        if (
            main.raw["inputs"]
            or catch.raw.get("connectedTo") not in (None, "", main.uid)
            or any(
                step.raw["definitionId"] != step.raw["flowLogicDefinition"]["id"]
                or [data.spec(row) for row in step.raw["inputs"]]
                != step.raw["flowLogicDefinition"]["inputs"]
                for step in wrappers
            )
        ):
            raise CapabilityError(
                "The legacy handler association or input schema is contradictory.",
                "UNAVAILABLE",
            )
    ordered = list(tree.walk(roots))
    tree.validate_projection(roots)
    if any(
        type(value) is not int or value != ordered.index(catch) for value in indexes
    ):
        raise CapabilityError(
            "The handler boundary index contradicts its wrappers.", "UNAVAILABLE"
        )
    enabled(catch)
    handler_uids = {s.uid for s in tree.walk([catch])}
    for s in tree.walk(roots):
        expected = s.uid in handler_uids
        # Creation marks Catch true (477283-477287), while GET reconstruction
        # marks only rows after Catch (204699-204704). Preserve either native
        # boolean on the wrapper. Child flags must still match their region.
        if s is catch and type(s.raw.get("errorHandlingInstance", False)) is bool:
            continue
        if (
            "errorHandlingInstance" in s.raw
            and s.raw["errorHandlingInstance"] is not expected
        ):
            raise CapabilityError(
                "A step's handler region flag contradicts its parent tree.",
                "UNAVAILABLE",
            )


def definitions(
    catalog: list[dict[str, Any]], *, legacy: bool | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Native UI falls back only from TOP_LEVEL_TRY to TRY, never from
    # TOP_LEVEL_CATCH to CATCH (bundle:470588-470602). An existing Rome
    # wrapper retains its TRY definition even after the catalog is upgraded.
    if legacy is None:
        legacy = not any(row.get("type") == TRY for row in catalog)
    found = []
    for kind in ("TRY" if legacy else TRY, CATCH):
        rows = [row for row in catalog if row.get("type") == kind]
        if len(rows) != 1 or rows[0].get("userCanRead") is not True:
            raise CapabilityError(
                "Readable top-level Try/Catch definitions are required.", "UNAVAILABLE"
            )
        found.append(copy.deepcopy(rows[0]))
    attributes = found[0].get("attributes")
    connection = found[1].get("connectedTo")
    if legacy and (not isinstance(attributes, dict) or not isinstance(connection, str)):
        raise CapabilityError(
            "Read the complete legacy wrapper attributes and Catch association.",
            "UNAVAILABLE",
        )
    inputs = found[1].get("inputs", [])
    by_name = {row.get("name"): row for row in inputs}
    status = by_name.get("__status__")
    if (
        found[0].get("inputs")
        or len(inputs) != len(by_name)
        or set(by_name) not in ({"enabled"}, {"enabled", "__status__"})
        or by_name["enabled"].get("type") != "boolean"
        or status is not None
        and (
            status.get("type") != "object"
            or status.get("attributes", {}).get("co_type_name") != "FDACTIONSTATUS"
            or len(status.get("children", [])) != 2
            or {row.get("name"): row.get("type") for row in status.get("children", [])}
            != {"code": "integer", "message": "string"}
        )
        or legacy
        and isinstance(attributes, dict)
        and isinstance(connection, str)
        and (
            found[0].get("inputs") != []
            or found[0].get("variables") != []
            or attributes.get("display_as_sandwich") not in (None, False, "false")
            or attributes.get("allow_children") in (False, "false")
            or found[0].get("id") not in connection.split(",")
        )
    ):
        raise CapabilityError(
            "This handler definition uses an unsupported input contract.", "UNAVAILABLE"
        )
    return found[0], found[1]


def edit(
    flow: dict[str, Any],
    roots: list[tree.Step],
    request: dict[str, Any],
    catalog: list[dict[str, Any]],
    seed: str,
) -> list[dict[str, str]]:
    wrappers = pair(roots)
    if wrappers:
        check_catalog(roots, catalog)
    if request["op"] == "remove_error_handler":
        if not wrappers:
            raise CapabilityError("The draft has no error handler.")
        if wrappers[1].children:
            raise CapabilityError(
                "Delete handler steps explicitly before removing the handler."
            )
        removed: list[dict[str, str]] = [
            {"id": s.raw["id"]} for s in wrappers if s.raw.get("id")
        ]
        roots[:] = wrappers[0].children
        return removed
    value = request.get("enabled")
    if type(value) is not bool:
        raise CapabilityError("enabled requires a JSON boolean.")
    if not wrappers:
        if not value:
            raise CapabilityError("The draft has no handler to disable.")
        # Save restores direct wrapper parents without flattening nested ancestry
        # (bundle:163513-163524); the shared tree projector owns visible labels.
        try_def, catch_def = definitions(catalog)
        if "displayOnUi" in try_def:
            try_def["displayOnUi"] = False
        main = tree.new_logic_step(flow["id"], try_def, {})
        main.handler_try = try_def["type"] == "TRY"
        catch = tree.new_logic_step(flow["id"], catch_def, {"enabled": True})
        for index, s in enumerate((main, catch)):
            s.raw["uiUniqueIdentifier"] = str(uuid.uuid5(uuid.UUID(seed), str(index)))
        main.children = list(roots)
        roots[:] = [main, catch]
        wrappers = main, catch
    main, catch = wrappers
    if not value and not catch.children:
        roots[:] = main.children
        return [{"id": s.raw["id"]} for s in wrappers if s.raw.get("id")]
    item = data.named(catch.raw["inputs"], "enabled")
    item["value"] = value
    if "data" in catch.raw:
        catch.raw["data"]["enabled"] = value
    for s in tree.walk([catch]):
        if s is not catch or "readOnly" in s.raw or not value:
            s.raw["readOnly"] = not value
    return []


def check_catalog(roots: list[tree.Step], catalog: list[dict[str, Any]]) -> None:
    wrappers = pair(roots)
    if wrappers is None:
        return
    for step, definition in zip(
        wrappers, definitions(catalog, legacy=wrappers[0].handler_try), strict=True
    ):
        if step.logic_type == TRY and "displayOnUi" in definition:
            definition["displayOnUi"] = False
        if (
            step.raw["flowLogicDefinition"] != definition
            or step.raw["definitionId"] != definition["id"]
            or [data.spec(row) for row in step.raw["inputs"]] != definition["inputs"]
        ):
            raise CapabilityError(
                "The saved handler definition differs from the current catalog.",
                "UNAVAILABLE",
            )


def serialize(
    flow: dict[str, Any], roots: list[tree.Step], original: dict[str, Any]
) -> None:
    wrappers = pair(roots)
    index = list(tree.walk(roots)).index(wrappers[1]) if wrappers else -1
    if wrappers or "startOfErrorHandlingInstances" in original:
        flow["startOfErrorHandlingInstances"] = index
    if "startingIndexOfErrorHandlingInstances" in original:
        flow["startingIndexOfErrorHandlingInstances"] = index
    if wrappers and len(list(tree.walk(wrappers[1].children))) > 10:
        raise CapabilityError(
            "The handler supports at most ten actions, logic steps, or subflow calls."
        )


def require_enabled(roots: list[tree.Step], uid: str) -> None:
    wrappers = pair(roots)
    if (
        wrappers
        and not enabled(wrappers[1])
        and uid in {s.uid for s in tree.walk([wrappers[1]])}
    ):
        raise CapabilityError(
            "Enable the preserved handler before editing its contents."
        )


def summary(flow: dict[str, Any]) -> dict[str, Any]:
    wrappers = pair(tree.build_tree(flow))
    return {
        "present": bool(wrappers),
        "enabled": enabled(wrappers[1]) if wrappers else False,
        "main": wrappers[0].uid if wrappers else None,
        "handler": wrappers[1].uid if wrappers else None,
        "steps": [s.uid for s in tree.walk(wrappers[1].children)] if wrappers else [],
        "outputs": _outputs(outputs(wrappers[1].raw["flowLogicDefinition"]))
        if wrappers
        else [],
    }


def outputs(definition: dict[str, Any]) -> list[dict[str, Any]]:
    status = [
        row for row in definition.get("inputs", []) if row.get("name") == "__status__"
    ]
    return status or definition.get("variables", [])


def _outputs(rows: list[dict[str, Any]], parent: str = "") -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        path = parent + "." + row["name"] if parent else row["name"]
        result.append({"path": path, "type": data.value_type(row).kind})
        result.extend(_outputs(data.spec(row).get("children", []), path))
    return result
