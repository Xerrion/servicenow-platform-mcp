"""Native top-level handler wrappers. No compile, activation, or execution calls.

Studio 27: errorHandlingToggledSaga:477258-477299; enabled:163491-163498;
save parents:163513-163524; disable/remove empty:204244-204299;
order zero:156510-156513. Wrapper definitions always come from the catalog.
"""

import copy
import uuid
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, tree

TRY = "TOP_LEVEL_TRY"
CATCH = "TOP_LEVEL_CATCH"
TYPES = {TRY, CATCH}
OPS = {"set_error_handler", "remove_error_handler"}


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
    ordered = list(tree.walk(roots))
    if int(main.raw["order"]) != 0 or any(
        int(s.raw["order"]) != i for i, s in enumerate(ordered)
    ):
        raise CapabilityError(
            "Handler orders must start at zero and remain contiguous.", "UNAVAILABLE"
        )
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


def definitions(catalog: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    found = []
    for kind in (TRY, CATCH):
        rows = [row for row in catalog if row.get("type") == kind]
        if len(rows) != 1 or rows[0].get("userCanRead") is not True:
            raise CapabilityError(
                "Readable top-level Try/Catch definitions are required.", "UNAVAILABLE"
            )
        found.append(copy.deepcopy(rows[0]))
    if (
        found[0].get("inputs")
        or len(found[1].get("inputs", [])) != 1
        or found[1]["inputs"][0].get("name") != "enabled"
        or found[1]["inputs"][0].get("type") != "boolean"
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
        if any(
            s.logic_type == "PARALLEL"
            or s.raw.get("flowLogicDefinition", {})
            .get("attributes", {})
            .get("display_as_sandwich")
            in {True, "true"}
            for s in tree.walk(roots)
        ):
            raise CapabilityError(
                "Handler wrapping with sandwich or Parallel order projections requires another adapter.",
                "UNAVAILABLE",
            )
        try_def, catch_def = definitions(catalog)
        if "displayOnUi" in try_def:
            try_def["displayOnUi"] = False
        main = tree.new_logic_step(flow["id"], try_def, {})
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
    for step, definition in zip(wrappers, definitions(catalog), strict=True):
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
    if wrappers or any(
        key in original
        for key in (
            "startOfErrorHandlingInstances",
            "startingIndexOfErrorHandlingInstances",
        )
    ):
        flow["startOfErrorHandlingInstances"] = index
    if "startingIndexOfErrorHandlingInstances" in original:
        flow["startingIndexOfErrorHandlingInstances"] = index
    if wrappers and any(
        s.logic_type == "PARALLEL"
        or s.raw.get("flowLogicDefinition", {})
        .get("attributes", {})
        .get("display_as_sandwich")
        in {True, "true"}
        for s in tree.walk(roots)
        if s.logic_type not in TYPES
    ):
        raise CapabilityError(
            "Handler sandwich/Parallel order projections require another adapter.",
            "UNAVAILABLE",
        )
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
        "outputs": _outputs(wrappers[1].raw["flowLogicDefinition"].get("variables", []))
        if wrappers
        else [],
    }


def _outputs(rows: list[dict[str, Any]], parent: str = "") -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        path = parent + "." + row["name"] if parent else row["name"]
        result.append({"path": path, "type": data.value_type(row).kind})
        result.extend(_outputs(data.spec(row).get("children", []), path))
    return result
