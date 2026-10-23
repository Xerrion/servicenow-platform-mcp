"""Fresh Studio REST DTOs. Saved instances never pass through these constructors.

Studio's editor constructors (156128, 221783, 58126) are not REST DTOs.
Binding/parameter fields:471783-471840,472022-472030. REST save:97521-97527,
130432-130460,101697-101712. Complete native forms and defaults are retained in
flow_full_native.json, with independently read definitions in the same fixture.
"""

import copy
import json
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data


def definition_parameter(value: dict[str, Any]) -> dict[str, Any]:
    """The published editor endpoint exposes nullable descriptor slots."""
    result = copy.deepcopy(value)
    for key in (
        "comments",
        "complexObjectValue",
        "dynamic",
        "parent",
        "placeholder",
        "scriptAsJsonString",
        "searchField",
        "uiDisplayType",
        "uiDisplayTypeLabel",
        "defaultDisplayValue",
        "hint",
        "internal_link",
        "script",
    ):
        if key in result and result[key] is None:
            result.pop(key)
    if "children" in result:
        result["children"] = [definition_parameter(row) for row in result["children"]]
    return result


def parameter(value: dict[str, Any]) -> dict[str, Any]:
    result = definition_parameter(value)
    for key in (
        "comments",
        "placeholder",
        "searchField",
        "uiDisplayType",
        "uiDisplayTypeLabel",
    ):
        result.pop(key, None)
    for key in ("dynamic", "internal_link", "defaultDisplayValue"):
        if key in result and result[key] in (None, "", [], {}):
            result.pop(key)
    return result


def binding(row: dict[str, Any]) -> dict[str, Any]:
    spec = data.spec(row)
    display = row.get("displayValue", "")
    if spec.get("type") == "string" and isinstance(row["value"], str):
        display = row["value"]
    elif spec.get("type") == "choice":
        display = next(
            (
                choice["label"]
                for choice in spec.get("choices", [])
                if choice["value"] == row["value"]
            ),
            display,
        )
    return {
        "id": row.get("id", data.spec(row).get("id", "")),
        "name": row["name"],
        "value": copy.deepcopy(row["value"]),
        "displayValue": copy.deepcopy(display),
        "children": [binding(child) for child in row.get("children", [])],
        "parameter": parameter(data.spec(row)),
        "scriptActive": row.get("scriptActive", False),
        **({"script": copy.deepcopy(row["script"])} if row.get("script") else {}),
    }


def _status(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    for key in (
        "sysId",
        "condition",
        "actionStatusMetadataId",
        "label",
        "order",
        "displayField",
        "displayValue",
        "id",
        "value",
        "script",
        "scriptAsJsonString",
        "children",
        "parameter",
    ):
        if key in result and result[key] is None:
            result.pop(key)
    if "parameter" in result:
        result["parameter"] = definition_parameter(result["parameter"])
    for key in ("conditions", "children"):
        if key in result:
            result[key] = [_status(row) for row in result[key]]
    if "status" in result:
        result["status"] = _status(result["status"])
    return result


def action_definition(
    definition: dict[str, Any], flow: dict[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(definition)
    for key in ("inputs", "outputs"):
        result[key] = [definition_parameter(row) for row in result.get(key, [])]
    for key in ("action_status_metadata", "action_status_metadata_template"):
        if key in result:
            result[key] = _status(result[key])
    # Native ActionType defaults, not values inferred from an existing instance.
    for key, value in {
        "domainName": flow.get("domainName", "global"),
        "ghostAction": False,
        "master": False,
        "steps": [],
        "userCanRead": definition.get("security", {}).get("can_read", False),
    }.items():
        result.setdefault(key, value)
    if "label_cache" in result:
        cache = result["label_cache"]
        for row in cache:
            if row.get("choices") is None:
                row.pop("choices", None)
        fields = {
            "name": "fName",
            "label": "fLabel",
            "type": "fType",
            "ref": "fReference",
            "reference_display": "fReferenceDisplay",
            "base_type": "fBaseType",
            "parent_table_name": "fParentTableName",
            "column_name": "fColumnName",
            "attributes": "fAttributes",
        }
        result["labelCacheAsJsonString"] = json.dumps(
            [
                {
                    target: row[source]
                    for source, target in fields.items()
                    if source in row
                }
                for row in cache
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return result


def action(raw: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(raw)
    for key in ("isNew", "isDirty", "errorHandlingInstance"):
        result.pop(key, None)
    result.update(
        displayText="",
        comment="",
        generationSource="",
        uiComponentIndex=0,
        compiledSnapshot="",
        aliasIds=[],
    )
    result["inputs"] = [binding(row) for row in raw["inputs"]]
    result["actionType"] = action_definition(raw["actionType"], flow)
    return result


def logic(raw: dict[str, Any]) -> dict[str, Any]:
    definition = raw["flowLogicDefinition"]
    inputs = [binding(row) for row in raw["inputs"]]
    return {
        "uiUniqueIdentifier": raw["uiUniqueIdentifier"],
        "flowSysId": raw["flowSysId"],
        "type": "flowlogic",
        "definitionId": definition["id"],
        "flowLogicDefinition": copy.deepcopy(definition),
        "quiescence": definition.get("quiescence"),
        "inputs": inputs,
        "flowVariables": [],
        "displayText": "",
        "deleted": False,
        "metadata": {},
        "comment": "",
        "generationSource": "",
        "uiComponentIndex": 0,
        "snapshot": False,
        "outputsToAssign": [],
        "variables": [],
        "errors": [],
        "connectedTo": "",
        "workflowReference": "",
        "workflowInputs": [],
        "workflowInformation": {},
        "decisionTableReference": "",
        "decisionTableInputs": [],
        "dynamicInputs": [],
        "dynamicOutputs": [],
        "decisionTableInformation": {},
        "continue": False,
        "afterTopLevelCatch": False,
        "goBackTo": False,
        "break": False,
        "updateFlowTemplate": False,
        "name": definition["name"]
        + ": "
        + next((r["value"] for r in inputs if r["name"] == "condition"), ""),
        "internalName": "",
    }


def catalog_trigger(raw: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    """Service Catalog wrapper DTO. Output schema remains catalog-owned."""
    inputs = []
    for row in raw["inputs"]:
        spec = copy.deepcopy(row["parameter"])
        if any(
            spec.get(key) not in (None, "")
            for key in ("data_structure", "internal_link", "sys_class_name")
        ):
            raise CapabilityError(
                "The catalog trigger parameter has unsupported structural metadata.",
                "UNAVAILABLE",
            )
        for key in ("type_label", "data_structure", "internal_link", "sys_class_name"):
            spec.pop(key, None)
        spec["uiDisplayTypeLabel"] = row["parameter"].get("type_label", "")
        inputs.append(
            {
                "name": row["name"],
                "value": row["value"],
                "displayValue": row["displayValue"],
                "children": [],
                "parameter": spec,
                "scriptActive": False,
                "label": row["label"],
                "internalType": row["internalType"],
                "fromTemplate": False,
                "order": row["order"],
                "valueSysId": row["valueSysId"],
                "mandatory": row["mandatory"],
                **(
                    {"choiceList": copy.deepcopy(row["choiceList"])}
                    if "choiceList" in row
                    else {}
                ),
            }
        )
    return {
        "flowSysId": raw["flowSysId"],
        "remoteSysId": "",
        "name": raw["name"],
        "inputs": inputs,
        "outputs": [
            {
                "name": s["name"],
                "parameter": copy.deepcopy(s["parameter"]),
                "scriptActive": False,
                "type": s["type"],
                "order": str(s["order"]),
                "label": s["label"],
            }
            for s in definition["outputs"]
        ],
        "displayText": "",
        "deleted": False,
        "metadata": {},
        "comment": "",
        "type": raw["type"],
        "triggerDefinitionId": raw["triggerDefinitionId"],
        "publishedVersion": "",
        "triggerType": raw["triggerType"],
        "hasDynamicOutputs": definition.get("has_dynamic_outputs", False),
        "userCanRead": True,
    }
