"""Native standard and conditional error stages with stable in-memory attachments."""

import re
from datetime import UTC, datetime
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, tree

OPS = {"create_stage", "update_stage", "delete_stage", "attach_stage", "detach_stage"}
STATES = {
    "pending": "Pending - has not started",
    "in_progress": "In Progress",
    "skipped": "Skipped",
    "complete": "Completed",
    "error": "Error",
}


def attachments(flow: dict[str, Any]) -> dict[str, list[str]]:
    stages = flow["stages"]
    if stages == []:
        flow["stages"] = stages = {}
    if not isinstance(stages, dict):
        raise CapabilityError(
            "Stage editing requires keyed stage objects.", "UNAVAILABLE"
        )
    ordered = sorted(
        (row for key in tree.COLLECTIONS.values() for row in flow[key]),
        key=lambda row: tree.order_key(row["order"]),
    )
    links: dict[str, list[str]] = {}
    for key, stage in stages.items():
        if (
            not isinstance(key, str)
            or not isinstance(stage, dict)
            or not isinstance(stage.get("componentIndexes"), list)
        ):
            raise CapabilityError(
                "Stage attachment metadata is incomplete.", "UNAVAILABLE"
            )
        indexes = stage["componentIndexes"]
        if any(
            type(i) is not int or i < 0 or i >= len(ordered) for i in indexes
        ) or len(set(indexes)) != len(indexes):
            raise CapabilityError("Stage component indexes are malformed.")
        uids = []
        for index in indexes:
            uid = ordered[index].get("uiUniqueIdentifier")
            if not isinstance(uid, str) or not uid:
                raise CapabilityError(
                    "Stage step identities are incomplete.", "UNAVAILABLE"
                )
            uids.append(uid)
        links[key] = uids
    return links


def _text(edit: dict[str, Any], key: str) -> str:
    value = edit.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise CapabilityError(
            "Stage label, value, and state labels require 1-255 characters."
        )
    return value


def _metadata(stage: dict[str, Any], edit: dict[str, Any]) -> None:
    for key in ("label", "value"):
        if key in edit:
            stage[key] = _text(edit, key)
    if "always_show" in edit:
        if type(edit["always_show"]) is not bool:
            raise CapabilityError("always_show requires a JSON boolean.")
        stage["alwaysShow"] = edit["always_show"]
    if "duration" in edit:
        value = edit["duration"]
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value
        ):
            raise CapabilityError(
                "duration requires a ServiceNow epoch-based duration timestamp."
            )
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            raise CapabilityError(
                "duration is not a valid ServiceNow duration timestamp."
            ) from None
        if (
            value < "1970-01-01 00:00:00"
            or parsed.strftime("%Y-%m-%d %H:%M:%S") != value
        ):
            raise CapabilityError("duration cannot precede the ServiceNow epoch.")
        stage["duration"] = value
    if "states" in edit:
        states = edit["states"]
        if not isinstance(states, dict) or set(states) != set(STATES):
            raise CapabilityError(
                "states requires pending, in_progress, skipped, complete, and error."
            )
        if not isinstance(stage.get("states"), dict):
            raise CapabilityError(
                "Existing stage states are incomplete.", "UNAVAILABLE"
            )
        stage["states"] = {
            **stage["states"],
            **{key: _text(states, key) for key in STATES},
        }


def edit_stage(
    flow: dict[str, Any],
    edit: dict[str, Any],
    roots: list[tree.Step],
    links: dict[str, list[str]],
    aliases: dict[str, str],
    new_id: str,
    sources: dict[str, tuple[str, str, str]],
) -> str:
    """Use createStage/addNewFlowStage/deleteStandardStage's keyed wire shape.

    Local bundle:184105-184139,204322-204398,458244-458271. Unlike the UI, no attachment or
    dependent error stage is silently removed when another stage is selected.
    """
    stages = flow["stages"]
    op = edit["op"]
    if op == "create_stage" and edit.get("type", "standard") not in {
        "standard",
        "error",
    }:
        raise CapabilityError("Stage type must be standard or error.")
    if op == "create_stage":
        key = new_id
        if key in stages:
            raise CapabilityError("The new stage identity already exists.")
        ref = edit.get("ref")
        if ref is not None:
            if (
                not isinstance(ref, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", ref)
                or ref in aliases
                or ref in stages
            ):
                raise CapabilityError(
                    "Stage ref must be a unique lowercase identifier, at most 64 characters."
                )
            aliases[ref] = key
        orders = [
            stage.get("order")
            for stage in stages.values()
            if stage.get("type") == "standard"
        ]
        if any(type(order) is not int or order < 0 for order in orders):
            raise CapabilityError(
                "Existing stage orders are incomplete.", "UNAVAILABLE"
            )
        stage = {
            "stageId": key,
            "componentIndexes": [],
            "label": _text(edit, "label") if edit.get("type") != "error" else "",
            "value": _text(edit, "value") if edit.get("type") != "error" else "",
            "duration": "1970-01-01 00:00:00",
            "type": "standard",
            "order": max(orders, default=-1) + 1,
            "alwaysShow": True,
            "states": dict(STATES),
        }
        if edit.get("type") == "error":
            if set(edit) - {"op", "type", "step", "ref"}:
                raise CapabilityError(
                    "Error stage creation accepts type, step, and ref. Its name and ancestor are derived natively."
                )
            ref = edit.get("step")
            if not isinstance(ref, str) or not ref:
                raise CapabilityError(
                    "Error stage creation requires a conditional step attachment."
                )
            siblings, position, _ = tree.find(roots, ref)
            step = siblings[position]
            ancestry = error_ancestry(flow, roots, links, step.uid)
            stage.update(
                type="error",
                order=None,
                label="Set current stage state to:  [Error]",
                value="Set current stage state to:  [Error]",
                **ancestry,
            )
            sources[key] = (
                ancestry["ancestorStageId"],
                list(tree.walk(roots))[ancestry["ancestorArrPos"]].uid,
                ancestry["ancestralIfElseLogic"],
            )
            if any(
                s.get("type") == "error"
                and all(
                    s.get(k) == stage[k]
                    for k in (
                        "ancestorStageId",
                        "ancestorArrPos",
                        "ancestralIfElseLogic",
                    )
                )
                for s in stages.values()
            ):
                raise CapabilityError(
                    "An error stage already exists for this conditional ancestor. Edit it explicitly."
                )
            if any(step.uid in values for values in links.values()):
                raise CapabilityError(
                    "Remove the existing stage attachment at this step explicitly first."
                )
        elif "step" in edit:
            raise CapabilityError(
                "Standard stage creation uses a separate attach_stage edit."
            )
        _metadata(stage, edit)
        stages[key] = stage
        links[key] = []
        if stage["type"] == "error":
            links[key] = [step.uid]
    else:
        ref = edit.get("stage")
        if not isinstance(ref, str) or not ref:
            raise CapabilityError("Stage operations require a stage key or ref.")
        key = aliases.get(ref, ref)
        stage = stages.get(key)
        if (
            not isinstance(stage, dict)
            or stage.get("stageId") != key
            or stage.get("type") not in {"standard", "error"}
        ):
            raise CapabilityError(
                "This operation requires an existing native stage.", "UNAVAILABLE"
            )
        if stage["type"] == "error":
            # deleteFlowStage dispatches deleteErrorStage for an error state,
            # even for a non-permanent attachment removal (204353-204409).
            if op in {"delete_stage", "detach_stage"}:
                if op == "detach_stage" and edit.get("step") not in links[key]:
                    raise CapabilityError(
                        "The error stage is not attached to this step."
                    )
                del stages[key]
                del links[key]
            elif op == "attach_stage":
                raise CapabilityError(
                    "Error stages attach during creation. Remove and recreate the error stage at its new step.",
                    "UNAVAILABLE",
                )
            elif op == "update_stage":
                if set(edit) - {
                    "op",
                    "stage",
                    "duration",
                    "always_show",
                    "states",
                } or not set(edit) & {"duration", "always_show", "states"}:
                    raise CapabilityError(
                        "Error stage updates accept duration, always_show, and states. Native error labels and ancestry cannot be renamed."
                    )
                _metadata(stage, edit)
            assert isinstance(key, str)
            return key
        if op == "delete_stage":
            if links[key] or any(
                s.get("ancestorStageId") == key for s in stages.values()
            ):
                raise CapabilityError(
                    "Detach stage steps and remove dependent error stages before deleting the stage."
                )
            order = stage.get("order")
            if type(order) is not int or any(
                type(s.get("order")) is not int
                for s in stages.values()
                if s.get("type") == "standard"
            ):
                raise CapabilityError(
                    "Existing stage orders are incomplete.", "UNAVAILABLE"
                )
            del stages[key]
            del links[key]
            for remaining in stages.values():
                if remaining.get("type") == "standard" and remaining["order"] > order:
                    remaining["order"] -= 1
        elif op in {"attach_stage", "detach_stage"}:
            ref = edit.get("step")
            if not isinstance(ref, str) or not ref:
                raise CapabilityError("Stage attachment operations require step.")
            siblings, index, _ = tree.find(roots, ref)
            step = siblings[index]
            if op == "attach_stage":
                if step.logic_type in {"ELSE", "ELSEIF"}:
                    raise CapabilityError(
                        "Attach a stage inside the branch, not before Else or Else If."
                    )
                if any(
                    parent.logic_type in {"FOREACH", "PARALLEL"}
                    for parent in data.Scope(roots).parents[step.uid]
                ):
                    raise CapabilityError(
                        "Stages cannot attach inside For Each or Parallel."
                    )
                parent = data.Scope(roots).parents[step.uid]
                if (
                    parent
                    and index == 0
                    and parent[-1]
                    .raw.get("flowLogicDefinition", {})
                    .get("attributes", {})
                    .get("is_dumb")
                    in {True, "true"}
                ):
                    raise CapabilityError(
                        "Stages cannot attach at the first child of a grouping block."
                    )
                if any(
                    step.uid in values
                    for other, values in links.items()
                    if other != key
                ):
                    raise CapabilityError(
                        "Detach the existing stage at this step before attaching another."
                    )
                if step.uid not in links[key]:
                    links[key].append(step.uid)
            else:
                if any(s.get("ancestorStageId") == key for s in stages.values()):
                    raise CapabilityError(
                        "Dependent error stages block detaching this stage."
                    )
                if step.uid not in links[key]:
                    raise CapabilityError("The stage is not attached to this step.")
                links[key].remove(step.uid)
        elif op == "update_stage":
            if not set(edit) & {
                "label",
                "value",
                "duration",
                "always_show",
                "states",
                "order",
            }:
                raise CapabilityError("update_stage requires a stage setting.")
            if any(s.get("ancestorStageId") == key for s in stages.values()):
                raise CapabilityError(
                    "Dependent error stages block changing this stage."
                )
            _metadata(stage, edit)
            if "order" in edit:
                if any(
                    type(s.get("order")) is not int or s["order"] < 0
                    for s in stages.values()
                    if s.get("type") == "standard"
                ):
                    raise CapabilityError(
                        "Existing stage orders are incomplete.", "UNAVAILABLE"
                    )
                standard = sorted(
                    (s for s in stages.values() if s.get("type") == "standard"),
                    key=lambda s: s.get("order", -1),
                )
                order = edit["order"]
                if type(order) is not int or not 0 <= order < len(standard):
                    raise CapabilityError(
                        "Stage order must be a zero-based standard-stage position."
                    )
                standard.remove(stage)
                standard.insert(order, stage)
                for index, item in enumerate(standard):
                    item["order"] = index
    for field in ("label", "value"):
        values = [s.get(field) for s in stages.values() if s.get("type") == "standard"]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise CapabilityError(
                "Standard stage labels and values must be nonempty strings."
            )
        if len(set(values)) != len(values):
            raise CapabilityError("Stage labels and values must be unique.")
    assert isinstance(key, str)
    return key


def serialize(
    flow: dict[str, Any],
    roots: list[tree.Step],
    links: dict[str, list[str]],
    original: dict[str, Any],
    sources: dict[str, tuple[str, str, str]],
) -> None:
    ordered = list(tree.walk(roots))
    positions = {step.uid: index for index, step in enumerate(ordered)}
    original_ordered = sorted(
        tree.walk(tree.build_tree(original)),
        key=lambda step: tree.order_key(step.raw["order"]),
    )
    original_parents = {step.uid: step.raw["parent"] for step in original_ordered}
    originally_attached = {
        original_ordered[index].uid
        for stage in (
            original["stages"].values() if isinstance(original["stages"], dict) else []
        )
        for index in stage["componentIndexes"]
    }
    scope = data.Scope(roots)
    for key, uids in links.items():
        indexes = []
        for uid in uids:
            if uid not in positions:
                raise CapabilityError(
                    "Detach the stage before deleting its attached step."
                )
            step = ordered[positions[uid]]
            if (
                uid in originally_attached
                and original_parents[uid] != step.raw["parent"]
                and not _wrapper_parent_change(
                    original_parents[uid], step.raw["parent"], roots, original
                )
            ):
                raise CapabilityError(
                    "Detach a stage before moving its step across parent regions."
                )
            if any(
                parent.logic_type in {"FOREACH", "PARALLEL"}
                for parent in scope.parents[uid]
            ):
                raise CapabilityError(
                    "Stages cannot attach inside For Each or Parallel."
                )
            indexes.append(positions[uid])
        flow["stages"][key]["componentIndexes"] = sorted(indexes)
    for key, stage in flow["stages"].items():
        if stage.get("type") != "error":
            continue
        if not links[key]:
            raise CapabilityError(
                "An error stage must have a native attachment. Remove detached error stages explicitly.",
                "UNAVAILABLE",
            )
        for uid in links[key]:
            ancestry = error_ancestry(flow, roots, links, uid)
            source = sources.get(key)
            if (
                not source
                or (
                    ancestry["ancestorStageId"],
                    ordered[ancestry["ancestorArrPos"]].uid,
                    ancestry["ancestralIfElseLogic"],
                )
                != source
            ):
                raise CapabilityError(
                    "The error stage source changed. Remove and recreate it explicitly."
                )
            stage.update(ancestry)
    error_sources(flow, roots, links)


def summary(flow: dict[str, Any], created: list[str]) -> list[dict[str, Any]]:
    """Show reviewed stage settings without copying opaque metadata into preview."""
    if not isinstance(flow["stages"], dict):
        return []
    return [
        {
            "stage": key,
            **{
                field: row[field]
                for field in (
                    "label",
                    "value",
                    "duration",
                    "alwaysShow",
                    "order",
                    "type",
                    "componentIndexes",
                    "ancestorStageId",
                    "ancestorArrPos",
                    "ancestralIfElseLogic",
                )
                if field in row
            },
            **(
                {
                    "states": {
                        state: row["states"][state]
                        for state in STATES
                        if state in row["states"]
                    }
                }
                if isinstance(row.get("states"), dict)
                else {}
            ),
            "new": key in created,
        }
        for key, row in flow["stages"].items()
        if isinstance(row, dict)
    ]


def error_ancestry(
    flow: dict[str, Any], roots: list[tree.Step], links: dict[str, list[str]], uid: str
) -> dict[str, Any]:
    """obtainStageDataForInstance/getPreviousAncestorStageInfo:184141-184197,184316-184355."""
    ordered = list(tree.walk(roots))
    positions = {s.uid: i for i, s in enumerate(ordered)}
    scope = data.Scope(roots)
    if uid not in positions:
        raise CapabilityError("The error stage target step is absent.")
    target = ordered[positions[uid]]
    branch = next(
        (
            s
            for s in reversed(scope.parents[uid])
            if s.logic_type in {"IF", "ELSEIF", "ELSE"}
        ),
        None,
    )
    if branch is None or target.logic_type in {
        "IF",
        "ELSE",
        "ELSEIF",
        "TOP_LEVEL_TRY",
        "TOP_LEVEL_CATCH",
    }:
        raise CapabilityError(
            "Attach the error state to a step inside If/Else If/Else, not the conditional wrapper itself."
        )
    candidates = [
        (positions[attached], key, attached)
        for key, attached_uids in links.items()
        if flow["stages"][key].get("type") == "standard"
        for attached in attached_uids
        if attached in positions
        and positions[attached] <= positions[branch.uid]
        and len(scope.parents[attached]) <= len(scope.parents[branch.uid])
        and ordered[positions[attached]].raw.get("errorHandlingInstance", False)
        == target.raw.get("errorHandlingInstance", False)
    ]
    if not candidates:
        raise CapabilityError(
            "Attach a standard stage before the conditional branch first."
        )
    position, key, _ = max(candidates)
    return {
        "ancestorStageId": key,
        "ancestorArrPos": position,
        "ancestralIfElseLogic": branch.uid,
    }


def _wrapper_parent_change(
    before: str, after: str, roots: list[tree.Step], original: dict[str, Any]
) -> bool:
    """A hidden Try wrapper changes parent ownership without moving stage scope."""
    wrappers = {
        s.uid
        for s in tree.walk([*roots, *tree.build_tree(original)])
        if s.logic_type == "TOP_LEVEL_TRY"
    }
    return (not before and after in wrappers) or (not after and before in wrappers)


def error_sources(
    flow: dict[str, Any], roots: list[tree.Step], links: dict[str, list[str]]
) -> dict[str, tuple[str, str, str]]:
    ordered = list(tree.walk(roots))
    sources: dict[str, tuple[str, str, str]] = {}
    ancestors: set[tuple[str, str, str]] = set()
    for key, stage in flow["stages"].items():
        if stage.get("type") != "error":
            continue
        position = stage.get("ancestorArrPos")
        if (
            type(position) is not int
            or not 0 <= position < len(ordered)
            or not links[key]
        ):
            raise CapabilityError(
                "The error stage requires a complete source and attachment.",
                "UNAVAILABLE",
            )
        for uid in links[key]:
            ancestry = error_ancestry(flow, roots, links, uid)
            if any(stage.get(k) != value for k, value in ancestry.items()):
                raise CapabilityError(
                    "The error stage ancestry contradicts its source topology.",
                    "UNAVAILABLE",
                )
        sources[key] = (
            stage["ancestorStageId"],
            ordered[position].uid,
            stage["ancestralIfElseLogic"],
        )
        if sources[key] in ancestors:
            raise CapabilityError(
                "Multiple error stages claim the same conditional source.",
                "UNAVAILABLE",
            )
        ancestors.add(sources[key])
    return sources
