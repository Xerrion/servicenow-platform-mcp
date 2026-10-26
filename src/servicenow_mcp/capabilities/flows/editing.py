"""Typed multi-step edits for Workflow Studio drafts.

Each edit is a small JSON object with an ``op`` key. Edits run in order against an
in-memory tree, so one preview can add a trigger, insert flow logic, and move
existing steps into a branch. The result is validated and saved in one PUT.
"""

import copy
import re
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    active_drafts,
    application_triggers,
    assignments,
    calls,
    connection_inputs,
    data,
    decisions,
    declaration_choices,
    declarations,
    dynamic_inputs,
    fields,
    handlers,
    input_modes,
    input_transforms,
    logic_blocks,
    native,
    payloads,
    saved_triggers,
    semantics,
    stages,
    support,
    target,
    template_modes,
    tree,
    widgets,
)
from servicenow_mcp.capabilities.flows import (
    triggers as trigger_edits,
)
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.telemetry import trace_flow_preview_boundary
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


OPS = {
    "insert_action",
    "insert_subflow",
    *handlers.OPS,
    *assignments.OPS,
    "set_trigger",
    "update_trigger",
    "remove_trigger",
    "insert_logic",
    "move_step",
    "delete_step",
    "update_inputs",
    "set_connection_overrides",
    "update_field",
    "create_field",
    "delete_field",
    "set_variables",
    "update_stage",
    "create_stage",
    "delete_stage",
    "attach_stage",
    "detach_stage",
}


def _str(edit: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = edit.get(key)
    if value is None:
        if required:
            raise CapabilityError(f"{edit.get('op')} requires '{key}'.")
        return None
    if not isinstance(value, str) or not value.strip():
        raise CapabilityError(f"'{key}' must be a non-empty string.")
    return value


def _inputs(edit: dict[str, Any]) -> dict[str, Any]:
    value = edit.get("inputs", {})
    if not isinstance(value, dict):
        raise CapabilityError("'inputs' must be an object.")
    return value


def _check_edit(edit: dict[str, Any]) -> None:
    placement = {"parent", "after", "before"}
    allowed = {
        "insert_action": {
            "action_type_sys_id",
            "inputs",
            "ref",
            "input_scripts",
            "input_transforms",
            "connection_overrides",
            *placement,
        },
        "insert_subflow": {
            "subflow_sys_id",
            "inputs",
            "ref",
            "input_scripts",
            "input_transforms",
            "connection_overrides",
            *placement,
        },
        "set_error_handler": {"enabled"},
        "remove_error_handler": set(),
        "set_trigger": {
            "type",
            "template_id",
            "inputs",
            "replace",
            "interface_schemas",
        },
        "update_trigger": {"inputs", "interface_schemas"},
        "remove_trigger": set(),
        "insert_logic": {
            "logic",
            "inputs",
            "ref",
            "decision_inputs",
            "branches",
            "input_scripts",
            "input_transforms",
            *placement,
        },
        "move_step": {"step", *placement},
        "delete_step": {"step", "children", "cascade"},
        "update_inputs": {
            "step",
            "inputs",
            "decision_inputs",
            "branches",
            "children",
            "input_scripts",
            "input_transforms",
            "connection_overrides",
        },
        "set_connection_overrides": {"connection_overrides"},
        "set_variables": {"inputs", "input_scripts", "input_transforms", *placement},
        "assign_outputs": {"outputs", "step", "ref", *placement},
        "clear_output_assignment": {"step", "name"},
        "create_field": {"collection", "name", "parent", *fields.SETTINGS},
        "update_field": {"collection", "name", *fields.SETTINGS},
        "delete_field": {"collection", "name"},
        "create_stage": {
            "label",
            "value",
            "duration",
            "always_show",
            "states",
            "ref",
            "type",
            "step",
        },
        "update_stage": {
            "stage",
            "label",
            "value",
            "duration",
            "always_show",
            "states",
            "order",
        },
        "delete_stage": {"stage"},
        "attach_stage": {"stage", "step"},
        "detach_stage": {"stage", "step"},
    }
    op = edit.get("op")
    if not isinstance(op, str) or op not in allowed or set(edit) - {"op", *allowed[op]}:
        raise CapabilityError(
            "The edit contains unsupported fields or an unavailable operation.",
            "UNAVAILABLE",
        )
    if edit.get("cascade") not in (None, "connected"):
        raise CapabilityError(
            "cascade must be connected. Dependent input values are never cleared automatically."
        )
    if "replace" in edit and not isinstance(edit["replace"], bool):
        raise CapabilityError("replace must be a JSON boolean.")
    if "interface_schemas" in edit and (
        not isinstance(edit["interface_schemas"], dict) or not edit["interface_schemas"]
    ):
        raise CapabilityError(
            "interface_schemas must be a nonempty object; clear an explicit root with children=[]."
        )


def require_editable(
    flow: dict[str, Any], sys_id: str, *, allow_active_draft: bool = False
) -> None:
    payloads.validate_definition(flow)
    for key in (
        "actionInstances",
        "triggerInstances",
        "flowLogicInstances",
        "subFlowInstances",
        "label_cache",
    ):
        if not isinstance(flow.get(key), list):
            raise CapabilityError(
                "Studio returned an incomplete flow definition. No draft write is allowed."
            )
    active = allow_active_draft and active_drafts.eligible(flow)
    if (
        flow.get("id") != sys_id
        or flow.get("type") not in {"flow", "subflow"}
        or (flow.get("active") is not False and not active)
    ):
        raise CapabilityError(
            "Only inactive drafts or supported active V2 root drafts can be edited."
        )
    if flow.get("type") == "subflow" and flow["triggerInstances"]:
        raise CapabilityError("Subflow drafts cannot contain triggers.")
    if (
        (flow.get("status") != "draft" and not active)
        or (flow.get("isPublished") is not False and not active)
        or (flow.get("masterSnapshotId") and not active)
    ):
        raise CapabilityError(
            "Published flows cannot be edited. Studio creates a new draft for them."
        )
    if flow.get("snapshot") is not False or flow.get("masterSnapshot") is not False:
        raise CapabilityError("Snapshots cannot be edited.")
    if flow.get("flowEngineVersion") != 2:
        raise CapabilityError("Only V2 flow definitions are supported.")
    if flow.get("security", {}).get("can_write") is not True:
        raise CapabilityError("Studio does not report write access to this flow.")
    if any(flow.get(key) for key in ("contentACLFilteredOrders", "nonCriticalErrors")):
        raise CapabilityError(
            "Studio returned a partial flow definition. No draft write is allowed."
        )
    semantics.studio_semantics(flow, allow_active_draft=active)


def trigger_catalog(categories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for category in categories:
        triggers = category.get("triggers")
        if not isinstance(triggers, dict):
            raise CapabilityError("Studio returned an unexpected trigger catalog.")
        for definition in triggers.values():
            if (
                not isinstance(definition.get("type"), str)
                or definition["type"] in catalog
            ):
                raise CapabilityError(
                    "Studio returned an ambiguous trigger catalog.", "UNAVAILABLE"
                )
            catalog[definition["type"]] = {
                **definition,
                "triggerType": category["name"],
            }
    return catalog


def new_trigger(
    flow: dict[str, Any], definition: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    known = {i["name"] for i in definition.get("inputs", [])}
    unknown = set(inputs) - known
    if unknown:
        raise CapabilityError(f"Unknown trigger inputs: {', '.join(sorted(unknown))}.")
    values = []
    for spec in definition.get("inputs", []):
        parameter = spec["parameter"]
        if spec["name"] in inputs and parameter.get("readonly"):
            raise CapabilityError("The requested trigger input is read-only.")
        value = inputs.get(
            spec["name"], spec.get("default_value", parameter.get("defaultValue", ""))
        )
        if isinstance(value, dict | list) or tree.pills(value):
            raise CapabilityError(
                "This trigger accepts static scalar configuration only."
            )
        if spec.get("mandatory") and value in ("", None):
            raise CapabilityError(
                f"The {definition['label']} trigger requires input '{spec['name']}'."
            )
        choices = trigger_edits.choices(spec)
        default = spec.get("default_value", parameter.get("defaultValue", ""))
        if (
            choices
            and value not in ("", *choices)
            and not (spec["name"] not in inputs and value == default)
        ):
            raise CapabilityError(
                f"The value is not allowed for trigger input '{spec['name']}'."
            )
        values.append(
            {
                "name": spec["name"],
                "label": spec.get("label", parameter.get("label")),
                "field_name": spec["name"],
                "type": spec["type"],
                "internalType": spec["type"],
                "data_format": "",
                "value": value,
                "displayValue": "",
                "valueSysId": "",
                "order": int(spec.get("order") or 100),
                "mandatory": bool(spec.get("mandatory")),
                "parameter": parameter,
                "uiUniqueId": parameter["id"],
                "children": [],
                "isNew": False,
            },
        )
        if spec.get("choiceList"):
            values[-1]["choiceList"] = copy.deepcopy(spec["choiceList"])
    outputs = [
        {
            "name": spec["name"],
            "label": spec.get("label"),
            "internalType": spec["type"],
            "type": spec["type"],
            "value": spec.get("default_value") or "",
            "displayValue": "",
            "valueSysId": "",
            "order": int(spec.get("order") or 100),
            "mandatory": bool(spec.get("mandatory")),
            "parameter": spec["parameter"],
            "uiUniqueId": spec["parameter"]["id"],
            "children": [],
        }
        for spec in definition.get("outputs", [])
    ]
    return {
        "order": 1,
        "isDirty": False,
        "metadata": {"predicates": []},
        "additionalMetadata": {"query_parse": {"predicates": []}},
        "name": definition["label"],
        "label": definition["label"],
        "type": definition["type"],
        "triggerType": definition["triggerType"],
        "inputs": sorted(values, key=lambda row: row["order"]),
        "outputs": sorted(outputs, key=lambda row: row["order"]),
        "scopeName": flow.get("scopeName", "global"),
        "scopeLabel": flow.get("scopeDisplayName", "Global"),
        "scopeLogo": "",
        "description": definition.get("description", ""),
        "triggerDefinitionId": definition["id"],
        "fromTemplate": False,
        "hasMissingMandatoryInputs": False,
        "flowSysId": flow["id"],
    }


def trigger_label(trigger: dict[str, Any]) -> str:
    # Studio returns only "name" for saved triggers, but "label" and "name" for new ones.
    value = trigger.get("label") or trigger.get("name")
    if not isinstance(value, str) or not value:
        raise CapabilityError(
            "Studio returned a trigger without a name. Inspect the flow.",
            "UNKNOWN_OUTCOME",
        )
    return value


def trigger_lines(triggers: list[dict[str, Any]]) -> list[str]:
    return [f"Trigger: {trigger_label(t)}" for t in triggers] or ["Trigger: none"]


def trigger_pill_prefix(trigger: dict[str, Any]) -> str:
    # Studio names trigger pills "<Trigger name>_1". Only one trigger is allowed per flow.
    return f"{trigger_label(trigger)}_1"


class PillResolver:
    """Resolve data pills against trigger outputs and dictionary metadata."""

    def __init__(
        self,
        client: ServiceNowClient,
        trigger: dict[str, Any] | None,
        flow: dict[str, Any] | None = None,
        roots: list[tree.Step] | None = None,
    ) -> None:
        self.client = client
        self.trigger = trigger
        self.flow = flow or {}
        self.scope = data.Scope(roots or [])
        self.cache: dict[str, dict[str, Any]] = {}
        self.dictionary_cache: dict[tuple[str, str], dict[str, str]] = {}
        self.table_cache: dict[tuple[str, str], bool] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.definition_cache: dict[str, dict[str, Any]] = {}
        self.linked_tables: dict[str, dict[str, Any]] = {}
        self.input_transform_metadata: dict[str, Any] | None = None
        self.input_transform_active: set[str] = set()

    async def static_table(self, source: dict[str, Any]) -> dict[str, Any]:
        table = source.get("value")
        if (
            data.value_type(source).kind != "table_name"
            or not isinstance(table, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", table)
            or source.get("scriptActive")
        ):
            raise CapabilityError(
                "Linked outputs and conditions require a static table-name input.",
                "UNAVAILABLE",
            )
        check_table_access(table)
        if table not in self.linked_tables:
            check_table_access("sys_db_object")
            records = (
                await self.client.query_records(
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
                    "The linked table is absent, ambiguous, or unreadable.",
                    "UNAVAILABLE",
                )
            validate_sys_id(records[0].get("sys_id", ""))
            descriptor = await self.client.studio_table_descriptor(table)
            display = descriptor.get("displayField")
            if not isinstance(display, str) or not re.fullmatch(
                r"[a-z][a-z0-9_]*", display
            ):
                raise CapabilityError(
                    "The linked table descriptor has no readable display field.",
                    "UNAVAILABLE",
                )
            self.linked_tables[table] = {"table": records[0], "descriptor": descriptor}
        return self.linked_tables[table]

    def prescription(self, pill: str) -> str:
        """Project identity pills only; transformed values retain their final types."""
        ordinary = input_transforms.source(self.flow, pill)
        if ordinary == pill:
            return pill
        identity = pill.removeprefix("fd_transform:")
        compounds = self.flow.get("pharmacy", {}).get("pharmacyCompound", {})
        return (
            pill
            if any(row.get("sourceUiId") == identity for row in compounds.values())
            else ordinary
        )

    async def resolve(
        self, pill: str, consumer: tree.Step | None = None
    ) -> dict[str, Any]:
        if pill.startswith("fd_transform:"):
            return await input_transforms.resolve(self.flow, pill, self, consumer)
        pill = self.prescription(pill)
        prefix, separator, suffix = pill.partition(".")
        if not separator or not suffix:
            raise CapabilityError("A pill requires a producer and output path.")
        if len(suffix.split(".")) > 10:
            raise CapabilityError("Pill paths support at most ten fields.")
        if prefix in self.scope.steps or prefix in {"flow_variable", "subflow"}:
            producer = self.scope.steps.get(prefix)
            path = suffix.split(".")
            if producer:
                if consumer:
                    self.scope.visible(producer, consumer, path)
                definition = producer.raw.get(
                    "actionType"
                    if producer.kind == "action"
                    else "subFlow"
                    if producer.kind == "subflow"
                    else "flowLogicDefinition",
                    {},
                )
                if (
                    definition.get("userCanRead") is False
                    or definition.get("security", {}).get("can_read") is False
                ):
                    raise CapabilityError(
                        "The producer definition is not readable.", "UNAVAILABLE"
                    )
                outputs = definition.get(
                    "variables" if producer.kind == "flowlogic" else "outputs"
                )
                if producer.logic_type == handlers.CATCH:
                    outputs = handlers.outputs(definition)
                elif producer.logic_type in {"DECISION", "DECISIONBLOCK"}:
                    outputs = decisions.output_schema(
                        producer, list(self.scope.steps.values())
                    )
                elif producer.logic_type in logic_blocks.TYPES:
                    selected_outputs = logic_blocks.output_schema(producer)
                    if selected_outputs is not None:
                        outputs = selected_outputs
                if (
                    producer.kind == "action"
                    and definition.get("type") == "GETCATALOGVARIABLES"
                ):
                    outputs = producer.raw.get("outputs")
                label = f"{producer.raw['order']} - {producer.raw.get('name', producer.kind)}"
            else:
                outputs = self.flow.get(
                    "flowVariables" if prefix == "flow_variable" else "inputs"
                )
                if prefix == "subflow" and self.flow.get("triggerInstances"):
                    raise CapabilityError(
                        "Trigger-owned flow inputs are available through the trigger, not subflow pills.",
                    )
                label = "Flow Variables" if prefix == "flow_variable" else "Input"
            if pill not in self.cache:
                output = data.named(outputs, path[0])
                if producer:
                    output = await calls.linked_output(producer, output, self)
                    if (
                        producer.raw.get("actionType", {}).get("type")
                        == "GETCATALOGVARIABLES"
                    ):
                        output = data.catalog_schema(output)
                if producer and producer.logic_type == "FOREACH" and path[0] == "item":
                    output = copy.deepcopy(output)
                    items = data.named(producer.raw["inputs"], "items")["value"]
                    references = tree.pills(items)
                    if (
                        len(references) != 1
                        or items != "{{" + next(iter(references)) + "}}"
                    ):
                        raise CapabilityError(
                            "For Each item typing requires one Records pill.",
                            "UNAVAILABLE",
                        )
                    source = await self.resolve(next(iter(references)), producer)
                    if source["type"] != "records" or not source.get("reference"):
                        raise CapabilityError(
                            "For Each requires a Records producer with a known table."
                        )
                    output.update(type="reference", reference=source["reference"])
                    source_producer = self.scope.steps.get(
                        next(iter(references)).partition(".")[0]
                    )
                    if (
                        source_producer is not None
                        and source_producer.logic_type == "DECISION"
                        and source["reference"] == "sys_decision_multi_result"
                    ):
                        output["attributes"] = {
                            **data.spec(output).get("attributes", {}),
                            "decision_table": data.named(
                                source_producer.raw["inputs"], "decision_table"
                            )["value"],
                        }
                attributes = data.spec(output).get("attributes", {})
                decision_id = (
                    attributes.get("decision_table")
                    if isinstance(attributes, dict)
                    else None
                )
                if (
                    data.value_type(output).reference == "sys_decision_multi_result"
                    and decision_id
                    and len(path) > 1
                ):
                    if path[1] != "result_elements" or len(path) < 3:
                        raise CapabilityError(
                            "Decision multi-result fields require result_elements and a native result field."
                        )
                    validate_sys_id(decision_id)
                    key = "decision/answers/" + decision_id
                    if key not in self.definition_cache:
                        self.definition_cache[key] = await decisions.answer_fields(
                            decision_id, self.client
                        )
                    label += (
                        "\u279b"
                        + str(
                            data.spec(output).get("label")
                            or output.get("display_name")
                            or output["name"]
                        )
                        + "\u279bResult elements"
                    )
                    output = data.named(self.definition_cache[key]["fields"], path[2])
                    path = [path[0], *path[3:]]
                for path_index, field in enumerate(path[1:], start=1):
                    value_type = data.value_type(output)
                    if value_type.many:
                        raise CapabilityError(
                            "Select a loop item before traversing a collection pill."
                        )
                    if value_type.kind in {"reference", "document_id"}:
                        if not value_type.reference:
                            raise CapabilityError(
                                "The output's reference table is unavailable.",
                                "UNAVAILABLE",
                            )
                        remaining = path[path_index:]
                        resolved = await self._field(
                            pill, label, value_type.reference, remaining
                        )
                        self.cache[pill] = resolved
                        break
                    output = data.child(output, field)
                else:
                    kind = data.value_type(output)
                    parameter = data.spec(output)
                    if kind.kind in declarations.LABELS or kind.kind == "array":
                        self.schemas[pill] = copy.deepcopy(parameter)
                    self.cache[pill] = {
                        "name": pill,
                        "label": label
                        + "\u279b"
                        + str(parameter.get("label") or output.get("name")),
                        "reference": kind.reference,
                        "reference_display": parameter.get("reference_display", ""),
                        "type": kind.kind,
                        "base_type": kind.kind,
                        "parent_table_name": None,
                        "column_name": None,
                        "choices": parameter.get("choices"),
                        "attributes": parameter.get("attributes"),
                    }
            return self.cache[pill]
        if self.trigger is None:
            raise CapabilityError(
                f"Data pill '{pill}' needs a trigger or a readable producer."
            )
        prefix = trigger_pill_prefix(self.trigger)
        if not pill.startswith(prefix + "."):
            raise CapabilityError(
                f"Data pill '{pill}' does not reference the trigger '{prefix}'."
            )
        path = pill[len(prefix) + 1 :].split(".")
        output = next(
            (o for o in self.trigger["outputs"] if o["name"] == path[0]), None
        )
        if output is None:
            raise CapabilityError(f"The trigger has no output named '{path[0]}'.")
        if data.value_type(output).kind in declarations.LABELS:
            key = "trigger/" + self.trigger["type"] + "/" + path[0]
            if key not in self.definition_cache:
                self.definition_cache[key] = await application_triggers.output_schema(
                    self.trigger, path[0], self.client
                )
            output = {**output, "parameter": self.definition_cache[key]}
        trigger_name = f"Trigger - {trigger_label(self.trigger)}"
        parameter = data.spec(output)
        label = f"{trigger_name}\u279b{parameter.get('label') or output.get('label') or output['name']}"
        resolved_type = trigger_edits.output_type(self.trigger, output)
        for path_index, field in enumerate(path[1:], start=1):
            if resolved_type.many:
                raise CapabilityError(
                    "Select a loop item before traversing a collection pill."
                )
            if resolved_type.kind in {"reference", "document_id"}:
                if not resolved_type.reference:
                    raise CapabilityError(
                        "The trigger reference table is unavailable.", "UNAVAILABLE"
                    )
                if pill not in self.cache:
                    self.cache[pill] = await self._field(
                        pill, label, resolved_type.reference, path[path_index:]
                    )
                return self.cache[pill]
            output = data.child(output, field)
            parameter = data.spec(output)
            label += "\u279b" + str(parameter.get("label") or output["name"])
            resolved_type = data.value_type(output)
        if resolved_type.kind in declarations.LABELS or resolved_type.kind == "array":
            self.schemas[pill] = copy.deepcopy(parameter)
        self.cache[pill] = {
            "name": pill,
            "label": label,
            "reference": resolved_type.reference,
            "reference_display": parameter.get("reference_display", ""),
            "type": resolved_type.kind,
            "base_type": resolved_type.kind,
            "parent_table_name": None,
            "column_name": None,
            "choices": parameter.get("choices"),
            "attributes": parameter.get("attributes"),
        }
        return self.cache[pill]

    async def validate(
        self,
        item: dict[str, Any],
        consumer: tree.Step,
        declared: dict[str, Any] | None = None,
    ) -> None:
        if (
            widgets.kind(declared if declared is not None else item)
            in template_modes.TYPES
        ):
            await self._template(
                item, declared if declared is not None else data.spec(item), consumer
            )
            return
        await input_modes.validate(item, consumer, self)
        if item.get("scriptActive"):
            return
        for binding, parameter in self._bindings(item, declared):
            kind = widgets.kind(parameter)
            if widgets.kind(parameter) in template_modes.TYPES:
                await self._template(binding, parameter, consumer)
                continue
            if binding is not item:
                await input_modes.validate(binding, consumer, self)
                if binding.get("scriptActive"):
                    continue
            if kind == "conditions" and (
                consumer.kind == "action" or consumer.logic_type == "DECISION"
            ):
                schema = data.spec(parameter)
                dependency = schema.get("dependent_on")
                if not isinstance(dependency, str) or not dependency:
                    raise CapabilityError(
                        "Action conditions require a declared static table dependency.",
                        "UNAVAILABLE",
                    )
                source = data.named(
                    consumer.raw[
                        "decisionTableInputs"
                        if consumer.logic_type == "DECISION"
                        else "inputs"
                    ],
                    dependency.removeprefix("inputs."),
                )
                await self.static_table(source)
                widgets.validate("conditions", binding.get("value"), schema)
                for path in widgets.condition_paths(binding["value"]):
                    await self._field(
                        "condition." + ".".join(path),
                        "Condition",
                        source["value"],
                        path,
                    )
            if kind == "field_list":
                schema = data.spec(parameter)
                if dependency := schema.get("dependent_on"):
                    source = data.named(
                        consumer.raw[
                            "decisionTableInputs"
                            if consumer.logic_type == "DECISION"
                            else "inputs"
                        ],
                        dependency.removeprefix("inputs."),
                    )
                else:
                    source = {
                        "parameter": {"type": "table_name"},
                        "value": schema.get("reference"),
                    }
                await self.static_table(source)
            approval_types = (
                dict(widgets.approval_references(binding.get("value", "")))
                if kind == "approval_rules"
                else {}
            )
            for value, target_kind in widgets.pill_bindings(
                binding, kind, data.spec(parameter)
            ):
                if isinstance(value, str):
                    remainder = tree.PILL.sub("", value)
                    if "{{" in remainder or "}}" in remainder:
                        raise CapabilityError(
                            "A saved binding contains malformed pill syntax."
                        )
                for pill in tree.pills(value):
                    if kind == "approval_rules" and pill.startswith("static."):
                        continue  # The native approval parser validates these literal identities.
                    resolved = await self.resolve(pill, consumer)
                    source = data.ValueType(
                        resolved["type"], resolved.get("reference") or ""
                    )
                    target_type = data.value_type(parameter)
                    target = data.ValueType(
                        target_kind or target_type.kind, target_type.reference
                    )
                    interpolated = kind == "json" or value != "{{" + pill + "}}"
                    if kind == "approval_rules":
                        expected = approval_types.get(pill)
                        if source.kind not in {"reference", "document_id"} or (
                            source.reference not in {"sys_user", "sys_user_group"}
                            if expected == "unknown"
                            else source.reference != expected
                        ):
                            raise CapabilityError(
                                "Approval approvers must use the declared user/group reference type."
                            )
                        continue
                    if kind == "schedule_date_time":
                        if interpolated or source.kind not in {
                            "date",
                            "datetime",
                            "glide_date",
                            "glide_date_time",
                            "due_date",
                            "schedule_date_time",
                            "integer_date",
                        }:
                            raise CapabilityError(
                                "Approval dates require one native Date-family producer pill."
                            )
                        continue
                    data.compatible(source, target, interpolated=interpolated)
                    if not interpolated and (
                        target.kind in {"object", "array"}
                        or target.kind.startswith("array.")
                    ):
                        if input_modes.readonly(parameter):
                            raise CapabilityError(
                                "Whole complex pill targets cannot be read-only.",
                                "UNAVAILABLE",
                            )
                        if binding.get("children"):
                            raise CapabilityError(
                                "Whole complex pills cannot coexist with child values."
                            )
                        schema = self.schemas.get(pill)
                        if schema is None:
                            raise CapabilityError(
                                "The whole-pill producer schema is unavailable.",
                                "UNAVAILABLE",
                            )
                        await self._structure(schema, parameter)
                        produced = await self._whole_schema(pill, consumer)
                        await self._structure(produced, parameter)
                    if not interpolated and target.kind == "choice":
                        allowed = {
                            c["value"] for c in data.spec(parameter).get("choices", [])
                        }
                        produced = {c["value"] for c in resolved.get("choices") or []}
                        if not allowed or not produced or not produced <= allowed:
                            raise CapabilityError(
                                "Choice pills require a declared producer choice set contained in the target choices."
                            )
                    if (
                        target.reference
                        and source.reference
                        and not await self._is_table(source.reference, target.reference)
                    ):
                        raise CapabilityError(
                            "The pill reference table is incompatible with the input."
                        )

    async def _template(
        self, binding: dict[str, Any], parameter: dict[str, Any], consumer: tree.Step
    ) -> None:
        await template_modes.validate_binding(binding, parameter, consumer, self)

    def _bindings(
        self, item: dict[str, Any], declared: dict[str, Any] | None = None
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        parameter = declared if declared is not None else data.spec(item)
        if data.spec(parameter).get("type") in dynamic_inputs.MODES:
            for binding in dynamic_inputs.bindings(item):
                yield from self._bindings(binding)
            return
        yield item, parameter
        actual_children = item.get("children", [])
        if not isinstance(actual_children, list) or any(
            not isinstance(child, dict) for child in actual_children
        ):
            raise CapabilityError("Complex input values are malformed.", "UNAVAILABLE")
        if not actual_children:
            return
        kind = data.value_type(parameter).kind
        if declared is not None and kind in declarations.LABELS:
            schema = data.children(parameter)
            for child in actual_children:
                target = (
                    data.child(parameter, child["name"].split(".")[-1])
                    if kind == "object"
                    else schema[0]
                )
                yield from self._bindings(child, target)
        else:
            for child in actual_children:
                yield from self._bindings(child)

    async def _whole_schema(self, pill: str, consumer: tree.Step) -> dict[str, Any]:
        await self.resolve(pill, consumer)
        schema = self.schemas.get(pill)
        if schema is None:
            raise CapabilityError(
                "The whole-pill producer schema is unavailable.", "UNAVAILABLE"
            )
        if pill.startswith("fd_transform:"):
            return copy.deepcopy(schema)
        if self.trigger is not None and pill.startswith(
            trigger_pill_prefix(self.trigger) + "."
        ):
            path = pill[len(trigger_pill_prefix(self.trigger)) + 1 :].split(".")
            key = "trigger/" + self.trigger["type"] + "/" + path[0]
            if key not in self.definition_cache:
                self.definition_cache[key] = await application_triggers.output_schema(
                    self.trigger, path[0], self.client
                )
            current_schema = self.definition_cache[key]
            for segment in path[1:]:
                current_schema = data.child(current_schema, segment)
            await self._structure(current_schema, schema)
            await self._structure(schema, current_schema)
            return current_schema
        prefix, _, path = pill.partition(".")
        producer = self.scope.steps.get(prefix)
        if producer is None and prefix != "subflow":
            raise CapabilityError(
                "Whole pills require a declared subflow input or a supported step output.",
                "UNAVAILABLE",
            )
        if producer is not None:
            if producer.raw.get("actionType", {}).get("type") == "GETCATALOGVARIABLES":
                key = "catalog/" + producer.uid
                if key not in self.definition_cache:
                    self.definition_cache[key] = await calls.catalog_selection(
                        producer, self.flow, self.client, changed=False
                    )
                segments = path.split(".")
                current_schema = data.catalog_schema(
                    data.named(self.definition_cache[key]["schema"], segments[0])
                )
                for segment in segments[1:]:
                    current_schema = data.child(current_schema, segment)
                await self._structure(current_schema, schema)
                await self._structure(schema, current_schema)
                return current_schema
            if producer.kind == "flowlogic" and producer.logic_type in {
                "RUNDYNAMICFLOW",
                "GETFLOWOUTPUTS",
            }:
                segments = path.split(".")
                current_schema = await logic_blocks.published_output(
                    producer, segments[0], self
                )
                for segment in segments[1:]:
                    current_schema = data.child(current_schema, segment)
                await self._structure(current_schema, schema)
                await self._structure(schema, current_schema)
                return current_schema
            if producer.kind not in {"action", "subflow"}:
                raise CapabilityError(
                    "Whole pills from this flow-logic family require another schema adapter.",
                    "UNAVAILABLE",
                )
            identity = str(
                producer.raw[
                    "actionTypeSysId" if producer.kind == "action" else "subflowSysId"
                ]
            )
            key = producer.kind + "/" + identity
            if key not in self.definition_cache:
                if len(self.definition_cache) >= 100:
                    raise CapabilityError(
                        "Whole-pill validation supports at most 100 producer definitions.",
                        "UNAVAILABLE",
                    )
                check_table_access(
                    "sys_hub_action_type_definition"
                    if producer.kind == "action"
                    else "sys_hub_flow"
                )
                current = await self.client.studio_request(
                    "action/action_types/" + identity
                    if producer.kind == "action"
                    else "flow/" + identity,
                    minimal=producer.kind == "subflow",
                )
                if (
                    current.get("id") != identity
                    or current.get("security", {}).get("can_read") is not True
                ):
                    raise CapabilityError(
                        "The current whole-pill producer definition is unreadable.",
                        "UNAVAILABLE",
                    )
                if producer.kind == "action" and (
                    current.get("entitled") is not True
                    or current.get("state") != "published"
                    or current.get("is_snapshot") is not True
                ):
                    raise CapabilityError(
                        "Whole action pills require an entitled published snapshot.",
                        "UNAVAILABLE",
                    )
                if producer.kind == "subflow" and (
                    current.get("type") != "subflow"
                    or current.get("isFlowEntitled") is not True
                    or current.get("snapshot") is not True
                    or current.get("masterSnapshot") is not True
                ):
                    raise CapabilityError(
                        "Whole subflow pills require an entitled published snapshot.",
                        "UNAVAILABLE",
                    )
                self.definition_cache[key] = current
            current_schema = data.named(
                self.definition_cache[key].get("outputs"), path.split(".")[0]
            )
            for segment in path.split(".")[1:]:
                current_schema = data.child(current_schema, segment)
            # Both directions establish that embedded saved schema has not
            # drifted from the independent definition used for this preview.
            await self._structure(current_schema, schema)
            await self._structure(schema, current_schema)
            schema = current_schema
        return schema

    async def _structure(
        self, source: dict[str, Any], target: dict[str, Any], depth: int = 0
    ) -> None:
        if depth > 10:
            raise CapabilityError(
                "Complex pill schemas support at most ten levels.", "UNAVAILABLE"
            )
        produced, wanted = data.value_type(source), data.value_type(target)
        if any(
            kind.kind in {"reference", "document_id"} and not kind.reference
            for kind in (produced, wanted)
        ):
            raise CapabilityError(
                "Whole-pill reference fields require declared source and target tables.",
                "UNAVAILABLE",
            )
        allowed = (
            fields.INTERFACE_TYPES.keys()
            | widgets.TEXT_TYPES
            | widgets.NUMERIC_TYPES
            | widgets.INTEGER_TYPES
            | widgets.TEMPORAL_TYPES
            | {"choice", "document_id", "array", "datetime"}
        )
        if produced.kind not in allowed or wanted.kind not in allowed:
            raise CapabilityError(
                "This whole-pill field type requires another schema adapter.",
                "UNAVAILABLE",
            )
        data.compatible(produced, wanted, interpolated=False)
        if wanted.kind == "array" and (data.children(source) or data.children(target)):
            raise CapabilityError(
                "Generic Array pills cannot carry an unregistered nested item schema.",
                "UNAVAILABLE",
            )
        if (
            wanted.reference
            and produced.reference
            and not await self._is_table(produced.reference, wanted.reference)
        ):
            raise CapabilityError(
                "A complex pill reference field has an incompatible table."
            )
        if wanted.kind in declarations.LABELS:
            # No schema is inferred from an absent children member. An explicit
            # empty Object is a known empty schema, not dynamic JSON.
            def children(row: dict[str, Any]) -> list[dict[str, Any]]:
                parameter = data.spec(row)
                if "children" not in parameter:
                    raise CapabilityError(
                        "The complex pill schema is incomplete.", "UNAVAILABLE"
                    )
                if (
                    parameter.get("dynamic")
                    or parameter.get("data_structure")
                    or parameter.get("dependent_on")
                ):
                    raise CapabilityError(
                        "Dynamic whole-pill schemas require another adapter.",
                        "UNAVAILABLE",
                    )
                return data.children(row)

            left, right = children(source), children(target)
            if wanted.kind == "object":

                def named(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
                    if any(
                        not isinstance(row.get("name"), str) or not row["name"]
                        for row in rows
                    ):
                        raise CapabilityError(
                            "Complex pill fields require readable names.", "UNAVAILABLE"
                        )
                    result = {row["name"].split(".")[-1]: row for row in rows}
                    if len(result) != len(rows):
                        raise CapabilityError(
                            "Complex pill field names are ambiguous.", "UNAVAILABLE"
                        )
                    return result

                left, right = named(left), named(right)
                if set(left) != set(right):
                    raise CapabilityError(
                        "Whole Object pills require the same declared field names."
                    )
                for name in right:
                    await self._structure(left[name], right[name], depth + 1)
            else:
                if (
                    wanted.kind not in declarations.ARRAYS
                    or len(left) != 1
                    or len(right) != 1
                    or data.value_type(left[0]).kind != declarations.ARRAYS[wanted.kind]
                    or data.value_type(right[0]).kind
                    != declarations.ARRAYS[wanted.kind]
                ):
                    raise CapabilityError(
                        "Whole Array pills require one supported declared item schema.",
                        "UNAVAILABLE",
                    )
                await self._structure(left[0], right[0], depth + 1)
        elif wanted.kind == "choice":
            allowed = {row["value"] for row in declarations.choices(data.spec(target))}
            actual = {row["value"] for row in declarations.choices(data.spec(source))}
            if not allowed or not actual or not actual <= allowed:
                raise CapabilityError(
                    "Whole-pill choice fields require compatible declared choice sets."
                )

    async def _is_table(self, source: str, target: str) -> bool:
        check_table_access("sys_db_object")
        validate_identifier(target)
        pair = source, target
        if pair in self.table_cache:
            return self.table_cache[pair]
        seen = set()
        for _ in range(10):
            if source == target:
                self.table_cache[pair] = True
                return True
            if source in seen:
                break
            seen.add(source)
            validate_identifier(source)
            result = await self.client.query_records(
                "sys_db_object",
                f"name={source}",
                fields=["super_class.name"],
                limit=1,
            )
            parent = (result["records"] or [{}])[0].get("super_class.name")
            source = (parent.get("value") if isinstance(parent, dict) else parent) or ""
            if not source:
                break
        self.table_cache[pair] = False
        return False

    async def _field(
        self, pill: str, label: str, table: str, path: list[str]
    ) -> dict[str, Any]:
        check_table_access(table)
        for name in path[:-1]:
            validate_identifier(name)
            row = await self._dictionary(table, name)
            if row.get("internal_type") != "reference" or not row.get("reference"):
                raise CapabilityError(f"'{name}' on {table} is not a reference field.")
            label = f"{label}\u279b{row['column_label']}"
            table = row["reference"]
            check_table_access(table)
        column = path[-1]
        validate_identifier(column)
        row = await self._dictionary(table, column)
        kind = row.get("internal_type") or "string"
        # Studio stores the referenced table, not the ancestor that defines the column.
        # Choice lists can be overridden per table, so prefer the referenced table's own list.
        choices = None
        if row.get("choice") not in ("", "0"):
            choices = await self._choices(table, column) or await self._choices(
                row["name"], column
            )
        return {
            "name": pill,
            "label": f"{label}\u279b{row['column_label']}",
            "reference": row.get("reference") or "",
            "reference_display": row["column_label"],
            "type": "choice" if choices else kind,
            "base_type": "choice" if choices else kind,
            "parent_table_name": table,
            "column_name": column,
            "choices": choices,
            "attributes": None,
        }

    async def _dictionary(self, table: str, column: str) -> dict[str, str]:
        check_table_access("sys_dictionary")
        validate_identifier(table)
        pair = table, column
        if pair in self.dictionary_cache:
            return self.dictionary_cache[pair]
        chain = [table]
        for _ in range(10):
            result = await self.client.query_records(
                "sys_dictionary",
                f"nameIN{','.join(chain)}^element={column}",
                fields=[
                    "name",
                    "element",
                    "column_label",
                    "internal_type",
                    "reference",
                    "choice",
                ],
                limit=len(chain),
            )
            rows = {r["name"]: r for r in result["records"]}
            for name in chain:
                if name in rows:
                    row = rows[name]
                    resolved: dict[str, str] = {
                        k: str((v.get("value") if isinstance(v, dict) else v) or "")
                        for k, v in row.items()
                    }
                    if not resolved.get("internal_type") or "reference" not in row:
                        raise CapabilityError(
                            "Dictionary field type metadata is incomplete.",
                            "UNAVAILABLE",
                        )
                    self.dictionary_cache[pair] = resolved
                    return resolved
            parent = await self.client.query_records(
                "sys_db_object",
                f"name={chain[-1]}",
                fields=["super_class.name"],
                limit=1,
            )
            super_name = (parent["records"] or [{}])[0].get("super_class.name")
            if isinstance(super_name, dict):
                super_name = super_name.get("value")
            if not super_name:
                break
            validate_identifier(super_name)
            if super_name in chain:
                raise CapabilityError(
                    "Dictionary inheritance is cyclic.", "UNAVAILABLE"
                )
            chain.append(super_name)
        raise CapabilityError(
            f"Field '{column}' was not found on {table} or its parent tables."
        )

    async def _choices(self, table: str, column: str) -> list[dict[str, Any]]:
        check_table_access("sys_choice")
        result = await self.client.query_records(
            "sys_choice",
            f"name={table}^element={column}^inactive=false^language=en^ORDERBYsequence",
            fields=["label", "value"],
            limit=200,
        )
        if len(result["records"]) >= 200:
            raise CapabilityError(
                "Choice metadata reached its bounded limit.", "UNAVAILABLE"
            )
        return [
            {
                "used": False,
                "label": row["label"],
                "image": "",
                "reference": False,
                "rawLabel": row["label"],
                "selected": False,
                "missing": False,
                "value": row["value"],
                "parameters": {"name": table, "dependent_values": [""]},
            }
            for row in result["records"]
        ]


async def load_state(
    command: FlowCommand,
    client: ServiceNowClient,
    *,
    initial_flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check_table_access("sys_hub_flow_logic_definition")
    flow = (
        initial_flow
        if initial_flow is not None
        else await client.studio_request(f"flow/{command.sys_id}")
    )
    require_editable(
        flow,
        flow["id"] if initial_flow is not None else command.sys_id or "",
        allow_active_draft=command.action == "edit",
    )
    if (
        command.expected_scope is not None
        and flow.get("scope") != command.expected_scope
    ):
        raise CapabilityError(
            "The Studio target scope differs from expected_scope.", "CONTEXT_MISMATCH"
        )
    if any(not isinstance(edit.get("op"), str) for edit in command.edits or []):
        raise CapabilityError("Each edit requires a string op.")
    needs = {str(edit.get("op")) for edit in command.edits or []}
    unknown = needs - OPS
    if unknown:
        raise CapabilityError(
            f"Unsupported edit operation(s): {', '.join(sorted(unknown))}. Allowed: {', '.join(sorted(OPS))}.",
        )
    logic = (
        await client.studio_definitions("flow_logic")
        if needs & {"insert_logic", "set_variables", *assignments.OPS, *handlers.OPS}
        or handlers.pair(tree.build_tree(flow))
        or any(
            step.logic_type in {"DECISION", "DECISIONBLOCK", *logic_blocks.TYPES}
            for step in tree.walk(tree.build_tree(flow))
        )
        else []
    )
    triggers = (
        await client.studio_definitions("triggers") if needs & trigger_edits.OPS else []
    )
    state = {"flow": flow, "logic": logic, "triggers": triggers}
    if needs & trigger_edits.OPS:
        template_ids = {
            _str(edit, "template_id", required=True) or ""
            for edit in command.edits or []
            if edit.get("op") == "set_trigger" and "template_id" in edit
        }
        template_ids.update(
            item["triggerDefinitionId"]
            for item in flow["triggerInstances"]
            if saved_triggers.is_saved(item)
        )
        if template_ids:
            saved_triggers.require_version(flow)
            state["saved_triggers"] = {
                identity: await saved_triggers.read(identity, client)
                for identity in sorted(template_ids)
            }
    if active_drafts.eligible(flow):
        state["active_runtime"] = await active_drafts.read(command, flow, client)
    return state


def _native_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    """Published editor descriptors to persisted parameter DTOs.

    Source: flowDesignParameterTemplateLiteralFrom:471783-471840 excludes the
    editor-only descriptor keys below. Empty optional display/link fields carry
    no default label or dependency. Nonempty values and all attributes stay.
    """
    result = copy.deepcopy(parameter)
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


def _native_variable_step(step: tree.Step) -> None:
    """Construct a fresh assignment, without saved-instance ownership fields.

    Studio addNewInstance:206651-206672 calls buildNewFlowLogicInstance:221783.
    Neither supplies flowBlockId. The native GET fixture establishes DTO defaults;
    binding/parameter serialization is defined at 472022 and 471783. Only new
    bindings are projected here. Saved steps and their opaque metadata stay intact.
    """
    definition = step.raw["flowLogicDefinition"]
    if (
        definition.get("type") != "SETFLOWVARIABLES"
        or definition.get("userCanRead") is not True
        or definition.get("inputs") != []
        or not isinstance(definition.get("id"), str)
        or not isinstance(definition.get("name"), str)
        or not definition["name"]
    ):
        raise CapabilityError(
            "The assignment catalog definition is incomplete.", "UNAVAILABLE"
        )
    validate_sys_id(definition["id"])
    inputs = copy.deepcopy(step.raw["inputs"])
    for binding in inputs:
        parameter = binding["parameter"]
        if parameter.get("type") != "string":
            continue
        for key, empty in (
            ("value", ""),
            ("display_value", ""),
            ("scriptActive", False),
        ):
            if key in parameter:
                if fingerprint(parameter[key]) != fingerprint(empty):
                    raise CapabilityError(
                        "The declaration has non-default runtime state.", "UNAVAILABLE"
                    )
                parameter.pop(key)
        # The UI cache is hydrated from attributes during readback, not stored as
        # a parameter field. All attributes, including unknown entries, remain.
        if "uiUniqueId" in parameter and parameter["uiUniqueId"] != parameter.get(
            "attributes", {}
        ).get("uiUniqueId"):
            raise CapabilityError(
                "The declaration UI identities conflict.", "UNAVAILABLE"
            )
        parameter.pop("uiUniqueId", None)
        for key in ("defaultDisplayValue", "internal_link"):
            if parameter.get(key) == "":
                parameter.pop(key)
        parameter.setdefault("hint", "")
        parameter.setdefault("ref_qual", "")
        # Flow-variable zero means no declared length. The native String
        # parameter DTO has its own 8000-character default (captured GET fixture).
        # This is construction, not size normalization during save comparison.
        if parameter.get("maxsize") == 0:
            parameter["maxsize"] = 8000
        if parameter.get("type_label") == "string":
            # getAdvancedInputNewItem:147809-147822 initializes this UI label.
            parameter["type_label"] = fields.TYPES["string"]
    step.raw = {
        "uiUniqueIdentifier": step.uid,
        "flowSysId": step.raw["flowSysId"],
        "type": "flowlogic",
        "definitionId": definition["id"],
        "flowLogicDefinition": copy.deepcopy(definition),
        "quiescence": definition.get("quiescence"),
        "inputs": inputs,
        "flowVariables": copy.deepcopy(inputs),
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
        "name": definition["name"] + ": ",
        "internalName": "",
    }


async def plan(
    command: FlowCommand, state: dict[str, Any], client: ServiceNowClient
) -> dict[str, Any]:
    """Apply the edits in memory and return the payload, outline, and change summary."""
    flow = state["flow"]
    trace_flow_preview_boundary(
        "planner.in", action=command.action, edits=command.edits, flow=flow
    )
    roots = tree.build_tree(flow)
    handlers.validate(flow, roots)
    if handlers.pair(roots):
        handlers.check_catalog(roots, state["logic"])
    assignments.hydrate(roots)
    script_bindings = input_modes.Scripts(roots)
    triggers = list(copy.deepcopy(flow["triggerInstances"]))
    logic_by_name = {d["name"].lower(): d for d in state["logic"]}
    logic_by_name.update({d["type"].lower(): d for d in state["logic"]})
    catalog = trigger_catalog(state["triggers"]) if state["triggers"] else {}
    created: list[str] = []
    summary: list[str] = []
    pending_deletions: dict[str, list[dict[str, str]]] = {}
    omitted_subflows: list[dict[str, str]] = []
    working = copy.deepcopy(flow)
    stage_edits = any(
        edit.get("op") in stages.OPS | handlers.OPS for edit in command.edits or []
    ) or (
        isinstance(flow["stages"], dict)
        and any(s.get("type") == "error" for s in flow["stages"].values())
    )
    stage_links = stages.attachments(working) if stage_edits else {}
    stage_sources = (
        stages.error_sources(working, roots, stage_links) if stage_edits else {}
    )
    stage_aliases: dict[str, str] = {}
    created_stages: list[str] = []
    created_fields: list[dict[str, str]] = []
    removed_fields: list[dict[str, str]] = []
    reference_tables: set[str] = set()
    declaration_choice_evidence: dict[str, Any] = {}
    declaration_type_evidence: dict[str, Any] = {}
    declaration_type_catalog: list[dict[str, Any]] | None = None
    declaration_qualifiers: list[tuple[str, str]] = []
    trigger_evidence: dict[str, Any] = {}
    call_evidence: dict[str, Any] = {}
    call_definitions: dict[tuple[bool, str], dict[str, Any]] = {}
    dependency_changed: set[str] = set()
    logic_evidence: dict[str, Any] = {}
    logic_changed: dict[str, set[str]] = {}
    transform_metadata: dict[str, Any] | None = None
    mode_schema_cache: dict[str, dict[str, Any]] = {}
    mode_targets: dict[str, set[str]] = {}
    generated_authoring_rows: dict[str, list[Any]] = {
        "transforms": [],
        "connections": [],
    }
    # Bind client stage UUIDs to this exact definition and edit list. Replanning
    # preview/apply must not create different stage keys.
    stage_seed = fingerprint(
        {
            "state": command.ui_id if command.action == "create" else state,
            "edits": command.model_dump(mode="json")["edits"],
        }
    )

    def record_deletions(steps: list[tree.Step]) -> None:
        components = {step.uid: step for step in tree.walk(roots)}
        components.update({step.uid: step for step in steps})
        connection_inputs.cleanup_removed(
            working, steps, components=components.values()
        )
        for removed_step in steps:
            if removed_step.kind == "subflow" and removed_step.raw.get("id"):
                # Complete-list omission is Studio's subflow call deletion
                # wire contract (bundle:205202, 205365, 130432).
                omitted_subflows.append(
                    {
                        "id": removed_step.raw["id"],
                        "uiUniqueIdentifier": removed_step.uid,
                    }
                )
            elif removed_step.raw.get("id"):
                key = (
                    "deletedActions"
                    if removed_step.kind == "action"
                    else "deletedFlowLogicInstances"
                )
                pending_deletions.setdefault(key, []).append(
                    {"id": removed_step.raw["id"]}
                )

    async def configure_decision(
        selected: tree.Step,
        edit: dict[str, Any],
        index: int,
        *,
        new: bool,
        previous: tree.Step | None = None,
    ) -> None:
        if "children" in edit and edit["children"] != "delete":
            raise CapabilityError(
                "Decision reconfiguration children must be delete when supplied."
            )
        configured = await decisions.configure(
            selected,
            working,
            state["logic"],
            client,
            decision_inputs=edit.get("decision_inputs"),
            branches=edit.get("branches"),
            delete_children=edit.get("children") == "delete",
            seed=fingerprint([stage_seed, "decision", index]),
            new=new,
            previous=previous,
            script_inputs={
                name
                for name, code in input_modes.scripts(edit.get("input_scripts")).items()
                if code
            },
        )
        created.extend(configured["created"])
        record_deletions(configured["removed"])
        logic_evidence["configure:" + str(index)] = configured["evidence"]

    def mode_flow() -> dict[str, Any]:
        current = {**working, "triggerInstances": triggers}
        for kind, collection in tree.COLLECTIONS.items():
            current[collection] = [
                step.raw for step in tree.walk(roots) if step.kind == kind
            ]
        return current

    def record_connections(
        owner: dict[str, Any], component_uid: str | None, prior_sources: set[str]
    ) -> None:
        for mapping in owner.get("aliasOverride", {}).get("aliasOverrideMappings", []):
            source = mapping.get("sourceAlias")
            if (
                source is not None
                and (source or component_uid is not None)
                and source not in prior_sources
                and mapping.get("id", "") == ""
            ):
                entry = {"component_ui_id": component_uid, "source_alias": source}
                if entry not in generated_authoring_rows["connections"]:
                    generated_authoring_rows["connections"].append(entry)

    def mode_aliases(value: Any) -> Any:
        aliases = {
            step.raw["_alias"]: step.uid
            for step in tree.walk(roots)
            if step.raw.get("_alias")
        }
        if wrappers := handlers.pair(roots):
            aliases["error_handler"] = wrappers[1].uid
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
        if isinstance(value, dict):
            return {key: mode_aliases(child) for key, child in value.items()}
        if isinstance(value, list):
            return [mode_aliases(child) for child in value]
        return value

    async def apply_modes(
        selected: tree.Step, edit: dict[str, Any], index: int
    ) -> None:
        nonlocal transform_metadata
        requested = input_modes.scripts(edit.get("input_scripts"))
        modes = {name: code for name, code in requested.items() if "." not in name}
        columns = {name: code for name, code in requested.items() if "." in name}
        transforms = (
            input_transforms.parse(edit["input_transforms"])
            if "input_transforms" in edit
            else {}
        )
        if not (requested or transforms):
            return
        if {name for name, code in requested.items() if code} & set(transforms):
            raise CapabilityError(
                "An input cannot use both script and transform modes."
            )
        if set(_inputs(edit)) & set(transforms):
            raise CapabilityError(
                "Transform source options and ordinary inputs cannot set the same input."
            )
        if set(transforms) & input_modes.STATIC_SELECTORS.get(
            selected.logic_type or "", set()
        ):
            raise CapabilityError(
                "Native structural target and schema selectors require static values."
            )
        tree.renumber(roots)
        current = mode_flow()
        resolver = PillResolver(
            client, triggers[0] if triggers else None, current, roots
        )
        resolver.definition_cache.update(mode_schema_cache)
        resolver.input_transform_metadata = transform_metadata
        if selected.logic_type in logic_blocks.TYPES:
            logic_evidence["hydrate:" + str(index)] = await logic_blocks.hydrate(
                selected,
                current,
                client,
                input_scripts=modes,
                resolver=resolver,
                definition=next(
                    row
                    for row in state["logic"]
                    if row["id"] == selected.raw["definitionId"]
                ),
                changed_inputs=logic_changed.get(selected.uid, set()),
            )
        if selected.kind in {"action", "subflow"}:
            subflow = selected.kind == "subflow"
            identity = (
                (
                    selected.raw.get("subFlow", {}).get("parentFlow")
                    or selected.raw["subflowSysId"]
                )
                if subflow
                else selected.raw["actionTypeSysId"]
            )
            key = (subflow, identity)
            if key not in call_definitions:
                call_definitions[key] = await calls.definition(
                    working, identity, subflow=subflow, client=client
                )
            names = (
                set(modes) | set(transforms) | {name.split(".")[0] for name in columns}
            )
            names = {
                name
                for name in names
                if data.spec(input_modes.input_binding(selected, name)).get("type")
                not in dynamic_inputs.MODES
            }
            input_modes.bind_schema(selected, call_definitions[key], names)
            call_evidence["input_modes:" + selected.uid] = call_definitions[key]
        fallback = {**_inputs(edit), **(edit.get("decision_inputs") or {})}
        input_modes.apply(selected, modes, fallback)
        if columns:
            await template_modes.apply(current, selected, columns, resolver)
        if requested:
            script_bindings.capture(
                roots,
                step_uid=selected.uid,
                names={
                    name.split(".")[0]
                    for name, code in requested.items()
                    if code is not None
                },
            )
        if transforms:
            old_ids = set(current.get("pharmacy", {}).get("pharmacyCompound", {}))
            call_evidence["transforms:" + str(index)] = await input_transforms.apply(
                current,
                selected,
                mode_aliases(transforms),
                resolver,
                seed=fingerprint([stage_seed, index]),
            )
            working["pharmacy"] = current["pharmacy"]
            generated_authoring_rows["transforms"].extend(
                sorted(set(current["pharmacy"]["pharmacyCompound"]) - old_ids)
            )
        transform_metadata = resolver.input_transform_metadata
        mode_schema_cache.update(resolver.definition_cache)
        mode_targets.setdefault(selected.uid, set()).update(
            set(requested) | set(transforms)
        )
        if selected.logic_type == "SETFLOWVARIABLES":
            selected.raw["flowVariables"] = copy.deepcopy(selected.raw["inputs"])

    for index, edit in enumerate(command.edits or [], start=1):
        _check_edit(edit)
        op = _str(edit, "op", required=True) or ""
        interface_schemas = edit.get("interface_schemas")
        assert interface_schemas is None or isinstance(interface_schemas, dict)
        input_modes.scripts(edit.get("input_scripts"))
        if "input_transforms" in edit:
            input_transforms.parse(edit["input_transforms"])
        if "connection_overrides" in edit:
            connection_inputs.parse(edit["connection_overrides"])
        script_bindings.rebind(roots)
        if flow["type"] == "subflow" and (op in trigger_edits.OPS or op in stages.OPS):
            raise CapabilityError(
                "Subflow trigger and stage authoring are unavailable in this adapter.",
                "UNAVAILABLE",
            )
        if (
            op
            in {
                "move_step",
                "delete_step",
                "insert_logic",
                "set_variables",
                "insert_action",
                "insert_subflow",
                *handlers.OPS,
            }
            or op == "assign_outputs"
            and "step" not in edit
        ):
            if tree.pills(
                [flow.get("inputs"), flow.get("outputs"), flow.get("flowVariables")]
            ):
                raise CapabilityError(
                    "Structural edits with interface-default pills require a complete default-binding adapter.",
                    "UNAVAILABLE",
                )
            if any(
                s.logic_type
                not in support.LOGIC_TYPES
                | {
                    "CATCH",
                    "SETFLOWVARIABLES",
                    assignments.KIND,
                    *handlers.TYPES,
                    "DECISIONBLOCK",
                    "PLACEHOLDER",
                    "UPDATEFLOWTEMPLATE",
                }
                for s in tree.walk(roots)
                if s.kind == "flowlogic"
            ):
                raise CapabilityError(
                    "Structural edits require family adapters for every existing flow-logic step.",
                    "UNAVAILABLE",
                )
        if op == "set_connection_overrides":
            if "connection_overrides" not in edit:
                raise CapabilityError(
                    "set_connection_overrides requires connection_overrides."
                )
            current = mode_flow()
            prior_sources = {
                row["sourceAlias"]
                for row in current.get("aliasOverride", {}).get(
                    "aliasOverrideMappings", []
                )
            }
            call_evidence["connections:" + str(index)] = await connection_inputs.apply(
                current,
                None,
                edit["connection_overrides"],
                client=client,
                definitions=call_definitions,
            )
            if "aliasOverride" in current:
                working["aliasOverride"] = current["aliasOverride"]
            record_connections(current, None, prior_sources)
            summary.append(f"{index}. Configure flow connections")
        elif op in {"insert_action", "insert_subflow"}:
            subflow = op == "insert_subflow"
            identity = (
                _str(
                    edit,
                    "subflow_sys_id" if subflow else "action_type_sys_id",
                    required=True,
                )
                or ""
            )
            call_key = (subflow, identity)
            if call_key not in call_definitions:
                call_definitions[call_key] = await calls.definition(
                    working, identity, subflow=subflow, client=client
                )
            definition = call_definitions[call_key]
            call_evidence[str(index)] = definition
            shell = {
                "type": "subflow" if subflow else "action",
                "flowSysId": working["id"],
                "subflowSysId" if subflow else "actionTypeSysId": identity,
                "subFlow" if subflow else "actionType": definition,
            }
            if "connection_overrides" in edit:
                call_evidence[
                    "connections:" + str(index)
                ] = await connection_inputs.apply(
                    working,
                    shell,
                    edit["connection_overrides"],
                    client=client,
                    definitions=call_definitions,
                )
            call_inputs = dict(_inputs(edit))
            for name, transform in input_transforms.parse(
                edit.get("input_transforms", {})
            ).items():
                if name in call_inputs:
                    raise CapabilityError(
                        "Transform source options and ordinary inputs cannot set the same input."
                    )
                if transform is not None:
                    call_inputs[name] = transform["source"]
            step = calls.step(
                working,
                definition,
                call_inputs,
                subflow=subflow,
                uid=str(
                    uuid.UUID(
                        hex=fingerprint([stage_seed, "call", index])[:32], version=4
                    )
                ),
                schema_evidence=list(command.schema_evidence),
                input_scripts={
                    name: code
                    for name, code in input_modes.scripts(
                        edit.get("input_scripts")
                    ).items()
                    if "." not in name
                },
                connection_context=connection_inputs.effective(working, shell),
            )
            if "aliasOverride" in shell:
                step.raw["aliasOverride"] = shell["aliasOverride"]
                record_connections(step.raw, step.uid, set())
            if definition.get("type") == "GETCATALOGVARIABLES":
                call_evidence[
                    "catalog_insert:" + str(index)
                ] = await calls.catalog_selection(step, working, client, changed=True)
            tree.insert(
                roots,
                step,
                parent=_resolve_alias(roots, _str(edit, "parent")),
                after=_resolve_alias(roots, _str(edit, "after")),
                before=_resolve_alias(roots, _str(edit, "before")),
            )
            if alias := _str(edit, "ref"):
                if (
                    not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", alias)
                    or alias in {"main", "error_handler"}
                    or alias in {s.uid for s in tree.walk(roots)}
                    or any(
                        s.raw.get("_alias") == alias
                        for s in tree.walk(roots)
                        if s is not step
                    )
                ):
                    raise CapabilityError(
                        "Call ref must be unique and cannot use a reserved branch alias."
                    )
                step.raw["_alias"] = alias
            created.append(step.uid)
            await apply_modes(step, edit, index)
            summary.append(f"{index}. Insert {definition['name']}")
        elif op in handlers.OPS:
            removed = handlers.edit(
                working,
                roots,
                edit,
                state["logic"],
                str(uuid.UUID(hex=fingerprint([stage_seed, "handler", index])[:32])),
            )
            if removed:
                pending_deletions.setdefault("deletedFlowLogicInstances", []).extend(
                    removed
                )
            summary.append(
                f"{index}. {op} "
                + (
                    str(edit.get("enabled"))
                    if op == "set_error_handler"
                    else "empty wrappers"
                )
            )
        elif op in {"create_field", "update_field", "delete_field"}:
            section = fields.collection(edit)
            changed_name = str(edit.get("new_name") or edit.get("name") or "")
            existing = next(
                (
                    row
                    for path, row in declarations.walk(working[section])
                    if path == edit.get("name")
                ),
                None,
            )
            previous_nodes = (
                copy.deepcopy(declarations.walk([existing]))
                if existing is not None
                else []
            )
            if op == "delete_field" and existing is not None:
                removed_fields.extend(
                    {
                        "collection": section,
                        **{
                            key: node[key]
                            for key in ("id", "uiUniqueId")
                            if isinstance(node.get(key), str) and node[key]
                        },
                    }
                    for _, node in declarations.walk([existing])
                )
            if (
                op == "update_field"
                and section != "flowVariables"
                and flow["type"] == "flow"
                and set(edit) <= {"op", "collection", "name", "label", "hint"}
            ):
                # Preserve the established metadata-only adapter for flow-owned interfaces.
                _update_field(working, edit)
            else:
                type_labels, type_catalog = await fields.resolve_types(
                    client, working, edit, catalog=declaration_type_catalog
                )
                if type_catalog is not None:
                    declaration_type_catalog = type_catalog
                    declaration_type_evidence[str(index)] = type_catalog
                choice_metadata, choice_evidence = await declaration_choices.resolve(
                    client, working, edit
                )
                if choice_evidence is not None:
                    declaration_choice_evidence[str(index)] = choice_evidence
                changed = fields.edit_field(
                    working,
                    edit,
                    roots,
                    str(
                        uuid.UUID(
                            hex=fingerprint([stage_seed, "field", index])[:32],
                            version=4,
                        )
                    ),
                    choice_metadata=choice_metadata,
                    new_identities={row["uiUniqueId"] for row in created_fields},
                    type_labels=type_labels,
                )
                if changed is not None:
                    created_fields.append(changed)
                if op != "delete_field":
                    changed_name = str(edit.get("new_name") or edit["name"])
                    if edit.get("new_name") and "." in str(edit["name"]):
                        changed_name = (
                            str(edit["name"]).rpartition(".")[0] + "." + changed_name
                        )
                    if edit.get("parent"):
                        changed_name = str(edit["parent"]) + "." + changed_name
                    for path, declared in declarations.walk(working[section]):
                        if path != changed_name:
                            continue
                        if declared.get(
                            "type"
                        ) in fields.REFERENCE_TYPES and declared.get("reference"):
                            reference_tables.add(declared["reference"])
                        if declared.get("type") in {
                            "reference",
                            "glide_list",
                        } and declared.get("ref_qual"):
                            declaration_qualifiers.append(
                                (declared["reference"], declared["ref_qual"])
                            )
                current_nodes = declarations.walk(working[section])
                current_by_ui = {
                    declarations.identity(node): (path, node)
                    for path, node in current_nodes
                }
                for created_field in created_fields:
                    current = current_by_ui.get(created_field["uiUniqueId"])
                    if created_field["collection"] == section and current is not None:
                        created_field["name"] = current[0]
                if op == "update_field":
                    present = {
                        node.get("id") for _, node in current_nodes if node.get("id")
                    }
                    removed_fields.extend(
                        {
                            "collection": section,
                            **{
                                key: node[key]
                                for key in ("id", "uiUniqueId")
                                if isinstance(node.get(key), str) and node[key]
                            },
                        }
                        for _, node in previous_nodes
                        if node.get("id") and node["id"] not in present
                    )
                if (
                    section == "flowVariables"
                    and existing is not None
                    and op == "update_field"
                ):
                    fields.refresh_assignments(
                        roots,
                        data.named(working[section], changed_name),
                        set(edit),
                    )
                if (
                    section == "outputs"
                    and existing is not None
                    and op == "update_field"
                    and "." not in str(edit["name"])
                ):
                    assignments.refresh_metadata(
                        roots,
                        data.named(working[section], changed_name),
                        set(edit),
                    )
            summary.append(
                f"{index}. {op.replace('_', ' ').capitalize()} {section}.{edit.get('name')}"
            )
        elif op in assignments.OPS:
            assignments.require_subflow(working)
            if "step" in edit:
                if set(edit) & {"parent", "after", "before", "ref"}:
                    raise CapabilityError(
                        "Updating an assignment step cannot also change its placement or alias."
                    )
                siblings, position, _ = tree.find(
                    roots,
                    _resolve_alias(roots, _str(edit, "step", required=True)) or "",
                )
                step = siblings[position]
                current = [
                    d
                    for d in state["logic"]
                    if d.get("id") == step.raw.get("definitionId")
                    and d.get("type") == assignments.KIND
                ]
                if len(current) != 1 or current[0].get("userCanRead") is not True:
                    raise CapabilityError(
                        "The assignment step's current definition is unreadable or ambiguous.",
                        "UNAVAILABLE",
                    )
            elif op == "clear_output_assignment":
                raise CapabilityError("clear_output_assignment requires step and name.")
            else:
                definitions = [
                    d for d in state["logic"] if d.get("type") == assignments.KIND
                ]
                if (
                    len(definitions) != 1
                    or definitions[0].get("userCanRead") is not True
                ):
                    raise CapabilityError(
                        "Assign Subflow Outputs is unavailable in the instance catalog.",
                        "UNAVAILABLE",
                    )
                definition = definitions[0]
                step = tree.new_logic_step(flow["id"], definition, {})
                step.raw["uiUniqueIdentifier"] = str(
                    uuid.UUID(
                        hex=fingerprint([stage_seed, "output_assignment", index])[:32],
                        version=4,
                    )
                )
                tree.insert(
                    roots,
                    step,
                    parent=_resolve_alias(roots, _str(edit, "parent")),
                    after=_resolve_alias(roots, _str(edit, "after")),
                    before=_resolve_alias(roots, _str(edit, "before")),
                )
                created.append(step.uid)
                if alias := _str(edit, "ref"):
                    step.raw["_alias"] = alias
            assignments.update(working, step, edit)
            summary.append(f"{index}. {op.replace('_', ' ').capitalize()}")
        elif op == "set_variables":
            definitions = [
                d for d in state["logic"] if d.get("type") == "SETFLOWVARIABLES"
            ]
            if len(definitions) != 1 or definitions[0].get("userCanRead") is not True:
                raise CapabilityError(
                    "Set Flow Variables is unavailable in the instance catalog.",
                    "UNAVAILABLE",
                )
            definition = definitions[0]
            step = tree.new_logic_step(flow["id"], definition, {})
            step.raw["inputs"] = []
            step.raw["uiUniqueIdentifier"] = str(
                uuid.UUID(
                    hex=fingerprint([stage_seed, "variables", index])[:32], version=4
                )
            )
            modes = input_modes.scripts(edit.get("input_scripts"))
            values = dict(_inputs(edit))
            for name in modes:
                values.setdefault(
                    name, data.default_value(data.named(working["flowVariables"], name))
                )
            for name, transform in input_transforms.parse(
                edit.get("input_transforms", {})
            ).items():
                if name in _inputs(edit):
                    raise CapabilityError(
                        "Transform source options and ordinary inputs cannot set the same input."
                    )
                if transform is not None:
                    values[name] = transform["source"]
            for name, value in values.items():
                parameter = data.named(working["flowVariables"], name)
                binding = {
                    "id": "",
                    "name": name,
                    "parameter": copy.deepcopy(parameter),
                    "children": [],
                    "value": "",
                    "scriptActive": False,
                }
                input_modes.value(binding, value, scripted=bool(modes.get(name)))
                step.raw["inputs"].append(binding)
            if not step.raw["inputs"]:
                raise CapabilityError("set_variables requires at least one assignment.")
            step.raw["flowVariables"] = copy.deepcopy(step.raw["inputs"])
            step.raw.pop("data", None)
            _native_variable_step(step)
            tree.insert(
                roots,
                step,
                parent=_resolve_alias(roots, _str(edit, "parent")),
                after=_resolve_alias(roots, _str(edit, "after")),
                before=_resolve_alias(roots, _str(edit, "before")),
            )
            await apply_modes(step, edit, index)
            summary.append(f"{index}. Assign flow variables")
        elif op in trigger_edits.OPS:
            if len(triggers) > 1:
                raise CapabilityError(
                    "This trigger adapter requires at most one trigger.", "UNAVAILABLE"
                )
            if triggers and saved_triggers.is_saved(triggers[0]):
                snapshot = state["saved_triggers"][triggers[0]["triggerDefinitionId"]]
                saved_triggers.validate(triggers[0], snapshot["definition"])
            if op == "set_trigger" and "template_id" in edit:
                template_id = _str(edit, "template_id", required=True) or ""
                snapshot = state["saved_triggers"][template_id]
                definition = snapshot["definition"]
                if "type" in edit and _str(edit, "type") != definition["type"]:
                    raise CapabilityError(
                        "The requested trigger type differs from the saved template."
                    )
                if triggers and not edit.get("replace"):
                    raise CapabilityError(
                        "The flow already has a trigger. Set replace=true to swap it."
                    )
                if triggers:
                    trigger_edits.require_supported(triggers[0])
                    if triggers[0].get("id"):
                        pending_deletions.setdefault("deletedTriggers", []).append(
                            {"id": triggers[0]["id"]}
                        )
                triggers = [
                    saved_triggers.new_instance(
                        flow,
                        definition,
                        _inputs(edit),
                        schemas=interface_schemas,
                    )
                ]
                trigger_evidence[str(index)] = await trigger_edits.prepare(
                    triggers[0],
                    definition,
                    client,
                    resolver=PillResolver(client, triggers[0]),
                    flow=working,
                    authored=True,
                )
                summary.append(f"{index}. Set saved trigger {triggers[0]['name']}")
                continue
            if op in {"update_trigger", "remove_trigger"}:
                if not triggers:
                    raise CapabilityError(
                        "The flow has no trigger to update or remove."
                    )
                trigger_edits.require_supported(triggers[0])
            if op == "remove_trigger":
                if triggers[0].get("id"):
                    pending_deletions.setdefault("deletedTriggers", []).append(
                        {"id": triggers[0]["id"]}
                    )
                triggers = []
                summary.append(f"{index}. Remove trigger")
                continue
            if op == "update_trigger":
                definition = (
                    state["saved_triggers"][triggers[0]["triggerDefinitionId"]][
                        "definition"
                    ]
                    if saved_triggers.is_saved(triggers[0])
                    else catalog.get(triggers[0]["type"])
                )
                if definition is None:
                    raise CapabilityError(
                        "The saved trigger definition is unavailable in the current catalog.",
                        "UNAVAILABLE",
                    )
                trigger_edits.update(
                    triggers[0],
                    definition,
                    _inputs(edit),
                    schemas=interface_schemas,
                )
                trigger_evidence[str(index)] = await trigger_edits.prepare(
                    triggers[0],
                    definition,
                    client,
                    resolver=PillResolver(client, triggers[0]),
                    flow=working,
                    authored=True,
                )
                summary.append(f"{index}. Update trigger inputs")
                continue
            kind = _str(edit, "type", required=True) or ""
            definition = catalog.get(kind)
            if definition is None:
                raise CapabilityError(
                    f"Unknown trigger type '{kind}'. Allowed: {', '.join(sorted(catalog))}."
                )
            if triggers and not edit.get("replace"):
                raise CapabilityError(
                    "The flow already has a trigger. Set replace=true to swap it."
                )
            if kind not in trigger_edits.SUPPORTED:
                raise CapabilityError(
                    "This trigger is discovered but its family serializer is unavailable.",
                    "UNAVAILABLE",
                )
            trigger_edits.require_supported(definition)
            if (
                definition.get("userCanRead") is False
                or definition.get("security", {}).get("can_read") is False
            ):
                raise CapabilityError(
                    "The trigger definition is unreadable.", "UNAVAILABLE"
                )
            if triggers:
                trigger_edits.require_supported(triggers[0])
            if triggers and triggers[0].get("id"):
                pending_deletions.setdefault("deletedTriggers", []).append(
                    {"id": triggers[0]["id"]}
                )
            inputs = _inputs(edit)
            if (
                kind in trigger_edits.SCHEDULED
                and not trigger_edits.schedule_inputs(definition).keys()
                <= inputs.keys()
            ):
                raise CapabilityError(
                    "Supply every native schedule input explicitly. Clock defaults are not selected automatically."
                )
            if kind in application_triggers.SUPPORTED:
                triggers = [
                    application_triggers.new_instance(
                        working, definition, inputs, interface_schemas
                    )
                ]
                trigger_evidence[str(index)] = await trigger_edits.prepare(
                    triggers[0],
                    definition,
                    client,
                    resolver=PillResolver(client, triggers[0]),
                    flow=working,
                    authored=True,
                )
                summary.append(f"{index}. Set trigger {definition['label']}")
                continue
            if "interface_schemas" in edit:
                raise CapabilityError("Only REST triggers accept interface_schemas.")
            triggers = [new_trigger(flow, definition, inputs)]
            if kind != "service_catalog":
                # Native blank conditions use ^EQ. Never choose wall-clock
                # schedule defaults or silently replace explicit scalar values.
                for item in triggers[0]["inputs"]:
                    if item["type"] == "conditions":
                        if item["value"] == "":
                            item["value"] = "^EQ"
                        item["displayValue"] = item["value"]
                    if item["type"] in {
                        "glide_time",
                        "glide_date_time",
                        "glide_duration",
                    }:
                        item["displayValue"] = item["value"]
                trigger_evidence[str(index)] = await trigger_edits.prepare(
                    triggers[0],
                    definition,
                    client,
                    resolver=PillResolver(client, triggers[0]),
                )
            summary.append(f"{index}. Set trigger {definition['label']}")
        elif op == "insert_logic":
            kind = (_str(edit, "logic", required=True) or "").lower()
            definition = logic_by_name.get(kind)
            if definition is None:
                raise CapabilityError(f"Unknown flow logic '{kind}'.")
            if definition["type"] not in support.LOGIC_TYPES:
                raise CapabilityError(
                    "This flow-logic family is discovered but its authoring contract is unavailable.",
                    "UNAVAILABLE",
                )
            if definition.get("userCanRead") is not True:
                raise CapabilityError(
                    "The flow-logic definition is unreadable.", "UNAVAILABLE"
                )
            values = dict(_inputs(edit))
            for name, transform in input_transforms.parse(
                edit.get("input_transforms", {})
            ).items():
                if name in values:
                    raise CapabilityError(
                        "Transform source options and ordinary inputs cannot set the same input."
                    )
                if transform is not None and name in {
                    row["name"] for row in definition.get("inputs", [])
                }:
                    values[name] = transform["source"]
            if definition["type"] == "GOBACKTO" and "go_back_to_step" in values:
                target_ref = values["go_back_to_step"]
                if not isinstance(target_ref, str) or not target_ref:
                    raise CapabilityError(
                        "Go Back To requires a target step reference."
                    )
                values["go_back_to_step"] = _resolve_alias(roots, target_ref)
            if definition["type"] == "DECISION" and "answer_table" in values:
                raise CapabilityError(
                    "The decision answer table is derived from its selected table."
                )
            if definition["type"] != "DECISION" and set(edit) & {
                "decision_inputs",
                "branches",
            }:
                raise CapabilityError(
                    "Decision configuration fields require Make a decision."
                )
            step = (
                logic_blocks.new_step(
                    flow,
                    definition,
                    values,
                    input_scripts={
                        name: code
                        for name, code in input_modes.scripts(
                            edit.get("input_scripts")
                        ).items()
                        if "." not in name
                    },
                )
                if definition["type"] in logic_blocks.TYPES
                else tree.new_logic_step(
                    flow["id"],
                    definition,
                    values,
                    input_scripts=input_modes.scripts(edit.get("input_scripts")),
                )
            )
            # A new error stage can reference this branch in the same preview.
            # Preserve that reviewed ancestry across preview/apply replanning.
            step.raw["uiUniqueIdentifier"] = str(
                uuid.UUID(hex=fingerprint([stage_seed, "logic", index])[:32], version=4)
            )
            if definition["type"] not in logic_blocks.TYPES:
                for item in step.raw["inputs"]:
                    if input_modes.readonly(item):
                        if item["name"] in values:
                            raise CapabilityError(
                                "Read-only flow-logic inputs cannot be set by callers."
                            )
                        continue
                    input_modes.value(
                        item, item["value"], scripted=bool(item.get("scriptActive"))
                    )
            logic_changed[step.uid] = set(values)
            tree.insert(
                roots,
                step,
                parent=_resolve_alias(roots, _str(edit, "parent")),
                after=_resolve_alias(roots, _str(edit, "after")),
                before=_resolve_alias(roots, _str(edit, "before")),
            )
            created.append(step.uid)
            alias = _str(edit, "ref")
            if alias:
                if alias in {"main", "error_handler"}:
                    raise CapabilityError(
                        "main and error_handler are reserved branch aliases."
                    )
                step.raw["_alias"] = alias
            if step.logic_type == "DECISION":
                await configure_decision(step, edit, index, new=True)
            if step.logic_type == "TRY":
                catch_def = next(
                    (d for d in state["logic"] if d["type"] == "CATCH"), None
                )
                if catch_def is None or catch_def.get("userCanRead") is not True:
                    raise CapabilityError(
                        "Try requires the instance Catch definition.", "UNAVAILABLE"
                    )
                catch = tree.new_logic_step(flow["id"], catch_def, {})
                catch.raw["uiUniqueIdentifier"] = str(
                    uuid.UUID(
                        hex=fingerprint([stage_seed, "catch", index])[:32], version=4
                    )
                )
                catch.raw["connectedTo"] = step.uid
                tree.insert(roots, catch, parent=None, after=step.uid, before=None)
                created.append(catch.uid)
            await apply_modes(step, edit, index)
            summary.append(f"{index}. Insert {definition['name']}")
        elif op in {"move_step", "delete_step", "update_inputs"}:
            label = _str(edit, "step", required=True) or ""
            ref = _resolve_alias(roots, label) or ""
            siblings, position, _ = tree.find(roots, ref)
            if siblings[position].logic_type == "UPDATEFLOWTEMPLATE":
                raise CapabilityError(
                    "The source-generated Update Flow Template helper is read-only and cannot be changed through generic step operations."
                )
            if siblings[position].logic_type in handlers.TYPES:
                raise CapabilityError(
                    "Use handler operations for hidden wrapper settings. Wrapper moves and generic deletion are unavailable."
                )
            if op == "delete_step" and (
                siblings[position]
                .raw.get("flowLogicDefinition", {})
                .get("attributes", {})
                .get("can_delete")
                in {False, "false"}
            ):
                raise CapabilityError(
                    "This native structural block is managed by its containing operation and cannot be deleted independently."
                )
            handlers.require_enabled(roots, ref)
            if op == "delete_step":
                siblings, position, _ = tree.find(roots, ref)
                removed = siblings[position]
                disposition = edit.get("children", "reject")
                if disposition not in {"reject", "delete", "promote"}:
                    raise CapabilityError(
                        "children must be reject, delete, or promote."
                    )
                if removed.logic_type == "DECISION" and disposition == "promote":
                    raise CapabilityError(
                        "Decision branches cannot be promoted outside their decision."
                    )
                if removed.children and disposition == "reject":
                    raise CapabilityError(
                        "The step has children. Choose children=delete or children=promote explicitly.",
                    )
                linked: list[tree.Step] = []
                if removed.logic_type in {"IF", "TRY"}:
                    next_index = position + 1
                    expected_types = (
                        {"ELSEIF", "ELSE"} if removed.logic_type == "IF" else {"CATCH"}
                    )
                    while (
                        next_index < len(siblings)
                        and siblings[next_index].logic_type in expected_types
                    ):
                        linked.append(siblings[next_index])
                        next_index += 1
                    if linked and edit.get("cascade") != "connected":
                        raise CapabilityError(
                            "The step has connected branches. Set cascade=connected to delete them explicitly.",
                        )
                    if linked and disposition != "delete":
                        raise CapabilityError(
                            "Connected branch deletion requires children=delete."
                        )
                deleting = (
                    list(tree.walk([removed, *linked]))
                    if disposition == "delete"
                    else [removed]
                )
                record_deletions(deleting)
                siblings.pop(position)
                for linked_step in linked:
                    siblings.remove(linked_step)
                if disposition == "promote":
                    siblings[position:position] = removed.children
                summary.append(f"{index}. Delete step {label}")
            elif op == "move_step":
                # Pin targets before detaching, because detaching shifts display orders.
                parent = _resolve_alias(roots, _str(edit, "parent"))
                after = _resolve_alias(roots, _str(edit, "after"))
                before = _resolve_alias(roots, _str(edit, "before"))
                if ref in (parent, after, before):
                    raise CapabilityError("A step cannot be moved relative to itself.")
                tree.move(roots, ref, parent=parent, after=after, before=before)
                summary.append(f"{index}. Move step {label}")
            else:
                siblings, position, _ = tree.find(roots, ref)
                selected = siblings[position]
                previous_decision = (
                    copy.deepcopy(selected)
                    if selected.logic_type == "DECISION"
                    else None
                )
                updates = _inputs(edit)
                modes = input_modes.scripts(edit.get("input_scripts"))
                existing_names = {
                    item.get("name")
                    for collection in (
                        "inputs",
                        "dynamicInputs",
                        "workflowInputs",
                        "decisionTableInputs",
                    )
                    for item in selected.raw.get(collection, [])
                }
                input_modes.prepare(
                    selected,
                    {
                        name: code
                        for name, code in modes.items()
                        if name in existing_names
                    },
                )
                resets = {}
                for name in modes:
                    if "." not in name:
                        continue
                    template, column = template_modes.parse_selector(
                        selected, name
                    ) or ({}, "")
                    if template.get("name") in updates and column in dict(
                        widgets.template_fields(template.get("value"))
                    ):
                        resets[name] = None
                if resets:
                    tree.renumber(roots)
                    reset_resolver = PillResolver(
                        client, triggers[0] if triggers else None, mode_flow(), roots
                    )
                    reset_resolver.definition_cache.update(mode_schema_cache)
                    await template_modes.apply(
                        mode_flow(), selected, resets, reset_resolver
                    )
                    mode_schema_cache.update(reset_resolver.definition_cache)
                if "connection_overrides" in edit:
                    prior_sources = {
                        row["sourceAlias"]
                        for row in selected.raw.get("aliasOverride", {}).get(
                            "aliasOverrideMappings", []
                        )
                        if row["sourceAlias"] or row.get("id", "") == ""
                    }
                    call_evidence[
                        "connections:" + str(index)
                    ] = await connection_inputs.apply(
                        working,
                        selected,
                        edit["connection_overrides"],
                        client=client,
                        definitions=call_definitions,
                    )
                    record_connections(selected.raw, selected.uid, prior_sources)
                if selected.logic_type == "GOBACKTO" and "go_back_to_step" in updates:
                    updates = dict(updates)
                    target_ref = updates["go_back_to_step"]
                    if not isinstance(target_ref, str) or not target_ref:
                        raise CapabilityError(
                            "Go Back To requires a target step reference."
                        )
                    updates["go_back_to_step"] = _resolve_alias(roots, target_ref)
                if selected.logic_type != "DECISION" and set(edit) & {
                    "decision_inputs",
                    "branches",
                    "children",
                }:
                    raise CapabilityError(
                        "Decision configuration fields require Make a decision."
                    )
                logic_changed.setdefault(selected.uid, set()).update(updates)
                if selected.kind == "action" and any(
                    data.spec(row).get("dynamic")
                    for row in selected.raw.get("inputs", [])
                ):
                    identity = selected.raw["actionTypeSysId"]
                    call_key = (False, identity)
                    if call_key not in call_definitions:
                        call_definitions[call_key] = await calls.definition(
                            working, identity, subflow=False, client=client
                        )
                    updates = dynamic_inputs.adopt_bindings(
                        selected.raw,
                        call_definitions[call_key],
                        updates,
                        list(command.schema_evidence),
                        connection_context=connection_inputs.effective(
                            working, selected
                        ),
                    )
                    call_evidence["dynamic:" + selected.uid] = call_definitions[
                        call_key
                    ]
                _update_inputs(
                    selected,
                    {
                        name: value
                        for name, value in updates.items()
                        if not modes.get(name)
                    },
                    input_scripts={
                        name: code for name, code in modes.items() if "." not in name
                    },
                )
                if selected.logic_type == "DECISION":
                    await configure_decision(
                        selected, edit, index, new=False, previous=previous_decision
                    )
                await apply_modes(selected, edit, index)
                if any(
                    item["name"] in _inputs(edit)
                    and data.value_type(item).kind
                    in {"document_id", "table_name", "field_name", "conditions"}
                    for item in siblings[position].raw["inputs"]
                ):
                    dependency_changed.add(siblings[position].uid)
                if set(_inputs(edit)) & {"catalog_variables", "template_catalog_item"}:
                    call_evidence[
                        "catalog:" + str(index)
                    ] = await calls.catalog_selection(
                        siblings[position], working, client, changed=True
                    )
                summary.append(f"{index}. Update inputs of step {label}")
        elif op in stages.OPS:
            tree.renumber(roots)
            if isinstance(edit.get("stage"), str):
                key = stage_aliases.get(edit["stage"], edit["stage"])
                for uid in stage_links.get(key, []):
                    handlers.require_enabled(roots, uid)
            if "step" in edit:
                handlers.require_enabled(
                    roots,
                    _resolve_alias(roots, _str(edit, "step", required=True)) or "",
                )
            identity = stage_aliases.get(str(edit.get("stage")), edit.get("stage"))
            existing_stage = (
                working["stages"].get(identity) if isinstance(identity, str) else None
            )
            if (
                op == "update_stage"
                and isinstance(existing_stage, dict)
                and "type" not in existing_stage
                and set(edit) <= {"op", "stage", "label", "value"}
            ):
                # Retain the existing metadata-only adapter for legacy stages.
                _update_stage(working, {**edit, "stage": identity})
            else:
                stage_edit = dict(edit)
                if "step" in edit:
                    stage_edit["step"] = _resolve_alias(
                        roots, _str(edit, "step", required=True)
                    )
                identity = stages.edit_stage(
                    working,
                    stage_edit,
                    roots,
                    stage_links,
                    stage_aliases,
                    str(
                        uuid.UUID(hex=fingerprint([stage_seed, index])[:32], version=4)
                    ),
                    stage_sources,
                )
                if op == "create_stage":
                    created_stages.append(identity)
            summary.append(f"{index}. {op.replace('_', ' ').capitalize()} {identity}")
    for step in tree.walk(roots):
        if step.kind != "action" or not any(
            data.spec(row).get("dynamic") for row in step.raw.get("inputs", [])
        ):
            continue
        identity = step.raw["actionTypeSysId"]
        call_key = (False, identity)
        if call_key not in call_definitions:
            call_definitions[call_key] = await calls.definition(
                working, identity, subflow=False, client=client
            )
        updates = dynamic_inputs.adopt_bindings(
            step.raw,
            call_definitions[call_key],
            {},
            list(command.schema_evidence),
            connection_context=connection_inputs.effective(working, step),
        )
        _update_inputs(step, updates)
        call_evidence["dynamic:" + step.uid] = call_definitions[call_key]
    pill_aliases = {
        s.raw["_alias"]: s.uid for s in tree.walk(roots) if s.raw.get("_alias")
    }
    reserved_producers = {
        "main",
        "error_handler",
        "flow_variable",
        "subflow",
        *[s.uid for s in tree.walk(roots)],
    }
    if triggers:
        reserved_producers.add(trigger_pill_prefix(triggers[0]))
    if set(pill_aliases) & reserved_producers or len(pill_aliases) != sum(
        bool(s.raw.get("_alias")) for s in tree.walk(roots)
    ):
        raise CapabilityError(
            "Step refs must be unique and cannot replace native producer names or UI identities."
        )
    wrappers = handlers.pair(roots)
    if wrappers:
        pill_aliases["error_handler"] = wrappers[1].uid
    for step in tree.walk(roots):
        if step.logic_type in logic_blocks.TYPES:
            logic_blocks.remap_staged(step, pill_aliases)
        step.raw.pop("_alias", None)
        rebound_handler_alias = False
        for key in (
            "inputs",
            "outputsToAssign",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        ):
            for item in data.bindings(step.raw.get(key, [])):
                value = item.get("value")
                if not isinstance(value, str) or not any(
                    pill.partition(".")[0] in pill_aliases for pill in tree.pills(value)
                ):
                    continue
                item["value"] = tree.PILL.sub(
                    lambda match: (
                        "{{"
                        + pill_aliases[match[1].partition(".")[0]]
                        + "."
                        + match[1].partition(".")[2]
                        + "}}"
                        if match[1].partition(".")[0] in pill_aliases
                        else match[0]
                    ),
                    value,
                )
                rebound_handler_alias = True
                if "data" in step.raw and item.get("name") in step.raw["data"]:
                    step.raw["data"][item["name"]] = item["value"]
        if (
            rebound_handler_alias
            and step.logic_type == assignments.KIND
            and "data" in step.raw
        ):
            for row in step.raw["outputsToAssign"]:
                step.raw["data"][row["name"]] = data.output_assignment_mirror(row)
        if rebound_handler_alias and step.logic_type == "SETFLOWVARIABLES":
            step.raw["flowVariables"] = copy.deepcopy(step.raw["inputs"])
    script_bindings.rebind(roots)
    payload = tree.flatten(working, roots)
    handlers.serialize(payload, roots, flow)
    original_wrappers = handlers.pair(tree.build_tree(flow))
    if original_wrappers and not handlers.pair(roots):
        removed_uids = {s.uid for s in original_wrappers}
        if any(
            pill.partition(".")[0] in removed_uids
            for pill in tree.pills(
                [
                    working["inputs"],
                    working["outputs"],
                    working["flowVariables"],
                    working.get("pharmacy"),
                ]
            )
        ):
            raise CapabilityError(
                "Remove handler pill defaults and transforms explicitly before removing its wrappers."
            )
    whole_bindings = any(
        data.whole_pill(item.get("value"))
        and data.value_type(item).kind in declarations.LABELS
        for step in tree.walk(roots + tree.build_tree(flow))
        for key in ("inputs", "outputsToAssign")
        for item in data.bindings(step.raw.get(key, []))
    )
    if (
        omitted_subflows
        or call_evidence
        or handlers.pair(roots)
        or any(edit.get("op") in handlers.OPS for edit in command.edits or [])
        or whole_bindings
        or any(
            edit.get("op")
            in {"create_field", "update_field", "delete_field", *assignments.OPS}
            for edit in command.edits or []
        )
    ):
        _refresh_component_mirror(flow, payload)
    if stage_edits:
        stages.serialize(payload, roots, stage_links, flow, stage_sources)
    else:
        _preserve_stage_attachments(flow, payload, roots)
    payload["triggerInstances"] = triggers
    catalog_before = catalog_after = None
    new_catalog_definition = None
    if any(edit.get("op") in trigger_edits.OPS for edit in command.edits or []):
        old_catalog_trigger = next(
            (
                row
                for row in flow["triggerInstances"]
                if row["type"] == "service_catalog"
            ),
            None,
        )
        if old_catalog_trigger is not None:
            definition = (
                state["saved_triggers"][old_catalog_trigger["triggerDefinitionId"]][
                    "definition"
                ]
                if saved_triggers.is_saved(old_catalog_trigger)
                else catalog.get("service_catalog")
            )
            if (
                definition is None
                or definition["id"] != old_catalog_trigger["triggerDefinitionId"]
            ):
                raise CapabilityError(
                    "The previous Service Catalog definition identity changed.",
                    "UNAVAILABLE",
                )
            catalog_before, owner_evidence = await saved_triggers.catalog_interface(
                flow, definition, client
            )
            trigger_evidence["catalog_input_owners"] = owner_evidence
        new_catalog_trigger = next(
            (row for row in triggers if row["type"] == "service_catalog"), None
        )
        if new_catalog_trigger is not None:
            new_catalog_definition = (
                state["saved_triggers"][new_catalog_trigger["triggerDefinitionId"]][
                    "definition"
                ]
                if saved_triggers.is_saved(new_catalog_trigger)
                else catalog.get("service_catalog")
            )
            if (
                new_catalog_definition is None
                or new_catalog_definition["id"]
                != new_catalog_trigger["triggerDefinitionId"]
            ):
                raise CapabilityError(
                    "The proposed Service Catalog definition identity changed.",
                    "UNAVAILABLE",
                )
            catalog_after = trigger_edits.service_catalog_inputs(new_catalog_definition)
        application_triggers.reconcile_interfaces(
            flow,
            payload,
            triggers,
            roots,
            catalog_before=catalog_before,
            catalog_after=catalog_after,
        )
        if (
            catalog_before is not None
            or catalog_after is not None
            or any(
                application_triggers.owns_interface(row)
                for row in flow["triggerInstances"] + triggers
            )
        ):
            new_interfaces, old_interfaces = application_triggers.interface_manifests(
                flow, payload
            )
            created_fields.extend(new_interfaces)
            removed_fields.extend(old_interfaces)
    generated_trigger_inputs = None
    if (
        payload["type"] == "flow"
        and not payload["inputs"]
        and len(triggers) == 1
        and triggers[0]["type"] == "service_catalog"
        and any(edit["op"] == "set_trigger" for edit in command.edits or [])
    ):
        definition = new_catalog_definition
        if definition is None or definition["id"] != triggers[0]["triggerDefinitionId"]:
            raise CapabilityError(
                "The Service Catalog definition identity changed.", "UNAVAILABLE"
            )
        check_table_access("sys_hub_flow_input")
        generated_trigger_inputs = {
            "definition_id": definition["id"],
            "declarations": trigger_edits.service_catalog_inputs(definition),
        }
    if any(edit.get("op") in trigger_edits.OPS for edit in command.edits or []):
        trigger_edits.check_dependencies(
            flow["triggerInstances"],
            triggers,
            payload,
            roots,
            replaced=any(
                edit["op"] in {"set_trigger", "remove_trigger"}
                for edit in command.edits or []
            ),
        )
    payload.update(pending_deletions)
    if "active_runtime" in state:
        payload["status"] = "draft"
        payload["isPublished"] = False
    trigger = triggers[0] if triggers else None
    assignments.validate(payload, roots)
    table_evidence = {}
    for table in sorted(reference_tables):
        check_table_access("sys_db_object")
        rows = (
            await client.query_records(
                "sys_db_object", f"name={table}", fields=["name"], limit=2
            )
        )["records"]
        if len(rows) != 1 or rows[0].get("name") != table:
            raise CapabilityError(
                "The declaration reference table is absent, ambiguous, or unreadable.",
                "UNAVAILABLE",
            )
        table_evidence[table] = rows[0]
    usage = tree.used_pills(roots)
    literal_uses: dict[str, dict[str, set[str]]] = {}
    for step in tree.walk(roots):
        for collection in input_modes.COLLECTIONS:
            for item in data.bindings(step.raw.get(collection, [])):
                if widgets.kind(item) == "approval_rules" and not item.get(
                    "scriptActive"
                ):
                    for pill in widgets.approval_literal_pills(item.get("value", "")):
                        literal_uses.setdefault(pill, {}).setdefault(
                            step.uid, set()
                        ).add(item["name"])
    for pill, used in list(usage.items()):
        if all(
            set(names) <= literal_uses.get(pill, {}).get(uid, set())
            for uid, names in used.items()
        ):
            usage.pop(pill)
    if len(usage) > 100:
        raise CapabilityError(
            "Reference validation supports at most 100 distinct pills per edit.",
            "UNAVAILABLE",
        )
    for step in tree.walk(roots):
        if step.logic_type == "FOREACH":
            items = data.named(step.raw["inputs"], "items")["value"]
            if not tree.pills(items):
                raise CapabilityError(
                    "For Each authoring requires a typed Records pill. Static-list table inference is unavailable.",
                    "UNAVAILABLE",
                )
        if step.logic_type == "SETFLOWVARIABLES":
            for item in step.raw["inputs"]:
                declared = data.named(payload["flowVariables"], item["name"])
                if data.value_type(item) != data.value_type(declared):
                    raise CapabilityError(
                        "The assignment type differs from the declared flow variable."
                    )
    for trigger in triggers:
        for item in trigger.get("inputs", []):
            if application_triggers.saved_path_input(trigger, item.get("name", "")):
                application_triggers.validate_path_input(trigger, item)
        if any(
            tree.pills(item.get("value"))
            for item in data.bindings(trigger.get("inputs", []))
            if not application_triggers.saved_path_input(trigger, item.get("name", ""))
        ):
            raise CapabilityError(
                "Trigger inputs cannot reference values produced by the flow."
            )
    resolver = PillResolver(client, trigger, payload, roots)
    resolver.definition_cache.update(mode_schema_cache)
    declaration_qualifier_evidence = (
        await fields.qualifier_evidence(client, resolver, declaration_qualifiers)
        if declaration_qualifiers
        else {}
    )
    resolver.input_transform_metadata = transform_metadata
    for (subflow, identity), definition in call_definitions.items():
        resolver.definition_cache[
            ("subflow" if subflow else "action") + "/" + identity
        ] = definition
    for step in tree.walk(roots):
        script_names = {
            item["name"]
            for item in step.raw.get("inputs", [])
            if item.get("script")
            and data.spec(item).get("type") not in dynamic_inputs.MODES
        }
        if step.kind in {"action", "subflow"} and script_names:
            subflow = step.kind == "subflow"
            identity = (
                (
                    step.raw.get("subFlow", {}).get("parentFlow")
                    or step.raw["subflowSysId"]
                )
                if subflow
                else step.raw["actionTypeSysId"]
            )
            key = (subflow, identity)
            if key not in call_definitions:
                call_definitions[key] = await calls.definition(
                    payload, identity, subflow=subflow, client=client
                )
            input_modes.bind_schema(step, call_definitions[key], script_names)
            call_evidence["scripts:" + step.uid] = call_definitions[key]
        if step.kind in {"action", "subflow"} and step.raw.get("aliasOverride", {}).get(
            "aliasOverrideMappings"
        ):
            call_evidence[
                "connections_saved:" + step.uid
            ] = await connection_inputs.validate_existing(
                payload, step, client=client, definitions=call_definitions
            )
    if payload.get("aliasOverride", {}).get("aliasOverrideMappings"):
        call_evidence[
            "connections_saved:flow"
        ] = await connection_inputs.validate_existing(
            payload, None, client=client, definitions=call_definitions
        )
    for step in tree.walk(roots):
        if step.logic_type in logic_blocks.TYPES:
            current = [
                definition
                for definition in state["logic"]
                if definition.get("id") == step.raw.get("definitionId")
                and definition.get("type") == step.logic_type
            ]
            if len(current) != 1 or current[0].get("userCanRead") is not True:
                raise CapabilityError(
                    "The utility block definition is unreadable or ambiguous.",
                    "UNAVAILABLE",
                )
            logic_evidence[step.uid] = await logic_blocks.prepare(
                step,
                payload,
                client,
                changed_inputs=logic_changed.get(step.uid, set()),
                resolver=resolver,
                definition=current[0],
            )
        elif step.logic_type == "DECISION":
            logic_evidence[step.uid] = await decisions.validate(step, payload, client)
        for name in mode_targets.get(step.uid, set()):
            if "." in name:
                template_modes.parse_selector(step, name)
            else:
                input_modes.input_binding(step, name)
        if step.logic_type == "DECISION" and step.raw.get("decisionTableInputs"):
            references = {}
            for item in step.raw["decisionTableInputs"]:
                pill = data.whole_pill(item.get("value"))
                if (
                    data.value_type(item).kind == "document_id"
                    and data.spec(item).get("dependent_on")
                    and pill
                    and not item.get("scriptActive")
                ):
                    source = await resolver.resolve(pill, step)
                    if source["type"] not in {
                        "reference",
                        "document_id",
                    } or not source.get("reference"):
                        raise CapabilityError(
                            "A dependent decision record input requires a known reference-table pill."
                        )
                    references[item["name"]] = source["reference"]
            call_evidence[
                "decision_dependencies:" + step.uid
            ] = await calls.table_dependencies(
                step,
                references,
                resolver,
                inputs=copy.deepcopy(step.raw["decisionTableInputs"]),
            )
    if logic_evidence:
        _refresh_component_mirror(flow, payload)
    for pill, used in list(usage.items()):
        ordinary = resolver.prescription(pill)
        if ordinary != pill:
            usage.pop(pill)
            for uid, names in used.items():
                existing = usage.setdefault(ordinary, {}).setdefault(uid, [])
                existing.extend(name for name in names if name not in existing)
    for step in tree.walk(roots):
        if (
            step.uid in created or step.uid in dependency_changed
        ) and step.kind == "action":
            references = {}
            for item in step.raw["inputs"]:
                if data.value_type(item).kind == "document_id" and data.spec(item).get(
                    "dependent_on"
                ):
                    pills = tree.pills(item["value"])
                    if (
                        len(pills) == 1
                        and item["value"] == "{{" + next(iter(pills)) + "}}"
                    ):
                        source = await resolver.resolve(next(iter(pills)), step)
                        if source["type"] not in {
                            "reference",
                            "document_id",
                        } or not source.get("reference"):
                            raise CapabilityError(
                                "A dependent record input requires a known reference-table pill."
                            )
                        references[item["name"]] = source["reference"]
            call_evidence["dependent:" + step.uid] = await calls.table_dependencies(
                step, references, resolver
            )
        if step.raw.get("actionType", {}).get("type") == "GETCATALOGVARIABLES":
            call_evidence["catalog_saved:" + step.uid] = await calls.catalog_selection(
                step, payload, client, changed=False
            )
    for step in tree.walk(roots):
        for item in step.raw.get("inputs", []):
            await resolver.validate(item, step)
        for key in (
            "outputsToAssign",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        ):
            for item in step.raw.get(key, []):
                parameter = (
                    data.named(payload["outputs"], item["name"])
                    if key == "outputsToAssign" and step.logic_type == assignments.KIND
                    else None
                )
                if (
                    key == "decisionTableInputs"
                    and data.value_type(item).kind == "document_id"
                    and data.spec(item).get("dependent_on")
                ):
                    dependency = data.spec(item)["dependent_on"].removeprefix("inputs.")
                    source = data.named(step.raw["decisionTableInputs"], dependency)
                    await resolver.static_table(source)
                    parameter = {
                        **copy.deepcopy(data.spec(item)),
                        "reference": source["value"],
                    }
                await resolver.validate(item, step, parameter)
    previous_cache = {
        entry["name"]: entry
        for entry in flow["label_cache"]
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    label_cache = [
        {
            **copy.deepcopy(previous_cache.get(pill, {})),
            **(await resolver.resolve(pill)),
            "usedInstances": used,
        }
        for pill, used in sorted(usage.items())
    ]
    payload["label_cache"] = label_cache
    # The saved getter represents the old cache, not the newly planned pills.
    # Native requests carry label_cache, not its server-generated JSON mirror.
    payload.pop("labelCacheAsJsonString", None)
    if call_evidence:
        _refresh_component_mirror(flow, payload)
    payloads.validate_definition(payload)
    semantics.studio_semantics(
        payload,
        pending_deletions=pending_deletions,
        allow_active_draft="active_runtime" in state,
    )
    reference_evidence: dict[str, Any] = {
        "calls": call_evidence,
        "logic": logic_evidence,
        "trigger_dependencies": trigger_evidence,
        "saved_triggers": state.get("saved_triggers", {}),
        "complex_schemas": resolver.schemas,
        "complex_definitions": resolver.definition_cache,
        "input_transforms": resolver.input_transform_metadata,
        "linked_tables": resolver.linked_tables,
        "declaration_tables": table_evidence,
        "declaration_choices": declaration_choice_evidence,
        "declaration_types": declaration_type_evidence,
        "declaration_qualifiers": declaration_qualifier_evidence,
        "pills": [
            {key: value for key, value in entry.items() if key != "usedInstances"}
            for entry in label_cache
        ],
        "dictionary": {
            f"{table}.{column}": row
            for (table, column), row in resolver.dictionary_cache.items()
        },
        "compatibility": {
            f"{source}:{target}": compatible
            for (source, target), compatible in resolver.table_cache.items()
        },
    }
    native_steps = False
    for step in tree.walk(roots):
        if step.uid not in created:
            continue
        if step.kind == "action" and "internal_name" in step.raw["actionType"]:
            step.raw = native.action(step.raw, payload)
        elif step.logic_type in {"DECISION", "DECISIONBLOCK"}:
            step.raw = decisions.serialize_new(step)
        elif step.logic_type in {"IF", "ELSE", "DOUNTIL", "GOBACKTO"}:
            step.raw = {
                **native.logic(step.raw),
                **{
                    key: step.raw[key]
                    for key in ("order", "parent", "children", *tree.UI_FIELDS)
                    if key in step.raw
                },
            }
        else:
            continue
        native_steps = True
        collection = tree.COLLECTIONS[step.kind]
        payload[collection] = [
            step.raw if row["uiUniqueIdentifier"] == step.uid else row
            for row in payload[collection]
        ]
    if native_steps:
        _refresh_component_mirror(flow, payload)
    if (
        triggers
        and not saved_triggers.is_saved(triggers[0])
        and any(
            edit.get("op") == "set_trigger"
            and edit.get("type") in {"service_catalog", *trigger_edits.SCHEDULED}
            for edit in command.edits or []
        )
    ):
        payload["triggerInstances"] = [
            native.catalog_trigger(triggers[0], catalog[triggers[0]["type"]])
        ]
    payloads.validate_definition(payload)
    if flow["active"] is False:
        semantics.studio_semantics(
            payload,
            pending_deletions=pending_deletions,
            allow_active_draft="active_runtime" in state,
        )
    result = {
        "payload": payload,
        "refs": {
            key: value for key, value in pill_aliases.items() if key != "error_handler"
        },
        "stage_refs": stage_aliases,
        "outline": trigger_lines(triggers) + tree.outline(roots),
        "summary": summary,
        "created": created,
        "pending_deletions": pending_deletions,
        "omitted_subflows": omitted_subflows,
        "created_stages": [key for key in created_stages if key in payload["stages"]],
        "created_fields": [
            row
            for row in created_fields
            if any(
                item.get("uiUniqueId") == row["uiUniqueId"]
                for _, item in declarations.walk(payload[row["collection"]])
            )
        ],
        "removed_fields": removed_fields,
        "generated_trigger_inputs": generated_trigger_inputs,
        "generated_authoring_rows": {
            "transforms": sorted(
                {
                    identity
                    for identity in generated_authoring_rows["transforms"]
                    if identity
                    in payload.get("pharmacy", {}).get("pharmacyCompound", {})
                }
            ),
            "connections": [
                entry
                for entry in generated_authoring_rows["connections"]
                if any(
                    row.get("sourceAlias") == entry["source_alias"]
                    and row.get("id", "") == ""
                    for owner in (
                        [payload]
                        if entry["component_ui_id"] is None
                        else [
                            step.raw
                            for step in tree.walk(roots)
                            if step.uid == entry["component_ui_id"]
                        ]
                    )
                    for row in owner.get("aliasOverride", {}).get(
                        "aliasOverrideMappings", []
                    )
                )
            ],
            "alias_ids": [
                step.uid
                for step in tree.walk(roots)
                if step.uid in created
                and step.kind == "action"
                and "aliasIds" not in step.raw
            ],
        },
        "reference_revision": fingerprint(reference_evidence),
    }
    trace_flow_preview_boundary(
        "planner.out",
        flow=payload,
        summary={
            "changes": summary,
            "after": result["outline"],
            "data_pills": label_cache,
        },
    )
    return result


def _refresh_component_mirror(before: dict[str, Any], after: dict[str, Any]) -> None:
    if "componentInstances" not in before:
        return
    # Studio updates the unified list before splitting the three wire collections
    # (bundle:205202-205236,99104,99193-99197). Untrimmed saves retain that list
    # (97521-97527,101697-101712). Do not discard unknown mirror-only metadata.
    original = sorted(
        (row for key in tree.COLLECTIONS.values() for row in before[key]),
        key=lambda row: tree.order_key(row["order"]),
    )
    if not isinstance(before["componentInstances"], list) or fingerprint(
        before["componentInstances"]
    ) != fingerprint(original):
        raise CapabilityError(
            "The componentInstances mirror differs from the complete ordered step lists. "
            "No mirror metadata is inferred or discarded.",
            "UNAVAILABLE",
        )
    after["componentInstances"] = copy.deepcopy(
        sorted(
            (row for key in tree.COLLECTIONS.values() for row in after[key]),
            key=lambda row: tree.order_key(row["order"]),
        )
    )


def _preserve_stage_attachments(
    before: dict[str, Any], after: dict[str, Any], roots: list[tree.Step]
) -> None:
    stages = after["stages"]
    if not isinstance(stages, dict):
        # Metadata-only edits can retain an opaque legacy stage representation.
        if stages and [
            (s.raw.get("id"), str(s.raw["order"]), s.raw["parent"])
            for s in tree.walk(roots)
        ] != [
            (s.raw.get("id"), str(s.raw["order"]), s.raw["parent"])
            for s in tree.walk(tree.build_tree(before))
        ]:
            raise CapabilityError(
                "Stage attachment format is unavailable for structural edits.",
                "UNAVAILABLE",
            )
        return
    original = sorted(
        (step for key in tree.COLLECTIONS.values() for step in before[key]),
        key=lambda s: tree.order_key(s["order"]),
    )
    ordered = list(tree.walk(roots))
    positions = {step.uid: i for i, step in enumerate(ordered)}
    scope = data.Scope(roots)
    for stage in stages.values():
        if not isinstance(stage, dict):
            raise CapabilityError("Stage metadata is malformed.", "UNAVAILABLE")
        indexes = stage.get("componentIndexes")
        if not isinstance(indexes, list):
            raise CapabilityError(
                "Stage component indexes are unavailable.", "UNAVAILABLE"
            )
        updated = []
        for index in indexes:
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(original)
            ):
                raise CapabilityError("Stage component indexes are malformed.")
            uid = original[index]["uiUniqueIdentifier"]
            if uid not in positions:
                raise CapabilityError(
                    "Deleting a stage-attached step requires explicit stage reassignment first."
                )
            if original[index]["parent"] != ordered[positions[uid]].raw["parent"]:
                raise CapabilityError(
                    "Stage-attached steps cannot change parent regions until the ancestry serializer is established.",
                    "UNAVAILABLE",
                )
            if any(parent.logic_type == "FOREACH" for parent in scope.parents[uid]):
                raise CapabilityError(
                    "A stage-attached step cannot move inside For Each."
                )
            updated.append(positions[uid])
        stage["componentIndexes"] = updated


def _update_stage(flow: dict[str, Any], edit: dict[str, Any]) -> None:
    stages = flow["stages"]
    identity = _str(edit, "stage", required=True) or ""
    if (
        not isinstance(stages, dict)
        or identity not in stages
        or not isinstance(stages[identity], dict)
    ):
        raise CapabilityError("The existing keyed stage is unavailable.", "UNAVAILABLE")
    if not set(edit) & {"label", "value"}:
        raise CapabilityError("update_stage requires label or value.")
    for key in ("label", "value"):
        if key in edit:
            value = _str(edit, key, required=True) or ""
            if len(value) > 255 or any(
                k != identity and (not isinstance(s, dict) or s.get(key) == value)
                for k, s in stages.items()
            ):
                raise CapabilityError(
                    "Stage names and values must be unique and at most 255 characters."
                )
            stages[identity][key] = value


def _update_field(flow: dict[str, Any], edit: dict[str, Any]) -> None:
    if set(edit) - {"op", "collection", "name", "label", "hint"}:
        raise CapabilityError(
            "update_field accepts collection, name, label, and hint only. Types and identities cannot change.",
        )
    collection = edit.get("collection")
    if collection not in {"inputs", "outputs", "flowVariables"}:
        raise CapabilityError("collection must be inputs, outputs, or flowVariables.")
    if collection in {"inputs", "outputs"} and flow.get("triggerInstances"):
        raise CapabilityError(
            "Trigger-owned interfaces cannot be edited independently.", "UNAVAILABLE"
        )
    field = data.named(flow[collection], _str(edit, "name", required=True) or "")
    if field.get("readonly") is True or field.get("readOnly") is True:
        raise CapabilityError("The declared field is read-only.")
    if not any(key in edit for key in ("label", "hint")):
        raise CapabilityError("update_field requires label or hint.")
    if any(key in edit and key not in field for key in ("label", "hint")):
        raise CapabilityError(
            "The field metadata is incomplete. Refusing to add an unobserved field.",
            "UNAVAILABLE",
        )
    for key in ("label", "hint"):
        if key in edit:
            value = _str(edit, key, required=True) or ""
            if len(value) > (255 if key == "label" else 4000):
                raise CapabilityError("Field metadata exceeds its maximum length.")
            if key == "label" and any(
                other is not field and other.get("label") == value
                for other in flow[collection]
            ):
                raise CapabilityError("Field labels must be unique.")
            field[key] = value


def _resolve_alias(roots: list[tree.Step], ref: str | None) -> str | None:
    """Translate an alias, sys_id, or display order into a stable UI identifier.

    Display orders shift as edits apply, so every reference is pinned to its UI
    identifier against the current tree before the edit changes it.
    """
    if ref is None:
        return None
    if ref in {"error_handler", "main"}:
        wrappers = handlers.pair(roots)
        if wrappers is None:
            raise CapabilityError(
                "This branch alias requires an existing error handler."
            )
        uid = wrappers[1 if ref == "error_handler" else 0].uid
        handlers.require_enabled(roots, uid)
        return uid
    for step in tree.walk(roots):
        if step.raw.get("_alias") == ref:
            handlers.require_enabled(roots, step.uid)
            return step.uid
    siblings, index, _ = tree.find(roots, ref)
    handlers.require_enabled(roots, siblings[index].uid)
    return siblings[index].uid


def _update_inputs(
    step: tree.Step,
    values: dict[str, Any],
    *,
    input_scripts: dict[str, str | None] | None = None,
) -> None:
    if step.logic_type in logic_blocks.TYPES:
        logic_blocks.update(step, values, input_scripts=input_scripts)
        return
    if step.logic_type == "DECISION" and "answer_table" in values:
        raise CapabilityError(
            "The decision answer table is derived from its selected table."
        )
    if step.logic_type == "DECISIONBLOCK" and set(values) - {"label"}:
        raise CapabilityError(
            "Decision branch answer identities are managed by their parent decision."
        )
    if step.kind == "flowlogic" and step.logic_type not in {
        "IF",
        "ELSEIF",
        "FOREACH",
        "SETFLOWVARIABLES",
        "DOUNTIL",
        "GOBACKTO",
        "DECISION",
        "DECISIONBLOCK",
    }:
        raise CapabilityError(
            "This flow-logic family's input adapter is unavailable.", "UNAVAILABLE"
        )
    by_name = {item["name"]: item for item in step.raw.get("inputs", [])}
    unknown = set(values) - set(by_name)
    if unknown:
        raise CapabilityError(f"Unknown inputs: {', '.join(sorted(unknown))}.")
    for name, value in values.items():
        item = by_name[name]
        data.set_value(item, value)
    if "data" in step.raw:
        step.raw["data"] = {
            item["name"]: item["value"] for item in step.raw.get("inputs", [])
        }
    if step.logic_type == "SETFLOWVARIABLES":
        step.raw["flowVariables"] = copy.deepcopy(step.raw["inputs"])


async def preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str]:
    state = await load_state(command, client)
    result = await plan(command, state, client)
    return preview_summary(state, result, command), plan_revision(state, result)


def plan_revision(state: dict[str, Any], result: dict[str, Any]) -> str:
    return fingerprint({"state": state, "references": result["reference_revision"]})


def preview_summary(
    state: dict[str, Any], result: dict[str, Any], command: FlowCommand
) -> dict[str, Any]:
    flow = state["flow"]
    summary = {
        "sys_id": flow["id"],
        "name": flow["name"],
        "active": flow["active"],
        "status": "draft",
        "changes": result["summary"],
        "before": trigger_lines(flow["triggerInstances"])
        + tree.outline(tree.build_tree(flow)),
        "after": result["outline"],
        "data_pills": [entry["name"] for entry in result["payload"]["label_cache"]],
        "adapter": support.ADAPTER,
        "verification": "offline_regression_only",
        "refs": result["refs"],
        "stage_refs": result["stage_refs"],
        "stages_before": stages.summary(flow, []),
        "stages": stages.summary(result["payload"], result["created_stages"]),
        "fields_before": fields.summary(flow),
        "fields": fields.summary(result["payload"]),
        "output_assignments_before": assignments.summary(tree.build_tree(flow)),
        "output_assignments": assignments.summary(tree.build_tree(result["payload"])),
        "error_handler_before": handlers.summary(flow),
        "error_handler": handlers.summary(result["payload"]),
        "triggers_before": trigger_edits.summary(flow["triggerInstances"]),
        "triggers": trigger_edits.summary(result["payload"]["triggerInstances"]),
        "deleted_instances": {
            **result["pending_deletions"],
            **(
                {"subFlowInstances": result["omitted_subflows"]}
                if result["omitted_subflows"]
                else {}
            ),
        },
        "deletion_methods": (
            {"subFlowInstances": "omit_from_complete_list"}
            if result["omitted_subflows"]
            else {}
        ),
    }
    if result["generated_trigger_inputs"]:
        summary["fields"]["inputs"] = fields.summary(
            {
                **result["payload"],
                "inputs": result["generated_trigger_inputs"]["declarations"],
            }
        )["inputs"]
    # A same-value input preview can locate serializer differences without another write.
    # It does not reconstruct an earlier request/echo or prove how the server normalizes saves.
    roots = tree.build_tree(flow)
    unchanged = bool(command.edits)
    for edit in command.edits or []:
        if edit["op"] != "update_inputs":
            unchanged = False
            break
        siblings, position, _ = tree.find(
            roots, _str(edit, "step", required=True) or ""
        )
        bindings = {item["name"]: item for item in siblings[position].raw["inputs"]}
        if set(_inputs(edit)) - set(bindings):
            unchanged = False
            break
        if any(
            fingerprint(bindings[name]["value"]) != fingerprint(value)
            for name, value in _inputs(edit).items()
        ):
            unchanged = False
            break
    if unchanged:
        check: dict[str, Any] = {
            "mode": "planned_vs_current_read_only",
            "matches": True,
        }
        try:
            semantics.compare_saved(
                result["payload"],
                flow,
                generated_ids=set(),
                allow_active_draft=active_drafts.eligible(flow),
            )
        except semantics.SemanticMismatch as exc:
            check.update(
                matches=False,
                path=exc.path,
                kind=exc.kind,
                side=exc.side,
                message=exc.detail(),
            )
        except CapabilityError:
            check.update(matches=False, path="flow", kind="COMPARISON_UNAVAILABLE")
        summary["serialization_check"] = check
    if "active_runtime" in state:
        summary["effect"] = "save_draft_preserving_current_runtime"
        summary["runtime_snapshot"] = state["active_runtime"]["snapshot"]["id"]
        summary["version"] = flow["version"]
    return summary


async def apply(
    command: FlowCommand,
    expected: str | None,
    client: ServiceNowClient,
    *,
    binding_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = await load_state(command, client)
    result = await plan(command, state, client)
    if plan_revision(state, result) != expected:
        raise CapabilityError(
            "The flow or its definitions changed since preview. Create a new preview.",
            "STALE_PREVIEW",
        )
    return await save_plan(
        command, result, client, binding_context=binding_context, active_state=state
    )


async def save_plan(
    command: FlowCommand,
    result: dict[str, Any],
    client: ServiceNowClient,
    *,
    binding_context: dict[str, Any] | None,
    active_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await target.require_current(command, client, binding_context)
    runtime = (active_state or {}).get("active_runtime")
    if result["payload"].get("active") is True and runtime is None:
        raise CapabilityError(
            "An active draft cannot be saved without bound runtime evidence."
        )
    if runtime is not None:
        assert active_state is not None
        await active_drafts.require_current(
            command, client, active_state["flow"], runtime
        )
    wire = copy.deepcopy(result["payload"])
    if runtime is not None:
        wire["status"] = "modified"
    saved = await semantics.save_draft(client, wire)
    actual = None
    if runtime is not None:
        try:
            actual = await client.studio_request(f"flow/{command.sys_id}")
            await active_drafts.verify_after(command, client, actual, runtime)
            if any(
                type(saved.get(k)) is not type(actual.get(k))
                or saved.get(k) != actual.get(k)
                for k in active_drafts.LIFECYCLE
            ):
                raise ValueError("Lifecycle echo mismatch")
        except Exception:  # noqa: BLE001 - contain unverified post-save effects
            raise CapabilityError(
                "Active-draft lifecycle or runtime verification failed. Inspect the flow. Do not replay the save.",
                "UNKNOWN_OUTCOME",
                outcome={
                    "runtime_preservation": "unknown",
                    "configuration": "unverified",
                },
            ) from None
    if (
        saved.get("id") != command.sys_id
        or saved.get("active") is not result["payload"]["active"]
        or saved.get("status") != "draft"
    ):
        raise CapabilityError(
            "The saved flow differs from the draft request. Inspect it before retrying.",
            "UNKNOWN_OUTCOME",
        )
    try:
        saved = await semantics.verify_saved(
            client,
            result["payload"],
            saved,
            generated_ids={""},
            pending_deletions=result["pending_deletions"],
            omitted_subflows=result["omitted_subflows"],
            generated_stages=set(result["created_stages"]),
            generated_fields=result["created_fields"],
            removed_fields=result["removed_fields"],
            generated_trigger_inputs=result["generated_trigger_inputs"],
            generated_authoring_rows=result.get("generated_authoring_rows"),
            allow_active_draft=runtime is not None,
            actual=actual,
        )
    except CapabilityError as exc:
        if runtime is None:
            raise
        raise CapabilityError(
            f"Active-draft configuration verification is inconclusive. {exc}",
            "UNKNOWN_OUTCOME",
            outcome={"runtime_preservation": "verified", "configuration": "unverified"},
        ) from None
    actual_outline = trigger_lines(saved["triggerInstances"]) + tree.outline(
        tree.build_tree(saved)
    )
    return {
        "action": "edit",
        "table": "sys_hub_flow",
        "sys_id": saved["id"],
        "name": saved.get("name"),
        "active": saved["active"],
        "status": saved["status"],
        "outline": actual_outline,
        "refs": persisted_refs(result, saved),
        "stage_refs": result["stage_refs"],
        **(
            {
                "runtime_preservation": "verified",
                "isPublished": saved["isPublished"],
                "version": saved["version"],
                "master_snapshot": saved["masterSnapshotId"],
                "latest_snapshot": saved["latestSnapshot"],
                "runtime_snapshot": runtime["snapshot"]["id"],
            }
            if runtime is not None
            else {}
        ),
    }


def persisted_refs(result: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    by_ui = {s.uid: s for s in tree.walk(tree.build_tree(saved))}
    return {
        alias: {"sys_id": by_ui[uid].raw["id"], "ui_id": uid, "kind": by_ui[uid].kind}
        for alias, uid in result["refs"].items()
    }
