"""Native Assign Subflow Outputs rows, with no implicit declaration changes.

Studio 27: initialState_itemToAssign (221685), two-column rows (293955-294228),
updateInstance/setOutputsToAssignToInstance (207223-207332), field mirror
(150454-150481), and complete-save output serialization (23462-23469).
"""

import copy
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, fields, tree
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field, mask_sensitive_fields

KIND = "ASSIGNSUBFLOWOUTPUTS"
OPS = {"assign_outputs", "clear_output_assignment"}


def require_subflow(flow: dict[str, Any]) -> None:
    if flow.get("type") != "subflow" or flow.get("triggerInstances"):
        raise CapabilityError(
            "Output assignment requires an unpublished triggerless subflow.",
            "UNAVAILABLE",
        )


def _rows(step: tree.Step) -> list[dict[str, Any]]:
    if step.logic_type != KIND:
        raise CapabilityError("The selected step is not Assign Subflow Outputs.")
    rows = step.raw.get("outputsToAssign")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("name"), str)
        or "value" not in row
        for row in rows
    ):
        raise CapabilityError("Output assignment rows are incomplete.", "UNAVAILABLE")
    if len({row["name"] for row in rows}) != len(rows):
        raise CapabilityError("Output assignment targets are ambiguous.", "UNAVAILABLE")
    return rows


def hydrate(roots: list[tree.Step]) -> None:
    """Use the same narrow GET fallback as Studio before editing or pill checks."""
    for step in tree.walk(roots):
        if step.logic_type == KIND:
            for row in _rows(step):
                row["value"] = data.output_assignment_value(row)


def _declaration(flow: dict[str, Any], name: str) -> dict[str, Any]:
    declared = data.named(flow["outputs"], name)
    if (
        declared.get("type") not in fields.TYPES
        or declared.get("parent")
        or declared.get("children")
    ):
        raise CapabilityError(
            "Output assignment currently supports top-level scalar declarations only.",
            "UNAVAILABLE",
        )
    if declared.get("readonly") or declared.get("readOnly"):
        raise CapabilityError("The output declaration is read-only.")
    return declared


def _mirror(step: tree.Step) -> None:
    step.raw["data"] = {
        item["name"]: item["value"]
        for item in step.raw["inputs"] + step.raw["outputsToAssign"]
    }


def update(flow: dict[str, Any], step: tree.Step, edit: dict[str, Any]) -> None:
    require_subflow(flow)
    rows = _rows(step)
    if edit["op"] == "clear_output_assignment":
        name = edit.get("name")
        if not isinstance(name, str) or not name:
            raise CapabilityError(
                "clear_output_assignment requires a nonempty output name."
            )
        row = data.named(rows, name)
        rows.remove(row)
    else:
        values = edit.get("outputs")
        if not isinstance(values, dict) or not values:
            raise CapabilityError("assign_outputs requires a nonempty outputs object.")
        for name, value in values.items():
            declared = _declaration(flow, name)
            row = next((item for item in rows if item["name"] == name), None)
            if row is None:
                # The native row copies the selected declaration, not a fake
                # action input or a JSON-encoded assignment expression.
                row = {
                    **copy.deepcopy(declared),
                    "value": "",
                    "displayValue": "",
                    "scriptActive": False,
                }
                rows.append(row)
            if data.value_type(row) != data.value_type(declared):
                raise CapabilityError(
                    "The saved assignment type differs from its output declaration."
                )
            data.set_value(row, value)
            # Studio's output wire fields carry the same value (472030).
            # GET may put it in displayValue with value blank (150470-150479).
            row["displayValue"] = value
    _mirror(step)


def validate(flow: dict[str, Any], roots: list[tree.Step]) -> None:
    scope = data.Scope(roots)
    for step in tree.walk(roots):
        if step.logic_type != KIND:
            continue
        require_subflow(flow)
        rows = _rows(step)
        if any(
            parent.logic_type in {"FOREACH", "DOUNTIL", "PARALLEL", "PARALLELBLOCK"}
            for parent in scope.parents[step.uid]
        ):
            raise CapabilityError(
                "Output assignments inside loops or parallel branches require another adapter.",
                "UNAVAILABLE",
            )
        for row in rows:
            declared = _declaration(flow, row["name"])
            if data.value_type(row) != data.value_type(declared):
                raise CapabilityError(
                    "The assignment type differs from its output declaration."
                )
            probe = {**copy.deepcopy(row), "parameter": copy.deepcopy(declared)}
            data.set_value(probe, row["value"])


def refresh_metadata(
    roots: list[tree.Step], declaration: dict[str, Any], changed: set[str]
) -> None:
    for step in tree.walk(roots):
        if step.logic_type != KIND:
            continue
        for row in _rows(step):
            if row["name"] == declaration["name"]:
                for key in changed & {"label", "hint", "maxsize"}:
                    data.spec(row)[key] = copy.deepcopy(declaration[key])
                    if key in row:
                        row[key] = copy.deepcopy(declaration[key])


def summary(roots: list[tree.Step]) -> list[dict[str, Any]]:
    return [
        {
            "step": step.uid,
            "order": step.raw["order"],
            "outputs": [
                {
                    "name": row["name"],
                    "type": data.value_type(row).kind,
                    "value": MASK_VALUE
                    if is_sensitive_field(row["name"])
                    else mask_sensitive_fields(
                        {"value": data.output_assignment_value(row)}
                    )["value"],
                }
                for row in _rows(step)
            ],
        }
        for step in tree.walk(roots)
        if step.logic_type == KIND
    ]
