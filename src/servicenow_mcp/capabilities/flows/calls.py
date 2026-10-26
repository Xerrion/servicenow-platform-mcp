"""Published call definitions and native step construction for ordered draft edits."""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    data,
    dynamic_inputs,
    input_modes,
    payloads,
    tree,
    widgets,
)
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


def _default_binding(parameter: dict[str, Any]) -> dict[str, Any]:
    """Native readonly values come from definitions, never caller overrides.

    remapInstanceValues/appendExtraProperties:156150-156234;
    getValueOrDefault:235798-235805 preserves explicit false, zero, and empty.
    """
    return {
        **copy.deepcopy(parameter),
        "parameter": copy.deepcopy(parameter),
        "value": copy.deepcopy(data.default_value(parameter)),
        "displayValue": copy.deepcopy(parameter.get("defaultDisplayValue", "")),
        "children": [_default_binding(child) for child in data.children(parameter)],
        "scriptActive": False,
    }


def step(
    flow: dict[str, Any],
    definition: dict[str, Any],
    inputs: dict[str, Any],
    *,
    subflow: bool,
    uid: str,
    schema_evidence: list[dict[str, Any]] | None = None,
    input_scripts: dict[str, str | None] | None = None,
    connection_context: dict[str, str] | None = None,
) -> tree.Step:
    """buildNewActionInstance/buildNewSubflowInstance:156128-156263."""
    effective, inputs = dynamic_inputs.adopt(
        definition, inputs, schema_evidence or [], connection_context=connection_context
    )
    modes = input_modes.scripts(input_scripts)
    parameters = effective.get("inputs")
    if not isinstance(parameters, list) or any(
        not isinstance(p.get("name"), str) for p in parameters
    ):
        raise CapabilityError("The call input schema is incomplete.", "UNAVAILABLE")
    if (set(inputs) | set(modes)) - {p["name"] for p in parameters}:
        raise CapabilityError("The call contains an unknown input.")
    bindings = []
    for parameter in parameters:
        dependency = parameter.get("dependent_on")
        simple_dependency = (
            isinstance(dependency, str)
            and dependency
            and data.value_type(parameter).kind
            in {
                "document_id",
                "field_name",
                "field_list",
                "template_value",
                "conditions",
            }
        )
        if (
            dependency
            and not simple_dependency
            and parameter.get("type") not in dynamic_inputs.MODES
            or parameter.get("dynamic")
            and parameter.get("type") not in dynamic_inputs.MODES
            or data.value_type(parameter).kind == "slushbucket"
            and definition.get("type") != "GETCATALOGVARIABLES"
            and isinstance(
                inputs.get(parameter["name"], data.default_value(parameter)), str
            )
            and bool(inputs.get(parameter["name"], data.default_value(parameter)))
        ):
            raise CapabilityError(
                "Dynamic, dependent, or catalog selection outputs require another insertion adapter.",
                "UNAVAILABLE",
            )
        value = inputs.get(parameter["name"], data.default_value(parameter))
        if input_modes.readonly(parameter):
            if parameter["name"] in inputs or parameter["name"] in modes:
                raise CapabilityError(
                    "Read-only call parameters cannot be set by callers. Their declared defaults or native table dependency supply the value."
                )
            bindings.append(_default_binding(parameter))
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
        input_modes.value(binding, value, scripted=bool(modes.get(parameter["name"])))
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
    result = tree.Step(raw, "subflow" if subflow else "action")
    input_modes.apply(result, modes)
    return result


async def table_dependencies(
    step: tree.Step,
    references: dict[str, str],
    resolver: PillResolver,
    *,
    inputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve table-backed reference/field metadata without changing the schema.

    updateDependentInputs:156348-156370. Read-only table from a reference pill
    uses the native source reference, not a caller override.
    """
    evidence = {}
    rows = step.raw["inputs"] if inputs is None else inputs
    ordered_inputs = sorted(
        rows, key=lambda item: data.value_type(item).kind != "document_id"
    )
    for item in ordered_inputs:
        parameter = data.spec(item)
        dependent = parameter.get("dependent_on")
        if not dependent or data.value_type(item).kind not in {
            "document_id",
            "field_name",
            "field_list",
            "conditions",
        }:
            continue
        name = dependent.removeprefix("inputs.")
        source = data.named(rows, name)
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
        if (
            not isinstance(table, str)
            or not table
            or tree.pills(table)
            or source.get("scriptActive")
        ):
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
        item.update(data_format=table, table=table, reference=table)
        if (
            data.value_type(item).kind == "field_name"
            and item["value"]
            and not item.get("scriptActive")
        ):
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
    """Bind native catalog DTOs without deriving schemas or evaluating defaults.

    collectAllProperties:130997-131039; generateActionDynamicOutputs:207454-207473;
    source filtering:131050-131096; metadata handlers:217777-217850.
    """
    import json

    from servicenow_mcp.capabilities.flows import declarations
    from servicenow_mcp.capabilities.mutations.models import fingerprint
    from servicenow_mcp.validation import resolve_ref_value

    definition = step.raw.get("actionType", {})
    if definition.get("type") != "GETCATALOGVARIABLES":
        return {}
    if (
        step.kind != "action"
        or definition.get("userCanRead") is False
        or definition.get("security", {}).get("can_read") is False
    ):
        raise CapabilityError("The catalog producer is not readable.", "UNAVAILABLE")
    selected = data.named(step.raw.get("inputs"), "catalog_variables")
    parameter = data.spec(selected)
    attributes = parameter.get("attributes")
    if (
        data.value_type(parameter).kind != "slushbucket"
        or not isinstance(attributes, dict)
        or attributes.get("source_table") != "item_option_new"
        or not isinstance(selected.get("value"), str)
        or selected.get("scriptActive")
    ):
        raise CapabilityError(
            "The native catalog selection source is unavailable.", "UNAVAILABLE"
        )
    widgets.validate("slushbucket", selected["value"], parameter)
    ids = widgets.selections(selected["value"], parameter)
    if len({identity for identity, _ in ids}) != len(ids):
        raise CapabilityError(
            "Catalog variable identities are ambiguous.", "UNAVAILABLE"
        )
    include_multirow = attributes.get("include_multirow_variablesets") == "true"
    preserve_selected = attributes.get("preserve_selected") == "true"
    catalog_input = data.named(step.raw["inputs"], "template_catalog_item")
    catalog = catalog_input.get("value")
    if (
        not isinstance(catalog, str)
        or catalog_input.get("scriptActive")
        or data.value_type(catalog_input) != data.ValueType("reference", "sc_cat_item")
    ):
        raise CapabilityError(
            "Catalog variable selection requires a static catalog item."
        )
    if catalog.startswith("{"):
        widgets.reference(catalog, data.spec(catalog_input))
        picker = json.loads(catalog)
        if picker.get("table") not in (None, "", "sc_cat_item"):
            raise CapabilityError("The template picker belongs to another table.")
        catalog = picker["value"]
    validate_sys_id(catalog)
    for table in ("sc_cat_item", "item_option_new", *(table for _, table in ids)):
        check_table_access(table)
    model = flow.get("flowCatalogVariableModelId")
    if model == "":
        model = None
    if model is not None:
        validate_sys_id(model)
    active: list[tuple[str, str]] | None = None
    if preserve_selected:
        selected_variables: dict[str, str] = {}
        for identity, table in ids:
            selected_variables[table] = ",".join(
                filter(None, (selected_variables.get(table), identity))
            )
        metadata = await client.studio_catalog_selection(
            catalog,
            model,
            include_multirow=include_multirow,
            selected_variables=selected_variables,
        )
        available = metadata.get("availableList")
        active_values = metadata.get("activeVariables")
        if not isinstance(active_values, list) or any(
            not isinstance(value, str) for value in active_values
        ):
            raise CapabilityError(
                "Active catalog selection metadata is incomplete.", "UNAVAILABLE"
            )
        active = []
        for value in active_values:
            entries = widgets.selections(value, parameter)
            if len(entries) != 1 or entries[0] in active:
                raise CapabilityError(
                    "Active catalog identities are ambiguous.", "UNAVAILABLE"
                )
            active.extend(entries)
    else:
        available = await client.studio_catalog_variables(
            catalog, model, include_multirow=include_multirow
        )
        metadata = available
    if not isinstance(available, list) or any(
        not isinstance(row, dict) for row in available
    ):
        raise CapabilityError("Catalog selection metadata is malformed.", "UNAVAILABLE")
    outputs = step.raw.get("outputs", [])
    if not isinstance(outputs, list) or any(
        not isinstance(row, dict) for row in outputs
    ):
        raise CapabilityError("Saved catalog outputs are incomplete.", "UNAVAILABLE")

    def source_table(row: dict[str, Any]) -> str:
        table = row.get("table") or "item_option_new"
        if not isinstance(table, str):
            raise CapabilityError(
                "Catalog source table metadata is incomplete.", "UNAVAILABLE"
            )
        validate_identifier(table)
        if "." in table:
            raise CapabilityError(
                "Catalog identities require one native source table.", "UNAVAILABLE"
            )
        check_table_access(table)
        return table

    def validate_schema(row: dict[str, Any], prefix: str = "") -> None:
        schema = data.spec(row)
        name = schema.get("name")
        if (
            not isinstance(name, str)
            or not name
            or any(part in name for part in ("{{", "}}"))
        ):
            raise CapabilityError("Catalog output names are incomplete.", "UNAVAILABLE")
        suffix = name.removeprefix(prefix + ".") if prefix else name
        if "." in suffix:
            raise CapabilityError(
                "Catalog child names contradict their producer.", "UNAVAILABLE"
            )
        path = (
            name if not prefix or name.startswith(prefix + ".") else prefix + "." + name
        )
        if schema.get("parent") and schema["parent"] != prefix:
            raise CapabilityError(
                "Catalog child parents contradict their producer.", "UNAVAILABLE"
            )
        for key in ("attributes", "fieldFacetMap"):
            if key in schema and not isinstance(schema[key], dict):
                raise CapabilityError(
                    "Catalog schema metadata is incomplete.", "UNAVAILABLE"
                )
        kind = data.value_type(schema)
        if (
            kind.kind in {"object", "array", *declarations.ARRAYS}
            and "children" not in schema
        ):
            raise CapabilityError(
                "The complex catalog schema is incomplete.", "UNAVAILABLE"
            )
        if (
            schema.get("userCanRead") is False
            or schema.get("security", {}).get("can_read") is False
        ):
            raise CapabilityError(
                "The catalog output schema is not readable.", "UNAVAILABLE"
            )
        if kind.kind not in widgets.TYPES | declarations.ARRAYS.keys() | {
            "boolean",
            "choice",
            "object",
            "array",
            "glide_date",
            "date",
            "ip_addr",
            "json",
            "xml",
            "password2",
            "mask_code",
        }:
            raise CapabilityError(
                "The catalog output type is unavailable.", "UNAVAILABLE"
            )
        if kind.kind in {
            "reference",
            "document_id",
            "records",
            "glide_list",
            "array.reference",
        }:
            if not kind.reference:
                raise CapabilityError(
                    "A catalog reference schema requires its native table.",
                    "UNAVAILABLE",
                )
            validate_identifier(kind.reference)
            if "." in kind.reference:
                raise CapabilityError(
                    "Catalog reference schemas require one table.", "UNAVAILABLE"
                )
            check_table_access(kind.reference)
        children = data.children(schema)
        if children and kind.kind not in {"object", *declarations.ARRAYS}:
            raise CapabilityError(
                "A scalar catalog schema contains child fields.", "UNAVAILABLE"
            )
        names = [data.spec(child).get("name") for child in children]
        if any(not isinstance(name, str) or not name for name in names):
            raise CapabilityError("Catalog child schemas are ambiguous.", "UNAVAILABLE")
        if len({name.split(".")[-1] for name in names if isinstance(name, str)}) != len(
            names
        ):
            raise CapabilityError("Catalog child schemas are ambiguous.", "UNAVAILABLE")
        if kind.kind in {"choice", "array.choice"} and "choices" in schema:
            choices = schema["choices"]
            if (
                not isinstance(choices, list)
                or any(
                    not isinstance(choice, dict)
                    or not isinstance(choice.get("value"), str)
                    or not isinstance(choice.get("label"), str)
                    for choice in choices
                )
                or len({choice["value"] for choice in choices}) != len(choices)
            ):
                raise CapabilityError(
                    "Catalog choice metadata is incomplete or ambiguous.", "UNAVAILABLE"
                )
        for child in children:
            validate_schema(child, path)

    def same_schema(saved: dict[str, Any], native: dict[str, Any]) -> bool:
        # Saved defaults/display values may reflect the earlier server read. They
        # are not overwritten; the complete new metadata still enters evidence.
        ignored = {
            "defaultValue",
            "defaultDisplayValue",
            "value",
            "displayValue",
            "display_value",
            "reference_display",
        }
        saved = data.spec(saved)
        native = data.spec(native)
        for key, value in native.items():
            if key in ignored:
                continue
            if key == "children":
                rows = saved.get("children", [])
                if (
                    not isinstance(rows, list)
                    or len(rows) != len(value)
                    or any(
                        not isinstance(row, dict) or not same_schema(row, child)
                        for row, child in zip(rows, value, strict=True)
                    )
                ):
                    return False
            elif fingerprint(saved.get(key)) != fingerprint(value):
                return False
        return True

    preserved_metadata = []
    saved_flow = None

    async def preserved_schema(identity: str, table: str) -> dict[str, Any]:
        nonlocal saved_flow
        existing = [row for row in outputs if row.get("id") == identity]
        if active is None or (identity, table) not in active or len(existing) != 1:
            raise CapabilityError(
                "The preserved catalog variable has no independently readable schema.",
                "UNAVAILABLE",
            )
        if saved_flow is None:
            flow_id = flow.get("id")
            if not isinstance(flow_id, str):
                raise CapabilityError(
                    "The saved catalog flow identity is incomplete.", "UNAVAILABLE"
                )
            validate_sys_id(flow_id)
            check_table_access("sys_hub_flow")
            saved_flow = await client.studio_request("flow/" + flow_id, minimal=True)
        if (
            saved_flow.get("id") != flow["id"]
            or saved_flow.get("security", {}).get("can_read") is False
        ):
            raise CapabilityError(
                "The preserved catalog producer is unreadable.", "UNAVAILABLE"
            )
        instances = saved_flow.get("actionInstances")
        if not isinstance(instances, list) or any(
            not isinstance(row, dict) for row in instances
        ):
            raise CapabilityError(
                "The saved catalog producer metadata is incomplete.", "UNAVAILABLE"
            )
        producers = [
            row
            for row in instances
            if all(
                row.get(key) == step.raw.get(key) and row.get(key)
                for key in ("id", "uiUniqueIdentifier", "actionTypeSysId")
            )
        ]
        if (
            len(producers) != 1
            or producers[0].get("actionType", {}).get("type") != "GETCATALOGVARIABLES"
        ):
            raise CapabilityError(
                "Only a preexisting saved catalog output can be preserved.",
                "UNAVAILABLE",
            )
        producer = producers[0]
        if not isinstance(producer.get("outputs"), list) or any(
            not isinstance(row, dict) for row in producer["outputs"]
        ):
            raise CapabilityError(
                "The saved catalog output metadata is incomplete.", "UNAVAILABLE"
            )
        saved_outputs = [
            row for row in producer.get("outputs", []) if row.get("id") == identity
        ]
        saved_selection = data.named(producer.get("inputs"), "catalog_variables")
        saved_value = saved_selection.get("value")
        if not isinstance(saved_value, str):
            raise CapabilityError(
                "The saved catalog selection is incomplete.", "UNAVAILABLE"
            )
        if (
            len(saved_outputs) != 1
            or fingerprint(saved_outputs[0]) != fingerprint(existing[0])
            or source_table(saved_outputs[0]) != table
            or (identity, table)
            not in widgets.selections(saved_value, data.spec(saved_selection))
        ):
            raise CapabilityError(
                "The preserved catalog output differs from the saved server schema.",
                "UNAVAILABLE",
            )
        schema = saved_outputs[0]
        record = await client.get_record(table, identity)
        if not isinstance(record, dict):
            raise CapabilityError(
                "The preserved catalog variable metadata is unreadable.", "UNAVAILABLE"
            )
        revision = resolve_ref_value(record.get("sys_mod_count"))
        catalog_type = data.spec(schema).get("attributes", {}).get("catalogType")
        if (
            resolve_ref_value(record.get("sys_id")) != identity
            or not revision.isdecimal()
            or not isinstance(catalog_type, str)
            or not catalog_type
            or resolve_ref_value(record.get("type")) != catalog_type
            or resolve_ref_value(record.get("name")) != data.spec(schema).get("name")
            or resolve_ref_value(record.get("sys_class_name"))
            not in ("", schema.get("sys_class_name") or table)
        ):
            raise CapabilityError(
                "The preserved catalog variable identity, revision, or type changed.",
                "UNAVAILABLE",
            )
        if table == "sys_flow_cat_variable":
            owner_matches = (
                model
                and saved_flow.get("flowCatalogVariableModelId") == model
                and resolve_ref_value(record.get("flow_catalog_model")) == model
            )
        else:
            template = data.named(producer.get("inputs"), "template_catalog_item").get(
                "value"
            )
            if isinstance(template, str) and template.startswith("{"):
                template = json.loads(template).get("value")
            owners = [
                (key, resolve_ref_value(record.get(key)))
                for key in ("cat_item", "variable_set")
                if resolve_ref_value(record.get(key))
            ]
            owner_matches = bool(owners) and all(
                owner == resolve_ref_value(schema.get(key)) or owner == template
                for key, owner in owners
            )
        reference = data.value_type(schema).reference
        if (
            not owner_matches
            or reference
            and reference
            != (
                resolve_ref_value(record.get("reference"))
                or resolve_ref_value(record.get("list_table"))
            )
        ):
            raise CapabilityError(
                "The preserved catalog variable owner or reference table changed.",
                "UNAVAILABLE",
            )
        preserved_metadata.append(
            {"producer": copy.deepcopy(producer), "variable": copy.deepcopy(record)}
        )
        return schema

    resolved = []
    evidence = []
    for identity, table in ids:
        # collectAllProperties finds by id, not by (id, table): duplicate IDs
        # across tables are therefore ambiguous even if the requested pair exists.
        matches = [row for row in available if row.get("id") == identity]
        if not matches:
            schema = await preserved_schema(identity, table)
        elif len(matches) != 1 or source_table(matches[0]) != table:
            raise CapabilityError(
                "A selected catalog variable is unavailable or ambiguous.",
                "UNAVAILABLE",
            )
        else:
            schema = matches[0]
        if active is not None and (identity, table) not in active:
            raise CapabilityError(
                "A selected catalog variable is inactive or unavailable.", "UNAVAILABLE"
            )
        native_attributes = schema.get("attributes")
        if not isinstance(native_attributes, dict) or not (
            native_attributes.get("child_type") == "object"
            or isinstance(native_attributes.get("catalogType"), str)
            and native_attributes["catalogType"] not in ("", "11")
        ):
            raise CapabilityError(
                "This catalog variable is not selectable in native Studio.",
                "UNAVAILABLE",
            )
        if (
            table == "sys_flow_cat_variable"
            or schema.get("sys_class_name") == "sys_flow_cat_variable"
        ) and (
            not model
            or not any(
                trigger.get("type") == "service_catalog"
                for trigger in flow.get("triggerInstances", [])
            )
        ):
            raise CapabilityError(
                "Flow catalog variables require their Service Catalog trigger and model.",
                "UNAVAILABLE",
            )
        if not include_multirow and table != "item_option_new":
            raise CapabilityError(
                "This catalog selection requires native multi-row source identities.",
                "UNAVAILABLE",
            )
        schema_view = data.catalog_schema(schema)
        validate_schema(schema_view)
        declarations.validate(schema_view)
        existing = [row for row in outputs if row.get("id") == identity]
        if len(existing) > 1:
            raise CapabilityError(
                "Catalog output identities are ambiguous.", "UNAVAILABLE"
            )
        if existing:
            row = existing[0]
            saved_view = data.catalog_schema(row)
            validate_schema(saved_view)
            declarations.validate(saved_view)
            if source_table(row) != table or not same_schema(row, schema):
                raise CapabilityError(
                    "The selected catalog output schema changed.", "UNAVAILABLE"
                )
        elif not changed:
            raise CapabilityError(
                "A saved catalog selection has no matching output schema.",
                "UNAVAILABLE",
            )
        else:
            # Native generation does not run the input binding constructor or
            # substitute blank defaults: its output is the complete source DTO.
            row = schema
        resolved.append(copy.deepcopy(row))
        evidence.append(copy.deepcopy(schema))
    if len({data.spec(row)["name"] for row in resolved}) != len(resolved):
        raise CapabilityError(
            "Selected catalog output names are ambiguous.", "UNAVAILABLE"
        )
    if not changed and [(row.get("id"), source_table(row)) for row in outputs] != ids:
        raise CapabilityError(
            "Catalog selected outputs do not match the saved selection order.",
            "UNAVAILABLE",
        )
    if changed:
        step.raw["outputs"] = resolved
        if resolved:
            selected["value"] = ",".join(
                row["id"] + (":" + source_table(row) if include_multirow else "")
                for row in resolved
            )
    return {
        "catalog": catalog,
        "model": model,
        "schema": evidence,
        "metadata": copy.deepcopy(metadata),
        "preserved_metadata": preserved_metadata,
    }
