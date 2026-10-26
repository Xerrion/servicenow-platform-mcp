"""Native Go Back To targets derived from the ordered structural tree.

Studio bundle:187356-187587. A target is a UI identity, never a display order.
"""

from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import tree


def targets(roots: list[tree.Step], source: tree.Step) -> set[str]:
    """Port the ancestor/start-of-construct target picker without UI caches."""
    locations: dict[str, tuple[list[tree.Step], int, tree.Step | None]] = {}

    def locate(siblings: list[tree.Step], parent: tree.Step | None) -> None:
        for index, step in enumerate(siblings):
            locations[step.uid] = siblings, index, parent
            locate(step.children, step)

    locate(roots, None)
    definition = source.raw.get("flowLogicDefinition", {})
    attributes = definition.get("attributes", {})
    if not isinstance(attributes, dict):
        raise CapabilityError("Go Back To placement attributes are malformed.")
    if attributes.get("set_target_placement") not in {True, "true"}:
        return set()
    bounded = str(attributes.get("bounded_types", "")).split("|")

    def start(step: tree.Step) -> tree.Step:
        siblings, position, parent = locations[step.uid]
        if step.logic_type in {"DECISIONBLOCK", "PARALLELBLOCK"}:
            if parent is None:
                raise CapabilityError("A branching block has no containing construct.")
            return parent
        if step.logic_type in {"ELSEIF", "ELSE", "CATCH"}:
            identity = step.raw.get("connectedTo")
            if identity:
                found = locations.get(identity)
                if found is None:
                    raise CapabilityError(
                        "A connected branch has no existing construct."
                    )
                return found[0][found[1]]
            # IF/ELSE bindings in saved DTOs can omit the derived connectedTo.
            # Their adjacent sibling chain remains authoritative.
            expected = "TRY" if step.logic_type == "CATCH" else "IF"
            while position > 0:
                position -= 1
                candidate = siblings[position]
                if candidate.logic_type == expected:
                    return candidate
                if candidate.logic_type not in {"ELSEIF", "ELSE"}:
                    break
            raise CapabilityError("A connected branch has no matching construct start.")
        return step

    result: set[str] = set()
    parent = locations[source.uid][2]
    while parent is not None:
        construct = start(parent)
        if construct.logic_type in bounded:
            break
        if construct.logic_type != "TOP_LEVEL_TRY":
            result.add(construct.uid)
        siblings, position, _ = locations[construct.uid]
        for previous in siblings[:position]:
            if previous.logic_type != "TOP_LEVEL_TRY" and start(previous) is previous:
                result.add(previous.uid)
        parent = locations[parent.uid][2]
    return result


def validate(roots: list[tree.Step]) -> None:
    """Reject target drift and invalid placement instead of clearing a selection."""

    def visit(siblings: list[tree.Step], ancestors: tuple[tree.Step, ...]) -> None:
        for position, step in enumerate(siblings):
            if step.logic_type == "GOBACKTO":
                attributes = step.raw.get("flowLogicDefinition", {}).get(
                    "attributes", {}
                )
                if not isinstance(attributes, dict):
                    raise CapabilityError(
                        "Go Back To placement attributes are malformed."
                    )
                allowed = str(attributes.get("valid_flowlogic_placements", "")).split(
                    "|"
                )
                if not ancestors or ancestors[-1].logic_type not in allowed:
                    raise CapabilityError(
                        "Go Back To requires a native branching parent."
                    )
                if position != len(siblings) - 1:
                    raise CapabilityError(
                        "Go Back To must be the final step in its branch."
                    )
                if attributes.get("exclude_placement_in_errorhandler") in {
                    True,
                    "true",
                } and any(
                    parent.logic_type == "TOP_LEVEL_CATCH" for parent in ancestors
                ):
                    raise CapabilityError(
                        "Go Back To cannot be placed in the error handler."
                    )
                inputs = step.raw.get("inputs", [])
                if not isinstance(inputs, list) or any(
                    not isinstance(item, dict) for item in inputs
                ):
                    raise CapabilityError("Go Back To target bindings are malformed.")
                selected = [
                    item for item in inputs if item.get("name") == "go_back_to_step"
                ]
                value: Any = selected[0].get("value") if len(selected) == 1 else None
                if (
                    not isinstance(value, str)
                    or not value
                    or selected[0].get("scriptActive")
                    or value not in targets(roots, step)
                ):
                    raise CapabilityError(
                        "Go Back To target is absent or no longer eligible in this structural region. "
                        "Retarget or remove the Go Back To step explicitly."
                    )
            visit(step.children, (*ancestors, step))

    visit(roots, ())
