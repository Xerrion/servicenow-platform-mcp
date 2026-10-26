"""Pure tree edits for Workflow Studio flow definitions.

The Studio payload stores steps in three flat lists (actions, flow logic, subflows).
The parent tree owns topology. Studio's separate order projection includes hidden
handler wrappers, sandwich closing labels, and Parallel ``->`` labels; array
positions still count actual instances only. Projection never replaces identities
or copies opaque instance metadata into another graph.
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
    handler_try: bool = False

    @property
    def uid(self) -> str:
        return str(self.raw["uiUniqueIdentifier"])

    @property
    def logic_type(self) -> str | None:
        if self.kind != "flowlogic":
            return None
        value = self.raw.get("flowLogicDefinition", {}).get("type")
        if self.handler_try and value == "TRY":
            return "TOP_LEVEL_TRY"
        return str(value) if value else None


@dataclass
class OrderProjection:
    order: int | str
    array_position: int
    order_bottom: int | None = None


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


def sandwich(step: Step) -> bool:
    value = (
        step.raw.get("flowLogicDefinition", {})
        .get("attributes", {})
        .get("display_as_sandwich")
    )
    return value is True or isinstance(value, str) and value == "true"


def projected(roots: list[Step]) -> bool:
    return any(sandwich(step) or step.logic_type == "PARALLEL" for step in walk(roots))


def project_orders(roots: list[Step]) -> dict[str, OrderProjection]:
    """Port Studio 27 updateActionInstanceIndices (156494-156713), without orphans.

    Save's common splitter retains these labels and array positions (130432-130460).
    Ancestors below are derived from the existing tree, nearest first, including
    getAncestorData's hidden Catch stopping rule (15835-15882).
    """
    ordered = list(walk(roots))
    parents: dict[str, Step] = {}
    ancestors: dict[str, list[Step]] = {}

    def ancestry(siblings: list[Step], parent: Step | None) -> None:
        for step in siblings:
            chain: list[Step] = []
            current = parent
            if parent:
                parents[step.uid] = parent
            while current:
                chain.append(current)
                grandparent = parents.get(current.uid)
                if grandparent and grandparent.logic_type == "TOP_LEVEL_CATCH":
                    break
                current = grandparent
            ancestors[step.uid] = chain
            ancestry(step.children, step)

    ancestry(roots, None)
    result: dict[str, OrderProjection] = {}
    inside_sandwich = False
    sandwich_count = 0
    sandwich_start: int | None = None
    new_order: int | str = 0 if roots and roots[0].logic_type == "TOP_LEVEL_TRY" else 1

    def previous_sandwich(index: int) -> bool:
        previous = index - sandwich_count - 1
        return index > 0 and previous >= 0 and sandwich(ordered[previous])

    def close_sandwich(start: int) -> int:
        projection = result[ordered[start].uid]
        bottom = order_key(projection.order) + sandwich_count + 1
        projection.order_bottom = bottom
        return bottom + 1

    for index, step in enumerate(ordered):
        is_sandwich = sandwich(step)
        previous_is_sandwich = previous_sandwich(index)
        parent = parents.get(step.uid)
        previous = result[ordered[index - 1].uid] if index else None
        # Studio spreads the original instance. A nested sandwich bottom that
        # its active-sandwich branch does not recalculate must remain intact.
        bottom = step.raw.get("orderBottom")
        if "orderBottom" in step.raw and type(bottom) is not int:
            raise CapabilityError(
                "The sandwich closing label is not an integer.", "UNAVAILABLE"
            )
        if parent is None:
            if previous:
                new_order = order_key(previous.order) + 1
            if previous_is_sandwich:
                sandwich_start = index - 1 if sandwich_start is None else sandwich_start
                new_order = close_sandwich(sandwich_start)
                sandwich_count = 0
                sandwich_start = None
                inside_sandwich = False
            if (inside_sandwich or sandwich_count > 0) and not ancestors[step.uid]:
                if sandwich_start is None:
                    raise CapabilityError(
                        "The sandwich projection has no opening block.", "UNAVAILABLE"
                    )
                new_order = close_sandwich(sandwich_start)
                sandwich_count = 0
                sandwich_start = None
                inside_sandwich = False
            if previous_sandwich(index) and not ancestors[ordered[index - 1].uid]:
                new_order = order_key(result[ordered[index - 1].uid].order) + 2
        else:
            if previous is None:
                raise CapabilityError(
                    "A projected child precedes its parent.", "UNAVAILABLE"
                )
            new_order = order_key(previous.order) + 1
            if step.logic_type == "PARALLELBLOCK" and isinstance(previous.order, str):
                previous_index = index - 1 - sandwich_count
                if previous_index >= 0 and sandwich(ordered[previous_index]):
                    result[ordered[previous_index].uid].order_bottom = (
                        order_key(previous.order) + sandwich_count + 1
                    )
            if parent.logic_type == "PARALLELBLOCK":
                parent_order = result[parent.uid].order
                if parent.children[0] is step and step.logic_type != "CATCH":
                    new_order = f"{parent_order}->{order_key(parent_order) + 1}"
                else:
                    new_order = order_key(previous.order) + 1
            if (
                isinstance(previous.order, str)
                and parent.logic_type != "PARALLELBLOCK"
                and step.logic_type != "PARALLELBLOCK"
            ):
                new_order = order_key(previous.order) + 1
            ancestor_is_sandwich = False
            chain = ancestors[step.uid]
            for ancestor_index, ancestor in enumerate(chain):
                if not sandwich(ancestor):
                    inside_sandwich = False
                    ancestor_is_sandwich = False
                else:
                    ancestor_is_sandwich = True
                    sandwich_start = (
                        index - 1 if sandwich_start is None else sandwich_start
                    )
                    if ancestor_index == len(chain) - 1:
                        inside_sandwich = True
                    sandwich_count += 1
                    break
            if not inside_sandwich and not ancestor_is_sandwich:
                not_nested = len(chain) <= len(ancestors[ordered[index - 1].uid])
                if previous_is_sandwich and (not is_sandwich or not_nested):
                    sandwich_start = (
                        index - 1 if sandwich_start is None else sandwich_start
                    )
                    new_order = close_sandwich(sandwich_start)
                    sandwich_count = 0
                    sandwich_start = None
                elif is_sandwich:
                    bottom = (
                        order_key(step.raw.get("order", new_order)) + sandwich_count + 1
                    )
            if inside_sandwich or ancestor_is_sandwich:
                if sandwich_start is None:
                    raise CapabilityError(
                        "The sandwich projection has no opening block.", "UNAVAILABLE"
                    )
                start_order = result[ordered[sandwich_start].uid].order
                if isinstance(start_order, str):
                    new_order = order_key(previous.order) + 1
                elif ordered[index - 1].logic_type != "PARALLELBLOCK":
                    new_order = sandwich_count + start_order
                if index == len(ordered) - 1:
                    close_sandwich(sandwich_start)
        if is_sandwich and not step.children:
            bottom = order_key(new_order) + 1
        if parent is None and index == len(ordered) - 1 and is_sandwich:
            bottom = order_key(new_order) + sandwich_count + 1
        result[step.uid] = OrderProjection(new_order, index, bottom)
    return result


def validate_projection(roots: list[Step]) -> None:
    """Reject contradictory wire/display labels rather than repairing a saved DTO."""
    projection = project_orders(roots)
    for step in walk(roots):
        expected = projection[step.uid]
        if sandwich(step) and expected.order_bottom is None:
            raise CapabilityError(
                "This nested sandwich has no source-defined closing order. "
                "Read a complete native projection before editing it.",
                "UNAVAILABLE",
            )
        actual = step.raw["order"]
        if (
            isinstance(expected.order, str)
            and actual != expected.order
            or isinstance(expected.order, int)
            and ("->" in str(actual) or order_key(actual) != expected.order)
        ):
            raise CapabilityError(
                "Step orders contradict Studio's sandwich/Parallel projection.",
                "UNAVAILABLE",
            )
        for name, value in (
            ("arrayPosition", expected.array_position),
            ("orderBottom", expected.order_bottom),
        ):
            if name in step.raw and (
                type(step.raw[name]) is not int or step.raw[name] != value
            ):
                raise CapabilityError(
                    f"Step {name} contradicts Studio's order projection.", "UNAVAILABLE"
                )


def legacy_handler_uid(flow: dict[str, Any]) -> str | None:
    """Recognize Rome's first TRY only beside a native TOP_LEVEL_CATCH.

    The source UUID map uses flowLogicInstances[0] (163588-163598), and
    isTopLevelTryInstance gates the Rome fallback on handler evidence
    (128916-128942). The role is transient; the saved definition stays TRY.
    """

    def logic_type(row: dict[str, Any]) -> Any:
        definition = row.get("flowLogicDefinition")
        return definition.get("type") if isinstance(definition, dict) else None

    logic = flow.get("flowLogicInstances")
    if not isinstance(logic, list) or not logic or not isinstance(logic[0], dict):
        return None
    main = logic[0]
    if (
        logic_type(main) != "TRY"
        or main.get("parent") != ""
        or type(main.get("order")) not in {int, str}
        or str(main["order"]) != "0"
    ):
        return None
    catches = [
        row
        for row in logic
        if isinstance(row, dict) and logic_type(row) == "TOP_LEVEL_CATCH"
    ]
    if len(catches) != 1 or catches[0].get("parent") != "":
        return None
    catch = catches[0]
    for collection in COLLECTIONS.values():
        for row in flow.get(collection, []):
            if not isinstance(row, dict):
                return None
            if row is not main and row is not catch and not row.get("parent"):
                return None
            if logic_type(row) == "TOP_LEVEL_TRY":
                return None
    uid = main.get("uiUniqueIdentifier")
    return uid if isinstance(uid, str) and uid else None


def build_tree(flow: dict[str, Any]) -> list[Step]:
    kinds: tuple[Kind, ...] = ("action", "flowlogic", "subflow")
    steps = [
        Step(copy.deepcopy(raw), kind)
        for kind in kinds
        for raw in flow[COLLECTIONS[kind]]
    ]
    legacy_main = legacy_handler_uid(flow)
    by_uid: dict[str, Step] = {}
    for step in steps:
        uid = step.raw.get("uiUniqueIdentifier")
        if not uid or uid in by_uid:
            raise CapabilityError(
                "Studio returned a step without a unique UI identifier. No draft write is allowed."
            )
        by_uid[uid] = step
        step.handler_try = uid == legacy_main
    roots: list[Step] = []
    for step in sorted(steps, key=lambda s: order_key(s.raw["order"])):
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
    """Locate a step by database/UI identity or its native projected display label."""
    projections = project_orders(roots)

    def visit(siblings: list[Step], parent: Step | None) -> Location | None:
        for index, step in enumerate(siblings):
            if ref in (step.raw.get("id"), step.uid, str(projections[step.uid].order)):
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
    if step.raw.get("flowLogicDefinition", {}).get("attributes", {}).get(
        "can_move"
    ) in {False, "false"}:
        raise CapabilityError("This native structural block cannot move independently.")
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
        "goBackToSources",
        "arrayPosition",
        "orderBottom",
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
                "ancestorType": (
                    ancestor.raw.get("flowLogicDefinition", {}).get("type")
                    if ancestor
                    else None
                ),
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
    projection = project_orders(roots)
    complex_order = projected(roots)
    for source in walk(roots):
        position = projection[source.uid]
        if complex_order or "arrayPosition" in source.raw:
            result[source.uid]["arrayPosition"] = position.array_position
        if sandwich(source) and position.order_bottom is not None:
            result[source.uid]["orderBottom"] = position.order_bottom
        if source.logic_type != "GOBACKTO":
            continue
        target = next(
            (
                item.get("value")
                for item in source.raw.get("inputs", [])
                if item.get("name") == "go_back_to_step"
            ),
            None,
        )
        if isinstance(target, str) and target in result:
            result[target].setdefault("goBackToSources", []).append(
                {
                    "uiUniqueIdentifier": source.uid,
                    "arrayPosition": position.array_position,
                    "order": position.order,
                }
            )
    return result


def renumber(roots: list[Step]) -> None:
    """Write the tree and Studio's reversible order projection into native rows."""
    projection = project_orders(roots)
    complex_order = projected(roots)
    metadata = ui_metadata(roots)

    def visit(siblings: list[Step], parent: Step | None) -> None:
        for step in siblings:
            item = projection[step.uid]
            step.raw.update(
                order=item.order,
                parent=parent.uid if parent else "",
                **metadata[step.uid],
            )
            if "goBackToSources" not in metadata[step.uid]:
                step.raw.pop("goBackToSources", None)
            if "arrayPosition" in step.raw or complex_order:
                step.raw["arrayPosition"] = item.array_position
            if item.order_bottom is not None:
                step.raw["orderBottom"] = item.order_bottom
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
                kind = step.logic_type
                attributes = definition.get("attributes", {})
                if not isinstance(attributes, dict):
                    raise CapabilityError(
                        "The native structural attributes are malformed."
                    )
                if attributes.get("can_be_nested") in {False, "false"} and any(
                    parent.logic_type == kind for parent in ancestors
                ):
                    raise CapabilityError(
                        f"{definition.get('name')} cannot be nested inside the same flow-logic family."
                    )
                if kind == "DECISIONBLOCK" and (
                    not ancestors or ancestors[-1].logic_type != "DECISION"
                ):
                    raise CapabilityError(
                        "Decision branches must be direct children of Make a decision."
                    )
                if kind == "DECISION" and any(
                    child.logic_type != "DECISIONBLOCK" for child in step.children
                ):
                    raise CapabilityError(
                        "Make a decision can contain only its native decision branches."
                    )
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
                    and kind != "DECISION"
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
                    and kind not in {"PARALLELBLOCK", "DECISIONBLOCK"}
                    and index + 1 < len(siblings)
                ):
                    raise CapabilityError(
                        "A terminal flow-logic step cannot have following siblings."
                    )
            visit(step.children, (*ancestors, step))
            previous = step

    visit(roots)
    # Imported lazily: target validation consumes this module's tree identities.
    from servicenow_mcp.capabilities.flows import loopbacks

    loopbacks.validate(roots)


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
    flow_id: str,
    definition: dict[str, Any],
    inputs: dict[str, Any],
    *,
    input_scripts: dict[str, str | None] | None = None,
) -> Step:
    from servicenow_mcp.capabilities.flows import input_modes

    modes = input_modes.scripts(input_scripts)
    parameters = definition.get("inputs") or []
    parameter_names = {parameter["name"] for parameter in parameters}
    unknown = set(inputs) - parameter_names
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
        if (
            parameter.get("mandatory")
            and value in ("", None)
            and not modes.get(parameter["name"])
        ):
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
    step = Step(raw, "flowlogic")
    declared_modes = {
        name: code for name, code in modes.items() if name in parameter_names
    }
    for item in values:
        if modes.get(item["name"]):
            input_modes.value(item, item["value"], scripted=True)
    input_modes.apply(step, declared_modes)
    return step


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
