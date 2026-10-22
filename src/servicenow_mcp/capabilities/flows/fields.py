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
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, tree
from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field, mask_sensitive_fields

COLLECTIONS = {"flowVariables", "inputs", "outputs"}
TYPES = {
    "string": "String",
    "integer": "Integer",
    "boolean": "True/False",
    "reference": "Reference",
    "json": "JSON",
}
INTERFACE_TYPES = {**TYPES, **declarations.LABELS}
SETTINGS = {
    "label",
    "hint",
    "type",
    "reference",
    "mandatory",
    "default_value",
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
    if any(
        item.get(key) for key in ("readonly", "readOnly", "read_only", "scriptActive")
    ):
        raise CapabilityError("Read-only or scripted declarations cannot be edited.")


def _editable(item: dict[str, Any]) -> None:
    if item.get("type") not in INTERFACE_TYPES or isinstance(
        item.get("parameter"), dict
    ):
        raise CapabilityError(
            "This declaration requires another serializer.", "UNAVAILABLE"
        )
    if any(
        item.get(key) for key in ("readonly", "readOnly", "read_only", "scriptActive")
    ):
        raise CapabilityError("Read-only or scripted declarations cannot be edited.")


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
            if value.get("dependent_on") == name or value.get("internalLink") == name:
                return True
            return any(scripts(child) for child in value.values())
        return isinstance(value, list) and any(scripts(child) for child in value)

    return scripts(values)


def _settings(item: dict[str, Any], edit: dict[str, Any], section: str) -> None:
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
        item["type_label"] = allowed[kind]
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
    if item["type"] == "reference" and not item.get("reference"):
        raise CapabilityError(
            "Reference declarations require reference table metadata."
        )
    if item["type"] != "reference" and item.get("reference"):
        raise CapabilityError(
            "Non-reference declarations cannot retain a reference table. Supply reference=''."
        )
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
    if "default_value" in edit:
        if item["type"] in declarations.LABELS:
            raise CapabilityError(
                "Complex declaration defaults require another value adapter.",
                "UNAVAILABLE",
            )
        if section == "flowVariables":
            raise CapabilityError(
                "Flow variables are initialized with set_variables, not declaration defaults."
            )
        value = edit["default_value"]
        if tree.pills(value):
            raise CapabilityError(
                "Declaration defaults must be static. Use step bindings for data pills."
            )
        binding = {**copy.deepcopy(item), "mandatory": False}
        data.set_value(binding, value)
        item["defaultValue"] = value
    elif item.get("defaultValue") not in (None, ""):
        binding = {**copy.deepcopy(item), "mandatory": False}
        data.set_value(binding, item["defaultValue"])


def edit_field(
    flow: dict[str, Any], edit: dict[str, Any], roots: list[tree.Step], uid: str
) -> dict[str, str] | None:
    section = collection(edit)
    if section != "flowVariables" and (
        flow.get("type") != "subflow" or flow.get("triggerInstances")
    ):
        raise CapabilityError(
            "Interface CRUD requires an unpublished subflow without a trigger.",
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
        _settings(item, edit, section)
        if parent:
            item["parentId"] = declarations.identity(parent)
            item["fieldFacetMap"] = {"uiUniqueId": uid}
            if item["type"] in {"reference", "json"}:
                raise CapabilityError(
                    "Studio's nested Object fields do not support direct Reference or JSON declarations.",
                    "UNAVAILABLE",
                )
        if item["type"] in declarations.LABELS:
            item["children"] = []
        if item["type"] in declarations.ARRAYS:
            item["fieldFacetMap" if parent else "attributes"]["max_rows"] = None
            child_type = declarations.ARRAYS[item["type"]]
            child_uid = str(uuid.uuid5(uuid.UUID(uid), "array_item"))
            item["children"] = [
                {
                    "name": name + "." + name.split(".")[-1] + "_child0",
                    "label": item["label"] + "_child0",
                    "type": child_type,
                    "type_label": INTERFACE_TYPES[child_type],
                    "hint": "",
                    "maxsize": "",
                    "parent": name,
                    "parentId": uid,
                    "uiUniqueId": child_uid,
                    "fieldFacetMap": {"uiUniqueId": child_uid},
                    "cannotBeDeleted": True,
                    "children": [],
                }
            ]
        if item["type"] == "boolean" and "default_value" not in edit:
            # setDefaultBooleanValue (bundle:15820-15833,232988-232990).
            item["defaultValue"] = False
        if item["type"] != "reference" and item["type"] not in declarations.LABELS:
            # getFlowVariables and generateNestedTree omit scalar children
            # (bundle:232975-232986,21898-21903).
            item.pop("children", None)
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
            if item.get("cannotBeDeleted") and set(edit) & {"mandatory", "reference"}:
                raise CapabilityError(
                    "Array item settings support label, hint, maxsize, and scalar default_value only.",
                    "UNAVAILABLE",
                )
            shape_changed = any(
                key in edit and edit[key] != item.get(key)
                for key in ("type", "reference")
            )
            if shape_changed and (
                item.get("type") in declarations.LABELS
                or edit.get("type") in declarations.LABELS
                or item.get("cannotBeDeleted")
            ):
                raise CapabilityError(
                    "Change complex structures with explicit child create/delete operations. Array item types are fixed.",
                    "UNAVAILABLE",
                )
            if shape_changed and _used(flow, roots, section, {**item, "name": name}):
                raise CapabilityError(
                    "The declaration type is in use. Update or remove consumers before changing it."
                )
            if shape_changed and item.get("value") not in (None, ""):
                raise CapabilityError(
                    "A declaration with a stored value cannot change type implicitly."
                )
            if (
                parent
                and edit.get("type") in {"reference", "json"}
                and edit["type"] != item.get("type")
            ):
                raise CapabilityError(
                    "Studio's nested Object fields do not support direct Reference or JSON declarations.",
                    "UNAVAILABLE",
                )
            _settings(item, edit, section)
            if (
                shape_changed
                and item["type"] == "boolean"
                and "default_value" not in edit
                and not item.get("defaultValue")
            ):
                item["defaultValue"] = False
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
                "mandatory",
                "hint",
                "defaultValue",
                "maxsize",
                "uiUniqueId",
            )
            if key in row
        }
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
            key: row[key]
            for key in ("name", "label", "type", "hint", "maxsize", "uiUniqueId")
            if key in row
        }
        if "children" in row:
            result["children"] = [describe_expanded(child) for child in row["children"]]
        return result

    return {
        section: [describe(row) for row in flow[section]]
        for section in sorted(COLLECTIONS)
    }
