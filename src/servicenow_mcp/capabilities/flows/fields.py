"""Flow-variable and subflow-interface declaration edits.

Source: local designer-bundle.min.js addInputsOutputs (22076-22125),
deleteInputsOutputs (22298-22312), mapProperties (24010-24023), variable
modal (458852-459117), and complete-flow save (99103-99170).
Nested interfaces use native child registration and array item packing.
This adapter never clears dependent bindings.
"""

import copy
import re
import uuid
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    data,
    declarations,
    input_modes,
    target,
    tree,
    widgets,
)
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field, mask_sensitive_fields
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

COLLECTIONS = {"flowVariables", "inputs", "outputs"}
TYPES = {
    "string": "String",
    "integer": "Integer",
    "boolean": "True/False",
    "glide_date_time": "Date/Time",
    "decimal": "Decimal",
    "float": "Floating Point Number",
    "reference": "Reference",
    "json": "JSON",
}
BASE_INTERFACE_TYPES = {
    **TYPES,
    "choice": "Choice",
    "number": "Number",
    **declarations.LABELS,
}
# Additional root types require the current native field_types picker catalog.
INTERFACE_TYPES = {
    **{kind: kind for kind in widgets.TYPES | {"long", "field_list"}},
    **BASE_INTERFACE_TYPES,
}
REFERENCE_TYPES = {
    "reference",
    "array.reference",
    "records",
    "glide_list",
    "document_id",
    "conditions",
    "template_value",
    "field_list",
}
DEPENDENT_TYPES = {"document_id", "conditions", "template_value", "field_list"}
# Source Object child picker: bundle:1222. Root and variable types are distinct.
OBJECT_TYPES = {
    "string",
    "boolean",
    "choice",
    "object",
    "integer",
    "glide_date_time",
    "number",
    *declarations.ARRAYS,
}
SETTINGS = {
    "label",
    "hint",
    "type",
    "reference",
    "mandatory",
    "choices",
    "default_value",
    "choice_source",
    "choice_option",
    "max_rows",
    "reference_qualifier",
    "dependent_on",
    "new_name",
    "reset_structure",
    "maxsize",
}


def collection(edit: dict[str, Any]) -> str:
    value = edit.get("collection")
    if not isinstance(value, str) or value not in COLLECTIONS:
        raise CapabilityError("collection must be inputs, outputs, or flowVariables.")
    return value


def _text(value: Any, limit: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or not empty
        and not value.strip()
    ):
        raise CapabilityError(
            "Declaration text is empty, malformed, or exceeds its length limit."
        )
    return value


def _scalar(item: dict[str, Any]) -> None:
    if (
        item.get("parent")
        or item.get("children")
        or isinstance(item.get("parameter"), dict)
    ):
        raise CapabilityError(
            "Declaration CRUD supports top-level scalar fields only.", "UNAVAILABLE"
        )
    if item.get("type") not in TYPES:
        raise CapabilityError(
            "This declaration type requires another serializer.", "UNAVAILABLE"
        )
    if input_modes.readonly(item) or item.get("scriptActive") in (True, "true"):
        raise CapabilityError("Read-only or scripted declarations cannot be edited.")


def _editable(item: dict[str, Any]) -> None:
    synthetic_array = item.get("type") == "array" and item.get("cannotBeDeleted")
    if (
        item.get("type") not in INTERFACE_TYPES
        and not synthetic_array
        or isinstance(item.get("parameter"), dict)
    ):
        raise CapabilityError(
            "This declaration requires another serializer.", "UNAVAILABLE"
        )
    if input_modes.readonly(item) or item.get("scriptActive") in (True, "true"):
        raise CapabilityError("Read-only or scripted declarations cannot be edited.")
    if item.get("type") in {"choice", "array.choice"}:
        declarations.choices(item)


def _used(
    flow: dict[str, Any], roots: list[tree.Step], section: str, item: dict[str, Any]
) -> bool:
    name = item["name"]
    prefix = "flow_variable" if section == "flowVariables" else "subflow"
    wanted = f"{prefix}.{name}"
    values = [row for key in COLLECTIONS for row in flow[key] if row is not item]
    values.extend([flow.get("pharmacy"), flow.get("triggerInstances")])
    for step in tree.walk(roots):
        raw = step.raw
        values.append(raw)
        if (
            section == "flowVariables"
            and step.logic_type == "SETFLOWVARIABLES"
            and any(
                row.get("name") == name
                for row in raw.get("inputs", []) + raw.get("flowVariables", [])
            )
        ):
            return True
        if section == "outputs" and any(
            row.get("name") == name or name.startswith(str(row.get("name")) + ".")
            for row in raw.get("outputsToAssign", [])
        ):
            return True
    # Definitions can contain nested literal values and transform prescriptions.
    # Scan for exact pills, never substring-match a similarly named declaration.
    if section != "outputs" and any(
        pill == wanted or pill.startswith(wanted + ".") or wanted.startswith(pill + ".")
        for pill in tree.pills(values)
    ):
        return True

    def scripts(value: Any) -> bool:
        if isinstance(value, dict):
            script = value.get("script")
            script_prefix = (
                "flow_var" if section == "flowVariables" else "subflow_inputs"
            )
            if script and re.search(
                rf"\bfd_data\s*(?:\.\s*{script_prefix}\b|\[)", str(script)
            ):
                return True
            source = value.get("sourceId")
            if (
                item.get("id")
                and source == item["id"]
                or item.get("uiUniqueId")
                and value.get("sourceUiUniqueId") == item["uiUniqueId"]
            ):
                return True
            if (
                value.get("dependent_on") in (name, "inputs." + name)
                or value.get("internalLink") == name
                or value.get("internal_link") == name
            ):
                return True
            return any(scripts(child) for child in value.values())
        return isinstance(value, list) and any(scripts(child) for child in value)

    return scripts(values)


async def resolve_types(
    client: ServiceNowClient,
    flow: dict[str, Any],
    edit: dict[str, Any],
    *,
    catalog: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]] | None]:
    """Bind extra root scalar types to the native picker, not a guessed enum.

    Source: bundle:232783-232795,232851-232873; root filter:152699-152724.
    Nested Object and flow-variable pickers retain their defining allowlists.
    """
    if edit.get("op") == "delete_field":
        return {}, None
    kind = edit.get("type")
    if kind is None:
        kind = next(
            (
                node.get("type")
                for path, node in declarations.walk(flow[collection(edit)])
                if path == edit.get("name")
            ),
            None,
        )
    section = collection(edit)
    if section == "flowVariables" and kind not in (
        "glide_date_time",
        "decimal",
        "float",
    ):
        return {}, None
    if (
        kind is None
        or isinstance(kind, str)
        and kind in BASE_INTERFACE_TYPES
        and kind not in {"decimal", "float"}
        and not (section == "flowVariables" and kind == "glide_date_time")
    ):
        return {}, None
    if not isinstance(kind, str) or kind not in INTERFACE_TYPES:
        raise CapabilityError(
            "The native declaration type requires its type-specific authoring configuration.",
            "UNAVAILABLE",
        )
    if edit.get("parent") or "." in str(edit.get("name", "")):
        raise CapabilityError(
            "Studio's Object child type picker excludes this declaration type.",
            "UNAVAILABLE",
        )
    rows = catalog if catalog is not None else await client.studio_field_types()
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 1000
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("value"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", row["value"])
            or not isinstance(row.get("label"), str)
            for row in rows
        )
        or len({row["value"] for row in rows}) != len(rows)
    ):
        raise CapabilityError(
            "Studio returned an incomplete or ambiguous declaration type catalog.",
            "UNAVAILABLE",
        )
    labels = {row["value"]: row["label"] or row["value"] for row in rows}
    if kind not in labels:
        raise CapabilityError(
            "This declaration type is absent from the current native picker.",
            "UNAVAILABLE",
        )
    return labels, rows


def _settings(
    item: dict[str, Any],
    edit: dict[str, Any],
    section: str,
    type_labels: dict[str, str] | None = None,
) -> None:
    if "label" in edit:
        item["label"] = _text(edit["label"], 255)
    if "hint" in edit:
        item["hint"] = _text(edit["hint"], 4000, empty=True)
    if "type" in edit:
        kind = edit["type"]
        allowed = TYPES if section == "flowVariables" else INTERFACE_TYPES
        if not isinstance(kind, str) or kind not in allowed:
            raise CapabilityError(
                "This declaration type is unavailable for the selected collection.",
                "UNAVAILABLE",
            )
        item["type"] = kind
        if (
            kind in {"decimal", "float"}
            or section == "flowVariables"
            and kind == "glide_date_time"
            or section != "flowVariables"
            and kind not in BASE_INTERFACE_TYPES
        ) and kind not in (type_labels or {}):
            raise CapabilityError(
                "The native declaration type must be resolved from the current picker.",
                "UNAVAILABLE",
            )
        item["type_label"] = (type_labels or {}).get(kind, allowed[kind])
    if "reference" in edit:
        reference = edit["reference"]
        if (
            not isinstance(reference, str)
            or reference
            and not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reference)
        ):
            raise CapabilityError("reference must be a table name or empty string.")
        item["reference"] = reference
        item["table"] = reference
        if "reference_display" in item:
            item["reference_display"] = ""
    if "dependent_on" in edit:
        dependency = edit["dependent_on"]
        if (
            section != "inputs"
            or item["type"] not in DEPENDENT_TYPES
            or not isinstance(dependency, str)
            or dependency
            and not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", dependency)
        ):
            raise CapabilityError(
                "dependent_on requires a native dependent input type and a sibling input name or empty string."
            )
        if dependency and item.get("reference"):
            raise CapabilityError(
                "Clear reference explicitly before selecting dependent_on."
            )
        item.update(dependent_on=dependency, use_dependent=bool(dependency))
    if (
        item["type"] in DEPENDENT_TYPES
        and item.get("reference")
        and item.get("dependent_on")
    ):
        raise CapabilityError(
            "Clear dependent_on explicitly before selecting a static reference table."
        )
    if item["type"] in REFERENCE_TYPES - DEPENDENT_TYPES and not item.get("reference"):
        raise CapabilityError(
            "Reference declarations require reference table metadata."
        )
    if item["type"] not in REFERENCE_TYPES and item.get("reference"):
        raise CapabilityError(
            "Non-reference declarations cannot retain a reference table. Supply reference=''."
        )
    if "reference_qualifier" in edit:
        if item["type"] not in {"reference", "glide_list"}:
            raise CapabilityError(
                "reference_qualifier is available for Reference and List declarations only."
            )
        qualifier = _text(edit["reference_qualifier"], 4000, empty=True)
        if qualifier:
            widgets.condition_paths(qualifier)
        if item.get("defaultValue") not in (None, "") and "default_value" not in edit:
            raise CapabilityError(
                "Changing a reference qualifier requires an explicit default_value."
            )
        item["ref_qual"] = qualifier
    if "mandatory" in edit:
        if section != "inputs" or type(edit["mandatory"]) is not bool:
            raise CapabilityError(
                "mandatory is a JSON boolean for subflow inputs only."
            )
        item["mandatory"] = edit["mandatory"]
    if "maxsize" in edit:
        if type(edit["maxsize"]) is not int or not 1 <= edit["maxsize"] <= 65000:
            raise CapabilityError("maxsize must be an integer from 1 through 65000.")
        item["maxsize"] = edit["maxsize"]
    if "max_rows" in edit:
        maximum = edit["max_rows"]
        if item["type"] not in {*declarations.ARRAYS, "array"} or (
            maximum is not None and (type(maximum) is not int or maximum < 1)
        ):
            raise CapabilityError(
                "max_rows requires an Array declaration and a positive integer or null."
            )
        item.setdefault("fieldFacetMap" if item.get("parent") else "attributes", {})[
            "max_rows"
        ] = maximum
    if "choices" in edit:
        if section == "flowVariables" or item["type"] not in {
            "choice",
            "array.choice",
        }:
            raise CapabilityError("choices are supported only for Choice declarations.")
        choices = edit["choices"]
        if not isinstance(choices, list) or any(
            not isinstance(choice, dict)
            or set(choice) - {"label", "value", "order"}
            or not {"label", "value"} <= choice.keys()
            or "order" in choice
            and (type(choice["order"]) is not int or choice["order"] < 0)
            for choice in choices
        ):
            raise CapabilityError(
                "choices must contain label, value, and optional nonnegative integer order."
            )
        if item.get("table") or item.get("columnName"):
            if "default_value" not in edit:
                raise CapabilityError(
                    "Switching choice sources requires an explicit default_value."
                )
            item.update(
                choiceType="1",
                table="",
                columnName="",
                defaultChoices=copy.deepcopy(choices),
            )
        item["choices"] = copy.deepcopy(choices)
    if "choice_option" in edit:
        if (
            item["type"] not in {"choice", "array.choice"}
            or not isinstance(edit["choice_option"], str)
            or edit["choice_option"] not in {"1", "3"}
        ):
            raise CapabilityError(
                "choice_option requires Choice and native dropdown value '1' or '3'."
            )
        item["choiceOption"] = edit["choice_option"]
    if item["type"] in {"choice", "array.choice"}:
        declarations.choices(item)
    if "default_value" in edit:
        if item["type"] in {"array", *declarations.LABELS} - {"array.choice"}:
            raise CapabilityError(
                "Studio exposes defaults on scalar child declarations, not whole Object or Array declarations.",
                "UNAVAILABLE",
            )
        if section == "flowVariables":
            raise CapabilityError(
                "Flow variables are initialized with set_variables, not declaration defaults."
            )
        value = edit["default_value"]
        if item["type"] in {"choice", "array.choice"} and not isinstance(value, str):
            raise CapabilityError(
                "Choice defaults require native string values; use an empty string to clear."
            )
        if (
            item["type"] in DEPENDENT_TYPES
            and not item.get("reference")
            and value not in (None, "")
        ):
            raise CapabilityError(
                "Studio exposes defaults only when the dependent declaration has a static table.",
                "UNAVAILABLE",
            )
        if (
            item["type"] in {"choice", "array.choice"}
            and item.get("choiceOption") == "3"
            and value == ""
        ):
            raise CapabilityError(
                "A dropdown without None requires a nonempty default_value."
            )
        if tree.pills(value):
            raise CapabilityError(
                "Declaration defaults must be static. Use step bindings for data pills."
            )
        if value is not None:
            binding = {**copy.deepcopy(item), "mandatory": False}
            if item["type"] == "array.choice":
                binding["type"] = "choice"
            data.set_value(binding, value)
        item["defaultValue"] = copy.deepcopy(value)
    elif item.get("defaultValue") not in (None, ""):
        if item["type"] in DEPENDENT_TYPES and not item.get("reference"):
            raise CapabilityError(
                "Clear the existing default_value explicitly before selecting a dependent table."
            )
        binding = {**copy.deepcopy(item), "mandatory": False}
        if item["type"] == "array.choice":
            binding["type"] = "choice"
        data.set_value(binding, item["defaultValue"])


def _string_variable_dto(item: dict[str, Any]) -> dict[str, Any]:
    """Construct the native new scalar DTO, not the modal's UI row.

    Source: parameter field map:471783-471833, cleared source state:22246-22254,
    variable label hydration:232973, mapper facets:93420-93431. The complete
    sanitized current GET fixture establishes legacy DTO defaults. Provider and
    UI type are requested explicitly and remain strict during echo/GET checks.
    This applies only to new String flow variables, never existing declarations.
    """
    result = {
        "name": item["name"],
        "label": item["label"],
        "type": "string",
        "type_label": "string",
        "order": 0,
        "maxsize": item.get("maxsize", 0),
        "mandatory": False,
        "readonly": False,
        "extended": False,
        "local": False,
        "children": [],
        "data_structure": "",
        "reference": "",
        "reference_display": "",
        "choiceOption": "",
        "table": "",
        "columnName": "",
        "defaultValue": "",
        "defaultDisplayValue": "",
        "use_dependent": False,
        "dependent_on": "",
        "internal_link": "",
        "show_ref_finder": False,
        "value": "",
        "display_value": "",
        "sys_class_name": "",
        "scriptActive": False,
        "uiUniqueId": item["uiUniqueId"],
        "attributes": {
            "uiUniqueId": item["uiUniqueId"],
            "uiType": "string",
            "uiTypeLabel": TYPES["string"],
            "element_mapping_provider": "com.glide.flow_design.action.data.FlowDesignVariableMapper",
        },
    }
    if item.get("hint"):
        result["hint"] = item["hint"]
    return result


def _string_input_dto(item: dict[str, Any]) -> dict[str, Any]:
    """Fresh subflow input parameter, not an editor modal row.

    Inputs use flowDesignParameter, unlike outputs/variables:472600. Preserve
    the requested constraints and stable UI identity in the native mapper.
    """
    result = _string_variable_dto(item)
    for key in (
        "value",
        "display_value",
        "scriptActive",
        "defaultDisplayValue",
        "internal_link",
    ):
        result.pop(key)
    result.update(
        type_label=TYPES["string"],
        order=item["order"],
        mandatory=item.get("mandatory", False),
        defaultValue=item.get("defaultValue", ""),
        hint=item.get("hint", ""),
        ref_qual="",
    )
    return result


def _array_item(item: dict[str, Any], uid: str) -> dict[str, Any]:
    kind = declarations.ARRAYS[item["type"]]
    identity = str(uuid.uuid5(uuid.UUID(uid), "array_item"))
    child = {
        "name": item["name"] + "." + item["name"].split(".")[-1] + "_child0",
        "label": item["label"] + "_child0",
        "type": kind,
        "type_label": "Array" if kind == "array" else INTERFACE_TYPES[kind],
        "hint": "",
        "maxsize": "",
        "parent": item["name"],
        "parentId": declarations.identity(item),
        "uiUniqueId": identity,
        "fieldFacetMap": {"uiUniqueId": identity},
        "cannotBeDeleted": True,
        "children": [],
    }
    if item["type"] == "array.reference":
        child["reference"] = item["reference"]
    if item["type"] == "array.choice":
        for key in declarations.CHOICE_FIELDS:
            if key in item:
                child[key] = copy.deepcopy(item[key])
    return child


def _rename(item: dict[str, Any], name: str) -> None:
    """Rename native tree registration without changing identities or values."""
    old = item["name"]
    for _, node in declarations.walk([item]):
        if node["name"] == old or node["name"].startswith(old + "."):
            node["name"] = name + node["name"][len(old) :]
        if node.get("parent") == old or str(node.get("parent", "")).startswith(
            old + "."
        ):
            node["parent"] = name + node["parent"][len(old) :]


def edit_field(
    flow: dict[str, Any],
    edit: dict[str, Any],
    roots: list[tree.Step],
    uid: str,
    *,
    choice_metadata: dict[str, Any] | None = None,
    new_identities: set[str] | None = None,
    type_labels: dict[str, str] | None = None,
) -> dict[str, str] | None:
    section = collection(edit)
    if "type" in edit and (
        not isinstance(edit["type"], str)
        or edit["type"]
        not in (TYPES if section == "flowVariables" else INTERFACE_TYPES)
    ):
        raise CapabilityError(
            "This declaration type is unavailable for the selected collection.",
            "UNAVAILABLE",
        )
    if "new_name" in edit and edit.get("op") != "update_field":
        raise CapabilityError("new_name is an update_field setting.")
    if "reset_structure" in edit and (
        edit.get("op") != "update_field" or type(edit["reset_structure"]) is not bool
    ):
        raise CapabilityError(
            "reset_structure is a JSON boolean for update_field only."
        )
    if section != "flowVariables" and (
        flow.get("type") != "subflow" or flow.get("triggerInstances")
    ):
        raise CapabilityError(
            "Interface CRUD requires a subflow owner without a trigger.",
            "UNAVAILABLE",
        )
    rows = flow[section]
    if any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str)
        for row in rows
    ) or len({row["name"] for row in rows}) != len(rows):
        raise CapabilityError(
            "Declaration names are incomplete or ambiguous.", "UNAVAILABLE"
        )
    names_key = "flowVariableNames" if section == "flowVariables" else None
    if (
        names_key
        and names_key in flow
        and flow[names_key] != [row["name"] for row in rows if not row.get("parent")]
    ):
        raise CapabilityError(
            "Flow variable name registration is contradictory.", "UNAVAILABLE"
        )
    name = edit.get("name")
    if not isinstance(name, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,79}(?:\.[a-z][a-z0-9_]{0,79}){0,8}", name
    ):
        raise CapabilityError(
            "Field names require lowercase identifiers, at most 80 characters per path segment."
        )
    name = str(name)
    op = edit["op"]
    parent = None
    root = None
    root_index = None
    parent_path = (
        edit.get("parent") if op == "create_field" else name.rpartition(".")[0]
    )
    if parent_path is not None and not isinstance(parent_path, str):
        raise CapabilityError("parent must be a declaration path.")
    if parent_path is not None:
        parent_path = str(parent_path)
    if op == "create_field" and "." in name:
        raise CapabilityError(
            "create_field uses a local name and optional parent path."
        )
    if parent_path or op != "create_field" and section != "flowVariables":
        if section == "flowVariables":
            raise CapabilityError(
                "Studio restricts flow variables to scalar types.", "UNAVAILABLE"
            )
        root_name = (parent_path or name).split(".")[0]
        original = data.named(rows, root_name)
        root_index = rows.index(original)
        root = declarations.unpack(original)
        declarations.validate(root)
        target_path = (parent_path or name) if op == "create_field" else name
        for ancestor_path, ancestor in declarations.walk([root]):
            if target_path == ancestor_path or target_path.startswith(
                ancestor_path + "."
            ):
                _editable(ancestor)
        if declarations.pack(root) != original:
            raise CapabilityError(
                "The nested declaration cannot round-trip without changing stored metadata.",
                "UNAVAILABLE",
            )
        if parent_path:
            _, parent = declarations.locate([root], parent_path)
            _editable(parent)
            if op == "create_field" and parent.get("type") != "object":
                raise CapabilityError(
                    "Children require an Object parent. Array items are registered automatically."
                )
            rows = parent.setdefault("children", [])
        else:
            rows = [root]
        if op == "create_field":
            assert isinstance(parent_path, str)
            name = parent_path + "." + name
            if len(name.split(".")) > 9:
                raise CapabilityError(
                    "Declaration paths support at most nine segments."
                )
            if _used(flow, roots, section, {"name": name}):
                raise CapabilityError(
                    "The parent schema is in use. Update consumers before adding a child."
                )
    created = None
    if op == "create_field":
        if any(
            row.get("name", "").split(".")[-1] == name.split(".")[-1] for row in rows
        ):
            raise CapabilityError("The declaration name already exists.")
        # addInputsOutputs registers root names, with no flattened child records
        # for scalar types. mapProperties stores uiUniqueId in attributes.
        item: dict[str, Any] = {
            "name": name,
            "label": _text(edit.get("label"), 255),
            "type": "string",
            "type_label": "String",
            "order": max(
                (row.get("order", 0) for row in rows if type(row.get("order")) is int),
                default=0,
            )
            + 1,
            "path": len(rows),
            "parent": parent_path or "",
            "children": [],
            "mandatory": False,
            "hint": "",
            "uiUniqueId": uid,
            "attributes": {
                "uiUniqueId": uid,
                "sourceUiUniqueId": "",
                "sourceType": "",
                "sourceId": "",
            },
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        }
        if "type" not in edit:
            raise CapabilityError("create_field requires type.")
        if choice_metadata is not None:
            item.update(copy.deepcopy(choice_metadata))
        _settings(item, edit, section, type_labels)
        if parent:
            item["parentId"] = declarations.identity(parent)
            item.setdefault("fieldFacetMap", {})["uiUniqueId"] = uid
            if item["type"] not in OBJECT_TYPES:
                raise CapabilityError(
                    "Studio's Object child type picker excludes this declaration type.",
                    "UNAVAILABLE",
                )
        if item["type"] in declarations.LABELS:
            item["children"] = []
        if item["type"] in declarations.ARRAYS:
            item["fieldFacetMap" if parent else "attributes"].setdefault(
                "max_rows", None
            )
            item["children"] = [_array_item(item, uid)]
        if item["type"] == "boolean" and "default_value" not in edit:
            # setDefaultBooleanValue (bundle:15820-15833,232988-232990).
            item["defaultValue"] = False
        if item["type"] != "reference" and item["type"] not in declarations.LABELS:
            # getFlowVariables and generateNestedTree omit scalar children
            # (bundle:232975-232986,21898-21903).
            item.pop("children", None)
        if section == "flowVariables" and item["type"] == "string":
            item = _string_variable_dto(item)
        elif section == "inputs" and item["type"] == "string" and not parent:
            item = _string_input_dto(item)
        rows.append(item)
        created = {"collection": section, "name": name, "uiUniqueId": uid}
    else:
        item = (
            data.named(rows, name)
            if root is None
            else declarations.locate([root], name)[1]
        )
        if section == "flowVariables":
            _scalar(item)
        else:
            _editable(item)
        if any(
            row.get("parent") == name or row["name"].startswith(name + ".")
            for row in flow[section]
            if row is not item
        ):
            raise CapabilityError(
                "Declaration CRUD supports top-level scalar fields without child registrations only.",
                "UNAVAILABLE",
            )
        if op == "delete_field":
            if item.get("cannotBeDeleted"):
                raise CapabilityError(
                    "Array item schemas cannot be deleted independently. Delete the array instead."
                )
            for _, descendant in declarations.walk([item]):
                _editable(descendant)
            if any(
                _used(flow, roots, section, {**node, "name": path})
                for path, node in declarations.walk([item], parent_path or "")
            ):
                raise CapabilityError(
                    "The declaration is still referenced or assigned. Update its consumers before removal."
                )
            rows.remove(item)
        else:
            if not set(edit) & SETTINGS:
                raise CapabilityError("update_field requires a declaration setting.")
            if item.get("cannotBeDeleted") and set(edit) & {
                "mandatory",
                "reference",
                "max_rows",
                "reference_qualifier",
            }:
                raise CapabilityError(
                    "Array item settings support name, label, hint, maxsize, Choice configuration, and scalar defaults.",
                    "UNAVAILABLE",
                )
            type_changed = "type" in edit and edit["type"] != item.get("type")
            shape_changed = type_changed or any(
                key in edit and edit[key] != item.get(key)
                for key in ("reference", "dependent_on")
            )
            if type_changed and item.get("cannotBeDeleted"):
                raise CapabilityError(
                    "Native Array item types are fixed. Change the parent Array type instead.",
                    "UNAVAILABLE",
                )
            if edit.get("reset_structure") and not type_changed:
                raise CapabilityError("reset_structure requires a type change.")
            if (
                type_changed
                and item.get("children")
                and not edit.get("reset_structure")
            ):
                raise CapabilityError(
                    "Changing a populated structure requires reset_structure=true or explicit child removal."
                )
            if "new_name" in edit and (
                not isinstance(edit["new_name"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", edit["new_name"])
            ):
                raise CapabilityError(
                    "new_name must be one lowercase identifier of at most 80 characters."
                )
            renamed = "new_name" in edit and edit["new_name"] != name.split(".")[-1]
            if renamed and (
                declarations.identity(item) not in (new_identities or set())
                and (
                    section == "flowVariables"
                    or not item.get("cannotBeDeleted")
                    and item.get("nameToBeEdited") is not True
                )
            ):
                raise CapabilityError(
                    "Studio makes saved declaration names read-only unless the native name editor is explicitly enabled.",
                    "UNAVAILABLE",
                )
            if renamed and any(
                row is not item
                and row.get("name", "").split(".")[-1] == edit["new_name"]
                for row in rows
            ):
                raise CapabilityError("The declaration name already exists.")
            choices_changed = any(
                key in edit for key in ("choices", "choice_source", "choice_option")
            )
            if (shape_changed or choices_changed or renamed) and any(
                _used(flow, roots, section, {**node, "name": path})
                for path, node in declarations.walk([item], parent_path or "")
            ):
                raise CapabilityError(
                    "The declaration schema is in use. Update or remove consumers before changing it."
                )
            if shape_changed and item.get("value") not in (None, "", [], {}):
                raise CapabilityError(
                    "A declaration with a stored value cannot change type implicitly."
                )
            if (
                parent
                and parent.get("type") == "object"
                and edit.get("type", item["type"]) not in OBJECT_TYPES
            ):
                raise CapabilityError(
                    "Studio's Object child type picker excludes this declaration type.",
                    "UNAVAILABLE",
                )
            if renamed:
                for _, descendant in declarations.walk([item]):
                    _editable(descendant)
            if type_changed:
                for _, descendant in declarations.walk([item]):
                    _editable(descendant)
                for _, descendant in declarations.walk(item.get("children", [])):
                    if (
                        "defaultValue" in descendant
                        and descendant["defaultValue"] is not None
                        or descendant.get("value") not in (None, "", [], {})
                    ):
                        raise CapabilityError(
                            "Clear descendant defaults and stored values explicitly before replacing their structure."
                        )
                if edit["type"] in declarations.LABELS and item.get(
                    "defaultValue"
                ) not in (None, ""):
                    raise CapabilityError(
                        "Clear the scalar default explicitly before changing to a complex type."
                    )
                if item["type"] in {"choice", "array.choice"} and edit["type"] not in {
                    "choice",
                    "array.choice",
                }:
                    for key in declarations.CHOICE_FIELDS:
                        if key != "defaultValue":
                            item.pop(key, None)
                if edit["type"] not in {"reference", "glide_list"}:
                    item.pop("ref_qual", None)
                if edit["type"] not in DEPENDENT_TYPES:
                    item.pop("dependent_on", None)
                    item.pop("use_dependent", None)
                for storage in ("attributes", "fieldFacetMap"):
                    metadata = item.get(storage, {})
                    for key in list(metadata):
                        if (
                            key.startswith("child_")
                            or key == "max_rows"
                            or key == "co_type_name"
                            and edit["type"] not in declarations.LABELS
                        ):
                            metadata.pop(key)
                    if "uiType" in metadata:
                        metadata["uiType"] = edit["type"]
                    if "uiTypeLabel" in metadata:
                        metadata["uiTypeLabel"] = (type_labels or {}).get(
                            edit["type"],
                            (
                                TYPES if section == "flowVariables" else INTERFACE_TYPES
                            ).get(edit["type"], edit["type"]),
                        )
                item["children"] = []
            if choice_metadata is not None:
                item.update(copy.deepcopy(choice_metadata))
            _settings(item, edit, section, type_labels)
            if type_changed:
                if item["type"] in declarations.ARRAYS:
                    item.setdefault(
                        "fieldFacetMap" if item.get("parent") else "attributes", {}
                    ).setdefault("max_rows", None)
                    item["children"] = [_array_item(item, uid)]
                elif item["type"] not in {"object", "reference"}:
                    item.pop("children", None)
            if renamed:
                _rename(
                    item, (parent_path + "." if parent_path else "") + edit["new_name"]
                )
            if item["type"] == "array.reference":
                item["children"][0]["reference"] = item["reference"]
            if (
                item["type"] == "array.choice"
                or parent
                and parent.get("type") == "array.choice"
            ):
                target = (
                    item["children"][0] if item["type"] == "array.choice" else parent
                )
                if not isinstance(target, dict):
                    raise CapabilityError(
                        "Array.Choice parent/item registration is incomplete.",
                        "UNAVAILABLE",
                    )
                for key in declarations.CHOICE_FIELDS:
                    if key in item:
                        target[key] = copy.deepcopy(item[key])
            if (
                shape_changed
                and item["type"] == "boolean"
                and "default_value" not in edit
                and not item.get("defaultValue")
            ):
                item["defaultValue"] = False
    if op != "delete_field" and item.get("dependent_on"):
        dependency = str(item["dependent_on"]).removeprefix("inputs.")
        seen = {item["name"]}
        while dependency:
            if dependency in seen:
                raise CapabilityError(
                    "Dependent declaration inputs cannot form a cycle."
                )
            seen.add(dependency)
            sibling = data.named(flow["inputs"], dependency)
            dependency = str(sibling.get("dependent_on") or "").removeprefix("inputs.")
    if parent is not None and op in {"create_field", "delete_field"}:
        # generateNestedTree serializes sibling declaration order as i + 1
        # (bundle:21846-21848). Do not renumber unrelated declaration roots.
        for position, sibling in enumerate(rows, start=1):
            sibling["order"] = position
    labels = [row.get("label") for row in rows if isinstance(row.get("label"), str)]
    if len(set(labels)) != len(labels):
        raise CapabilityError(
            "Declaration labels must be unique within their collection."
        )
    if names_key and names_key in flow:
        flow[names_key] = [row["name"] for row in rows if not row.get("parent")]
    if root is not None:
        if op == "delete_field" and not parent_path:
            flow[section].pop(root_index)
        else:
            flow[section][root_index] = declarations.pack(root)
    elif op == "create_field" and item["type"] in declarations.LABELS:
        rows[-1] = declarations.pack(item)
    return created


async def qualifier_evidence(
    client: ServiceNowClient, resolver: Any, qualifiers: list[tuple[str, str]]
) -> dict[str, Any]:
    """Validate static qualifier columns and bind full dictionary revisions."""
    for table, qualifier in qualifiers:
        for path in widgets.condition_paths(qualifier):
            await resolver._field(
                "qualifier." + ".".join(path), "Qualifier", table, path
            )
    evidence: dict[str, Any] = {"dictionary": {}, "tables": {}}

    def revision(row: dict[str, Any]) -> None:
        identity, count = (
            target.scalar(row.get("sys_id")),
            target.scalar(row.get("sys_mod_count")),
        )
        if (
            not identity
            or not count
            or not count.isascii()
            or not count.isdecimal()
            or not target.scalar(row.get("sys_updated_on"))
        ):
            raise CapabilityError(
                "Reference qualifier metadata revisions are incomplete.", "UNAVAILABLE"
            )
        validate_sys_id(identity)

    from servicenow_mcp.policy import check_table_access

    for (table, column), resolved in resolver.dictionary_cache.items():
        owner = resolved["name"]
        check_table_access("sys_dictionary")
        rows = (
            await client.query_records(
                "sys_dictionary", f"name={owner}^element={column}", limit=2
            )
        )["records"]
        if (
            len(rows) != 1
            or target.scalar(rows[0].get("name")) != owner
            or target.scalar(rows[0].get("element")) != column
        ):
            raise CapabilityError(
                "Reference qualifier dictionary evidence is absent or ambiguous.",
                "UNAVAILABLE",
            )
        revision(rows[0])
        evidence["dictionary"][table + "." + column] = rows[0]
        seen: set[str] = set()
        while table:
            if table in seen or len(seen) >= 10:
                raise CapabilityError(
                    "Reference qualifier table inheritance is cyclic or exceeds its bound.",
                    "UNAVAILABLE",
                )
            seen.add(table)
            check_table_access("sys_db_object")
            records = (
                await client.query_records(
                    "sys_db_object",
                    f"name={table}",
                    fields=[
                        "sys_id",
                        "sys_mod_count",
                        "sys_updated_on",
                        "name",
                        "super_class",
                        "super_class.name",
                    ],
                    limit=2,
                )
            )["records"]
            if (
                len(records) != 1
                or target.scalar(records[0].get("name")) != table
                or "super_class.name" not in records[0]
            ):
                raise CapabilityError(
                    "Reference qualifier table evidence is incomplete.", "UNAVAILABLE"
                )
            revision(records[0])
            evidence["tables"][table] = records[0]
            table = target.scalar(records[0]["super_class.name"]) or ""
    return evidence


def refresh_assignments(
    roots: list[tree.Step], declaration: dict[str, Any], changed: set[str]
) -> None:
    # Metadata-only edits keep each assignment's opaque parameter settings.
    for step in tree.walk(roots):
        if step.logic_type != "SETFLOWVARIABLES":
            continue
        for key in ("inputs", "flowVariables"):
            for item in step.raw.get(key, []):
                if item.get("name") == declaration["name"] and isinstance(
                    item.get("parameter"), dict
                ):
                    for field in changed & (SETTINGS - {"default_value"}):
                        if field in declaration:
                            item["parameter"][field] = copy.deepcopy(declaration[field])
                            if field in item:
                                item[field] = copy.deepcopy(declaration[field])


def summary(flow: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    def describe(row: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: (
                MASK_VALUE
                if key == "defaultValue"
                and is_sensitive_field(str(row.get("name", "")))
                else mask_sensitive_fields({"value": row[key]})["value"]
            )
            for key in (
                "name",
                "label",
                "type",
                "reference",
                "choices",
                "table",
                "columnName",
                "choiceType",
                "choiceOption",
                "defaultChoices",
                "ref_qual",
                "dependent_on",
                "mandatory",
                "hint",
                "defaultValue",
                "maxsize",
                "uiUniqueId",
            )
            if key in row
        }
        metadata = row.get("fieldFacetMap" if row.get("parent") else "attributes", {})
        if "max_rows" in metadata:
            result["max_rows"] = metadata["max_rows"]
        if row.get("type") in declarations.LABELS:
            try:
                expanded = declarations.unpack(row)
            except CapabilityError:
                result["schema"] = "unavailable"
            else:
                # Descendants are already expanded in this local view.
                result["children"] = [
                    describe_expanded(child) for child in expanded.get("children", [])
                ]
        return result

    def describe_expanded(row: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: (
                MASK_VALUE
                if key == "defaultValue"
                and is_sensitive_field(str(row.get("name", "")))
                else mask_sensitive_fields({"value": row[key]})["value"]
            )
            for key in (
                "name",
                "label",
                "type",
                "reference",
                "choices",
                "defaultValue",
                "table",
                "columnName",
                "choiceType",
                "choiceOption",
                "defaultChoices",
                "ref_qual",
                "dependent_on",
                "hint",
                "maxsize",
                "uiUniqueId",
            )
            if key in row
        }
        metadata = row.get("fieldFacetMap" if row.get("parent") else "attributes", {})
        if "max_rows" in metadata:
            result["max_rows"] = metadata["max_rows"]
        if "children" in row:
            result["children"] = [describe_expanded(child) for child in row["children"]]
        return result

    return {
        section: [describe(row) for row in flow[section]]
        for section in sorted(COLLECTIONS)
    }
