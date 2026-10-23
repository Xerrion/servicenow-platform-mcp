"""Published call definitions and native step construction for ordered draft edits."""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, payloads, tree, widgets
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver
    from servicenow_mcp.platform.client import ServiceNowClient


async def definition(
    flow: dict[str, Any], identity: str, *, subflow: bool, client: ServiceNowClient
) -> dict[str, Any]:
    validate_sys_id(identity)
    check_table_access(
        "sys_hub_flow_snapshot" if subflow else "sys_hub_action_type_snapshot"
    )
    if subflow:
        if identity == flow["id"]:
            raise CapabilityError("A flow cannot call itself.")
        check_table_access("sys_hub_flow")
        parent = await client.get_record(
            "sys_hub_flow",
            identity,
            fields=["sys_id", "type", "active", "master_snapshot", "sys_scope"],
        )
        snapshot = parent.get("master_snapshot")
        if isinstance(snapshot, dict):
            snapshot = snapshot.get("value")
        if (
            parent.get("sys_id") != identity
            or parent.get("type") != "subflow"
            or parent.get("active") not in (True, "true")
            or not isinstance(snapshot, str)
        ):
            raise CapabilityError("The subflow has no active published snapshot.")
        validate_sys_id(snapshot)
        result = await client.studio_request(f"flow/{snapshot}", minimal=True)
        if (
            result.get("id") != snapshot
            or result.get("parentFlow") != identity
            or result.get("type") != "subflow"
            or result.get("snapshot") is not True
            or result.get("masterSnapshot") is not True
        ):
            raise CapabilityError(
                "The subflow snapshot identity or publication state is incomplete."
            )
        if (
            result.get("scope") != flow.get("scope")
            and result.get("access") != "public"
        ):
            raise CapabilityError(
                "The subflow is not accessible from the target scope."
            )
    else:
        result = await client.studio_request(f"action/action_types/{identity}")
        if (
            result.get("id") != identity
            or result.get("state") != "published"
            or result.get("is_snapshot") is not True
        ):
            raise CapabilityError(
                "Action insertion requires a published action snapshot."
            )
    if (
        result.get("security", {}).get("can_read") is not True
        or result.get("isFlowEntitled" if subflow else "entitled") is not True
    ):
        raise CapabilityError("The call definition is not readable and entitled.")
    payloads.validate_definition(result, field_name="call")
    return result


def step(
    flow: dict[str, Any],
    definition: dict[str, Any],
    inputs: dict[str, Any],
    *,
    subflow: bool,
    uid: str,
) -> tree.Step:
    """buildNewActionInstance/buildNewSubflowInstance:156128-156263."""
    parameters = definition.get("inputs")
    if not isinstance(parameters, list) or any(
        not isinstance(p.get("name"), str) for p in parameters
    ):
        raise CapabilityError("The call input schema is incomplete.", "UNAVAILABLE")
    if set(inputs) - {p["name"] for p in parameters}:
        raise CapabilityError("The call contains an unknown input.")
    bindings = []
    for parameter in parameters:
        dependency = parameter.get("dependent_on")
        simple_dependency = (
            isinstance(dependency, str)
            and dependency
            and data.value_type(parameter).kind
            in {"document_id", "field_name", "template_value", "conditions"}
        )
        if (
            dependency
            and not simple_dependency
            or parameter.get("dynamic")
            or data.value_type(parameter).kind == "slushbucket"
            and definition.get("type") != "GETCATALOGVARIABLES"
            and bool(inputs.get(parameter["name"], parameter.get("defaultValue", "")))
        ):
            raise CapabilityError(
                "Dynamic, dependent, or catalog selection outputs require another insertion adapter.",
                "UNAVAILABLE",
            )
        value = inputs.get(parameter["name"], parameter.get("defaultValue", ""))
        if parameter.get("readonly"):
            if data.value_type(parameter).kind != "table_name":
                raise CapabilityError(
                    "Read-only call defaults require a dedicated adapter except for native table-name dependencies.",
                    "UNAVAILABLE",
                )
            if parameter["name"] in inputs:
                raise CapabilityError(
                    "Read-only call parameters cannot be set by callers. Their declared defaults or native table dependency supply the value."
                )
            bindings.append(
                {
                    **copy.deepcopy(parameter),
                    "parameter": copy.deepcopy(parameter),
                    "value": value,
                    "displayValue": parameter.get("defaultDisplayValue") or "",
                    "children": [],
                    "scriptActive": False,
                }
            )
            continue
        if (
            data.value_type(parameter).kind == "boolean"
            and parameter["name"] not in inputs
        ):
            value = value in ("true", "1", True)
        binding = {
            **copy.deepcopy(parameter),
            "name": parameter["name"],
            "parameter": copy.deepcopy(parameter),
            "value": "",
            "children": [],
            "scriptActive": False,
        }
        data.set_value(binding, value)
        bindings.append(binding)
    for binding in bindings:
        if data.value_type(binding).kind == "table_name" and binding["value"]:
            table_name = binding["value"]
            if not isinstance(table_name, str):
                raise CapabilityError(
                    "Call table defaults require a native table-name string."
                )
            validate_identifier(table_name)
            if "." in table_name:
                raise CapabilityError(
                    "Call table defaults require a single table name."
                )
            check_table_access(table_name)
    raw = {
        "uiUniqueIdentifier": uid,
        "flowSysId": flow["id"],
        "type": "subflow" if subflow else "action",
        "name": definition["name"],
        "internalName": definition.get(
            "internalName", definition.get("internal_name", "")
        ),
        "inputs": bindings,
        "metadata": {},
        "deleted": False,
        "snapshot": False,
        "isNew": True,
        "isDirty": False,
        "errorHandlingInstance": False,
    }
    if subflow:
        raw.update(
            subflowSysId=definition["id"],
            subFlow=copy.deepcopy(definition),
            waitForCompletion={
                "name": "wait_for_completion",
                "type": "boolean",
                "value": True,
            },
        )
    else:
        raw.update(
            actionTypeSysId=definition["id"],
            actionType=copy.deepcopy(definition),
            parentActionTypeId=definition.get("parent_action", ""),
        )
    return tree.Step(raw, "subflow" if subflow else "action")


async def table_dependencies(
    step: tree.Step, references: dict[str, str], resolver: PillResolver
) -> dict[str, Any]:
    """Resolve table-backed reference/field metadata without changing the schema.

    updateDependentInputs:156348-156370. Read-only table from a reference pill
    uses the native source reference, not a caller override.
    """
    evidence = {}
    ordered_inputs = sorted(
        step.raw["inputs"], key=lambda item: data.value_type(item).kind != "document_id"
    )
    for item in ordered_inputs:
        parameter = data.spec(item)
        dependent = parameter.get("dependent_on")
        if not dependent or data.value_type(item).kind not in {
            "document_id",
            "field_name",
            "conditions",
        }:
            continue
        name = dependent.removeprefix("inputs.")
        source = data.named(step.raw["inputs"], name)
        if data.value_type(source).kind != "table_name":
            raise CapabilityError(
                "The call dependency requires a declared table-name parameter.",
                "UNAVAILABLE",
            )
        table = source["value"]
        reference = references.get(item["name"])
        if not table and data.spec(source).get("readonly") and reference:
            source.update(value=reference, displayValue=reference)
            table = reference
        if not isinstance(table, str) or not table or tree.pills(table):
            raise CapabilityError(
                "The call dependency requires a static resolved table.", "UNAVAILABLE"
            )
        validate_identifier(table)
        if "." in table:
            raise CapabilityError("A dependent input requires one table name.")
        check_table_access(table)
        if data.value_type(item).kind == "conditions":
            evidence[name] = await resolver.static_table(source)
            # Studio updates table input reference metadata before its dependants
            # (bundle:156335-156359). Do not rewrite the declared parameter.
            source.update(
                reference=table, reference_display=source.get("displayValue") or table
            )
        if reference and reference != table:
            raise CapabilityError(
                "The dependent record pill table differs from the resolved table."
            )
        if item.get("scriptActive"):
            raise CapabilityError("Scripted dependent inputs cannot be rebound.")
        item.update(data_format=table, table=table, reference=table)
        if data.value_type(item).kind == "field_name" and item["value"]:
            evidence[name + "." + item["value"]] = await resolver._dictionary(
                table, item["value"]
            )
    return evidence


async def linked_output(
    producer: tree.Step, output: dict[str, Any], resolver: PillResolver
) -> dict[str, Any]:
    """Resolve native internal_link against a proven static table, not query text.

    Studio data-pane projection:62774-62800. The published schema is unchanged.
    """
    parameter = data.spec(output)
    link = parameter.get("internal_link")
    if not link or producer.kind != "action":
        return output
    if parameter.get("type") not in {
        "document_id",
        "reference",
        "records",
        "table_name",
    } or not isinstance(link, str):
        raise CapabilityError(
            "This internally linked output requires another type adapter.",
            "UNAVAILABLE",
        )
    key = "action/" + producer.raw["actionTypeSysId"]
    if key not in resolver.definition_cache:
        if len(resolver.definition_cache) >= 100:
            raise CapabilityError(
                "Linked output validation supports at most 100 definitions.",
                "UNAVAILABLE",
            )
        resolver.definition_cache[key] = await definition(
            resolver.flow,
            producer.raw["actionTypeSysId"],
            subflow=False,
            client=resolver.client,
        )
    current = resolver.definition_cache[key]
    declared = data.named(current.get("outputs"), parameter["name"])
    if any(
        declared.get(k) != parameter.get(k)
        for k in (
            "id",
            "name",
            "type",
            "reference",
            "internal_link",
            "children",
            "attributes",
            "dynamic",
        )
    ):
        raise CapabilityError(
            "The linked output schema differs from its published definition.",
            "UNAVAILABLE",
        )
    source = data.named(producer.raw["inputs"], link)
    current_source = data.named(current.get("inputs"), link)
    if any(
        data.spec(source).get(k) != current_source.get(k)
        for k in ("name", "type", "reference", "dependent_on", "dynamic")
    ):
        raise CapabilityError(
            "The linked table parameter differs from its published definition.",
            "UNAVAILABLE",
        )
    await resolver.static_table(source)
    schema = {
        **copy.deepcopy(parameter),
        "reference": source["value"],
        "type": "reference"
        if parameter["type"] == "document_id"
        else parameter["type"],
    }
    return schema


async def catalog_selection(
    step: tree.Step, flow: dict[str, Any], client: ServiceNowClient, *, changed: bool
) -> dict[str, Any]:
    """Keep selected outputs native and definition-bound; never infer dynamic types.

    collectAllProperties:130997-131039; generateActionDynamicOutputs:207454-207473.
    Only ordinary item_option_new leaf selections are covered here.
    """
    definition = step.raw.get("actionType", {})
    if definition.get("type") != "GETCATALOGVARIABLES":
        return {}
    selected = data.named(step.raw["inputs"], "catalog_variables")
    widgets.validate("slushbucket", selected["value"], data.spec(selected))
    ids = widgets.selections(selected["value"], data.spec(selected))
    catalog = data.named(step.raw["inputs"], "template_catalog_item")["value"]
    if not isinstance(catalog, str):
        raise CapabilityError(
            "Catalog variable selection requires a static catalog item."
        )
    validate_sys_id(catalog)
    for table in ("sc_cat_item", "item_option_new"):
        check_table_access(table)
    available = await client.studio_catalog_variables(
        catalog, flow.get("flowCatalogVariableModelId")
    )
    if len(available) > 100 or any(not isinstance(row, dict) for row in available):
        raise CapabilityError(
            "Catalog selection metadata is malformed or exceeds 100 fields.",
            "UNAVAILABLE",
        )
    outputs = step.raw.get("outputs", [])
    if not isinstance(outputs, list):
        raise CapabilityError("Saved catalog outputs are incomplete.", "UNAVAILABLE")
    resolved = []
    evidence = []
    for identity, table in ids:
        matches = [
            row
            for row in available
            if row.get("id") == identity and row.get("table") == table
        ]
        if len(matches) != 1:
            raise CapabilityError(
                "A selected catalog variable is unavailable or ambiguous.",
                "UNAVAILABLE",
            )
        schema = matches[0]
        if (
            schema.get("children")
            or schema.get("dynamic")
            or data.value_type(schema).kind
            not in {"string", "integer", "boolean", "reference", "choice"}
        ):
            raise CapabilityError(
                "This selected catalog variable requires another schema adapter.",
                "UNAVAILABLE",
            )
        existing = [row for row in outputs if row.get("id") == identity]
        if len(existing) > 1:
            raise CapabilityError(
                "Catalog output identities are ambiguous.", "UNAVAILABLE"
            )
        if existing:
            row = existing[0]
            if any(
                data.spec(row).get(key) != schema.get(key)
                for key in (
                    "name",
                    "type",
                    "reference",
                    "mandatory",
                    "maxsize",
                    "children",
                    "choices",
                )
            ):
                raise CapabilityError(
                    "The selected catalog output schema changed.", "UNAVAILABLE"
                )
        else:
            if not changed:
                raise CapabilityError(
                    "A saved catalog selection has no matching output schema.",
                    "UNAVAILABLE",
                )
            row = {
                **copy.deepcopy(schema),
                "defaultValue": "",
                "defaultDisplayValue": "",
                "value": "",
                "display_value": "",
                "reference_display": "",
                "ref_qual": "",
                "sys_class_name": table,
                "scriptActive": False,
            }
        resolved.append(copy.deepcopy(row))
        # Do not persist or fingerprint session-personal default/display values.
        # The existing schema row remains unchanged in the saved payload.
        evidence.append(
            {
                key: value
                for key, value in schema.items()
                if key
                not in {"defaultValue", "defaultDisplayValue", "display_value", "value"}
            }
        )
    if len({row.get("name") for row in resolved}) != len(resolved):
        raise CapabilityError(
            "Selected catalog output names are ambiguous.", "UNAVAILABLE"
        )
    if not changed and [row.get("id") for row in outputs] != [
        identity for identity, _ in ids
    ]:
        raise CapabilityError(
            "Catalog selected outputs do not match the saved selection order.",
            "UNAVAILABLE",
        )
    if changed:
        step.raw["outputs"] = resolved
    return {"catalog": catalog, "schema": evidence}
