"""Typed multi-step edits for Workflow Studio drafts.

Each edit is a small JSON object with an ``op`` key. Edits run in order against an
in-memory tree, so one preview can add a trigger, insert flow logic, and move
existing steps into a branch. The result is validated and saved in one PUT.
"""

import copy
import json
import re
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    active_drafts,
    assignments,
    calls,
    data,
    declarations,
    fields,
    handlers,
    native,
    payloads,
    semantics,
    stages,
    support,
    target,
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
        "insert_action": {"action_type_sys_id", "inputs", "ref", *placement},
        "insert_subflow": {"subflow_sys_id", "inputs", "ref", *placement},
        "set_error_handler": {"enabled"},
        "remove_error_handler": set(),
        "set_trigger": {"type", "inputs", "replace"},
        "update_trigger": {"inputs"},
        "remove_trigger": set(),
        "insert_logic": {"logic", "inputs", "ref", *placement},
        "move_step": {"step", *placement},
        "delete_step": {"step", "children", "cascade"},
        "update_inputs": {"step", "inputs"},
        "set_variables": {"inputs", *placement},
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
        flow.get("status") != "draft"
        or flow.get("isPublished") is not False
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
        """Resolve only saved identity transforms. Preserve their opaque metadata.

        pharmacyPill:61324-61331. Nonidentity transforms need typed adapters.
        """
        if not pill.startswith("fd_transform:"):
            return pill
        identity = pill.removeprefix("fd_transform:")
        compound = (
            self.flow.get("pharmacy", {}).get("pharmacyCompound", {}).get(identity)
        )
        if (
            not isinstance(compound, dict)
            or compound.get("uiId") != identity
            or compound.get("attachedTo") != self.flow.get("id")
            or compound.get("scopeId") != self.flow.get("scope")
        ):
            raise CapabilityError(
                "The encoded transform is missing or belongs to another flow.",
                "UNAVAILABLE",
            )
        if (
            compound.get("order") != 0
            or compound.get("sourceUiId")
            or compound.get("transformDefinitionId")
            or compound.get("compositions")
        ):
            raise CapabilityError(
                "Nonidentity transforms require another type adapter.", "UNAVAILABLE"
            )
        expression = compound.get("prescription")
        result = data.whole_pill(expression)
        if result is None or result.startswith("fd_transform:"):
            raise CapabilityError(
                "The transform prescription requires one ordinary typed pill.",
                "UNAVAILABLE",
            )
        return result

    async def resolve(
        self, pill: str, consumer: tree.Step | None = None
    ) -> dict[str, Any]:
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
                outputs = (
                    handlers.outputs(definition)
                    if producer.logic_type == handlers.CATCH
                    else definition.get(
                        "variables" if producer.kind == "flowlogic" else "outputs"
                    )
                )
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
                    if kind.kind in declarations.LABELS:
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
        trigger_name = f"Trigger - {trigger_label(self.trigger)}"
        label = (
            f"{trigger_name}\u279b{output['parameter'].get('label') or output['label']}"
        )
        if len(path) == 1:
            resolved_type = trigger_edits.output_type(self.trigger, output)
            reference = resolved_type.reference
            return {
                "name": pill,
                "label": label,
                "reference": reference,
                "reference_display": output["parameter"].get("reference_display", ""),
                "type": resolved_type.kind,
                "base_type": resolved_type.kind,
                "parent_table_name": None,
                "column_name": None,
                "choices": None,
                "attributes": output["parameter"].get("attributes"),
            }
        table = trigger_edits.output_type(self.trigger, output).reference
        if not table:
            raise CapabilityError(
                f"Trigger output '{path[0]}' is not a record and has no fields."
            )
        if pill not in self.cache:
            self.cache[pill] = await self._field(pill, label, table, path[1:])
        return self.cache[pill]

    async def validate(
        self,
        item: dict[str, Any],
        consumer: tree.Step,
        declared: dict[str, Any] | None = None,
    ) -> None:
        for binding, parameter in self._bindings(item, declared):
            kind = widgets.kind(parameter)
            if kind == "template_value":
                await self._template(binding, parameter, consumer)
                continue
            if kind == "conditions" and consumer.kind == "action":
                schema = data.spec(parameter)
                dependency = schema.get("dependent_on")
                if not isinstance(dependency, str) or not dependency:
                    raise CapabilityError(
                        "Action conditions require a declared static table dependency.",
                        "UNAVAILABLE",
                    )
                source = data.named(
                    consumer.raw["inputs"], dependency.removeprefix("inputs.")
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
            value = widgets.pill_text(binding, kind, data.spec(parameter))
            approval_types = (
                dict(widgets.approval_references(value))
                if kind == "approval_rules"
                else {}
            )
            for pill in tree.pills(value):
                resolved = await self.resolve(pill, consumer)
                source = data.ValueType(
                    resolved["type"], resolved.get("reference") or ""
                )
                target = data.value_type(parameter)
                interpolated = value != "{{" + pill + "}}"
                if kind == "approval_rules":
                    if source.kind not in {
                        "reference",
                        "document_id",
                    } or source.reference != approval_types.get(pill):
                        raise CapabilityError(
                            "Approval approvers must use the declared user/group reference type."
                        )
                    continue
                data.compatible(source, target, interpolated=interpolated)
                if not interpolated and (
                    target.kind == "object" or target.kind.startswith("array.")
                ):
                    if binding.get("scriptActive") or any(
                        data.spec(parameter).get(key)
                        for key in ("readonly", "readOnly", "read_only")
                    ):
                        raise CapabilityError(
                            "Whole complex pill targets cannot be scripted or read-only.",
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
                            "Choice pills require a declared producer choice set contained in the target choices.",
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
        value = binding.get("value")
        widgets.validate("template_value", value, data.spec(parameter))
        schema = data.spec(parameter)
        table = schema.get("reference")
        if dependency := schema.get("dependent_on"):
            source = data.named(
                consumer.raw["inputs"], dependency.removeprefix("inputs.")
            )
            if (
                data.value_type(source).kind != "table_name"
                or not isinstance(source.get("value"), str)
                or tree.pills(source["value"])
            ):
                raise CapabilityError(
                    "Template fields require a static declared table dependency."
                )
            if table and source["value"] != table:
                raise CapabilityError(
                    "The template table differs from its declared reference."
                )
            table = source["value"]
        if not table:
            raise CapabilityError(
                "The template field table is unavailable.", "UNAVAILABLE"
            )
        validate_identifier(table)
        check_table_access(table)
        for column, encoded in widgets.template_fields(value):
            row = await self._dictionary(table, column)
            kind = row["internal_type"]
            if kind not in {
                "reference",
                "string",
                "html",
                "translated_html",
                "integer",
                "boolean",
                "choice",
                "journal_input",
            }:
                raise CapabilityError(
                    "This template column type requires another adapter.", "UNAVAILABLE"
                )
            pills = tree.pills(encoded)
            if pills:
                target = data.ValueType(
                    "string"
                    if kind in {"html", "translated_html", "journal_input"}
                    else kind,
                    row.get("reference", ""),
                )
                for pill in pills:
                    resolved = await self.resolve(pill, consumer)
                    source = data.ValueType(
                        resolved["type"], resolved.get("reference") or ""
                    )
                    data.compatible(
                        source, target, interpolated=encoded != "{{" + pill + "}}"
                    )
                    if target.reference and not await self._is_table(
                        source.reference, target.reference
                    ):
                        raise CapabilityError(
                            "The template reference pill has an incompatible table."
                        )
            elif kind == "reference" and encoded:
                try:
                    reference = json.loads(encoded)
                except ValueError:
                    raise CapabilityError(
                        "Template references require native reference JSON."
                    ) from None
                if (
                    not isinstance(reference, dict)
                    or reference.get("table") != row["reference"]
                    or reference.get("value") != reference.get("sys_id")
                ):
                    raise CapabilityError(
                        "Template reference JSON identity or table is inconsistent."
                    )
                validate_sys_id(reference["value"])
            elif (
                kind in {"integer", "boolean"}
                and encoded
                and not re.fullmatch(
                    r"-?\d+" if kind == "integer" else r"true|false|0|1", encoded
                )
            ):
                raise CapabilityError(
                    "The template scalar value has an invalid native type."
                )

    def _bindings(
        self, item: dict[str, Any], declared: dict[str, Any] | None = None
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        parameter = declared if declared is not None else data.spec(item)
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
        prefix, _, path = pill.partition(".")
        producer = self.scope.steps.get(prefix)
        if producer is None and prefix != "subflow":
            raise CapabilityError(
                "Whole pills require a declared subflow input or a supported step output.",
                "UNAVAILABLE",
            )
        if producer is not None:
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
        allowed = fields.INTERFACE_TYPES.keys() | {"choice", "document_id"}
        if produced.kind not in allowed or wanted.kind not in allowed:
            raise CapabilityError(
                "This whole-pill field type requires another schema adapter.",
                "UNAVAILABLE",
            )
        data.compatible(produced, wanted, interpolated=False)
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
            allowed = {row["value"] for row in data.spec(target).get("choices", [])}
            actual = {row["value"] for row in data.spec(source).get("choices", [])}
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
        for name in path[:-1]:
            validate_identifier(name)
            row = await self._dictionary(table, name)
            if row.get("internal_type") != "reference" or not row.get("reference"):
                raise CapabilityError(f"'{name}' on {table} is not a reference field.")
            label = f"{label}\u279b{row['column_label']}"
            table = row["reference"]
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
        else []
    )
    triggers = (
        await client.studio_definitions("triggers") if needs & trigger_edits.OPS else []
    )
    state = {"flow": flow, "logic": logic, "triggers": triggers}
    if flow["active"] is True:
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
    trigger_evidence: dict[str, Any] = {}
    call_evidence: dict[str, Any] = {}
    call_definitions: dict[tuple[bool, str], dict[str, Any]] = {}
    dependency_changed: set[str] = set()
    # Bind client stage UUIDs to this exact definition and edit list. Replanning
    # preview/apply must not create different stage keys.
    stage_seed = fingerprint(
        {
            "state": command.ui_id if command.action == "create" else state,
            "edits": command.model_dump(mode="json")["edits"],
        }
    )
    for index, edit in enumerate(command.edits or [], start=1):
        _check_edit(edit)
        op = _str(edit, "op", required=True) or ""
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
            for step in tree.walk(roots):
                for key in (
                    "inputs",
                    "outputsToAssign",
                    "dynamicInputs",
                    "workflowInputs",
                    "decisionTableInputs",
                ):
                    for item in data.bindings(step.raw.get(key, [])):
                        if item.get("scriptActive") or "fd_data." in str(
                            item.get("script", "")
                        ):
                            raise CapabilityError(
                                "Structural edits cannot renumber inline-script references.",
                                "UNAVAILABLE",
                            )
            if any(
                s.logic_type
                not in support.LOGIC_TYPES
                | {"CATCH", "SETFLOWVARIABLES", assignments.KIND, *handlers.TYPES}
                for s in tree.walk(roots)
                if s.kind == "flowlogic"
            ):
                raise CapabilityError(
                    "Structural edits require family adapters for every existing flow-logic step.",
                    "UNAVAILABLE",
                )
        if op in {"insert_action", "insert_subflow"}:
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
            step = calls.step(
                working,
                definition,
                _inputs(edit),
                subflow=subflow,
                uid=str(
                    uuid.UUID(
                        hex=fingerprint([stage_seed, "call", index])[:32], version=4
                    )
                ),
            )
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
            existing = next(
                (
                    row
                    for path, row in declarations.walk(working[section])
                    if path == edit.get("name")
                ),
                None,
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
                )
                if changed is not None:
                    created_fields.append(changed)
                if op != "delete_field":
                    changed_name = str(edit["name"])
                    if edit.get("parent"):
                        changed_name = str(edit["parent"]) + "." + changed_name
                    for path, declared in declarations.walk(working[section]):
                        if path != changed_name:
                            continue
                        if declared.get("type") == "reference":
                            reference_tables.add(declared["reference"])
                if (
                    section == "flowVariables"
                    and existing is not None
                    and op == "update_field"
                ):
                    fields.refresh_assignments(
                        roots,
                        data.named(working[section], str(edit["name"])),
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
                        data.named(working[section], str(edit["name"])),
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
            for name, value in _inputs(edit).items():
                parameter = data.named(working["flowVariables"], name)
                binding = {
                    "id": "",
                    "name": name,
                    "parameter": copy.deepcopy(parameter),
                    "children": [],
                    "value": "",
                    "scriptActive": False,
                }
                data.set_value(binding, value)
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
            summary.append(f"{index}. Assign flow variables")
        elif op in trigger_edits.OPS:
            if len(triggers) > 1:
                raise CapabilityError(
                    "This trigger adapter requires at most one trigger.", "UNAVAILABLE"
                )
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
                definition = catalog.get(triggers[0]["type"])
                if definition is None:
                    raise CapabilityError(
                        "The saved trigger definition is unavailable in the current catalog.",
                        "UNAVAILABLE",
                    )
                trigger_edits.update(triggers[0], definition, _inputs(edit))
                trigger_evidence[str(index)] = await trigger_edits.prepare(
                    triggers[0],
                    definition,
                    client,
                    resolver=PillResolver(client, triggers[0]),
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
                and not trigger_edits.SCHEDULE_INPUTS[kind].keys() <= inputs.keys()
            ):
                raise CapabilityError(
                    "Supply every native schedule input explicitly. Clock defaults are not selected automatically."
                )
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
            step = tree.new_logic_step(flow["id"], definition, _inputs(edit))
            # A new error stage can reference this branch in the same preview.
            # Preserve that reviewed ancestry across preview/apply replanning.
            step.raw["uiUniqueIdentifier"] = str(
                uuid.UUID(hex=fingerprint([stage_seed, "logic", index])[:32], version=4)
            )
            for item in step.raw["inputs"]:
                data.set_value(item, item["value"])
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
            summary.append(f"{index}. Insert {definition['name']}")
        elif op in {"move_step", "delete_step", "update_inputs"}:
            label = _str(edit, "step", required=True) or ""
            ref = _resolve_alias(roots, label) or ""
            siblings, position, _ = tree.find(roots, ref)
            if siblings[position].logic_type in handlers.TYPES:
                raise CapabilityError(
                    "Use handler operations for hidden wrapper settings. Wrapper moves and generic deletion are unavailable."
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
                for step in deleting:
                    if step.kind == "subflow" and step.raw.get("id"):
                        # Complete-list omission is Studio's subflow call deletion
                        # wire contract (bundle:205202, 205365, 130432).
                        overrides = working.get("aliasOverride", {})
                        if not isinstance(overrides, dict) or overrides.get(
                            "aliasOverrideMappings"
                        ):
                            raise CapabilityError(
                                "Subflow deletion with connection overrides is unavailable. "
                                "The override cleanup contract is not implemented.",
                                "UNAVAILABLE",
                            )
                        omitted_subflows.append(
                            {
                                "id": step.raw["id"],
                                "uiUniqueIdentifier": step.uid,
                            }
                        )
                    elif step.raw.get("id"):
                        key = (
                            "deletedActions"
                            if step.kind == "action"
                            else "deletedFlowLogicInstances"
                        )
                        pending_deletions.setdefault(key, []).append(
                            {"id": step.raw["id"]}
                        )
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
                _update_inputs(siblings[position], _inputs(edit))
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
    generated_trigger_inputs = None
    if (
        payload["type"] == "flow"
        and not flow["inputs"]
        and len(triggers) == 1
        and triggers[0]["type"] == "service_catalog"
        and any(edit["op"] == "set_trigger" for edit in command.edits or [])
    ):
        definition = catalog["service_catalog"]
        if definition["id"] != triggers[0]["triggerDefinitionId"]:
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
        for key in (
            "inputs",
            "outputsToAssign",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        ):
            for item in data.bindings(step.raw.get(key, [])):
                value = widgets.pill_text(item, widgets.kind(item), data.spec(item))
                if isinstance(value, str):
                    remainder = tree.PILL.sub("", value)
                    if "{{" in remainder or "}}" in remainder:
                        raise CapabilityError(
                            "A saved binding contains malformed pill syntax."
                        )
    for trigger in triggers:
        if any(
            tree.pills(item.get("value"))
            for item in data.bindings(trigger.get("inputs", []))
        ):
            raise CapabilityError(
                "Trigger inputs cannot reference values produced by the flow."
            )
    resolver = PillResolver(client, trigger, payload, roots)
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
        allow_active_draft=flow["active"] is True,
    )
    reference_evidence: dict[str, Any] = {
        "calls": call_evidence,
        "trigger_dependencies": trigger_evidence,
        "complex_schemas": resolver.schemas,
        "complex_definitions": resolver.definition_cache,
        "linked_tables": resolver.linked_tables,
        "declaration_tables": table_evidence,
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
        if step.uid not in created or flow.get("active") is not False:
            continue
        if step.kind == "action" and "internal_name" in step.raw["actionType"]:
            step.raw = native.action(step.raw, payload)
        elif step.logic_type in {"IF", "ELSE"}:
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
    if flow["active"] is False and any(
        edit.get("op") == "set_trigger" and edit.get("type") == "service_catalog"
        for edit in command.edits or []
    ):
        payload["triggerInstances"] = [
            native.catalog_trigger(triggers[0], catalog["service_catalog"])
        ]
    payloads.validate_definition(payload)
    if flow["active"] is False:
        semantics.studio_semantics(payload, pending_deletions=pending_deletions)
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
        key=lambda row: int(row["order"]),
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
            key=lambda row: int(row["order"]),
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
        key=lambda s: int(s["order"]),
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


def _update_inputs(step: tree.Step, values: dict[str, Any]) -> None:
    if step.kind == "flowlogic" and step.logic_type not in {
        "IF",
        "ELSEIF",
        "FOREACH",
        "SETFLOWVARIABLES",
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
                allow_active_draft=flow["active"] is True,
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
        or saved.get("active") is not (runtime is not None)
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
