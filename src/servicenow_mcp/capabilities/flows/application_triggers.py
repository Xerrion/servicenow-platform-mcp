"""Definition-owned Application trigger authoring, without trigger effects.

Studio 27.0.13: catalog constructor 58112-58179; metric/analytics metadata
446804-446834,446895-446997; input/dependency hydration 203551-203585,
204981-205010; REST schema projection 106646-106687,217704-217727;
REST flow interface 184408-184414; path conversion 194278-194298.
"""

import copy
import re
import uuid
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, native, tree, widgets
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import (
    resolve_ref_value,
    validate_identifier,
    validate_sys_id,
)

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver
    from servicenow_mcp.platform.client import ServiceNowClient

SUPPORTED = {
    "email",
    "remote_table_query",
    "rest_async",
    "analytics",
    "metric",
    "sla_task",
}
_PROVIDER = {
    "metric": ("metric_trigger", "sys_metric_trigger_definition", "metric_table"),
    "analytics": (
        "proactive_analytics_trigger",
        "sys_analytics_trigger_definition",
        "event_table",
    ),
}
# Native tree labels fall back to the type token when metadata has no label
# (bundle:21876). No human numeric label is inferred from a type name.
_LABELS = {
    "string": "String",
    "integer": "Integer",
    "boolean": "True/False",
    "float": "float",
    "decimal": "decimal",
    **declarations.LABELS,
}
_SCHEMA_KEYS = {"type", "children"}
_FIELD_KEYS = {"name", "label", "type", "mandatory", "maxsize", "children"}


def _definition(definition: dict[str, Any]) -> None:
    if (
        definition.get("type") not in SUPPORTED
        or definition.get("triggerType", "Application") != "Application"
        or definition.get("userCanRead") is False
        or definition.get("security", {}).get("can_read") is False
    ):
        raise CapabilityError(
            "This is not an installed Application trigger definition.", "UNAVAILABLE"
        )
    validate_sys_id(definition.get("id", ""))
    for section in ("inputs", "outputs"):
        rows = definition.get(section)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CapabilityError(
                "The Application trigger definition is incomplete.", "UNAVAILABLE"
            )
        names = []
        for row in rows:
            parameter = row.get("parameter")
            if (
                not isinstance(parameter, dict)
                or not isinstance(row.get("name"), str)
                or not row["name"]
                or parameter.get("name") != row["name"]
                or parameter.get("type") != row.get("type")
                or not isinstance(parameter.get("attributes", {}), dict)
            ):
                raise CapabilityError(
                    "The Application trigger parameter contract is incomplete.",
                    "UNAVAILABLE",
                )
            names.append(row["name"])
        if len(names) != len(set(names)):
            raise CapabilityError(
                "The Application trigger parameter names are ambiguous.", "UNAVAILABLE"
            )
    if definition["type"] in _PROVIDER:
        name, table, _ = _PROVIDER[definition["type"]]
        provider = data.named(definition["inputs"], name)
        table_input = data.named(definition["inputs"], "table_name")
        if (
            data.value_type(provider) != data.ValueType("reference", table)
            or data.value_type(table_input).kind != "table_name"
        ):
            raise CapabilityError(
                "The native provider/table contract has changed.", "UNAVAILABLE"
            )


def _default(row: dict[str, Any]) -> Any:
    return copy.deepcopy(
        row.get("default_value", data.spec(row).get("defaultValue", ""))
    )


def _value(
    item: dict[str, Any], value: Any, *, default: Any = None, explicit: bool = False
) -> None:
    parameter = data.spec(item)
    kind = parameter["type"]
    if tree.pills(value) or isinstance(value, str) and ("{{" in value or "}}" in value):
        raise CapabilityError(
            "Application trigger configuration requires static values."
        )
    if isinstance(value, dict | list):
        raise CapabilityError(
            "Trigger schemas use interface_schemas, not runtime input values."
        )
    if kind == "conditions":
        if not isinstance(value, str) or len(value) > int(
            parameter.get("maxsize") or 65000
        ):
            raise CapabilityError(
                "Trigger conditions require bounded encoded query text."
            )
        widgets.condition_paths(value)
        return
    # Native catalog constructors preserve their string boolean defaults. New
    # explicit values still cross the ordinary JSON boolean type boundary.
    if (
        kind == "boolean"
        and not explicit
        and value == default
        and isinstance(value, str)
        and value in {"true", "false", "1", "0"}
    ):
        return
    probe = {
        **copy.deepcopy(item),
        "parameter": {**copy.deepcopy(parameter), "readonly": False},
    }
    data.set_value(probe, value)


def _schema_roots(definition: dict[str, Any]) -> set[str]:
    return {
        row["name"]
        for row in definition["inputs"]
        if data.value_type(row).kind in declarations.LABELS
    }


def _allowed(parameter: dict[str, Any], key: str) -> set[str]:
    value: object = parameter.get("attributes", {}).get(key)
    if not isinstance(value, str) or not value:
        raise CapabilityError(
            "The native REST field-type restriction is unavailable.", "UNAVAILABLE"
        )
    result: set[str] = set(value.split(";"))
    if not result <= _LABELS.keys():
        raise CapabilityError(
            "The REST catalog declares a field type without a serializer.",
            "UNAVAILABLE",
        )
    return result


def _schema_field(
    raw: dict[str, Any],
    path: str,
    parent_id: str,
    allowed: set[str],
    nested_allowed: set[str],
    order: int,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or set(raw) - _FIELD_KEYS
        or not {"name", "label", "type"} <= raw.keys()
    ):
        raise CapabilityError(
            "REST schema fields require name, label, type and optional mandatory, maxsize, children."
        )
    name, label, kind = raw["name"], raw["label"], raw["type"]
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,79}", name)
        or not isinstance(label, str)
        or not label.strip()
        or len(label) > 255
        or not isinstance(kind, str)
        or kind not in allowed
    ):
        raise CapabilityError(
            "The REST schema field name, label or native type is invalid."
        )
    full_name = path + "." + name
    if len(full_name.split(".")) > 10:
        raise CapabilityError("REST schemas support at most ten named levels.")
    previous = previous or {}
    identity = previous.get("uiUniqueId") or str(uuid.uuid5(uuid.UUID(parent_id), name))
    type_label = previous.get("type_label") if previous.get("type") == kind else None
    result = {
        **copy.deepcopy(previous),
        "name": full_name,
        "label": label,
        "type": kind,
        "type_label": type_label or _LABELS[kind],
        "parent": parent_id,
        "parentId": parent_id,
        "order": order,
        "uiUniqueId": identity,
        "fieldFacetMap": {
            **copy.deepcopy(previous.get("fieldFacetMap", {})),
            "uiUniqueId": identity,
        },
        "canDrag": True,
        "canDrop": True,
    }
    if "mandatory" in raw:
        if type(raw["mandatory"]) is not bool:
            raise CapabilityError("REST field mandatory must be a JSON boolean.")
        result["mandatory"] = raw["mandatory"]
    else:
        result.setdefault("mandatory", False)
    if "maxsize" in raw:
        if type(raw["maxsize"]) is not int or not 1 <= raw["maxsize"] <= 65000:
            raise CapabilityError("REST field maxsize must be from 1 through 65000.")
        result["maxsize"] = raw["maxsize"]
    children = raw.get("children", [])
    if not isinstance(children, list) or kind not in declarations.LABELS and children:
        raise CapabilityError("Only complex REST schema fields can have children.")
    if kind in declarations.ARRAYS:
        item_type = declarations.ARRAYS[kind]
        old_item = data.children(previous)[0] if previous.get("type") == kind else {}
        item_name = old_item.get("name", name + "_child0").split(".")[-1]
        item_id = declarations.identity(old_item) or str(
            uuid.uuid5(uuid.UUID(identity), "array_item")
        )
        if item_type != "object" and children:
            raise CapabilityError("Primitive REST arrays cannot declare child fields.")
        item_path = full_name + "." + item_name
        item_children = _schema_children(
            children,
            item_path,
            item_id,
            nested_allowed,
            nested_allowed,
            previous.get("children", []),
        )
        result["children"] = [
            {
                **copy.deepcopy(old_item),
                "name": item_path,
                "label": old_item.get("label", label + "_child0"),
                "type": item_type,
                "type_label": old_item.get("type_label", _LABELS[item_type]),
                "parent": identity,
                "parentId": identity,
                "uiUniqueId": item_id,
                "fieldFacetMap": {
                    **copy.deepcopy(old_item.get("fieldFacetMap", {})),
                    "uiUniqueId": item_id,
                },
                "cannotBeDeleted": True,
                "children": item_children,
            }
        ]
    else:
        result["children"] = _schema_children(
            children,
            full_name,
            identity,
            nested_allowed,
            nested_allowed,
            previous.get("children", []),
        )
    if kind not in declarations.LABELS:
        result.pop("children")
    return result


def _schema_children(
    rows: list[dict[str, Any]],
    path: str,
    identity: str,
    allowed: set[str],
    nested_allowed: set[str],
    previous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if (
        len(rows) > 100
        or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            for row in rows
        )
        or len({row["name"] for row in rows}) != len(rows)
    ):
        raise CapabilityError(
            "REST schemas require at most 100 uniquely named fields per object."
        )
    return [
        _schema_field(
            row,
            path,
            identity,
            allowed,
            nested_allowed,
            index,
            previous=next(
                (
                    old
                    for old in previous
                    if old.get("name", "").split(".")[-1] == row["name"]
                ),
                None,
            ),
        )
        for index, row in enumerate(rows, start=1)
    ]


def schema_view(item: dict[str, Any]) -> dict[str, Any]:
    """Expanded REST schema; input parameter children remain definition-owned."""
    parameter = copy.deepcopy(data.spec(item))
    for key in ("type", "type_label", "children", "attributes"):
        if key in item:
            parameter[key] = copy.deepcopy(item[key])
    return parameter


def _schema_mirrors(item: dict[str, Any]) -> None:
    parameter = data.spec(item)
    label = (
        parameter.get("type_label")
        or parameter.get("uiDisplayTypeLabel")
        or parameter["type"]
    )
    expected = {
        "type": parameter["type"],
        "type_label": label,
        "attributes": parameter.get("attributes", {}),
    }
    if any(key in item and item[key] != value for key, value in expected.items()):
        raise CapabilityError(
            "The expanded REST input metadata differs from its parameter mirror.",
            "UNAVAILABLE",
        )


def _schema_registration(parameter: dict[str, Any]) -> None:
    # Reuse declaration validation on a detached view; native REST links parents
    # by UI UUID (InputsManager), whereas subflow declarations use field paths.
    root = declarations.unpack(parameter)

    def parents(node: dict[str, Any], path: str) -> None:
        identity = declarations.identity(node) or node.get("id")
        for child in node.get("children", []):
            synthetic = node.get("type") in declarations.ARRAYS and child.get(
                "cannotBeDeleted"
            )
            if (
                not synthetic
                and identity
                and child.get("parent") not in (None, "", identity)
            ):
                raise CapabilityError(
                    "REST schema parents must reference their owning UI UUID.",
                    "UNAVAILABLE",
                )
            child["parent"] = path
            parents(child, path + "." + child["name"].split(".")[-1])

    parents(root, root["name"])
    declarations.validate(root)


def _apply_schemas(
    trigger: dict[str, Any], definition: dict[str, Any], schemas: dict[str, Any] | None
) -> None:
    if schemas is None:
        return
    if (
        not is_rest(trigger)
        or not isinstance(schemas, dict)
        or not schemas
        or set(schemas) - _schema_roots(definition)
    ):
        raise CapabilityError(
            "interface_schemas requires declared REST trigger input roots."
        )
    for name, raw in schemas.items():
        if (
            not isinstance(raw, dict)
            or set(raw) != _SCHEMA_KEYS
            or not isinstance(raw["children"], list)
        ):
            raise CapabilityError(
                "Each REST interface schema requires type and children."
            )
        item = data.named(trigger["inputs"], name)
        original = data.spec(data.named(definition["inputs"], name))
        if (
            original.get("readonly")
            or original.get("readOnly")
            or original.get("attributes", {}).get("fromTemplate") == "true"
        ):
            raise CapabilityError("The REST schema root is inherited or read-only.")
        allowed = _allowed(original, "customSupportedRootLevelFieldTypes")
        nested = (
            _allowed(original, "customSupportedChildFieldTypes")
            if original.get("attributes", {}).get("customSupportedChildFieldTypes")
            else allowed
        )
        kind = raw["type"]
        # The key/value editors hide their fixed Object root. Their declared
        # supported root types therefore constrain the visible child fields.
        fixed_object = (
            original.get("attributes", {}).get("customSupportedChildFieldTypes") is None
        )
        if not isinstance(kind, str) or (
            kind != "object" if fixed_object else kind not in allowed
        ):
            raise CapabilityError(
                "The REST root type is absent from the current catalog."
            )
        parameter = copy.deepcopy(data.spec(item))
        identity = item.get("uiUniqueId") or original["id"]
        try:
            uuid.UUID(identity)
        except ValueError:
            identity = str(uuid.UUID(hex=identity))
        previous = data.children(schema_view(item))
        old_item = (
            previous[0]
            if parameter["type"] == kind and kind in declarations.ARRAYS
            else {}
        )
        if data.value_type(item).kind == "array.object":
            previous = data.children(previous[0])
        children = (
            []
            if kind in declarations.ARRAYS
            else _schema_children(
                raw["children"],
                name,
                identity,
                allowed if fixed_object else nested,
                nested,
                previous,
            )
        )
        type_label = (
            (parameter.get("type_label") or parameter.get("uiDisplayTypeLabel"))
            if parameter["type"] == kind
            else None
        )
        type_label = type_label or _LABELS[kind]
        declaration = copy.deepcopy(parameter)
        declaration.update(type=kind, type_label=type_label, children=children)
        declaration.setdefault("attributes", {}).update(
            uiType=kind, uiTypeLabel=type_label
        )
        if kind in declarations.ARRAYS:
            child_type = declarations.ARRAYS[kind]
            if child_type != "object" and raw["children"]:
                raise CapabilityError(
                    "Primitive REST array roots cannot have child fields."
                )
            child_id = declarations.identity(old_item) or str(
                uuid.uuid5(uuid.UUID(identity), "array_item")
            )
            item_path = (
                name + "." + old_item.get("name", name + "_child0").split(".")[-1]
            )
            item_children = _schema_children(
                raw["children"], item_path, child_id, nested, nested, previous
            )
            declaration["children"] = [
                {
                    **copy.deepcopy(old_item),
                    "name": item_path,
                    "label": old_item.get("label", item["label"] + "_child0"),
                    "type": child_type,
                    "type_label": old_item.get("type_label", _LABELS[child_type]),
                    "parent": identity,
                    "parentId": identity,
                    "uiUniqueId": child_id,
                    "fieldFacetMap": {
                        **copy.deepcopy(old_item.get("fieldFacetMap", {})),
                        "uiUniqueId": child_id,
                    },
                    "cannotBeDeleted": True,
                    "children": item_children,
                }
            ]
        packed = declarations.pack(declaration)
        parameter.update(type=kind, attributes=copy.deepcopy(packed["attributes"]))
        parameter[
            "uiDisplayTypeLabel" if "uiDisplayTypeLabel" in parameter else "type_label"
        ] = type_label
        item.update(
            type=kind,
            type_label=type_label,
            uiUniqueId=identity,
            children=copy.deepcopy(packed.get("children", [])),
            attributes=copy.deepcopy(packed["attributes"]),
            parameter=parameter,
            canDrag=True,
            canDrop=True,
        )
    _rest_outputs(trigger)


def _rest_outputs(trigger: dict[str, Any]) -> None:
    for output in trigger["outputs"]:
        matches = [
            item for item in trigger["inputs"] if item.get("name") == output["name"]
        ]
        if len(matches) != 1:
            raise CapabilityError(
                "The native REST output has no unambiguous matching input schema.",
                "UNAVAILABLE",
            )
        item = matches[0]
        parameter = data.spec(item)
        item.setdefault("type", parameter["type"])
        item["type_label"] = (
            item.get("type_label")
            or parameter.get("type_label")
            or parameter.get("uiDisplayTypeLabel")
            or parameter["type"]
        )
        item.setdefault("children", copy.deepcopy(parameter.get("children", [])))
        item.setdefault("attributes", copy.deepcopy(parameter.get("attributes", {})))
        item.setdefault(
            "uiUniqueId", parameter.get("uiUniqueId") or parameter.get("id")
        )
        item.setdefault("canDrag", True)
        if parameter["type"] in declarations.LABELS:
            parameter.update(
                type=item["type"], attributes=copy.deepcopy(item["attributes"])
            )
            parameter[
                "uiDisplayTypeLabel"
                if "uiDisplayTypeLabel" in parameter
                else "type_label"
            ] = item["type_label"]
        for key in (
            "type",
            "type_label",
            "children",
            "attributes",
            "uiUniqueId",
            "canDrag",
            "canDrop",
        ):
            if key in item:
                output[key] = copy.deepcopy(item[key])
        output["parameter"] = {
            **copy.deepcopy(output["parameter"]),
            **copy.deepcopy(item["parameter"]),
            **{
                key: copy.deepcopy(item[key])
                for key in (
                    "type",
                    "type_label",
                    "children",
                    "attributes",
                    "canDrag",
                    "canDrop",
                )
                if key in item
            },
        }


def _schema_contract(parameter: dict[str, Any], original: dict[str, Any]) -> None:
    allowed = _allowed(original, "customSupportedRootLevelFieldTypes")
    fixed = not original.get("attributes", {}).get("customSupportedChildFieldTypes")
    nested = (
        _allowed(original, "customSupportedChildFieldTypes") if not fixed else allowed
    )
    kind = parameter.get("type")
    if not isinstance(kind, str) or (
        kind != "object" if fixed else kind not in allowed
    ):
        raise CapabilityError(
            "The saved REST root type is absent from the current catalog.",
            "UNAVAILABLE",
        )
    _schema_registration(parameter)

    def visit(node: dict[str, Any], allowed_types: set[str]) -> None:
        node_kind = node.get("type")
        if node_kind not in allowed_types:
            raise CapabilityError(
                "The saved REST child type is absent from the current catalog.",
                "UNAVAILABLE",
            )
        if any(
            node.get(key)
            for key in (
                "reference",
                "dynamic",
                "data_structure",
                "scriptActive",
                "readonly",
                "readOnly",
            )
        ):
            raise CapabilityError(
                "REST request fields cannot introduce reference, provider or scripted schemas.",
                "UNAVAILABLE",
            )
        if "mandatory" in node and type(node["mandatory"]) is not bool:
            raise CapabilityError(
                "REST field mandatory metadata is not a boolean.", "UNAVAILABLE"
            )
        children = data.children(node)
        if node_kind in declarations.ARRAYS:
            children = (
                data.children(children[0])
                if declarations.ARRAYS[node_kind] == "object"
                else []
            )
        for child in children:
            visit(child, nested)

    children = data.children(parameter)
    if kind in declarations.ARRAYS:
        children = (
            data.children(children[0]) if declarations.ARRAYS[kind] == "object" else []
        )
    for child in children:
        visit(child, allowed if fixed else nested)


def new_instance(
    flow: dict[str, Any],
    definition: dict[str, Any],
    inputs: dict[str, Any],
    schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _definition(definition)
    raw: dict[str, Any] = {
        "flowSysId": flow["id"],
        "name": definition["label"],
        "type": definition["type"],
        "triggerDefinitionId": definition["id"],
        "triggerType": "Application",
        "inputs": [],
    }
    for spec in definition["inputs"]:
        parameter = copy.deepcopy(spec["parameter"])
        raw["inputs"].append(
            {
                "name": spec["name"],
                "label": spec["label"],
                "internalType": spec["type"],
                "value": _default(spec),
                "displayValue": "",
                "valueSysId": "",
                "order": int(spec.get("order") or 100),
                "mandatory": bool(spec.get("mandatory")),
                "parameter": parameter,
                **(
                    {"choiceList": copy.deepcopy(spec["choiceList"])}
                    if "choiceList" in spec
                    else {}
                ),
            }
        )
    raw["inputs"].sort(key=lambda item: item["order"])
    trigger = native.catalog_trigger(raw, definition)
    _changes(trigger, definition, inputs)
    if trigger["type"] == "rest_async":
        for name in _schema_roots(definition):
            item = data.named(trigger["inputs"], name)
            item["children"] = copy.deepcopy(data.spec(item).get("children", []))
        expand_saved_rest(trigger)
    _apply_schemas(trigger, definition, schemas)
    validate(trigger, definition)
    return trigger


def _changes(
    trigger: dict[str, Any], definition: dict[str, Any], changes: dict[str, Any]
) -> None:
    for name, value in changes.items():
        item = data.named(trigger["inputs"], name)
        spec = data.named(definition["inputs"], name)
        if (
            data.spec(spec).get("readonly")
            or trigger["type"] in _PROVIDER
            and name == "table_name"
            or trigger["type"] == "rest_async"
            and name == "path"
        ):
            raise CapabilityError("The trigger input is derived or read-only.")
        if data.value_type(spec).kind in declarations.LABELS:
            raise CapabilityError(
                "REST input schemas must be declared with interface_schemas."
            )
        if trigger["type"] == "rest_async" and name == "display_path":
            if not isinstance(value, str):
                raise CapabilityError("REST display_path requires path text.")
        else:
            _value(item, value, default=_default(spec), explicit=True)
        if data.value_type(spec).kind == "conditions" and value == "":
            value = "^EQ"
        item.update(
            value=copy.deepcopy(value),
            displayValue=value if data.value_type(spec).kind == "conditions" else "",
        )


def validate(trigger: dict[str, Any], definition: dict[str, Any]) -> None:
    _definition(definition)
    if (
        trigger.get("triggerDefinitionId") != definition["id"]
        or trigger.get("type") != definition["type"]
    ):
        raise CapabilityError(
            "The Application trigger definition identity has changed.", "UNAVAILABLE"
        )
    for section in ("inputs", "outputs"):
        actual = trigger.get(section)
        if not isinstance(actual, list) or len(actual) != len(definition[section]):
            raise CapabilityError(
                "The Application trigger parameter list differs from its catalog.",
                "UNAVAILABLE",
            )
        for spec in definition[section]:
            item = data.named(actual, spec["name"])
            expected = (
                native.trigger_parameter(spec["parameter"])
                if section == "inputs" and "remoteSysId" in trigger
                else spec["parameter"]
            )
            parameter = data.spec(item)
            if trigger["type"] == "rest_async" and spec["name"] in _schema_roots(
                definition
            ):
                input_item = data.named(trigger["inputs"], spec["name"])
                _schema_mirrors(input_item)
                original_input = data.spec(input_item)
                base_input = data.spec(data.named(definition["inputs"], spec["name"]))
                ignored = {
                    "type",
                    "type_label",
                    "uiDisplayTypeLabel",
                    "attributes",
                    "canDrag",
                    "canDrop",
                }
                if {
                    key: value
                    for key, value in original_input.items()
                    if key not in ignored
                } != {
                    key: value
                    for key, value in native.trigger_parameter(base_input).items()
                    if key not in ignored
                }:
                    raise CapabilityError(
                        "The REST schema changed catalog-owned parameter metadata.",
                        "UNAVAILABLE",
                    )
                attributes = original_input.get("attributes", {})
                if any(
                    attributes.get(key) != value
                    for key, value in base_input.get("attributes", {}).items()
                    if key not in {"uiType", "uiTypeLabel"}
                ):
                    raise CapabilityError(
                        "The REST schema field restrictions differ from the current catalog.",
                        "UNAVAILABLE",
                    )
                _schema_contract(schema_view(input_item), base_input)
                if section == "outputs":
                    projected = copy.deepcopy(trigger)
                    projected["outputs"] = [
                        copy.deepcopy(row) for row in definition["outputs"]
                    ]
                    _rest_outputs(projected)
                    if parameter != data.spec(
                        data.named(projected["outputs"], spec["name"])
                    ):
                        raise CapabilityError(
                            "REST trigger inputs and output schemas disagree.",
                            "UNAVAILABLE",
                        )
            elif parameter != expected:
                raise CapabilityError(
                    "The saved Application trigger schema differs from the current catalog.",
                    "UNAVAILABLE",
                )
            if section == "inputs":
                if item.get("scriptActive"):
                    raise CapabilityError(
                        "Scripted trigger configuration is unavailable.", "UNAVAILABLE"
                    )
                if trigger["type"] == "rest_async" and spec["name"] == "display_path":
                    continue
                if data.value_type(spec).kind in declarations.LABELS:
                    if item.get("value") not in (None, ""):
                        raise CapabilityError(
                            "REST trigger schema roots cannot contain runtime values."
                        )
                else:
                    _value(item, item.get("value"), default=_default(spec))


def update(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    changes: dict[str, Any],
    schemas: dict[str, Any] | None = None,
) -> None:
    validate(trigger, definition)
    if not changes and not schemas:
        raise CapabilityError("update_trigger requires inputs or interface_schemas.")
    condition_names = {
        row["name"]
        for row in definition["inputs"]
        if data.value_type(row).kind == "conditions"
    }
    source_names = {
        row["name"]
        for row in definition["inputs"]
        if data.value_type(row).kind == "table_name"
    }
    if trigger["type"] in _PROVIDER:
        source_names.add(_PROVIDER[trigger["type"]][0])
    if (
        any(
            name in changes
            and changes[name] != data.named(trigger["inputs"], name)["value"]
            for name in source_names
        )
        and not condition_names <= changes.keys()
    ):
        raise CapabilityError(
            "Changing the trigger source requires explicit condition inputs; no condition is cleared implicitly."
        )
    if condition_names & changes.keys() and any(
        metadata.get("predicates") or metadata.get("query_parse", {}).get("predicates")
        for metadata in (
            trigger.get("metadata", {}),
            trigger.get("additionalMetadata", {}),
        )
    ):
        raise CapabilityError(
            "Parsed trigger condition metadata requires its native adapter.",
            "UNAVAILABLE",
        )
    _changes(trigger, definition, changes)
    _apply_schemas(trigger, definition, schemas)
    validate(trigger, definition)


def _revision(row: dict[str, Any]) -> None:
    value = resolve_ref_value(row.get("sys_mod_count"))
    if not (
        type(value) is int
        and value >= 0
        or isinstance(value, str)
        and value.isdecimal()
    ):
        raise CapabilityError(
            "The Application trigger metadata revision is unreadable.", "UNAVAILABLE"
        )


async def _table(
    name: str, client: ServiceNowClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_identifier(name)
    check_table_access(name)
    check_table_access("sys_db_object")
    rows = (
        await client.query_records(
            "sys_db_object", f"name={name}", fields=["name", "label", "sys_id"], limit=2
        )
    )["records"]
    if len(rows) != 1 or rows[0].get("name") != name or not rows[0].get("label"):
        raise CapabilityError(
            "The Application trigger table is absent, ambiguous or unreadable.",
            "UNAVAILABLE",
        )
    validate_sys_id(rows[0]["sys_id"])
    descriptor = await client.studio_table_descriptor(name)
    validate_identifier(descriptor.get("displayField", ""))
    return rows[0], descriptor


async def prepare(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    client: ServiceNowClient,
    *,
    resolver: PillResolver,
    flow: dict[str, Any] | None = None,
    authored: bool = False,
) -> dict[str, Any]:
    validate(trigger, definition)
    evidence: dict[str, Any] = {"references": {}, "tables": {}, "providers": {}}
    for item in trigger["inputs"]:
        parameter, value = data.spec(item), item["value"]
        if parameter["type"] not in {"reference", "glide_list", "records"} or not value:
            continue
        table = parameter.get("reference")
        if not isinstance(table, str) or not table:
            raise CapabilityError(
                "The trigger reference table is unreadable.", "UNAVAILABLE"
            )
        validate_identifier(table)
        check_table_access(table)
        qualifier = parameter.get("ref_qual", "")
        if not isinstance(qualifier, str) or any(
            marker in ("^" + qualifier).lower()
            for marker in ("javascript:", "^nq", "^orderby", "^groupby")
        ):
            raise CapabilityError(
                "The trigger reference qualifier requires a dynamic metadata resolver.",
                "UNAVAILABLE",
            )
        identities = value.split(",")
        if len(identities) > 100:
            raise CapabilityError(
                "Trigger reference validation supports at most 100 identities.",
                "UNAVAILABLE",
            )
        for identity in identities:
            validate_sys_id(identity)
            rows = (
                await client.query_records(
                    table,
                    f"sys_id={identity}" + (f"^{qualifier}" if qualifier else ""),
                    fields=["sys_id", "sys_mod_count"],
                    limit=2,
                )
            )["records"]
            if len(rows) != 1 or rows[0].get("sys_id") != identity:
                raise CapabilityError(
                    "The trigger reference is absent, unreadable or excluded by its qualifier."
                )
            _revision(rows[0])
            evidence["references"][f"{table}.{identity}"] = rows[0]
        item.update(
            reference=table, reference_display=parameter.get("reference_display", "")
        )
    if trigger["type"] in _PROVIDER:
        source, provider_table, provider_field = _PROVIDER[trigger["type"]]
        identity = data.named(trigger["inputs"], source)["value"]
        table_input = data.named(trigger["inputs"], "table_name")
        if identity:
            fields = [
                "sys_id",
                "sys_mod_count",
                provider_field,
                provider_field + ".name",
            ]
            if trigger["type"] == "analytics":
                fields.append("encoded_query")
            provider = await client.get_record(provider_table, identity, fields=fields)
            if resolve_ref_value(provider.get("sys_id")) != identity:
                raise CapabilityError(
                    "The native trigger provider identity is unreadable.", "UNAVAILABLE"
                )
            _revision(provider)
            table_name = resolve_ref_value(provider.get(provider_field + ".name"))
            validate_identifier(table_name or "")
            if not authored and table_input["value"] != table_name:
                raise CapabilityError(
                    "The saved trigger table differs from its current provider metadata.",
                    "UNAVAILABLE",
                )
            table_input["value"] = table_name
            evidence["providers"][source] = provider
            if trigger["type"] == "analytics":
                condition = data.named(trigger["inputs"], "condition")
                # The native suggestion is adopted only for an empty default;
                # explicitly authored queries are never overwritten on reread.
                if condition["value"] in ("", None):
                    suggested = resolve_ref_value(provider.get("encoded_query"))
                    if not isinstance(suggested, str):
                        raise CapabilityError(
                            "The analytics provider condition is unreadable.",
                            "UNAVAILABLE",
                        )
                    condition.update(
                        value=suggested or "^EQ", displayValue=suggested or "^EQ"
                    )
        elif table_input["value"]:
            if not authored:
                raise CapabilityError(
                    "A derived trigger table cannot survive removal of its provider."
                )
            table_input.update(
                value="",
                displayValue="",
                reference="",
                reference_display="",
                valueSysId="",
                displayField="",
            )
    for item in trigger["inputs"]:
        parameter = data.spec(item)
        if parameter["type"] != "table_name" or not item["value"]:
            continue
        name = item["value"]
        record, descriptor = await _table(name, client)
        evidence["tables"][name] = {"record": record, "descriptor": descriptor}
        item.update(
            reference=name,
            reference_display=record["label"],
            displayValue=record["label"],
            displayField=descriptor["displayField"],
            valueSysId=record["sys_id"],
        )
        if parameter.get("reference"):
            reference_table = parameter["reference"]
            validate_identifier(reference_table)
            check_table_access(reference_table)
            rows = (
                await client.query_records(
                    reference_table,
                    f"name={name}",
                    fields=["sys_id", "name", "sys_mod_count"],
                    limit=2,
                )
            )["records"]
            if len(rows) != 1 or rows[0].get("name") != name:
                raise CapabilityError(
                    "The selected table is absent from the native table reference."
                )
            _revision(rows[0])
            evidence["references"][f"{reference_table}.{name}"] = rows[0]
    for item in trigger["inputs"]:
        parameter = data.spec(item)
        if parameter["type"] != "conditions":
            continue
        if item["value"] == "":
            item.update(value="^EQ", displayValue="^EQ")
        table = parameter.get("reference")
        dependency = parameter.get("dependent_on")
        if dependency:
            table = data.named(trigger["inputs"], dependency)["value"]
        paths = widgets.condition_paths(item["value"])
        if paths and not table:
            raise CapabilityError(
                "Trigger conditions require their native reference table."
            )
        if table:
            check_table_access(table)
            item.update(data_format=table, table=table, reference=table)
        for path in paths:
            await resolver._field(
                "condition." + ".".join(path), "Condition", str(table), path
            )
    if trigger["type"] == "rest_async":
        evidence.update(
            await prepare_saved_rest(
                trigger, definition, client, flow=flow, authored=authored
            )
        )
    evidence["dictionary"] = {
        f"{table}.{column}": row
        for (table, column), row in resolver.dictionary_cache.items()
    }
    validate(trigger, definition)
    return evidence


def validate_path_input(trigger: dict[str, Any], display_input: dict[str, Any]) -> str:
    if data.value_type(display_input).kind != "string":
        raise CapabilityError(
            "The native REST display-path control is not a string.", "UNAVAILABLE"
        )
    display = display_input["value"]
    if not isinstance(display, str) or re.search(r"\s", tree.PILL.sub("", display)):
        raise CapabilityError(
            "REST paths cannot contain whitespace outside path parameter pills."
        )
    maximum = data.spec(display_input).get("maxsize")
    if maximum and len(display) > int(maximum):
        raise CapabilityError(
            "The REST display path exceeds its declared maximum length."
        )
    expected_prefix = (trigger.get("label") or trigger["name"]) + "_1.path_parameters."
    pills = [match.group(1) for match in tree.PILL.finditer(display)]
    if len(set(pills)) != len(pills):
        raise CapabilityError("REST paths cannot repeat a path parameter pill.")
    for pill in pills:
        if not pill.startswith(expected_prefix) or "." in pill[len(expected_prefix) :]:
            raise CapabilityError(
                "REST paths accept only this trigger's declared path parameter pills."
            )
        data.child(
            data.named(trigger["outputs"], "path_parameters"),
            pill[len(expected_prefix) :],
        )
    converted = tree.PILL.sub(
        lambda match: "{" + match.group(1).split(".")[-1] + "}", display
    )
    if (
        "{{" in converted
        or "}}" in converted
        or re.search(r"[^/]\{\{|\}\}[^/]", display)
    ):
        raise CapabilityError("REST path parameters require complete path segments.")
    return converted


async def prepare_saved_rest(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    client: ServiceNowClient,
    *,
    flow: dict[str, Any] | None,
    authored: bool = False,
) -> dict[str, Any]:
    """Bind declared native REST path controls, including saved-template controls."""
    if not is_rest(trigger):
        return {}
    names = {row["name"] for row in definition.get("inputs", [])}
    evidence = {}
    if "base_path" in names:
        if flow is None:
            raise CapabilityError(
                "REST trigger metadata requires the owning flow header.", "UNAVAILABLE"
            )
        base = data.named(trigger["inputs"], "base_path")
        if data.value_type(base).kind != "string":
            raise CapabilityError(
                "The native REST base-path control is not a string.", "UNAVAILABLE"
            )
        info = {
            "scopeId": flow["scope"],
            "flowName": flow["name"],
            "basePath": {"value": base["value"]},
        }
        path = await client.studio_rest_trigger_base_path(info)
        if not authored and base["value"] != path:
            raise CapabilityError(
                "The saved REST base path differs from its native metadata.",
                "UNAVAILABLE",
            )
        base.update(value=path, displayValue=path)
        evidence["rest_base_path"] = {**info, "path": path}
    if "display_path" in names:
        display_input = data.named(trigger["inputs"], "display_path")
        converted = validate_path_input(trigger, display_input)
        path_input = data.named(trigger["inputs"], "path")
        if data.value_type(path_input).kind != "string":
            raise CapabilityError(
                "The native REST path control is not a string.", "UNAVAILABLE"
            )
        if not authored and path_input["value"] != converted:
            raise CapabilityError(
                "The saved REST path differs from its declared display path.",
                "UNAVAILABLE",
            )
        path_input.update(value=converted, displayValue=converted)
    return evidence


def saved_derived_input(trigger: dict[str, Any], name: str) -> bool:
    return is_rest(trigger) and name in {"base_path", "path"}


def saved_path_input(trigger: dict[str, Any], name: str) -> bool:
    return is_rest(trigger) and name == "display_path"


def is_rest(trigger: dict[str, Any]) -> bool:
    """Native isRestTrigger predicate, separate from installed catalog support."""
    category: object = trigger.get("triggerType")
    kind: object = trigger.get("type")
    return isinstance(category, str) and (
        category == "REST"
        or category == "Application"
        and isinstance(kind, str)
        and kind in {"rest_async", "rest_sync"}
    )


def owns_interface(trigger: dict[str, Any]) -> bool:
    kind: object = trigger.get("type")
    return is_rest(trigger) or isinstance(kind, str) and kind == "external_trigger"


def interfaces(trigger: dict[str, Any]) -> list[dict[str, Any]]:
    """createFlowInputs uses REST output rows, not configurable runtime values."""
    return copy.deepcopy(trigger["outputs"]) if owns_interface(trigger) else []


def _shape(row: dict[str, Any]) -> Any:
    kind = data.value_type(row)
    parameter = data.spec(row)
    constraints = tuple(
        (key, parameter.get(key))
        for key in (
            "mandatory",
            "readonly",
            "ref_qual",
            "defaultValue",
            "maxsize",
            "choices",
        )
    )
    return (
        kind.kind,
        kind.reference,
        constraints,
        tuple(
            (child["name"].split(".")[-1], _shape(child))
            for child in data.children(row)
        ),
    )


def _output_path(trigger: dict[str, Any], path: str) -> dict[str, Any]:
    segments = path.split(".")
    row = data.named(trigger["outputs"], segments[0])
    for segment in segments[1:]:
        row = data.child(row, segment)
    return row


def _owned_projection(
    previous: dict[str, Any], original: dict[str, Any], proposed: dict[str, Any]
) -> dict[str, Any]:
    """Keep declaration-owned extensions when its source schema is regenerated."""
    result = copy.deepcopy(proposed)
    for key, value in previous.items():
        if key == "id" or key not in original and key not in result:
            result[key] = copy.deepcopy(value)
        elif (
            isinstance(value, dict)
            and isinstance(original.get(key), dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _owned_projection(value, original[key], result[key])
        elif (
            key == "children"
            and isinstance(value, list)
            and isinstance(result.get(key), list)
        ):
            old_children = {row["name"]: row for row in original.get(key, [])}
            previous_children = {row["name"]: row for row in value}
            result[key] = [
                _owned_projection(
                    previous_children[row["name"]], old_children[row["name"]], row
                )
                if row["name"] in old_children and row["name"] in previous_children
                else row
                for row in result[key]
            ]
    return result


def reconcile_interfaces(
    before: dict[str, Any],
    after: dict[str, Any],
    triggers: list[dict[str, Any]],
    roots: list[tree.Step],
    *,
    catalog_before: list[dict[str, Any]] | None = None,
    catalog_after: list[dict[str, Any]] | None = None,
) -> None:
    """Regenerate source-owned REST/external or independently proven catalog interfaces.

    Static trigger parameters remain catalog-owned. Request schemas are authored
    declarations, so surviving root/default/transform/script consumers must be
    checked before omission or retargeting; nothing is cleared to make it fit.
    """
    old: dict[str, Any] | None = next(
        (row for row in before["triggerInstances"] if owns_interface(row)), None
    )
    new: dict[str, Any] | None = next(
        (row for row in triggers if owns_interface(row)), None
    )
    if catalog_before is not None:
        catalog_trigger = next(
            (
                row
                for row in before["triggerInstances"]
                if row.get("type") == "service_catalog"
            ),
            None,
        )
        if catalog_trigger is None or old is not None:
            raise CapabilityError(
                "The previous catalog interface ownership proof is inconsistent.",
                "UNAVAILABLE",
            )
        old = {**catalog_trigger, "outputs": catalog_before}
    if catalog_after is not None:
        catalog_trigger = next(
            (row for row in triggers if row.get("type") == "service_catalog"), None
        )
        if catalog_trigger is None or new is not None:
            raise CapabilityError(
                "The proposed catalog interface ownership proof is inconsistent.",
                "UNAVAILABLE",
            )
        new = {**catalog_trigger, "outputs": catalog_after}
    if old is None and new is None:
        return
    check_table_access("sys_hub_flow_input")
    if old is None and before["inputs"]:
        raise CapabilityError(
            "Replacing a non-REST flow interface requires its native ownership adapter.",
            "UNAVAILABLE",
        )
    if old is not None:
        if len(before["inputs"]) != len(old["outputs"]) or any(
            _shape(data.named(before["inputs"], row["name"])) != _shape(row)
            for row in old["outputs"]
        ):
            raise CapabilityError(
                "The saved REST interface differs from its trigger-owned output schema.",
                "UNAVAILABLE",
            )
        old_name: object = old.get("label") or old.get("name")
        new_name: object = (new.get("label") or new.get("name")) if new else ""
        if (
            not isinstance(old_name, str)
            or not old_name
            or not isinstance(new_name, str)
            or new is not None
            and not new_name
        ):
            raise CapabilityError(
                "The trigger interface owner has no readable native label.",
                "UNAVAILABLE",
            )
        old_label = old_name + "_1."
        new_label = new_name + "_1." if new else ""
        values = [
            after.get("pharmacy"),
            after["inputs"],
            after["outputs"],
            after["flowVariables"],
            *[step.raw for step in tree.walk(roots)],
        ]
        changed = (
            new is None
            or old_label != new_label
            or any(
                not any(
                    row["name"] == other["name"] and _shape(row) == _shape(other)
                    for other in new["outputs"]
                )
                for row in old["outputs"]
            )
        )
        for pill in tree.pills(values):
            if pill.startswith(old_label):
                path = pill[len(old_label) :]
            elif pill.startswith("subflow."):
                path = pill.removeprefix("subflow.")
            else:
                continue
            if not changed:
                continue
            try:
                retained = (
                    new is not None
                    and old_label == new_label
                    and _shape(_output_path(old, path))
                    == _shape(_output_path(new, path))
                )
            except CapabilityError:
                retained = False
            if not retained:
                raise CapabilityError(
                    "A removed or retargeted REST interface is still referenced; update consumers explicitly first."
                )

        def scripts(value: Any) -> bool:
            if isinstance(value, dict):
                script = value.get("script")
                if script and re.search(
                    r"\bfd_data\s*(?:\.\s*(?:trigger|subflow)\b|\[)", str(script)
                ):
                    return True
                return any(scripts(item) for item in value.values())
            return isinstance(value, list) and any(scripts(item) for item in value)

        if changed and scripts(values):
            raise CapabilityError(
                "REST interface changes cannot rewrite inline interface script references.",
                "UNAVAILABLE",
            )
    if catalog_after is not None:
        retained = (
            old is not None
            and len(before["inputs"]) == len(catalog_after)
            and all(
                any(
                    row["name"] == expected["name"] and _shape(row) == _shape(expected)
                    for row in before["inputs"]
                )
                for expected in catalog_after
            )
        )
        after["inputs"] = copy.deepcopy(before["inputs"]) if retained else []
    else:
        after["inputs"] = interfaces(new) if new else []
    if new is not None and old is not None:
        for index, row in enumerate(after["inputs"]):
            previous = next(
                (item for item in before["inputs"] if item["name"] == row["name"]), None
            )
            original = next(
                (item for item in old["outputs"] if item["name"] == row["name"]), None
            )
            if previous is not None and original is not None:
                after["inputs"][index] = _owned_projection(previous, original, row)


def interface_manifests(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    old = {path: row for path, row in declarations.walk(before["inputs"])}
    new = {path: row for path, row in declarations.walk(after["inputs"])}
    created: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    for path, row in new.items():
        identity = declarations.identity(row)
        if (
            identity
            and not row.get("id")
            and (path not in old or declarations.identity(old[path]) != identity)
        ):
            created.append(
                {"collection": "inputs", "name": path, "uiUniqueId": identity}
            )
    for path, row in old.items():
        database_id: object = row.get("id")
        identity = declarations.identity(row)
        if database_id and (
            path not in new or identity != declarations.identity(new[path])
        ):
            if not isinstance(database_id, str):
                raise CapabilityError(
                    "The removed interface declaration identity is unreadable.",
                    "UNAVAILABLE",
                )
            manifest = {"collection": "inputs", "name": path, "id": database_id}
            if identity:
                manifest["uiUniqueId"] = identity
            removed.append(manifest)
    return created, removed


def _wrapped_definition(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        **definition,
        "triggerType": definition["category"],
        **{
            section: [
                {
                    **copy.deepcopy(row),
                    "parameter": copy.deepcopy(row),
                    "default_value": copy.deepcopy(row.get("defaultValue", "")),
                }
                for row in definition[section]
            ]
            for section in ("inputs", "outputs")
        },
    }


def apply_saved_schemas(
    trigger: dict[str, Any], definition: dict[str, Any], schemas: dict[str, Any] | None
) -> None:
    if schemas is None:
        return
    if not isinstance(schemas, dict) or not schemas or not is_rest(trigger):
        raise CapabilityError(
            "Only native REST trigger interfaces accept interface_schemas."
        )
    for name in schemas:
        parameter = data.spec(data.named(trigger["inputs"], name))
        if (
            parameter.get("readonly")
            or parameter.get("readOnly")
            or parameter.get("attributes", {}).get("fromTemplate") == "true"
        ):
            raise CapabilityError(
                "Inherited or read-only REST schema roots cannot be edited."
            )
    _apply_schemas(trigger, _wrapped_definition(definition), schemas)


def validate_saved_schema(item: dict[str, Any], expected: dict[str, Any]) -> None:
    actual = data.spec(item)
    _schema_mirrors(item)
    if (
        expected.get("readonly")
        or expected.get("readOnly")
        or expected.get("attributes", {}).get("fromTemplate") == "true"
    ):
        baseline = copy.deepcopy(expected)
        baseline.setdefault("children", [])
        baseline.setdefault("attributes", {})
        baseline.setdefault(
            "type_label", expected.get("uiDisplayTypeLabel") or expected["type"]
        )
        if schema_view(item) != baseline:
            raise CapabilityError(
                "Inherited or read-only REST schemas differ from their saved template.",
                "UNAVAILABLE",
            )
        return
    ignored = {
        "type",
        "type_label",
        "uiDisplayTypeLabel",
        "attributes",
        "canDrag",
        "canDrop",
    }
    if {key: value for key, value in actual.items() if key not in ignored} != {
        key: value for key, value in expected.items() if key not in ignored
    }:
        raise CapabilityError(
            "The saved REST schema changed template-owned parameter metadata.",
            "UNAVAILABLE",
        )
    attributes = actual.get("attributes", {})
    if any(
        attributes.get(key) != value
        for key, value in expected.get("attributes", {}).items()
        if key not in {"uiType", "uiTypeLabel"}
    ):
        raise CapabilityError(
            "The saved REST schema changed template-owned field restrictions.",
            "UNAVAILABLE",
        )
    _schema_contract(schema_view(item), expected)


def expand_saved_rest(trigger: dict[str, Any]) -> None:
    if is_rest(trigger):
        _rest_outputs(trigger)


def projected_saved_outputs(
    trigger: dict[str, Any], expected_outputs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    projected = copy.deepcopy(trigger)
    projected["outputs"] = copy.deepcopy(expected_outputs)
    if is_rest(trigger):
        _rest_outputs(projected)
    return projected["outputs"]


async def output_schema(
    trigger: dict[str, Any], output_name: str, client: ServiceNowClient
) -> dict[str, Any]:
    from servicenow_mcp.capabilities.flows import saved_triggers

    if saved_triggers.is_saved(trigger):
        snapshot = await saved_triggers.read(trigger["triggerDefinitionId"], client)
        saved_triggers.validate(trigger, snapshot["definition"])
        return copy.deepcopy(data.spec(data.named(trigger["outputs"], output_name)))
    categories = await client.studio_definitions("triggers")
    matches = [
        definition
        for category in categories
        for definition in category.get("triggers", {}).values()
        if definition.get("type") == trigger["type"]
    ]
    if len(matches) != 1:
        raise CapabilityError(
            "The current trigger catalog is absent or ambiguous.", "UNAVAILABLE"
        )
    definition = {**matches[0], "triggerType": "Application"}
    validate(trigger, definition)
    return copy.deepcopy(data.spec(data.named(trigger["outputs"], output_name)))
