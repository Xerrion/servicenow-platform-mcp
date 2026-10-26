"""Saved-trigger picker adapter, owned by the returned native definition.

Studio 27.0.13: picker GET/selection 228646-228654,478245-478265;
constructor 227216-227329; v1 exclusion 93865-93866,227146-227147;
inherited input read-only rule 336955; template save flags 99125-99142.
No recurrence field names or wall-clock/time-zone conversions are inferred.
"""

import copy
import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    application_triggers,
    data,
    declarations,
    tree,
)
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import (
    resolve_ref_value,
    validate_identifier,
    validate_sys_id,
)

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

TABLE = "sys_hub_trigger_template"
_FIELDS = [
    "sys_id",
    "sys_class_name",
    "trigger_definition",
    "trigger_values",
    "template_properties",
    "sys_updated_on",
    "sys_mod_count",
]


def is_saved(trigger: dict[str, Any]) -> bool:
    table = trigger.get("triggerTable")
    return (
        trigger.get("fromTemplate") is True
        or isinstance(table, str)
        and table == TABLE
        or any(
            data.spec(row).get("attributes", {}).get("fromTemplate") == "true"
            for row in trigger.get("inputs", [])
            if isinstance(row, dict)
        )
    )


def require_version(flow: dict[str, Any]) -> None:
    # getFlowVersion defaults a missing version to v1 (bundle:62133).
    version = flow.get("version") or "1"
    if not isinstance(version, str) or version == "1":
        raise CapabilityError(
            "Saved triggers require a flow version supported by the native trigger picker; version 1 excludes saved triggers.",
            "UNAVAILABLE",
        )


def _json_field(value: Any, empty: Any) -> Any:
    if value == "":
        return empty
    if not isinstance(value, str):
        raise CapabilityError(
            "The saved trigger template metadata is unreadable.", "UNAVAILABLE"
        )
    try:
        return json.loads(value)
    except ValueError:
        raise CapabilityError(
            "The saved trigger template metadata is not native JSON.", "UNAVAILABLE"
        ) from None


def _definition(definition: dict[str, Any], template_id: str) -> None:
    if (
        definition.get("id") != template_id
        or not isinstance(definition.get("type"), str)
        or not definition["type"]
        or not isinstance(definition.get("name") or definition.get("label"), str)
        or not (definition.get("name") or definition.get("label"))
        or not isinstance(definition.get("category"), str)
        or not isinstance(definition.get("templateProperties"), dict)
        or not isinstance(definition.get("triggerValues"), list)
        or definition.get("recordClassName", TABLE) != TABLE
        or definition.get("userCanRead") is False
        or definition.get("security", {}).get("can_read") is False
    ):
        raise CapabilityError(
            "The selected saved-trigger picker contract is unavailable.", "UNAVAILABLE"
        )
    validate_sys_id(definition.get("triggerDefinition", ""))
    for collection in ("inputs", "outputs"):
        rows = definition.get(collection)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CapabilityError(
                "The saved-trigger parameter schema is unreadable.", "UNAVAILABLE"
            )
        names = []
        for row in rows:
            if (
                not isinstance(row.get("name"), str)
                or not row["name"]
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or not isinstance(row.get("type"), str)
                or not row["type"]
                or not isinstance(row.get("attributes", {}), dict)
                or not isinstance(row.get("children", []), list)
            ):
                raise CapabilityError(
                    "The saved-trigger parameter schema is incomplete.", "UNAVAILABLE"
                )
            if (
                row.get("attributes", {}).get("fromTemplate") == "true"
                and "defaultValue" not in row
            ):
                raise CapabilityError(
                    "The inherited native default is unreadable.", "UNAVAILABLE"
                )
            names.append(row["name"])
        if len(names) != len(set(names)):
            raise CapabilityError(
                "The saved-trigger parameter names are ambiguous.", "UNAVAILABLE"
            )


def _revision(row: dict[str, Any]) -> bool:
    value = row.get("sys_mod_count")
    return (
        type(value) is int
        and value >= 0
        or isinstance(value, str)
        and value.isdecimal()
    )


async def read(template_id: str, client: ServiceNowClient) -> dict[str, Any]:
    """Bind both independent template storage and the effective picker schema."""
    validate_sys_id(template_id)
    check_table_access(TABLE)
    check_table_access("sys_hub_trigger_definition")
    row = await client.get_record(TABLE, template_id, fields=_FIELDS)
    definition = await client.studio_saved_trigger(template_id)
    _definition(definition, template_id)
    if (
        not _revision(row)
        or not isinstance(row.get("sys_updated_on"), str)
        or not row["sys_updated_on"]
    ):
        raise CapabilityError(
            "The independent saved-template revision is unreadable.", "UNAVAILABLE"
        )
    if (
        row.get("sys_id") != template_id
        or row.get("sys_class_name") != TABLE
        or resolve_ref_value(row.get("trigger_definition"))
        != definition["triggerDefinition"]
        or _json_field(row.get("trigger_values"), []) != definition["triggerValues"]
        or _json_field(row.get("template_properties"), {})
        != definition["templateProperties"]
    ):
        raise CapabilityError(
            "The saved template and effective trigger definition disagree.",
            "UNAVAILABLE",
        )
    source_definition = await client.get_record(
        "sys_hub_trigger_definition",
        definition["triggerDefinition"],
        fields=["sys_id", "sys_mod_count", "sys_updated_on"],
    )
    if (
        source_definition.get("sys_id") != definition["triggerDefinition"]
        or not _revision(source_definition)
        or not isinstance(source_definition.get("sys_updated_on"), str)
        or not source_definition["sys_updated_on"]
    ):
        raise CapabilityError(
            "The saved trigger's underlying definition revision is unreadable.",
            "UNAVAILABLE",
        )
    return {
        "definition": definition,
        "template": row,
        "source_definition": source_definition,
    }


def _parameter(raw: dict[str, Any], seed: str, parent: str = "") -> dict[str, Any]:
    result = copy.deepcopy(raw)
    identity = raw.get("id") or raw.get("fieldFacetMap", {}).get("uiUniqueId")
    if not identity:
        identity = str(
            uuid.UUID(hex=hashlib.sha256(seed.encode()).hexdigest()[:32], version=4)
        )
    result["uiUniqueId"] = identity
    if parent:
        result["parent"] = parent
    if raw.get("children"):
        result["children"] = [
            _parameter(child, f"{seed}.{index}", identity)
            for index, child in enumerate(raw["children"])
        ]
    return result


def _default(parameter: dict[str, Any]) -> Any:
    value = parameter.get("defaultValue", "")
    # JavaScript's constructor treats empty Objects/Arrays as truthy. Python's
    # `or` would erase exactly the opaque native values this adapter must retain.
    if (
        value is None
        or value is False
        or value == ""
        or type(value) in {int, float}
        and value == 0
    ):
        return ""
    return copy.deepcopy(value)


def _bindings(
    rows: list[dict[str, Any]], template_id: str, inherited: bool
) -> list[dict[str, Any]]:
    result = []
    for raw in rows:
        parameter = _parameter(raw, f"{template_id}.{raw['name']}")
        item = {
            "name": raw["name"],
            "label": raw.get("label", raw["name"]),
            "internalType": raw["type"],
            "type": raw["type"],
            "value": _default(raw),
            "displayValue": "",
            "valueSysId": "",
            "order": raw.get("order", 100),
            "mandatory": raw.get("mandatory", False),
            "parameter": parameter,
            "uiUniqueId": raw["id"],
            "children": copy.deepcopy(parameter.get("children", [])),
        }
        if "choiceList" in raw:
            item["choiceList"] = copy.deepcopy(raw["choiceList"])
        if raw["type"] == "choice":
            item["choiceList"] = copy.deepcopy(raw.get("choices", []))
        if inherited and raw.get("attributes", {}).get("fromTemplate"):
            item["templateValue"] = copy.deepcopy(raw.get("defaultValue"))
            item["fromTemplate"] = True
        if inherited:
            item.update(field_name=raw["name"], data_format="")
            if raw["type"] == "table_name":
                item.update(
                    reference=item["value"], reference_display=item["displayValue"]
                )
            elif raw["type"] == "reference":
                item.update(
                    reference=parameter.get("reference", ""),
                    reference_display=parameter.get("reference_display", ""),
                )
            elif raw["type"] == "conditions" and item["value"] == "":
                item["value"] = "^EQ"
        result.append(item)
    return sorted(result, key=lambda item: int(item["order"]))


def new_instance(
    flow: dict[str, Any],
    definition: dict[str, Any],
    inputs: dict[str, Any],
    *,
    schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_version(flow)
    _definition(definition, definition["id"])
    name = definition.get("name") or definition["label"]
    trigger = {
        "name": name,
        "label": name,
        "type": definition["type"],
        "triggerType": definition["category"],
        "inputs": _bindings(definition["inputs"], definition["id"], True),
        "outputs": _bindings(definition["outputs"], definition["id"], False),
        "order": definition.get("order", 1),
        "triggerDefinitionId": definition["id"],
        "fromTemplate": any(
            row.get("attributes", {}).get("fromTemplate") == "true"
            for row in definition["inputs"]
        ),
        "templateProperties": copy.deepcopy(definition["templateProperties"]),
        "isDirty": False,
        "hasMissingMandatoryInputs": False,
    }
    for source, target in (
        ("scopeName", "scopeName"),
        ("scopeLabel", "scopeLabel"),
        ("scopeLogo", "scopeLogo"),
        ("description", "description"),
        ("publishedTriggerId", "publishedVersion"),
        ("hasDynamicOutputs", "hasDynamicOutputs"),
        ("recordClassName", "triggerTable"),
        ("annotation", "comment"),
    ):
        if source in definition:
            trigger[target] = copy.deepcopy(definition[source])
    if not trigger["fromTemplate"]:
        # A saved definition need not inherit any inputs (for example SLA).
        # Preserve its independently proven table identity when no inherited
        # parameter can carry it; openTrigger uses this same table default.
        trigger.setdefault("triggerTable", TABLE)
    # These are native reducer defaults, not inferred server configuration.
    trigger["metadata"] = {"query_parse": {"predicates": []}}
    trigger["additionalMetadata"] = {"query_parse": {"predicates": []}}
    if definition["templateProperties"].get("showDefinitionConditions"):
        trigger["metadata"] = {}
        trigger["additionalMetadata"] = {}
    if inputs:
        _changes(trigger, inputs)
    application_triggers.expand_saved_rest(trigger)
    application_triggers.apply_saved_schemas(trigger, definition, schemas)
    validate(trigger, definition)
    return trigger


def validate(trigger: dict[str, Any], definition: dict[str, Any]) -> None:
    _definition(definition, definition["id"])
    if (
        trigger.get("triggerDefinitionId") != definition["id"]
        or trigger.get("type") != definition["type"]
        or trigger.get("templateProperties") != definition["templateProperties"]
        or not is_saved(trigger)
    ):
        raise CapabilityError(
            "The saved trigger identity or template properties changed.", "UNAVAILABLE"
        )
    expected_outputs = _bindings(definition["outputs"], definition["id"], False)
    if application_triggers.is_rest(trigger):
        expected_outputs = application_triggers.projected_saved_outputs(
            trigger, expected_outputs
        )
    for collection in ("inputs", "outputs"):
        rows = trigger.get(collection, [])
        if (
            not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
            or len(rows) != len(definition[collection])
        ):
            raise CapabilityError("The saved trigger schema changed.", "UNAVAILABLE")
        for raw in definition[collection]:
            item = data.named(rows, raw["name"])
            expected = (
                data.spec(data.named(expected_outputs, raw["name"]))
                if collection == "outputs"
                else _parameter(raw, f"{definition['id']}.{raw['name']}")
            )
            expected_item = (
                data.named(expected_outputs, raw["name"])
                if collection == "outputs"
                else {"type": raw["type"], "internalType": raw["type"]}
            )
            schema_root = (
                collection == "inputs"
                and application_triggers.is_rest(trigger)
                and raw["type"] in declarations.LABELS
            )
            schema_changed = data.spec(item) != expected
            if schema_root:
                application_triggers.validate_saved_schema(item, expected)
            elif schema_changed:
                raise CapabilityError(
                    "The saved trigger parameter differs from the current picker schema.",
                    "UNAVAILABLE",
                )
            elif any(
                item.get(key, expected_item.get(key)) != expected_item.get(key)
                for key in ("type", "internalType")
            ):
                raise CapabilityError(
                    "The saved trigger wrapper type differs from its parameter.",
                    "UNAVAILABLE",
                )
            if collection == "outputs":
                continue
            value = item.get("value")
            inherited = raw.get("attributes", {}).get("fromTemplate") == "true"
            derived = application_triggers.saved_derived_input(trigger, raw["name"])
            native_path = application_triggers.saved_path_input(trigger, raw["name"])
            if item.get("scriptActive") or tree.pills(value) and not native_path:
                raise CapabilityError(
                    "Saved trigger configuration requires static native values."
                )
            if raw.get("mandatory") and value in ("", None) and not derived:
                raise CapabilityError("A required saved-trigger input is empty.")
            if inherited:
                expected_value = _default(raw)
                if raw["type"] == "conditions" and expected_value == "":
                    expected_value = "^EQ"
                if (
                    not derived
                    and value != expected_value
                    or item.get("templateValue") != raw.get("defaultValue")
                    or item.get("fromTemplate")
                    is not (
                        not (_default(raw) != "" and value != raw.get("defaultValue"))
                        if derived
                        else True
                    )
                ):
                    raise CapabilityError(
                        "Inherited saved-trigger values differ from their current template.",
                        "UNAVAILABLE",
                    )
                # Opaque inherited values are definition-owned, not reconstructed
                # using guessed recurrence/business-calendar field names.
                if raw["type"] not in {
                    "boolean",
                    "integer",
                    "long",
                    "string",
                    "choice",
                    "reference",
                    "document_id",
                    "records",
                    "glide_list",
                    "table_name",
                    "field_name",
                    "json",
                    "conditions",
                    "glide_time",
                    "glide_date_time",
                    "glide_duration",
                }:
                    continue
            if schema_root:
                if value not in ("", None):
                    raise CapabilityError(
                        "REST request schema roots cannot contain runtime values."
                    )
                continue
            if derived and value in ("", None):
                # The source-owned path resolver supplies these values below;
                # caller input cannot manufacture or override a generated path.
                continue
            if native_path:
                if not isinstance(value, str):
                    raise CapabilityError(
                        "REST display paths require native path text."
                    )
            else:
                _value(item, value)


def _value(item: dict[str, Any], value: Any) -> None:
    parameter = data.spec(item)
    kind = parameter["type"]
    if isinstance(value, str) and ("{{" in value or "}}" in value) or tree.pills(value):
        raise CapabilityError(
            "Saved trigger configuration requires static native values."
        )
    if kind in {"glide_time", "glide_date_time", "glide_duration"}:
        if value in ("", None) and not parameter.get("mandatory"):
            return
        from servicenow_mcp.capabilities.flows.triggers import _datetime

        stamp = _datetime(value)
        if kind == "glide_time" and stamp.date().isoformat() != "1970-01-01":
            raise CapabilityError(
                "glide_time requires the ServiceNow epoch date 1970-01-01."
            )
        if kind == "glide_duration":
            from datetime import datetime

            minimum = max(
                1, int(parameter.get("attributes", {}).get("minimum_seconds", 1))
            )
            if (stamp - datetime(1970, 1, 1)).total_seconds() < minimum:  # noqa: DTZ001 - native duration epoch
                raise CapabilityError(
                    "The interval is below the catalog minimum duration."
                )
        return
    if kind == "conditions":
        if not isinstance(value, str) or any(
            marker in value.lower()
            for marker in ("javascript:", "^nq", "^orderby", "^groupby")
        ):
            raise CapabilityError("Trigger conditions require a static encoded query.")
        return
    probe_parameter = {**copy.deepcopy(parameter), "readonly": False}
    if (
        kind == "choice"
        and isinstance(value, str)
        and value == parameter.get("defaultValue")
    ):
        # The constructor preserves a source-owned historical default even when
        # it is no longer selectable. Check its scalar boundary without changing
        # the definition's choice metadata; explicit edits check the enum below.
        probe_parameter["type"] = "string"
    probe = {**copy.deepcopy(item), "parameter": probe_parameter}
    data.set_value(probe, value)
    if kind == "choice" and value not in ("", None):
        choices = declarations.choices(parameter)
        if value not in [row["value"] for row in choices] and value != parameter.get(
            "defaultValue"
        ):
            raise CapabilityError(
                "The saved-trigger choice is absent from its current definition."
            )


def update(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    changes: dict[str, Any],
    *,
    schemas: dict[str, Any] | None = None,
) -> None:
    if not changes and schemas is None:
        raise CapabilityError("update_trigger requires inputs or interface_schemas.")
    validate(trigger, definition)
    _changes(trigger, changes)
    application_triggers.apply_saved_schemas(trigger, definition, schemas)
    validate(trigger, definition)


def _changes(trigger: dict[str, Any], changes: dict[str, Any]) -> None:
    for name, value in changes.items():
        item = data.named(trigger["inputs"], name)
        parameter = data.spec(item)
        if application_triggers.saved_derived_input(trigger, name):
            raise CapabilityError(
                "The native REST path input is generated, not caller-authored."
            )
        if (
            parameter.get("readonly")
            or parameter.get("attributes", {}).get("fromTemplate") == "true"
            or item.get("readonly")
        ):
            raise CapabilityError("The inherited saved-trigger input is read-only.")
        if (
            application_triggers.is_rest(trigger)
            and parameter["type"] in declarations.LABELS
        ):
            raise CapabilityError(
                "REST input schemas must be declared with interface_schemas."
            )
        if application_triggers.saved_path_input(trigger, name):
            if not isinstance(value, str):
                raise CapabilityError("REST display paths require native path text.")
        else:
            _value(item, value)
        if (
            parameter["type"] == "choice"
            and value not in ("", None)
            and value not in [row["value"] for row in declarations.choices(parameter)]
        ):
            raise CapabilityError(
                "The saved-trigger choice is absent from its current definition."
            )
        item["value"] = copy.deepcopy(value)
        item["displayValue"] = (
            value
            if parameter["type"]
            in {"conditions", "glide_time", "glide_date_time", "glide_duration"}
            else ""
        )
        if item.get("fromTemplate"):
            item["fromTemplate"] = item.get("templateValue") == value


async def prepare(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    client: ServiceNowClient,
    *,
    flow: dict[str, Any] | None = None,
    authored: bool = False,
) -> dict[str, Any]:
    """Recheck metadata-declared reference identities without interpreting schedules."""
    validate(trigger, definition)
    evidence = {}
    for item in trigger["inputs"]:
        parameter = data.spec(item)
        value = item["value"]
        if parameter["type"] not in {"reference", "glide_list", "records"} or not value:
            continue
        table = parameter.get("reference")
        if not isinstance(table, str) or not table:
            raise CapabilityError(
                "The saved-trigger reference table is unavailable.", "UNAVAILABLE"
            )
        validate_identifier(table)
        check_table_access(table)
        qualifier = parameter.get("ref_qual", "")
        if not isinstance(qualifier, str) or any(
            marker in ("^" + qualifier).lower()
            for marker in ("javascript:", "^nq", "^orderby", "^groupby")
        ):
            raise CapabilityError(
                "This saved-trigger reference qualifier requires a native dynamic resolver.",
                "UNAVAILABLE",
            )
        identities = value.split(",")
        if len(identities) > 100:
            raise CapabilityError(
                "Saved-trigger reference validation supports at most 100 identities.",
                "UNAVAILABLE",
            )
        for identity in identities:
            validate_sys_id(identity)
            query = f"sys_id={identity}" + (f"^{qualifier}" if qualifier else "")
            rows = (
                await client.query_records(
                    table, query, fields=["sys_id", "sys_mod_count"], limit=2
                )
            )["records"]
            if len(rows) != 1 or rows[0].get("sys_id") != identity:
                raise CapabilityError(
                    "The saved-trigger reference is absent, unreadable, or excluded by its native qualifier."
                )
            if not _revision(rows[0]):
                raise CapabilityError(
                    "The saved-trigger reference revision is unreadable.", "UNAVAILABLE"
                )
            evidence[f"{table}.{identity}"] = rows[0]
    evidence.update(
        await application_triggers.prepare_saved_rest(
            trigger, definition, client, flow=flow, authored=authored
        )
    )
    for item in trigger["inputs"]:
        raw = data.named(definition["inputs"], item["name"])
        if raw.get("attributes", {}).get(
            "fromTemplate"
        ) == "true" and application_triggers.saved_derived_input(trigger, item["name"]):
            item["fromTemplate"] = not (
                _default(raw) != "" and item["value"] != raw["defaultValue"]
            )
    validate(trigger, definition)
    return evidence


async def catalog_interface(
    flow: dict[str, Any],
    definition: dict[str, Any],
    client: ServiceNowClient,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prove existing Service Catalog call-input ownership before a cutover.

    The catalog owns the two declaration roles; database owners are read using
    the established generated-input mapping (semantics._bind_trigger_inputs).
    Unknown or additional declarations are never removed by trigger cleanup.
    """
    from servicenow_mcp.capabilities.flows import triggers

    declarations = triggers.service_catalog_inputs(definition)
    rows = flow["inputs"]
    if not rows:
        return [], {}
    if len(rows) != len(declarations):
        raise CapabilityError(
            "The Service Catalog call interface has unknown declarations.",
            "UNAVAILABLE",
        )
    check_table_access("sys_hub_flow_input")
    evidence = {}
    identities = set()
    for declaration in declarations:
        row = data.named(rows, declaration["name"])
        if data.value_type(row) != data.value_type(declaration) or data.children(row):
            raise CapabilityError(
                "The Service Catalog call interface differs from its native declaration roles.",
                "UNAVAILABLE",
            )
        identity = row.get("id", "")
        validate_sys_id(identity)
        if identity in identities:
            raise CapabilityError(
                "The Service Catalog call declarations share a database identity.",
                "UNAVAILABLE",
            )
        identities.add(identity)
        record = await client.get_record(
            "sys_hub_flow_input",
            identity,
            fields=["sys_id", "model", "element", "sys_class_name", "sys_mod_count"],
        )
        if not _revision(record) or any(
            resolve_ref_value(record.get(key)) != value
            for key, value in {
                "sys_id": identity,
                "model": flow["id"],
                "element": declaration["name"],
                "sys_class_name": "sys_hub_flow_input",
            }.items()
        ):
            raise CapabilityError(
                "The independent Service Catalog call-input owner or revision is unreadable.",
                "UNAVAILABLE",
            )
        evidence[identity] = record
    return declarations, evidence
