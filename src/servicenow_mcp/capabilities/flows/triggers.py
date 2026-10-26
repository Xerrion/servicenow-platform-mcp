"""Inactive draft trigger serializers from Studio 27, without effect endpoints.

Catalog construction:58112-58179. Value/dependency updates:203551-203605,
204942-205053,156320-156374. Schedule input names:96437-96464.
Record output typing follows internal_link at63341-63368, not schema replacement.
"""

import copy
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    application_triggers,
    data,
    native,
    saved_triggers,
    tree,
)
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver
    from servicenow_mcp.platform.client import ServiceNowClient

RECORD = {"record_create", "record_update", "record_create_or_update"}
SCHEDULED = {"daily", "weekly", "monthly", "run_once", "repeat"}
SUPPORTED = RECORD | SCHEDULED | {"service_catalog"} | application_triggers.SUPPORTED
OPS = {"set_trigger", "update_trigger", "remove_trigger"}
SCHEDULE_INPUTS = {
    "daily": {"glide_time": "glide_time"},
    "weekly": {"day_of_week": "day_of_week", "glide_time": "glide_time"},
    "monthly": {"integer": "integer", "glide_time": "glide_time"},
    "run_once": {"glide_date_time": "glide_date_time"},
    "repeat": {"glide_duration": "glide_duration"},
}


def schedule_inputs(definition: dict[str, Any]) -> dict[str, str]:
    """Bind schedule roles by declared native types, not historical UI names."""
    kind = definition["type"]
    rows = definition.get("inputs", [])
    actual: dict[str, str] = {}
    for row in rows:
        name, parameter_type = row["name"], row["type"]
        if not isinstance(name, str) or not isinstance(parameter_type, str):
            raise CapabilityError(
                "Scheduled parameter names and types must be readable.", "UNAVAILABLE"
            )
        actual[name] = parameter_type
    if (
        len(actual) != len(rows)
        or sorted(actual.values()) != sorted(SCHEDULE_INPUTS[kind].values())
        or any(row["parameter"].get("name") != row["name"] for row in rows)
    ):
        raise CapabilityError(
            "This scheduled definition uses a different input contract, such as recurrence/timezone metadata.",
            "UNAVAILABLE",
        )
    return actual


def service_catalog_inputs(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Expected backend-owned call inputs, bound to the independent catalog.

    Service Catalog supplies a requested record (create-sc-flow.md:27). Its
    native input DTO projection is captured in flow_service_catalog_inputs.json.
    REST/external createFlowInputs:184408-184414 does NOT cover this trigger.
    Runtime timestamps remain trigger outputs, not authored flow interfaces.
    """
    if definition.get("type") != "service_catalog":
        return []
    outputs = definition.get("outputs", [])
    if any(
        data.value_type(row).kind not in {"table_name", "reference"}
        and not (
            data.value_type(row).kind == "glide_date_time"
            and data.spec(row).get("attributes", {}).get("test_input_hidden") == "true"
        )
        for row in outputs
    ):
        raise CapabilityError(
            "The catalog has an unsupported call-input contract.", "UNAVAILABLE"
        )
    result = []
    for kind in ("table_name", "reference"):
        matches = [
            data.spec(row) for row in outputs if data.value_type(row).kind == kind
        ]
        if len(matches) != 1:
            raise CapabilityError(
                "The Service Catalog call-input schema is ambiguous.", "UNAVAILABLE"
            )
        row = copy.deepcopy(matches[0])
        if (
            not isinstance(row.get("name"), str)
            or not isinstance(row.get("type_label"), str)
            or not isinstance(row.get("attributes"), dict)
        ):
            raise CapabilityError(
                "The catalog call-input parameter is incomplete.", "UNAVAILABLE"
            )
        row.pop("id", None)  # New flow-owned records receive different database IDs.
        if row.get("internal_link") == "":
            row.pop("internal_link")
        # Native declaration flag, separate from the output picker's control.
        row["readonly"] = False
        attributes = row.setdefault("attributes", {})
        for key, value in {
            "uiType": row["type"],
            "uiTypeLabel": row["type_label"],
            "element_mapping_provider": "com.glide.flow_design.action.data.FlowDesignVariableMapper",
        }.items():
            if key in attributes and attributes[key] != value:
                raise CapabilityError(
                    "The catalog call-input mapping metadata conflicts.", "UNAVAILABLE"
                )
            attributes[key] = value
        result.append(row)
    if len({row["name"] for row in result}) != len(result):
        raise CapabilityError(
            "The catalog call-input names are ambiguous.", "UNAVAILABLE"
        )
    return result


def choices(spec: dict[str, Any]) -> set[Any]:
    return {
        row["value"]
        for row in (spec.get("choiceList") or data.spec(spec).get("choices") or [])
    }


def require_supported(trigger: dict[str, Any]) -> None:
    if saved_triggers.is_saved(trigger):
        return
    if (
        trigger.get("type") not in SUPPORTED
        or trigger.get("fromTemplate")
        or trigger.get("templateProperties")
    ):
        raise CapabilityError(
            "This trigger family or saved/template trigger requires another adapter.",
            "UNAVAILABLE",
        )


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value
    ):
        raise CapabilityError("Trigger date/time values require YYYY-MM-DD HH:MM:SS.")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - native stored fields have no zone suffix
    except ValueError:
        raise CapabilityError(
            "The trigger date/time value is not a valid calendar value."
        ) from None


def validate_values(trigger: dict[str, Any], definition: dict[str, Any]) -> None:
    if saved_triggers.is_saved(trigger):
        saved_triggers.validate(trigger, definition)
        return
    if trigger.get("type") in application_triggers.SUPPORTED:
        application_triggers.validate(trigger, definition)
        return
    require_supported(trigger)
    if trigger.get("triggerDefinitionId") != definition.get("id") or trigger[
        "type"
    ] != definition.get("type"):
        raise CapabilityError(
            "The trigger definition identity differs from the current catalog.",
            "UNAVAILABLE",
        )
    inputs = trigger["inputs"]
    actual_outputs, expected_outputs = trigger.get("outputs", []), definition["outputs"]
    if len(actual_outputs) != len(expected_outputs) or any(
        data.spec(data.named(actual_outputs, spec["name"])) != spec["parameter"]
        for spec in expected_outputs
    ):
        raise CapabilityError(
            "The trigger output schema differs from the current catalog.", "UNAVAILABLE"
        )
    if len(inputs) != len(definition["inputs"]):
        raise CapabilityError(
            "The trigger input list differs from its current definition.", "UNAVAILABLE"
        )
    for spec in definition["inputs"]:
        item = data.named(inputs, spec["name"])
        parameter = spec["parameter"]
        if data.value_type(item) != data.value_type(parameter) or item.get(
            "parameter"
        ) != (
            native.trigger_parameter(parameter)
            if "remoteSysId" in trigger
            else parameter
        ):
            raise CapabilityError(
                "The saved trigger parameter differs from its current definition. Create a fresh trigger preview.",
                "UNAVAILABLE",
            )
        value = item["value"]
        if item.get("scriptActive"):
            raise CapabilityError(
                "Scripted trigger inputs require another adapter.", "UNAVAILABLE"
            )
        if (
            isinstance(value, dict | list)
            or tree.pills(value)
            or isinstance(value, str)
            and ("{{" in value or "}}" in value)
        ):
            raise CapabilityError(
                "Trigger configuration requires static native scalar values."
            )
        if (spec.get("mandatory") or parameter.get("mandatory")) and value in (
            None,
            "",
        ):
            raise CapabilityError("A required trigger input is empty.")
        kind = spec["type"]
        allowed = choices(spec)
        default = spec.get("default_value", parameter.get("defaultValue", ""))
        if (
            allowed
            and value not in ("", None)
            and value not in allowed
            and value != default
        ):
            raise CapabilityError(
                "A trigger choice value is absent from the current catalog."
            )
        if kind in {"choice", "day_of_week"}:
            if not allowed and kind == "choice" and value not in ("", default):
                raise CapabilityError(
                    "This trigger choice has no readable current choice set.",
                    "UNAVAILABLE",
                )
            if kind == "day_of_week" and not allowed:
                raise CapabilityError(
                    "day_of_week requires the instance catalog's weekday choice list.",
                    "UNAVAILABLE",
                )
        elif kind in {"glide_time", "glide_date_time", "glide_duration"}:
            stamp = _datetime(value)
            if kind == "glide_time" and stamp.date().isoformat() != "1970-01-01":
                raise CapabilityError(
                    "glide_time requires the ServiceNow epoch date 1970-01-01."
                )
            if kind == "glide_duration":
                minimum = int(parameter.get("attributes", {}).get("minimum_seconds", 1))
                if (stamp - datetime(1970, 1, 1)).total_seconds() < max(1, minimum):  # noqa: DTZ001 - duration epoch, not a wall-clock instant
                    raise CapabilityError(
                        "The repeat interval is below the catalog minimum duration."
                    )
        elif kind == "conditions":
            if isinstance(value, str) and len(value) > int(
                parameter.get("maxsize") or 65000
            ):
                raise CapabilityError(
                    "The trigger condition exceeds the declared maximum length."
                )
            if not isinstance(value, str) or any(
                marker in value.lower()
                for marker in ("javascript:", "^nq", "^orderby", "^groupby")
            ):
                raise CapabilityError(
                    "Trigger conditions require a static encoded query, not JavaScript."
                )
        elif kind in {"table_name", "glide_list", "string", "integer", "boolean"}:
            probe = {
                **copy.deepcopy(item),
                "parameter": {**copy.deepcopy(parameter), "readonly": False},
            }
            data.set_value(probe, value)
        else:
            raise CapabilityError(
                "This trigger input type requires another value adapter.", "UNAVAILABLE"
            )
    if trigger["type"] in SCHEDULED:
        expected = schedule_inputs(definition)
        if trigger["type"] == "monthly":
            day = data.named(
                inputs,
                next(name for name, kind in expected.items() if kind == "integer"),
            )["value"]
            if type(day) is not int or not 1 <= day <= 31:
                raise CapabilityError(
                    "Monthly integer requires a native day number from 1 through 31."
                )
    if trigger["type"] in RECORD:
        table = data.named(inputs, "table")["value"]
        if not isinstance(table, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,79}", table
        ):
            raise CapabilityError("Record triggers require a single table name.")
        if table.startswith("sys_") or table == "sc_req_item":
            raise CapabilityError(
                "System tables and Requested Item require a dedicated trigger adapter.",
                "UNAVAILABLE",
            )
        check_table_access(table)
        settings = {item["name"]: item["value"] for item in inputs}
        if settings.get("run_when_user_setting") in {
            "one_of",
            "not_one_of",
        } and not settings.get("run_when_user_list"):
            raise CapabilityError(
                "The selected trigger user filter requires a user list."
            )


def update(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    changes: dict[str, Any],
    *,
    schemas: dict[str, Any] | None = None,
) -> None:
    if (
        not saved_triggers.is_saved(trigger)
        and trigger.get("type") in application_triggers.SUPPORTED
    ):
        application_triggers.update(trigger, definition, changes, schemas)
        return
    if saved_triggers.is_saved(trigger):
        saved_triggers.update(trigger, definition, changes, schemas=schemas)
        return
    if schemas is not None:
        raise CapabilityError("Only REST triggers accept interface_schemas.")
    require_supported(trigger)
    if not changes:
        raise CapabilityError("update_trigger requires nonempty inputs.")
    # Validate before edits so that current definitions never silently replace
    # unknown parameter metadata in an existing saved trigger.
    validate_values(trigger, definition)
    table = next(
        (item["value"] for item in trigger["inputs"] if item["name"] == "table"), None
    )
    if "table" in changes and changes["table"] != table:
        condition_names = {
            item["name"]
            for item in trigger["inputs"]
            if data.value_type(item).kind == "conditions"
        }
        if not condition_names <= changes.keys():
            raise CapabilityError(
                "Changing a trigger table requires explicit values for all condition inputs. No condition is cleared implicitly."
            )
    for name, value in changes.items():
        item = data.named(trigger["inputs"], name)
        if data.spec(item).get("readonly") or item.get("scriptActive"):
            raise CapabilityError("The trigger input is read-only or scripted.")
        spec = data.named(definition["inputs"], name)
        if choices(spec) and value not in choices(spec) and value != "":
            raise CapabilityError(
                "The requested trigger choice is absent from the current catalog."
            )
        item["value"] = value
        item["displayValue"] = (
            value
            if data.value_type(item).kind
            in {"conditions", "glide_time", "glide_duration", "glide_date_time"}
            else ""
        )
        if data.value_type(item).kind == "conditions" and value == "":
            item["value"] = item["displayValue"] = "^EQ"
        if data.value_type(item).kind == "conditions":
            key = "additionalMetadata" if name == "additional_condition" else "metadata"
            metadata = trigger.get(key, {})
            if metadata.get("predicates") or metadata.get("query_parse", {}).get(
                "predicates"
            ):
                raise CapabilityError(
                    "Updating a trigger with parsed condition metadata requires a dedicated metadata adapter.",
                    "UNAVAILABLE",
                )
    validate_values(trigger, definition)


async def prepare(
    trigger: dict[str, Any],
    definition: dict[str, Any],
    client: ServiceNowClient,
    *,
    resolver: PillResolver,
    flow: dict[str, Any] | None = None,
    authored: bool = False,
) -> dict[str, Any]:
    saved = saved_triggers.is_saved(trigger)
    if not saved and trigger.get("type") in application_triggers.SUPPORTED:
        return await application_triggers.prepare(
            trigger, definition, client, resolver=resolver, flow=flow, authored=authored
        )
    saved_evidence = {}
    if saved:
        saved_evidence = await saved_triggers.prepare(
            trigger, definition, client, flow=flow, authored=authored
        )
    else:
        validate_values(trigger, definition)
    if trigger["type"] not in RECORD:
        return saved_evidence
    table_input = data.named(trigger["inputs"], "table")
    table = table_input["value"]
    check_table_access("sys_db_object")
    records = (
        await client.query_records(
            "sys_db_object",
            f"name={table}",
            fields=["name", "label", "sys_id"],
            limit=2,
        )
    )["records"]
    if (
        len(records) != 1
        or records[0].get("name") != table
        or not records[0].get("label")
    ):
        raise CapabilityError(
            "The record trigger table is absent, ambiguous, or unreadable.",
            "UNAVAILABLE",
        )
    validate_sys_id(records[0]["sys_id"])
    descriptor = await client.studio_table_descriptor(table)
    display_field = descriptor.get("displayField")
    if not isinstance(display_field, str) or not display_field:
        raise CapabilityError(
            "The trigger table descriptor has no readable display field.", "UNAVAILABLE"
        )
    validate_identifier(display_field)
    table_input.update(
        reference=table,
        reference_display=records[0]["label"],
        displayValue=records[0]["label"],
        displayField=display_field,
        valueSysId=records[0]["sys_id"],
    )
    for item in trigger["inputs"]:
        if data.spec(item).get("dependent_on") == "table":
            item.update(data_format=table, table=table, reference=table)
    # Reuse the bounded inherited dictionary resolver for static condition
    # fields. An unreadable/unknown field cannot silently broaden a trigger.
    terms_checked = 0
    for item in trigger["inputs"]:
        if data.value_type(item).kind != "conditions" or item["value"] in ("", "^EQ"):
            continue
        for term in item["value"].split("^"):
            if not term or term == "EQ":
                continue
            term = term.removeprefix("OR")
            terms_checked += 1
            if terms_checked > 50:
                raise CapabilityError(
                    "Record trigger condition validation supports at most 50 clauses.",
                    "UNAVAILABLE",
                )
            match = re.match(
                r"([a-z][a-z0-9_.]*?)(?:!=|>=|<=|=|>|<|NOT IN|IN|ISNOTEMPTY|ISEMPTY|STARTSWITH|ENDSWITH|NOT LIKE|LIKE|CHANGESFROM|CHANGESTO|CHANGES|BETWEEN|ON|BEFORE|AFTER)",
                term,
            )
            if not match:
                raise CapabilityError(
                    "This record-trigger condition operator requires another query adapter.",
                    "UNAVAILABLE",
                )
            path = match.group(1).split(".")
            if len(path) > 10:
                raise CapabilityError(
                    "Trigger condition paths support at most ten fields."
                )
            await resolver._field(
                "condition." + match.group(1), "Condition", table, path
            )
    return {
        **({"saved_references": saved_evidence} if saved else {}),
        "table": records[0],
        "descriptor": descriptor,
        "dictionary": {
            f"{table}.{column}": row
            for (table, column), row in resolver.dictionary_cache.items()
        },
    }


def output_type(trigger: dict[str, Any], output: dict[str, Any]) -> data.ValueType:
    parameter = data.spec(output)
    link = parameter.get("internal_link")
    if link:
        linked = data.named(trigger["inputs"], link)
        reference = linked.get("reference") or linked.get("value")
        if output["type"] in {"reference", "document_id"}:
            return data.ValueType("reference", str(reference or ""))
    return data.value_type(output)


def check_dependencies(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    flow: dict[str, Any],
    roots: list[tree.Step],
    *,
    replaced: bool,
) -> None:
    """Do not clear hidden transform/default/script references on trigger edits."""
    if not before:
        return
    old = before[0]
    old_label = old.get("label") or old["name"]
    changed_source = replaced or not after or after[0]["type"] != old["type"]
    if after:
        old_table = {
            row["name"]: row["value"]
            for row in old["inputs"]
            if data.value_type(row).kind == "table_name"
        }
        new_table = {
            row["name"]: row["value"]
            for row in after[0]["inputs"]
            if data.value_type(row).kind == "table_name"
        }
        changed_source |= old_table != new_table or old_label != (
            after[0].get("label") or after[0]["name"]
        )
    if not changed_source:
        return
    values = [
        flow.get("pharmacy"),
        flow["inputs"],
        flow["outputs"],
        flow["flowVariables"],
        *[step.raw for step in tree.walk(roots)],
    ]
    if any(pill.startswith(old_label + "_1.") for pill in tree.pills(values)):
        raise CapabilityError(
            "The removed or changed trigger source is still referenced. Update consumers explicitly first."
        )

    def scripts(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("script") and re.search(
                r"\bfd_data\s*(?:\.\s*trigger\b|\[)", str(value["script"])
            ):
                return True
            return any(scripts(item) for item in value.values())
        return isinstance(value, list) and any(scripts(item) for item in value)

    if scripts(values):
        raise CapabilityError(
            "Trigger edits cannot rewrite inline trigger script references.",
            "UNAVAILABLE",
        )


def summary(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": item["type"],
            "id": item.get("id"),
            "definition": item["triggerDefinitionId"],
            "inputs": mask_sensitive_fields(
                {row["name"]: row["value"] for row in item["inputs"]}
            ),
            "schedule_timezone": (
                "native_definition"
                if saved_triggers.is_saved(item)
                else "instance"
                if item["type"] in SCHEDULED
                else None
            ),
        }
        for item in instances
    ]
