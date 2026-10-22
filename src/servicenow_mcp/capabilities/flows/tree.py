"""Pure tree edits for Workflow Studio flow definitions.

The Studio payload stores steps in three flat lists (actions, flow logic, subflows).
Each step has a global ``order`` and an optional ``parent`` that holds the
``uiUniqueIdentifier`` of its enclosing flow-logic block. This module converts the
lists into one ordered tree, applies typed edits, and writes the lists back with
contiguous orders, parent links, and the ancestry fields Studio expects.
"""

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from servicenow_mcp.capabilities.contracts import CapabilityError

if TYPE_CHECKING:
    from collections.abc import Iterator


COLLECTIONS: dict[str, str] = {
    "action": "actionInstances",
    "flowlogic": "flowLogicInstances",
    "subflow": "subFlowInstances",
}
PILL = re.compile(r"\{\{([^{}]+)\}\}")
# Flow logic that may only follow a specific sibling. Studio models Else If and Else as siblings, not children.
CONNECTED_TYPES = {"ELSEIF", "ELSE", "CATCH", "TOP_LEVEL_CATCH"}
Kind = Literal["action", "flowlogic", "subflow"]
Location = tuple[list["Step"], int, "Step | None"]


@dataclass
class Step:
    raw: dict[str, Any]
    kind: Kind
    children: list[Step] = field(default_factory=list)

    @property
    def uid(self) -> str:
        return str(self.raw["uiUniqueIdentifier"])

    @property
    def logic_type(self) -> str | None:
        if self.kind != "flowlogic":
            return None
        value = self.raw.get("flowLogicDefinition", {}).get("type")
        return str(value) if value else None


def build_tree(flow: dict[str, Any]) -> list[Step]:
    kinds: tuple[Kind, ...] = ("action", "flowlogic", "subflow")
    steps = [
        Step(copy.deepcopy(raw), kind)
        for kind in kinds
        for raw in flow[COLLECTIONS[kind]]
    ]
    by_uid: dict[str, Step] = {}
    for step in steps:
        uid = step.raw.get("uiUniqueIdentifier")
        if not uid or uid in by_uid:
            raise CapabilityError(
                "Studio returned a step without a unique UI identifier. No draft write is allowed."
            )
        by_uid[uid] = step
    roots: list[Step] = []
    for step in sorted(steps, key=lambda s: int(s.raw["order"])):
        parent = step.raw.get("parent") or ""
        if parent:
            if parent not in by_uid or by_uid[parent].kind != "flowlogic":
                raise CapabilityError(
                    "Studio returned a step whose parent is not a flow-logic block."
                )
            by_uid[parent].children.append(step)
        else:
            roots.append(step)
    if sum(1 for _ in walk(roots)) != len(steps):
        raise CapabilityError(
            "Studio returned cyclic parent links. No draft write is allowed."
        )
    return roots


def walk(roots: list[Step]) -> Iterator[Step]:
    for step in roots:
        yield step
        yield from walk(step.children)


def find(roots: list[Step], ref: str) -> Location:
    """Locate a step by sys_id, UI identifier, or native display order (hidden Try is zero)."""
    position = -1 if roots and roots[0].logic_type == "TOP_LEVEL_TRY" else 0

    def visit(siblings: list[Step], parent: Step | None) -> Location | None:
        nonlocal position
        for index, step in enumerate(siblings):
            position += 1
            if ref in (step.raw.get("id"), step.uid, str(position)):
                return siblings, index, parent
            found = visit(step.children, step)
            if found is not None:
                return found
        return None

    found = visit(roots, None)
    if found is None:
        raise CapabilityError(
            f"No step matches '{ref}'. Use a step sys_id, UI identifier, or display order."
        )
    return found


def detach(roots: list[Step], ref: str) -> Step:
    siblings, index, _ = find(roots, ref)
    return siblings.pop(index)


def move(
    roots: list[Step],
    ref: str,
    *,
    parent: str | None,
    after: str | None,
    before: str | None,
) -> None:
    """Move Try together with its Catch and both subtrees (bundle:154568-154706)."""
    siblings, position, _ = find(roots, ref)
    step = siblings[position]
    if step.logic_type == "CATCH":
        raise CapabilityError("Move the paired Try. Catch cannot move independently.")
    block = [step]
    if step.logic_type == "TRY":
        if (
            position + 1 >= len(siblings)
            or siblings[position + 1].logic_type != "CATCH"
            or siblings[position + 1].raw.get("connectedTo") != step.uid
        ):
            raise CapabilityError(
                "Try movement requires its adjacent, identity-linked Catch."
            )
        block.append(siblings[position + 1])
    members = {s.uid for s in walk(block)}
    if any(target in members for target in (parent, after, before) if target):
        raise CapabilityError(
            "A block cannot move into or relative to its own pair or descendants."
        )
    del siblings[position : position + len(block)]
    insert(roots, step, parent=parent, after=after, before=before)
    if len(block) == 2:
        insert(roots, block[1], parent=None, after=step.uid, before=None)


def insert(
    roots: list[Step],
    step: Step,
    *,
    parent: str | None,
    after: str | None,
    before: str | None,
) -> None:
    if after and before:
        raise CapabilityError("Use either after or before, not both.")
    anchor = after or before
    if anchor:
        siblings, index, anchor_parent = find(roots, anchor)
        if siblings[index].logic_type in {"TOP_LEVEL_TRY", "TOP_LEVEL_CATCH"}:
            raise CapabilityError(
                "Use main or error_handler as parent, not a hidden wrapper as an anchor."
            )
        if siblings[index].raw.get("readOnly"):
            raise CapabilityError(
                "Enable the handler before inserting at a read-only step."
            )
        if parent and (
            anchor_parent is None
            or parent not in (anchor_parent.raw.get("id"), anchor_parent.uid)
        ):
            raise CapabilityError("The anchor step is not inside the requested parent.")
        siblings.insert(index + 1 if after else index, step)
        return
    if parent:
        container_siblings, index, _ = find(roots, parent)
        container = container_siblings[index]
        if container.raw.get("readOnly"):
            raise CapabilityError(
                "Enable the handler before inserting into its read-only branch."
            )
        if container.kind != "flowlogic":
            raise CapabilityError("Only flow-logic blocks can contain steps.")
        if (
            container.raw.get("flowLogicDefinition", {})
            .get("attributes", {})
            .get("allow_children")
            == "false"
        ):
            raise CapabilityError(
                f"{container.raw['flowLogicDefinition']['name']} cannot contain steps."
            )
        container.children.append(step)
        return
    if roots and roots[0].logic_type == "TOP_LEVEL_TRY":
        roots[0].children.append(step)
    else:
        roots.append(step)


UI_FIELDS = frozenset(
    {
        "depth",
        "ancestorUiId",
        "ancestorType",
        "isFirstFlowLogicChild",
        "isPrecededByThen",
    }
)


def ui_metadata(roots: list[Step]) -> dict[str, dict[str, Any]]:
    """Derive Studio's UI fields from the authoritative ordered parent tree."""
    result: dict[str, dict[str, Any]] = {}

    def visit(
        siblings: list[Step], parent: Step | None, ancestor: Step | None, depth: int
    ) -> None:
        for index, step in enumerate(siblings):
            result[step.uid] = {
                "depth": depth,
                # getAncestorData walks to the outermost enclosing block, not the nearest one.
                "ancestorUiId": ancestor.uid if ancestor else None,
                "ancestorType": ancestor.logic_type if ancestor else None,
                "isFirstFlowLogicChild": bool(parent) and index == 0,
                "isPrecededByThen": bool(parent)
                and index == 0
                and parent.logic_type in {"IF", "ELSEIF", "CATCH"},
            }
            if step.kind == "flowlogic":
                result[step.uid]["children"] = [child.uid for child in step.children]
            # getAncestorData stops before a hidden TOP_LEVEL_CATCH grandparent.
            next_ancestor = (
                step
                if parent and parent.logic_type == "TOP_LEVEL_CATCH"
                else ancestor or step
            )
            visit(step.children, step, next_ancestor, depth + 1)

    visit(roots, None, None, 1)
    return result


def renumber(roots: list[Step]) -> None:
    """Write global order, parent links, and Studio ancestry fields back into each step."""
    order = -1 if roots and roots[0].logic_type == "TOP_LEVEL_TRY" else 0
    metadata = ui_metadata(roots)

    def visit(siblings: list[Step], parent: Step | None) -> None:
        nonlocal order
        for step in siblings:
            order += 1
            step.raw.update(
                order=order, parent=parent.uid if parent else "", **metadata[step.uid]
            )
            if "arrayPosition" in step.raw:
                step.raw["arrayPosition"] = (
                    order
                    if roots and roots[0].logic_type == "TOP_LEVEL_TRY"
                    else order - 1
                )
            visit(step.children, step)

    visit(roots, None)
    if roots and roots[0].logic_type == "TOP_LEVEL_TRY":
        handler_ids = {s.uid for s in walk(roots[1:])}
        for s in walk(roots):
            if (
                s.logic_type != "TOP_LEVEL_CATCH"
                or "errorHandlingInstance" not in s.raw
            ):
                s.raw["errorHandlingInstance"] = s.uid in handler_ids
    else:
        for s in walk(roots):
            if "errorHandlingInstance" in s.raw:
                s.raw["errorHandlingInstance"] = False


def validate_structure(roots: list[Step]) -> None:
    def visit(siblings: list[Step], ancestors: tuple[Step, ...] = ()) -> None:
        previous: Step | None = None
        for index, step in enumerate(siblings):
            if step.kind == "flowlogic":
                definition = step.raw.get("flowLogicDefinition", {})
                kind = definition.get("type")
                if kind in CONNECTED_TYPES:
                    allowed = {
                        item
                        for item in (definition.get("connectedTo") or "").split(",")
                        if item
                    }
                    if (
                        previous is None
                        or previous.raw.get("definitionId") not in allowed
                    ):
                        raise CapabilityError(
                            f"{definition.get('name')} must directly follow a matching block."
                        )
                    if kind == "CATCH" and step.raw.get("connectedTo") != previous.uid:
                        raise CapabilityError(
                            "Catch must reference its paired Try UI identity."
                        )
                if (
                    definition.get("attributes", {}).get("allow_children") == "false"
                    and step.children
                ):
                    raise CapabilityError(
                        f"{definition.get('name')} cannot contain steps."
                    )
                if kind == "TRY" and (
                    index + 1 == len(siblings)
                    or siblings[index + 1].logic_type != "CATCH"
                ):
                    raise CapabilityError(
                        "Try requires one adjacent Catch in the same parent region."
                    )
                if kind == "PARALLEL":
                    if any(parent.logic_type == "PARALLEL" for parent in ancestors):
                        raise CapabilityError("Parallel blocks cannot be nested.")
                    if not step.children or any(
                        child.logic_type != "PARALLELBLOCK" for child in step.children
                    ):
                        raise CapabilityError(
                            "Parallel requires explicit Parallel Branch children."
                        )
                if kind == "PARALLELBLOCK":
                    if not ancestors or ancestors[-1].logic_type != "PARALLEL":
                        raise CapabilityError(
                            "Parallel Branch must be a direct child of Parallel."
                        )
                    if not step.children:
                        raise CapabilityError(
                            "Empty parallel branches are not authorable."
                        )
                if kind in {"BREAK", "CONTINUE"}:
                    types = [parent.logic_type for parent in ancestors]
                    loop = max(
                        (i for i, t in enumerate(types) if t in {"FOREACH", "DOUNTIL"}),
                        default=-1,
                    )
                    if loop < 0 or not any(
                        t in {"IF", "ELSEIF", "ELSE"} for t in types[loop + 1 :]
                    ):
                        raise CapabilityError(
                            "Exit Loop and Skip Iteration require a conditional branch inside a loop.",
                        )
                if (
                    definition.get("attributes", {}).get("allow_siblings") == "false"
                    and kind != "PARALLELBLOCK"
                    and index + 1 < len(siblings)
                ):
                    raise CapabilityError(
                        "A terminal flow-logic step cannot have following siblings."
                    )
            visit(step.children, (*ancestors, step))
            previous = step

    visit(roots)


def flatten(flow: dict[str, Any], roots: list[Step]) -> dict[str, Any]:
    renumber(roots)
    validate_structure(roots)
    payload = copy.deepcopy(flow)
    for key in COLLECTIONS.values():
        payload[key] = []
    for step in walk(roots):
        payload[COLLECTIONS[step.kind]].append(step.raw)
    return payload


def outline(roots: list[Step]) -> list[str]:
    """Human-readable tree used in previews."""
    lines: list[str] = []

    def label(step: Step) -> str:
        raw = step.raw
        if step.kind == "flowlogic":
            condition = next(
                (
                    i.get("value")
                    for i in raw.get("inputs", [])
                    if i.get("name") == "condition"
                ),
                "",
            )
            name = raw.get("flowLogicDefinition", {}).get("name", "Flow logic")
            return f"{name} {condition}".strip()
        detail = next(
            (
                str(i.get("value"))
                for i in raw.get("inputs", [])
                if i.get("value") not in ("", None)
            ),
            "",
        )
        return f"{raw.get('name', step.kind)}" + (f" ({detail})" if detail else "")

    def visit(siblings: list[Step], depth: int) -> None:
        for step in siblings:
            lines.append(f"{'    ' * depth}{label(step)}")
            visit(step.children, depth + 1)

    visit(roots, 0)
    return lines


def new_logic_step(
    flow_id: str, definition: dict[str, Any], inputs: dict[str, Any]
) -> Step:
    parameters = definition.get("inputs") or []
    unknown = set(inputs) - {p["name"] for p in parameters}
    if unknown:
        raise CapabilityError(
            f"Unknown inputs for {definition['name']}: {', '.join(sorted(unknown))}."
        )
    values = []
    for index, parameter in enumerate(parameters):
        default = parameter.get("defaultValue", "")
        if parameter.get("type") == "boolean":
            default = default in ("1", "true", True)
        value = inputs.get(parameter["name"], default)
        if parameter.get("mandatory") and value in ("", None):
            raise CapabilityError(
                f"{definition['name']} requires input '{parameter['name']}'."
            )
        if (
            isinstance(value, str)
            and parameter.get("maxsize")
            and len(value) > int(parameter["maxsize"])
        ):
            raise CapabilityError(
                f"Input '{parameter['name']}' exceeds its maximum length."
            )
        values.append(
            {
                **parameter,
                "field_name": parameter["name"],
                "value": value,
                "displayValue": "",
                "parameter": parameter,
                "children": [],
                "order": index + 1,
                "isNew": False,
                "parent": "",
                "data_format": "",
            },
        )
    raw = {
        "uiUniqueIdentifier": str(uuid.uuid4()),
        "type": "flowlogic",
        "definitionId": definition["id"],
        "flowLogicDefinition": definition,
        "flowSysId": flow_id,
        "inputs": values,
        "data": {v["name"]: v["value"] for v in values},
        "children": [],
        "metadata": {"predicates": []},
        "outputsToAssign": [],
        "isNew": True,
        "isDirty": False,
        "errorHandlingInstance": False,
        "generationSource": "",
        "validation_errors": [],
    }
    return Step(raw, "flowlogic")


def pills(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PILL.findall(value))
    elif isinstance(value, list):
        for item in value:
            found |= pills(item)
    elif isinstance(value, dict):
        for item in value.values():
            found |= pills(item)
    return found


def used_pills(roots: list[Step]) -> dict[str, dict[str, list[str]]]:
    """Map each pill to the steps and inputs that reference it."""
    usage: dict[str, dict[str, list[str]]] = {}

    def values(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        for item in items:
            yield item
            yield from values(item.get("children", []))

    for step in walk(roots):
        for key in (
            "inputs",
            "outputsToAssign",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        ):
            for item in values(step.raw.get(key, [])):
                for pill in pills(item.get("value")):
                    names = usage.setdefault(pill, {}).setdefault(step.uid, [])
                    if item["name"] not in names:
                        names.append(item["name"])
    return usage
