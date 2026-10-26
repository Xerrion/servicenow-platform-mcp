"""Native utility flow-logic authoring. Reference reads never run the referenced flow.

Studio 27 contracts: workflow selection/configuration 333397-333409,
333448-333459,333766-333815; dynamic template binding/output projection
189725-189775,206790-206810,210262-210276; timer widgets 334240-334435.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date, time
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, input_modes, native, payloads, tree
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver
    from servicenow_mcp.platform.client import ServiceNowClient


TYPES = frozenset({"TIMER", "RUNWORKFLOW", "RUNDYNAMICFLOW", "GETFLOWOUTPUTS"})
STRUCTURAL_PRESERVED = frozenset({"UPDATEFLOWTEMPLATE"})
DYNAMIC_OUTPUT = "FLOW_TEMPLATE_DYNAMIC_OUTPUT"
_DATE_ONLY = frozenset({"date", "glide_date", "due_date"})
_TEMPORAL = _DATE_ONLY | frozenset(
    {
        "glide_duration",
        "duration",
        "glide_date_time",
        "datetime",
        "glide_time",
        "calendar_date_time",
        "schedule_date_time",
    }
)
_STATIC_SCRIPT_SELECTORS = frozenset({"flow_template", "wait_for_completion"})
_EXTRA = {
    "RUNWORKFLOW": {"workflow_reference", "workflow_inputs"},
    "RUNDYNAMICFLOW": {"dynamic_inputs"},
}
_SYS_ID = re.compile(r"[0-9a-f]{32}")


def _value(record: dict[str, Any], key: str) -> Any:
    value = record.get(key)
    return value.get("value") if isinstance(value, dict) else value


def _stamp(value: str) -> tuple[date, time]:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value
    ):
        raise CapabilityError(
            "Temporal values require native YYYY-MM-DD HH:mm:ss text."
        )
    try:
        return date.fromisoformat(value[:10]), time(
            int(value[11:13]), int(value[14:16]), int(value[17:19])
        )
    except ValueError:
        raise CapabilityError(
            "The temporal value is not a valid calendar date and time."
        ) from None


def validate_value(kind: str, value: Any, schema: dict[str, Any]) -> None:
    """Validate native scalar temporal widgets without timezone reinterpretation.

    Durations are offsets from ServiceNow's epoch, not wall-clock dates. The
    timer's hideDays/maxHours=9999 widget additionally bounds its own duration.
    """
    if kind not in _TEMPORAL:
        raise CapabilityError(
            "This value is not a native temporal widget.", "UNAVAILABLE"
        )
    if not isinstance(value, str):
        raise CapabilityError(
            "Native temporal values require strings or one typed pill."
        )
    if value == "":
        if schema.get("mandatory") in (True, "true", "1"):
            raise CapabilityError("A mandatory temporal input is empty.")
        return
    if data.whole_pill(value):
        return
    if tree.pills(value) or "{{" in value or "}}" in value:
        raise CapabilityError(
            "Temporal widgets require one complete typed pill, not interpolation."
        )
    if kind in _DATE_ONLY:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise CapabilityError("Date-only values require native YYYY-MM-DD text.")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise CapabilityError(
                "The date-only value is not a valid calendar date."
            ) from None
        return
    if kind == "schedule_date_time" and schema.get("attributes", {}).get(
        "ui_approval"
    ) in (True, "true"):
        raise CapabilityError(
            "Approval due dates require their dedicated native JSON widget adapter.",
            "UNAVAILABLE",
        )
    day, _ = _stamp(value)
    if kind in {"glide_duration", "duration"} and day < date(1970, 1, 1):
        raise CapabilityError("A native duration cannot be negative.")
    if kind == "glide_time" and day != date(1970, 1, 1):
        raise CapabilityError("Native Time values require the ServiceNow epoch date.")


def _set(item: dict[str, Any], value: Any, *, reference_name: bool = False) -> None:
    parameter = data.spec(item)
    if input_modes.readonly(item):
        raise CapabilityError("Read-only utility inputs cannot be replaced.")
    if item.get("scriptActive"):
        if (
            parameter.get("attributes", {}).get("is_scriptable", True)
            not in (True, "true")
            or item.get("name") in _STATIC_SCRIPT_SELECTORS
        ):
            raise CapabilityError(
                "The native utility input does not permit this inline script."
            )
        input_modes.value(item, value, scripted=True)
        return
    if parameter.get("mandatory") in (True, "true", "1") and value in ("", None):
        raise CapabilityError("A mandatory utility input is empty.")
    kind = data.value_type(item).kind
    if kind in _TEMPORAL and not (
        kind == "schedule_date_time"
        and parameter.get("attributes", {}).get("ui_approval") in (True, "true")
    ):
        validate_value(kind, value, parameter)
        item.update(value=value, displayValue="" if tree.pills(value) else value)
    elif reference_name and value not in ("", None) and not data.whole_pill(value):
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(marker in value for marker in ("^", "{{", "}}"))
        ):
            raise CapabilityError(
                "The utility reference requires a sys_id or exact name."
            )
        item.update(value=value, displayValue="")
    else:
        data.set_value(item, value)
        if kind == "choice" and not tree.pills(value):
            item["displayValue"] = next(
                (
                    choice["label"]
                    for choice in parameter.get("choices", [])
                    if choice["value"] == value
                ),
                item.get("displayValue", ""),
            )


def _rows(value: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or len(value) > 100
        or any(not isinstance(row, dict) for row in value)
    ):
        raise CapabilityError(
            "The native utility interface is incomplete or exceeds 100 fields.",
            "UNAVAILABLE",
        )
    names = [row.get("element") or row.get("name") for row in value]
    if any(not isinstance(name, str) or not name for name in names) or len(
        set(names)
    ) != len(names):
        raise CapabilityError(
            "The native utility interface has absent or ambiguous field names.",
            "UNAVAILABLE",
        )
    return value


def _staged(step: tree.Step, key: str, values: Any) -> None:
    if not isinstance(values, dict) or any(
        not isinstance(name, str) or not name for name in values
    ):
        raise CapabilityError(
            "Utility interface inputs require an object of native field names."
        )
    supplied = step.raw.setdefault("_utility_fields", {})
    supplied.setdefault(key, []).extend(
        name for name in values if name not in supplied.get(key, [])
    )
    existing = {
        row.get("element") or row.get("name"): row
        for row in _rows(step.raw.get(key, []))
    }
    for name, value in values.items():
        if name in existing:
            # Defer schema validation until the current reference has been read.
            existing[name]["value"] = copy.deepcopy(value)
        else:
            existing[name] = {"name": name, "value": copy.deepcopy(value)}
    step.raw[key] = list(existing.values())


def remap_staged(step: tree.Step, aliases: dict[str, str]) -> None:
    """Remap caller-owned complex literals before their deferred schema hydration."""

    def remap(value: Any) -> Any:
        if isinstance(value, str):
            return tree.PILL.sub(
                lambda match: (
                    "{{"
                    + aliases[match[1].partition(".")[0]]
                    + "."
                    + match[1].partition(".")[2]
                    + "}}"
                    if match[1].partition(".")[0] in aliases
                    else match[0]
                ),
                value,
            )
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        return value

    for key, names in step.raw.get("_utility_fields", {}).items():
        for row in step.raw.get(key, []):
            if (row.get("element") or row.get("name")) in names:
                row["value"] = remap(row.get("value"))


def _mirror(step: tree.Step) -> None:
    if "data" in step.raw:
        step.raw["data"] = {row["name"]: row["value"] for row in step.raw["inputs"]}


def update(
    step: tree.Step,
    values: dict[str, Any],
    *,
    input_scripts: dict[str, str | None] | None = None,
) -> None:
    kind = step.logic_type
    if kind not in TYPES:
        raise CapabilityError(
            "This utility block is source-generated and cannot be configured by generic edits.",
            "UNAVAILABLE",
        )
    rows = {row["name"]: row for row in _rows(step.raw.get("inputs"))}
    unknown = set(values) - rows.keys() - _EXTRA.get(kind, set())
    if unknown:
        raise CapabilityError(
            "Unknown utility inputs: " + ", ".join(sorted(unknown)) + "."
        )
    modes = input_modes.scripts(input_scripts)
    core_modes = {name: code for name, code in modes.items() if name in rows}
    input_modes.prepare(step, core_modes)
    input_modes.apply(step, {name: code for name, code in core_modes.items() if code})
    pending = step.raw.get("_utility_fields", {})
    if "workflow_reference" in values:
        pending.pop("workflowInputs", None)
    if "flow_template" in values:
        pending.pop("dynamicInputs", None)
    for name, value in values.items():
        if name == "workflow_reference":
            if not isinstance(value, str) or not value.strip() or tree.pills(value):
                raise CapabilityError(
                    "A workflow reference requires a static sys_id or exact name."
                )
            step.raw["workflowReference"] = value
        elif name == "workflow_inputs":
            _staged(step, "workflowInputs", value)
        elif name == "dynamic_inputs":
            _staged(step, "dynamicInputs", value)
        else:
            _set(
                rows[name],
                value,
                reference_name=name in {"flow_template", "timer_schedule"},
            )
    input_modes.apply(
        step, {name: code for name, code in core_modes.items() if not code}
    )
    _mirror(step)


def new_step(
    flow: dict[str, Any],
    definition: dict[str, Any],
    values: dict[str, Any],
    *,
    input_scripts: dict[str, str | None] | None = None,
) -> tree.Step:
    kind = definition.get("type")
    if kind not in TYPES or definition.get("userCanRead") is not True:
        raise CapabilityError(
            "The requested utility block is not an authorable native definition.",
            "UNAVAILABLE",
        )
    required = {
        "TIMER": {"duration_type"},
        "RUNWORKFLOW": {"workflow_reference", "wait"},
        "RUNDYNAMICFLOW": {"flow_template", "flow", "wait_for_completion"},
        "GETFLOWOUTPUTS": {"context"},
    }[kind]
    modes = input_modes.scripts(input_scripts)
    required -= {name for name in ("flow", "context") if modes.get(name)}
    if not required <= values.keys():
        raise CapabilityError(
            "Supply the utility's source reference and asynchronous/wait settings explicitly."
        )
    inputs = []
    for index, parameter in enumerate(_rows(definition.get("inputs"))):
        default = parameter.get("defaultValue", parameter.get("default_value", ""))
        if parameter.get("type") == "boolean":
            default = default in (True, "true", "1")
        inputs.append(
            {
                **copy.deepcopy(parameter),
                "name": parameter["name"],
                "field_name": parameter["name"],
                "parameter": copy.deepcopy(parameter),
                "value": default,
                "displayValue": parameter.get("defaultDisplayValue", ""),
                "children": [],
                "order": index + 1,
                "scriptActive": False,
            }
        )
    raw = native.logic(
        {
            "uiUniqueIdentifier": str(uuid.uuid4()),
            "flowSysId": flow["id"],
            "flowLogicDefinition": copy.deepcopy(definition),
            "inputs": inputs,
        }
    )
    raw["name"] = definition["name"]
    result = tree.Step(raw, "flowlogic")
    update(result, values, input_scripts=modes)
    return result


async def _reference(
    client: ServiceNowClient,
    table: str,
    value: str,
    fields: list[str],
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    check_table_access(table)
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(marker in value for marker in ("^", "{{", "}}"))
    ):
        raise CapabilityError(
            "The native reference requires a static sys_id or exact name."
        )
    if _SYS_ID.fullmatch(value):
        row = await client.get_record(table, value, fields=fields)
        if _value(row, "sys_id") != value:
            raise CapabilityError(
                "The native reference record identity is incomplete.", "UNAVAILABLE"
            )
    else:
        query = ServiceNowQuery().equals("name", value)
        if scope:
            query = query.equals("sys_scope", scope)
        result = await client.query_records(
            table, query.build(), fields=fields, limit=2
        )
        matches = result.get("records")
        if (
            not isinstance(matches, list)
            or len(matches) != 1
            or not isinstance(matches[0], dict)
        ):
            raise CapabilityError(
                "The native reference name is absent or ambiguous; use its sys_id."
            )
        row = matches[0]
        if _value(row, "name") != value:
            raise CapabilityError(
                "The native reference name differs from the requested name.",
                "UNAVAILABLE",
            )
    validate_sys_id(_value(row, "sys_id"))
    if not isinstance(_value(row, "name"), str) or not _value(row, "name"):
        raise CapabilityError(
            "The native reference display name is incomplete.", "UNAVAILABLE"
        )
    if scope is not None and _value(row, "sys_scope") != scope:
        raise CapabilityError(
            "The named native reference does not belong to its selected scope.",
            "UNAVAILABLE",
        )
    return row


async def _flow_definition(
    flow: dict[str, Any],
    reference: str,
    client: ServiceNowClient,
    *,
    qualified: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scope = flow.get("scope")
    if (
        qualified
        and "." in reference
        and re.fullmatch(r"[a-z][a-z0-9_]*", reference.partition(".")[0])
    ):
        scope_name, _, reference = reference.partition(".")
        check_table_access("sys_scope")
        result = await client.query_records(
            "sys_scope",
            ServiceNowQuery().equals("scope", scope_name).build(),
            fields=["sys_id", "scope"],
            limit=2,
        )
        scopes = result.get("records")
        if (
            not isinstance(scopes, list)
            or len(scopes) != 1
            or not isinstance(scopes[0], dict)
            or _value(scopes[0], "scope") != scope_name
        ):
            raise CapabilityError("The callable flow scope is absent or ambiguous.")
        scope = _value(scopes[0], "sys_id")
        if scope != "global":
            validate_sys_id(scope)
    parent = await _reference(
        client,
        "sys_hub_flow",
        reference,
        ["sys_id", "name", "type", "active", "master_snapshot", "sys_scope"],
        scope=scope if not _SYS_ID.fullmatch(reference) else None,
    )
    identity, snapshot = _value(parent, "sys_id"), _value(parent, "master_snapshot")
    if (
        _value(parent, "type") not in {"flow", "subflow"}
        or not isinstance(snapshot, str)
        or not snapshot
    ):
        raise CapabilityError(
            "The utility reference requires a published flow or subflow interface."
        )
    validate_sys_id(snapshot)
    check_table_access("sys_hub_flow_snapshot")
    definition = await client.studio_request(f"flow/{snapshot}", minimal=True)
    if (
        definition.get("id") != snapshot
        or definition.get("parentFlow") != identity
        or definition.get("type") != _value(parent, "type")
        or definition.get("snapshot") is not True
        or definition.get("masterSnapshot") is not True
    ):
        raise CapabilityError(
            "The published utility interface identity is incomplete.", "UNAVAILABLE"
        )
    if definition.get("scope") != _value(parent, "sys_scope"):
        raise CapabilityError(
            "The published utility snapshot scope contradicts its root reference.",
            "UNAVAILABLE",
        )
    if (
        definition.get("security", {}).get("can_read") is not True
        or definition.get("isFlowEntitled") is not True
    ):
        raise CapabilityError(
            "The utility flow definition is not readable and entitled."
        )
    if (
        definition.get("scope") != flow.get("scope")
        and definition.get("access") != "public"
    ):
        raise CapabilityError(
            "The utility flow definition is inaccessible from the target scope."
        )
    payloads.validate_definition(definition, field_name="utility")
    _rows(definition.get("inputs"))
    _rows(definition.get("outputs"))
    return parent, definition


def _schema(row: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(data.spec(row))


def _literal(binding: dict[str, Any]) -> Any:
    value = binding.get("value")
    if isinstance(value, dict | list) or data.whole_pill(value):
        return copy.deepcopy(value)
    if not data.spec(binding).get("type"):
        return value
    kind = data.value_type(binding).kind
    if kind == "object":
        return {row["name"]: _literal(row) for row in binding.get("children", [])}
    if kind.startswith("array."):
        return [_literal(row) for row in binding.get("children", [])]
    return value


def _interface(
    step: tree.Step,
    key: str,
    declarations: list[dict[str, Any]],
    *,
    changed: bool,
    validate_values: bool = True,
) -> None:
    old = {
        row.get("element") or row.get("name"): row
        for row in _rows(step.raw.get(key, []))
    }
    declared = {
        row.get("element") or row.get("name"): row for row in _rows(declarations)
    }
    pending = step.raw.get("_utility_fields", {})
    supplied = set(pending.pop(key, []))
    if not pending:
        step.raw.pop("_utility_fields", None)
    if (supplied if changed else old.keys()) - declared.keys():
        raise CapabilityError(
            "A utility interface input is absent from the current source definition."
        )
    if not changed and old.keys() != declared.keys():
        raise CapabilityError(
            "The saved utility interface differs from its published source.",
            "UNAVAILABLE",
        )
    result = []
    for name, source in declared.items():
        previous = old.get(name)
        schema = _schema(source)
        schema.setdefault("name", name)
        if schema.get("type") in {
            "dynamic_inputs",
            "dynamic_choice",
            "dynamic_template",
        }:
            raise CapabilityError(
                "Provider-backed action input modes are not native workflow/subflow template declarations.",
                "UNAVAILABLE",
            )
        if previous is not None and not changed and _schema(previous) != schema:
            raise CapabilityError(
                "The saved utility input schema drifted from its source.", "UNAVAILABLE"
            )
        binding = copy.deepcopy(source if previous is None or changed else previous)
        if previous is None or changed:
            binding.update(
                name=name,
                parameter=schema,
                children=copy.deepcopy(binding.get("children", [])),
                scriptActive=binding.get("scriptActive", False),
            )
        if previous is not None and changed:
            for field in ("script", "scriptActive", "scriptAsJsonString"):
                if field in previous:
                    binding[field] = copy.deepcopy(previous[field])
        value = (
            previous.get("value")
            if previous is not None
            else source.get(
                "value", source.get("defaultValue", source.get("default_value", ""))
            )
        )
        if previous is not None and name not in supplied:
            value = _literal(previous)
        if (
            schema.get("type") == "boolean"
            and previous is None
            and value in ("", "true", "false", "1", "0")
        ):
            value = value in ("true", "1")
        readonly = any(
            schema.get(field) in (True, "true")
            for field in ("readonly", "readOnly", "read_only")
        )
        if readonly and name in supplied:
            raise CapabilityError(
                "Read-only utility interface inputs cannot be supplied by callers."
            )
        if validate_values and not readonly:
            _set(
                binding if changed or name in supplied else copy.deepcopy(binding),
                value,
            )
        elif previous is None or changed:
            if not readonly and (name in supplied or previous is not None):
                input_modes.value(binding, value, scripted=True)
            else:
                binding["value"] = copy.deepcopy(value)
        result.append(binding)
    step.raw[key] = result


def _timer(step: tree.Step) -> None:
    rows = {row["name"]: row for row in _rows(step.raw.get("inputs"))}
    mode = rows["duration_type"]["value"]
    required = {
        "explicit_duration": {"timer_duration"},
        "relative_duration": {
            "timer_duration",
            "timer_relative_duration_datetime",
            "timer_relative_duration_operator",
        },
        "percentage_duration": {"timer_percentage", "percentage_timer_input_datetime"},
    }.get(mode)
    if required is None:
        raise CapabilityError(
            "The timer duration type is not a native installed choice."
        )
    if any(
        not rows[name].get("scriptActive") and rows[name].get("value") in ("", None)
        for name in required
    ):
        raise CapabilityError(
            "The selected timer mode requires its duration/date/percentage inputs explicitly."
        )
    for row in rows.values():
        if row["name"] != "timer_schedule":
            _set(copy.deepcopy(row), row.get("value"))
    duration = rows["timer_duration"].get("value")
    if duration and not tree.pills(duration):
        day, clock = _stamp(duration)
        seconds = (
            (day - date(1970, 1, 1)).days * 86400
            + clock.hour * 3600
            + clock.minute * 60
            + clock.second
        )
        if seconds > 9999 * 3600 + 59 * 60 + 59:
            raise CapabilityError(
                "The timer duration exceeds the native 9999-hour widget range."
            )
    percentage = rows["timer_percentage"].get("value")
    if (
        percentage not in ("", None)
        and not data.whole_pill(percentage)
        and (type(percentage) is not int or not 0 <= percentage <= 100)
    ):
        raise CapabilityError(
            "The timer percentage requires an integer from zero through 100."
        )


async def _record_input(
    item: dict[str, Any],
    table: str,
    step: tree.Step,
    resolver: PillResolver,
    client: ServiceNowClient,
    *,
    required: bool,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    value = item.get("value")
    if item.get("scriptActive"):
        _set(copy.deepcopy(item), value)
        return {"script": copy.deepcopy(item.get("script"))}
    if value in ("", None):
        if required:
            raise CapabilityError(
                "The utility requires a current record or execution context."
            )
        return {}
    pill = data.whole_pill(value)
    if pill:
        source = await resolver.resolve(pill, step)
        if (
            source["type"] not in {"reference", "document_id"}
            or not source.get("reference")
            or not await resolver._is_table(source["reference"], table)
        ):
            raise CapabilityError(
                "The utility record pill does not match its source table."
            )
        return {"pill": source}
    if tree.pills(value):
        raise CapabilityError(
            "A utility record input requires one complete typed pill."
        )
    validate_sys_id(value)
    check_table_access(table)
    record = await client.get_record(table, value, fields=fields or ["sys_id"])
    if _value(record, "sys_id") != value:
        raise CapabilityError(
            "The utility record identity is incomplete.", "UNAVAILABLE"
        )
    return {"record": record}


def _wait(value: Any) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str) and value in {"true", "false", "1", "0"}:
        return value in {"true", "1"}
    raise CapabilityError(
        "The saved native wait setting is not a supported boolean representation.",
        "UNAVAILABLE",
    )


def output_schema(step: tree.Step) -> list[dict[str, Any]] | None:
    if step.logic_type == "RUNWORKFLOW" and not _wait(
        data.named(step.raw["inputs"], "wait").get("value")
    ):
        return []
    if step.logic_type not in {"RUNDYNAMICFLOW", "GETFLOWOUTPUTS"}:
        return None
    return _rows(step.raw.get("flowLogicDefinition", {}).get("variables"))


def _workflow_interface(
    config: dict[str, Any] | list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    entries = list(config.values()) if isinstance(config, dict) else config
    if len(entries) > 100 or any(not isinstance(entry, dict) for entry in entries):
        raise CapabilityError(
            "The workflow configuration collection is incomplete.", "UNAVAILABLE"
        )
    inputs = [entry["inputs"] for entry in entries if "inputs" in entry]
    tables = [entry["table"] for entry in entries if "table" in entry]
    if (
        len(inputs) > 1
        or len(tables) > 1
        or tables
        and tables[0] is not None
        and not isinstance(tables[0], str)
    ):
        raise CapabilityError(
            "The workflow configuration has ambiguous input interfaces or tables.",
            "UNAVAILABLE",
        )
    return _rows(inputs[0]) if inputs else [], tables[0] if tables else None


def _catalog(step: tree.Step, definition: dict[str, Any]) -> None:
    saved = step.raw["flowLogicDefinition"]
    if (
        definition.get("id") != step.raw.get("definitionId")
        or definition.get("type") != step.logic_type
        or definition.get("userCanRead") is not True
    ):
        raise CapabilityError(
            "The utility definition identity or read access changed.", "UNAVAILABLE"
        )
    expected = {
        row["name"]: native.parameter(row) for row in _rows(definition.get("inputs"))
    }
    actual = {
        row["name"]: native.parameter(data.spec(row))
        for row in _rows(step.raw.get("inputs"))
    }
    if actual != expected:
        raise CapabilityError(
            "The saved utility input schema differs from its installed definition.",
            "UNAVAILABLE",
        )
    saved_static = [
        row
        for row in saved.get("variables", [])
        if row.get("output_type") != DYNAMIC_OUTPUT
    ]
    if saved_static != definition.get("variables", []):
        raise CapabilityError(
            "The utility static output schema differs from its installed definition.",
            "UNAVAILABLE",
        )
    for key, value in definition.items():
        if key != "variables" and saved.get(key) != value:
            raise CapabilityError(
                "The saved utility definition metadata drifted from its installed source.",
                "UNAVAILABLE",
            )


async def _published_workflow(
    client: ServiceNowClient, identity: str, table: str | None
) -> list[dict[str, Any]]:
    # The native picker selects workflow roots having a published available
    # version, not an arbitrary version ID (333409). Installed metadata defines
    # workflow/published/active/table on wf_workflow_version, not wf_workflow.
    check_table_access("wf_workflow_version")
    result = await client.query_records(
        "wf_workflow_version",
        ServiceNowQuery()
        .equals("workflow", identity)
        .equals("published", "true")
        .equals("active", "true")
        .build(),
        fields=["sys_id", "workflow", "published", "active", "table"],
        limit=100,
    )
    versions = result.get("records")
    if (
        not isinstance(versions, list)
        or not versions
        or len(versions) >= 100
        or any(not isinstance(row, dict) for row in versions)
    ):
        raise CapabilityError(
            "The workflow has no readable active published version or exceeds the metadata bound."
        )
    for version in versions:
        validate_sys_id(_value(version, "sys_id"))
        if (
            _value(version, "workflow") != identity
            or _value(version, "published") not in (True, "true")
            or _value(version, "active") not in (True, "true")
            or (_value(version, "table") or "global") != (table or "global")
        ):
            raise CapabilityError(
                "The published workflow metadata contradicts its native configuration.",
                "UNAVAILABLE",
            )
    if len({_value(version, "sys_id") for version in versions}) != len(versions):
        raise CapabilityError(
            "The published workflow version identities are ambiguous.", "UNAVAILABLE"
        )
    return sorted(versions, key=lambda row: _value(row, "sys_id"))


def _contract(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = {
        "name",
        "label",
        "type",
        "reference",
        "choices",
        "choiceOption",
        "mandatory",
        "readonly",
        "readOnly",
        "dependent_on",
        "data_structure",
    }
    result = []
    for row in rows:
        schema = _schema(row)
        view = {key: value for key, value in schema.items() if key in fields}
        view["children"] = _contract(data.children(schema))
        result.append(view)
    return result


async def published_output(
    producer: tree.Step, name: str, resolver: PillResolver
) -> dict[str, Any]:
    """Recheck a complex utility output against its selected published interface."""
    if producer.logic_type not in {"RUNDYNAMICFLOW", "GETFLOWOUTPUTS"}:
        raise CapabilityError(
            "This flow-logic producer has no dynamic interface.", "UNAVAILABLE"
        )
    if producer.logic_type == "RUNDYNAMICFLOW" and not _wait(
        data.named(producer.raw["inputs"], "wait_for_completion")["value"]
    ):
        raise CapabilityError(
            "An asynchronous Dynamic Flow does not expose its template outputs."
        )
    template_id = data.named(producer.raw["inputs"], "flow_template")["value"]
    validate_sys_id(template_id)
    key = "utility/" + template_id
    if key not in resolver.definition_cache:
        _, definition = await _flow_definition(
            resolver.flow, template_id, resolver.client
        )
        resolver.definition_cache[key] = definition
    definition = resolver.definition_cache[key]
    if producer.raw.get("dynamicOutputs") != definition["outputs"]:
        raise CapabilityError(
            "The utility output schema differs from its published template.",
            "UNAVAILABLE",
        )
    output = data.named(definition["outputs"], name)
    stored = data.named(producer.raw["flowLogicDefinition"]["variables"], name)
    if stored != {**copy.deepcopy(output), "output_type": DYNAMIC_OUTPUT}:
        raise CapabilityError(
            "The utility output pill projection differs from its published template.",
            "UNAVAILABLE",
        )
    return _schema(output)


async def hydrate(
    step: tree.Step,
    flow: dict[str, Any],
    client: ServiceNowClient,
    *,
    input_scripts: dict[str, str | None] | None = None,
    resolver: PillResolver,
    definition: dict[str, Any] | None = None,
    changed_inputs: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve native interfaces before common script modes address inherited fields."""
    modes = input_modes.scripts(input_scripts)
    core = {row["name"] for row in step.raw.get("inputs", [])}
    input_modes.apply(
        step, {name: code for name, code in modes.items() if name in core}
    )
    return await prepare(
        step,
        flow,
        client,
        changed_inputs=changed_inputs or set(),
        resolver=resolver,
        definition=definition,
        _hydrating=True,
    )


async def prepare(
    step: tree.Step,
    flow: dict[str, Any],
    client: ServiceNowClient,
    *,
    changed_inputs: set[str],
    resolver: PillResolver,
    definition: dict[str, Any] | None = None,
    _hydrating: bool = False,
) -> dict[str, Any]:
    """Bind source metadata to the preview/apply revision and preserve saved DTOs.

    The planner calls this after alias remapping and before pill discovery. All
    metadata returned here participates in its reference revision fingerprint.
    """
    kind = step.logic_type
    if kind not in TYPES:
        return {}
    if definition is not None:
        _catalog(step, definition)
    new = not step.raw.get("id")
    rows = {row["name"]: row for row in _rows(step.raw.get("inputs"))}
    if any(
        rows[name].get("scriptActive")
        for name in rows.keys() & _STATIC_SCRIPT_SELECTORS
    ):
        raise CapabilityError(
            "Native template and output-visibility selectors require static values."
        )
    if kind == "TIMER":
        if _hydrating:
            return {}
        _timer(step)
        schedule = rows["timer_schedule"]
        value = schedule.get("value")
        _set(copy.deepcopy(schedule), value, reference_name=True)
        evidence = {}
        if not schedule.get("scriptActive") and value and not data.whole_pill(value):
            record = await _reference(client, "cmn_schedule", value, ["sys_id", "name"])
            if (
                not new
                and "timer_schedule" not in changed_inputs
                and (
                    schedule.get("value") != _value(record, "sys_id")
                    or schedule.get("displayValue") != _value(record, "name")
                )
            ):
                raise CapabilityError(
                    "The saved timer schedule metadata drifted.", "UNAVAILABLE"
                )
            if new or "timer_schedule" in changed_inputs:
                schedule.update(
                    value=_value(record, "sys_id"), displayValue=_value(record, "name")
                )
            evidence["schedule"] = record
        elif not schedule.get("scriptActive") and value:
            await resolver.validate(schedule, step)
        _mirror(step)
        return evidence
    if kind == "RUNWORKFLOW":
        workflow = await _reference(
            client,
            "wf_workflow",
            step.raw.get("workflowReference", ""),
            ["sys_id", "name", "sys_scope", "table"],
        )
        identity = _value(workflow, "sys_id")
        config = await client.studio_workflow_config(identity)
        declarations, table = _workflow_interface(config)
        if table == "sc_req_item":
            raise CapabilityError(
                "The native workflow picker excludes Requested Item workflows."
            )
        if table:
            validate_identifier(table)
            if "." in table:
                raise CapabilityError(
                    "Workflow metadata must identify one native table."
                )
        if (_value(workflow, "table") or "global") != (table or "global"):
            raise CapabilityError(
                "The workflow reference table contradicts its native configuration.",
                "UNAVAILABLE",
            )
        versions = await _published_workflow(client, identity, table)
        information = {
            **copy.deepcopy(step.raw.get("workflowInformation", {})),
            "display_value": _value(workflow, "name"),
            "table": table,
        }
        if (
            not new
            and "workflow_reference" not in changed_inputs
            and step.raw.get("workflowInformation") != information
        ):
            raise CapabilityError(
                "The saved workflow reference metadata drifted.", "UNAVAILABLE"
            )
        step.raw.update(workflowReference=identity, workflowInformation=information)
        _interface(
            step,
            "workflowInputs",
            declarations,
            changed=new or "workflow_reference" in changed_inputs,
            validate_values=not _hydrating,
        )
        _wait(rows["wait"].get("value"))
        current = rows["current"]
        if table and table != "global":
            # Studio changes these binding-level UI table slots, not its catalog parameter.
            expected_current = {
                "reference": table,
                "dependent_on": table,
                "table": table,
            }
            if new or "workflow_reference" in changed_inputs:
                current.update(expected_current)
            elif any(
                key in current and current[key] != expected
                for key, expected in expected_current.items()
            ):
                raise CapabilityError(
                    "The saved workflow current-record table metadata drifted.",
                    "UNAVAILABLE",
                )
            record = (
                {}
                if _hydrating
                else await _record_input(
                    current, table, step, resolver, client, required=True
                )
            )
        else:
            record = {}
            if current.get("scriptActive") or current.get("value") not in ("", None):
                raise CapabilityError(
                    "A global workflow does not accept a table-specific current record."
                )
        _mirror(step)
        return {
            "workflow": workflow,
            "config": config,
            "published_versions": versions,
            "current": record,
        }
    template_value = rows["flow_template"].get("value")
    _set(copy.deepcopy(rows["flow_template"]), template_value, reference_name=True)
    evidence = {}
    template = None
    if template_value:
        parent, template = await _flow_definition(flow, template_value, client)
        if (
            not new
            and "flow_template" not in changed_inputs
            and (
                rows["flow_template"].get("value") != _value(parent, "sys_id")
                or rows["flow_template"].get("displayValue") != template["name"]
            )
        ):
            raise CapabilityError(
                "The saved template reference metadata drifted.", "UNAVAILABLE"
            )
        if new or "flow_template" in changed_inputs:
            rows["flow_template"].update(
                value=_value(parent, "sys_id"), displayValue=template["name"]
            )
        evidence["template"] = {"parent": parent, "definition": template}
        resolver.definition_cache["utility/" + rows["flow_template"]["value"]] = (
            template
        )
        changed = new or "flow_template" in changed_inputs
        _interface(
            step,
            "dynamicInputs",
            template["inputs"],
            changed=changed,
            validate_values=kind == "RUNDYNAMICFLOW" and not _hydrating,
        )
        outputs = copy.deepcopy(template["outputs"])
        if not changed and step.raw.get("dynamicOutputs") != outputs:
            raise CapabilityError(
                "The saved dynamic output schema drifted from its template.",
                "UNAVAILABLE",
            )
        step.raw["dynamicOutputs"] = outputs
    elif kind == "RUNDYNAMICFLOW":
        raise CapabilityError("Dynamic Flow requires a published template reference.")
    elif "flow_template" in changed_inputs:
        step.raw["dynamicInputs"] = []
        step.raw["dynamicOutputs"] = []
    elif step.raw.get("dynamicInputs") or step.raw.get("dynamicOutputs"):
        raise CapabilityError(
            "Saved dynamic schemas require their native template reference.",
            "UNAVAILABLE",
        )
    if _hydrating:
        return evidence
    if kind == "RUNDYNAMICFLOW":
        assert template is not None
        wait = _wait(rows["wait_for_completion"].get("value"))
        callable_value = rows["flow"].get("value")
        if (
            not isinstance(callable_value, str)
            or not callable_value
            and not rows["flow"].get("scriptActive")
        ):
            raise CapabilityError(
                "Dynamic Flow requires a callable flow name, sys_id, or typed expression."
            )
        _set(copy.deepcopy(rows["flow"]), callable_value)
        if not rows["flow"].get("scriptActive") and not tree.pills(callable_value):
            parent, called = await _flow_definition(
                flow, callable_value, client, qualified=True
            )
            for collection in ("inputs", "outputs"):
                if _contract(template[collection]) != _contract(called[collection]):
                    raise CapabilityError(
                        "The callable flow interface differs from its selected template."
                    )
            evidence["callable"] = {"parent": parent, "definition": called}
        show_outputs = wait
    else:
        context = rows["context"]
        evidence["context"] = await _record_input(
            context,
            "sys_flow_context",
            step,
            resolver,
            client,
            required=True,
            fields=["sys_id", "flow", "snapshot"],
        )
        pill = (
            data.whole_pill(context.get("value"))
            if not context.get("scriptActive")
            else None
        )
        if pill:
            producer = resolver.scope.steps.get(pill.partition(".")[0])
            if (
                producer
                and producer.logic_type == "RUNDYNAMICFLOW"
                and template is not None
            ):
                source = data.named(producer.raw["inputs"], "flow_template")["value"]
                source_key = "utility/" + source
                source_template = resolver.definition_cache.get(source_key)
                if source_template is None:
                    _, source_template = await _flow_definition(flow, source, client)
                    resolver.definition_cache[source_key] = source_template
                if any(
                    _contract(template[collection])
                    != _contract(source_template[collection])
                    for collection in ("inputs", "outputs")
                ):
                    raise CapabilityError(
                        "Get Flow Outputs must match the context producer's template interface."
                    )
                evidence["context_template"] = source_template
        show_outputs = True
    static = [
        copy.deepcopy(row)
        for row in step.raw["flowLogicDefinition"].get("variables", [])
        if row.get("output_type") != DYNAMIC_OUTPUT
    ]
    projected = static + (
        [
            {**copy.deepcopy(row), "output_type": DYNAMIC_OUTPUT}
            for row in step.raw.get("dynamicOutputs", [])
        ]
        if show_outputs
        else []
    )
    if (
        not new
        and not changed_inputs & {"flow_template", "wait_for_completion"}
        and step.raw["flowLogicDefinition"].get("variables") != projected
    ):
        raise CapabilityError(
            "The saved dynamic output pill projection differs from its native source.",
            "UNAVAILABLE",
        )
    step.raw["flowLogicDefinition"]["variables"] = projected
    _mirror(step)
    return evidence
