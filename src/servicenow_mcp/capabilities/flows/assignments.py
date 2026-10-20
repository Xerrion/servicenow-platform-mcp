"""Native Assign Subflow Outputs rows, with no implicit declaration changes.

Studio 27: initialState_itemToAssign (221685), two-column rows (293955-294228),
updateInstance/setOutputsToAssignToInstance (207223-207332), field mirror
(150454-150481), and complete-save output serialization (23462-23469).
"""

import copy
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, fields, tree
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
    if declared.get("type") not in fields.INTERFACE_TYPES or declared.get("parent"):
        raise CapabilityError(
            "Output assignment requires a supported top-level declaration.",
            "UNAVAILABLE",
        )
    if declared.get("readonly") or declared.get("readOnly"):
        raise CapabilityError("The output declaration is read-only.")
    return declared


def _children(row: dict[str, Any]) -> list[dict[str, Any]]:
    children = row.get("children", [])
    if not isinstance(children, list) or any(
        not isinstance(child, dict) or not isinstance(child.get("name"), str)
        for child in children
    ):
        raise CapabilityError(
            "Complex assignment children are incomplete.", "UNAVAILABLE"
        )
    return children


def _object_schema(declared: dict[str, Any]) -> dict[str, dict[str, Any]]:
    children = data.children(declared)
    names = [child["name"].split(".")[-1] for child in children]
    if len(names) != len(set(names)):
        raise CapabilityError(
            "The Object declaration has ambiguous child names.", "UNAVAILABLE"
        )
    return dict(zip(names, children, strict=True))


def _item_schema(declared: dict[str, Any]) -> dict[str, Any]:
    children = data.children(declared)
    kind = data.value_type(declared).kind
    if (
        kind not in declarations.ARRAYS
        or len(children) != 1
        or data.value_type(children[0]).kind != declarations.ARRAYS[kind]
    ):
        raise CapabilityError(
            "The Array declaration requires one supported item schema.", "UNAVAILABLE"
        )
    return children[0]


def _check_type(row: dict[str, Any], declared: dict[str, Any]) -> str:
    if data.value_type(row) != data.value_type(declared):
        raise CapabilityError(
            "The saved assignment type differs from its output declaration."
        )
    kind = data.value_type(declared).kind
    if kind not in fields.INTERFACE_TYPES:
        raise CapabilityError(
            "This output value requires another serializer.", "UNAVAILABLE"
        )
    if any(
        row.get(key) for key in ("scriptActive", "readonly", "readOnly", "read_only")
    ) or any(
        data.spec(declared).get(key)
        for key in ("scriptActive", "readonly", "readOnly", "read_only")
    ):
        raise CapabilityError(
            "Scripted or read-only output values cannot be edited.", "UNAVAILABLE"
        )
    return kind


def _read(row: dict[str, Any], declared: dict[str, Any]) -> Any:
    """Check a complete wire value against the independent current declaration."""
    kind = _check_type(row, declared)
    if kind in declarations.LABELS:
        if row.get("value") != "" or row.get("displayValue", "") != "":
            raise CapabilityError(
                "Complex assignment parents require blank wire values and serialized children.",
                "UNAVAILABLE",
            )
        children = _children(row)
        if kind == "object":
            schema = _object_schema(declared)
            names = [child["name"] for child in children]
            if len(names) != len(set(names)) or set(names) != set(schema):
                raise CapabilityError(
                    "Object assignment children differ from the declared schema."
                )
            return {
                child["name"]: _read(child, schema[child["name"]]) for child in children
            }
        item = _item_schema(declared)
        if any(child["name"] != item["name"].split(".")[-1] for child in children):
            raise CapabilityError(
                "Array assignment item names differ from the declared schema."
            )
        return [_read(child, item) for child in children]
    if _children(row):
        raise CapabilityError("Scalar output values cannot contain child bindings.")
    if "value" not in row:
        raise CapabilityError("An output binding has no stored value.", "UNAVAILABLE")
    probe = {**copy.deepcopy(row), "parameter": copy.deepcopy(data.spec(declared))}
    data.set_value(probe, row["value"])
    return row["value"]


def _write(row: dict[str, Any], declared: dict[str, Any], value: Any) -> None:
    """Serialize the same children/value shape as constructInstanceValuesForArrays."""
    kind = _check_type(row, declared)
    if kind == "object":
        if not isinstance(value, dict):
            raise CapabilityError(
                "Object outputs require a native object. Whole-object pills remain unavailable."
            )
        schema = _object_schema(declared)
        if set(value) - set(schema):
            raise CapabilityError("The Object output value contains undeclared fields.")
        existing = {child["name"]: child for child in _children(row)}
        children = []
        for name, parameter in schema.items():
            binding = {
                **copy.deepcopy(parameter),
                **copy.deepcopy(existing.get(name, {})),
                "name": name,
            }
            if (
                name not in existing
                and data.value_type(parameter).kind in declarations.LABELS
            ):
                binding["children"] = []
            child_kind = data.value_type(parameter).kind
            default = (
                {}
                if child_kind == "object"
                else []
                if child_kind in declarations.ARRAYS
                else parameter.get(
                    "defaultValue", False if child_kind == "boolean" else ""
                )
            )
            _write(binding, parameter, value.get(name, default))
            children.append(binding)
        row.update(value="", displayValue="", children=children)
    elif kind in declarations.ARRAYS:
        if not isinstance(value, list):
            raise CapabilityError(
                "Array outputs require a native array. Whole-array pills remain unavailable."
            )
        parameter = _item_schema(declared)
        existing = _children(row)
        children = []
        for index, element in enumerate(value):
            binding = {
                **copy.deepcopy(parameter),
                **copy.deepcopy(existing[index] if index < len(existing) else {}),
                "name": parameter["name"].split(".")[-1],
            }
            if (
                index >= len(existing)
                and data.value_type(parameter).kind in declarations.LABELS
            ):
                binding["children"] = []
            _write(binding, parameter, element)
            children.append(binding)
        row.update(value="", displayValue="", children=children)
    else:
        probe = {**copy.deepcopy(row), "parameter": copy.deepcopy(data.spec(declared))}
        data.set_value(probe, value)
        row.update(value=probe["value"], displayValue=probe["displayValue"])
        if row.get("children"):
            raise CapabilityError("A scalar output cannot retain undeclared children.")


def _mirror(step: tree.Step) -> None:
    step.raw["data"] = {
        **{item["name"]: item["value"] for item in step.raw["inputs"]},
        **{
            item["name"]: data.output_assignment_mirror(item)
            for item in step.raw["outputsToAssign"]
        },
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
                if declared["type"] in declarations.LABELS:
                    row["children"] = []
                rows.append(row)
            else:
                _read(row, declared)
            _write(row, declared, value)
            # Studio's output wire fields carry the same value (472030).
            # GET may put it in displayValue with value blank (150470-150479).
            if declared["type"] not in declarations.LABELS:
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
            _read(row, declared)


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
    def value(row: dict[str, Any]) -> Any:
        if is_sensitive_field(row["name"]):
            return MASK_VALUE
        kind = data.value_type(row).kind
        if kind == "object":
            return {child["name"]: value(child) for child in _children(row)}
        if kind in declarations.ARRAYS:
            return [value(child) for child in _children(row)]
        return mask_sensitive_fields({"value": data.output_assignment_value(row)})[
            "value"
        ]

    return [
        {
            "step": step.uid,
            "order": step.raw["order"],
            "outputs": [
                {
                    "name": row["name"],
                    "type": data.value_type(row).kind,
                    "value": value(row),
                }
                for row in _rows(step)
            ],
        }
        for step in tree.walk(roots)
        if step.logic_type == KIND
    ]
