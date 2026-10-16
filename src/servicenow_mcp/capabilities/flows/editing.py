"""Typed multi-step edits for Workflow Studio drafts.

Each edit is a small JSON object with an ``op`` key. Edits run in order against an
in-memory tree, so one preview can add a trigger, insert flow logic, and move
existing steps into a branch. The result is validated and saved in one PUT.
"""

import copy
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.capabilities.flows import capture, data, semantics, support, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.telemetry import trace_flow_preview_boundary
from servicenow_mcp.validation import validate_identifier

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


OPS = {
    "set_trigger",
    "insert_logic",
    "move_step",
    "delete_step",
    "update_inputs",
    "update_field",
    "set_variables",
    "update_stage",
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
        "set_trigger": {"type", "inputs", "replace"},
        "insert_logic": {"logic", "inputs", "ref", *placement},
        "move_step": {"step", *placement},
        "delete_step": {"step", "children", "cascade"},
        "update_inputs": {"step", "inputs"},
        "set_variables": {"inputs", *placement},
        "update_field": {"collection", "name", "label", "hint"},
        "update_stage": {"stage", "label", "value"},
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


def require_editable(flow: dict[str, Any], sys_id: str) -> None:
    validate_payload(flow, field_name="flow", validate_keys=False)
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
    if (
        flow.get("id") != sys_id
        or flow.get("type") != "flow"
        or flow.get("active") is not False
    ):
        raise CapabilityError("Only inactive flows can be edited.")
    if (
        flow.get("status") != "draft"
        or flow.get("isPublished") is not False
        or flow.get("masterSnapshotId")
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
    semantics.studio_semantics(flow)


def trigger_catalog(categories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for category in categories:
        triggers = category.get("triggers")
        if not isinstance(triggers, dict):
            raise CapabilityError("Studio returned an unexpected trigger catalog.")
        for definition in triggers.values():
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
        value = inputs.get(spec["name"], spec.get("default_value") or "")
        if isinstance(value, dict | list) or tree.pills(value):
            raise CapabilityError(
                "This trigger accepts static scalar configuration only."
            )
        if spec.get("mandatory") and value in ("", None):
            raise CapabilityError(
                f"The {definition['label']} trigger requires input '{spec['name']}'."
            )
        choices = parameter.get("choices") or []
        if choices and value not in ("", *(c["value"] for c in choices)):
            raise CapabilityError(
                f"'{value}' is not an allowed value for trigger input '{spec['name']}'."
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
        "inputs": values,
        "outputs": outputs,
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

    async def resolve(
        self, pill: str, consumer: tree.Step | None = None
    ) -> dict[str, Any]:
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
                    output = data.named(data.children(output), field)
                else:
                    kind = data.value_type(output)
                    parameter = data.spec(output)
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
            reference = output["parameter"].get("reference", "")
            return {
                "name": pill,
                "label": label,
                "reference": reference,
                "reference_display": output["parameter"].get("reference_display", ""),
                "type": output["type"],
                "base_type": output["type"],
                "parent_table_name": None,
                "column_name": None,
                "choices": None,
                "attributes": output["parameter"].get("attributes"),
            }
        table = output["parameter"].get("reference")
        if not table:
            raise CapabilityError(
                f"Trigger output '{path[0]}' is not a record and has no fields."
            )
        if pill not in self.cache:
            self.cache[pill] = await self._field(pill, label, table, path[1:])
        return self.cache[pill]

    async def validate(self, item: dict[str, Any], consumer: tree.Step) -> None:
        for binding in data.bindings([item]):
            value = binding.get("value")
            for pill in tree.pills(value):
                resolved = await self.resolve(pill, consumer)
                source = data.ValueType(
                    resolved["type"], resolved.get("reference") or ""
                )
                target = data.value_type(binding)
                interpolated = value != "{{" + pill + "}}"
                data.compatible(source, target, interpolated=interpolated)
                if not interpolated and (
                    target.kind == "object" or target.kind.startswith("array.")
                ):
                    raise CapabilityError(
                        "Whole object/array pills require a captured serializer and structural schema "
                        "compatibility adapter. Use declared leaf fields.",
                        "UNAVAILABLE",
                    )
                if not interpolated and target.kind == "choice":
                    allowed = {
                        c["value"] for c in data.spec(binding).get("choices", [])
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


async def load_state(command: FlowCommand, client: ServiceNowClient) -> dict[str, Any]:
    check_table_access("sys_hub_flow_logic_definition")
    flow = await client.studio_request(f"flow/{command.sys_id}")
    require_editable(flow, command.sys_id or "")
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
        if needs & {"insert_logic", "set_variables"}
        else []
    )
    triggers = (
        await client.studio_definitions("triggers") if "set_trigger" in needs else []
    )
    return {"flow": flow, "logic": logic, "triggers": triggers}


async def plan(
    command: FlowCommand, state: dict[str, Any], client: ServiceNowClient
) -> dict[str, Any]:
    """Apply the edits in memory and return the payload, outline, and change summary."""
    flow = state["flow"]
    trace_flow_preview_boundary(
        "planner.in", action=command.action, edits=command.edits, flow=flow
    )
    roots = tree.build_tree(flow)
    triggers = list(copy.deepcopy(flow["triggerInstances"]))
    logic_by_name = {d["name"].lower(): d for d in state["logic"]}
    logic_by_name.update({d["type"].lower(): d for d in state["logic"]})
    catalog = trigger_catalog(state["triggers"]) if state["triggers"] else {}
    created: list[str] = []
    summary: list[str] = []
    pending_deletions: dict[str, list[dict[str, str]]] = {}
    omitted_subflows: list[dict[str, str]] = []
    working = copy.deepcopy(flow)
    for index, edit in enumerate(command.edits or [], start=1):
        _check_edit(edit)
        op = edit["op"]
        if flow.get("startingIndexOfErrorHandlingInstances", -1) != -1:
            raise CapabilityError(
                "Top-level handler flows require an order-zero wrapper adapter before edits are available.",
                "UNAVAILABLE",
            )
        if op in {"move_step", "delete_step", "insert_logic", "set_variables"}:
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
                s.logic_type not in support.LOGIC_TYPES | {"CATCH", "SETFLOWVARIABLES"}
                for s in tree.walk(roots)
                if s.kind == "flowlogic"
            ):
                raise CapabilityError(
                    "Structural edits require family adapters for every existing flow-logic step.",
                    "UNAVAILABLE",
                )
        if op == "update_field":
            _update_field(working, edit)
            summary.append(f"{index}. Update declared field metadata")
        elif op == "set_variables":
            definition = next(
                (d for d in state["logic"] if d["type"] == "SETFLOWVARIABLES"), None
            )
            if definition is None or definition.get("userCanRead") is not True:
                raise CapabilityError(
                    "Set Flow Variables is unavailable in the instance catalog.",
                    "UNAVAILABLE",
                )
            step = tree.new_logic_step(flow["id"], definition, {})
            step.raw["inputs"] = []
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
            tree.insert(
                roots,
                step,
                parent=_resolve_alias(roots, _str(edit, "parent")),
                after=_resolve_alias(roots, _str(edit, "after")),
                before=_resolve_alias(roots, _str(edit, "before")),
            )
            summary.append(f"{index}. Assign flow variables")
        elif op == "set_trigger":
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
            if kind != "service_catalog":
                raise CapabilityError(
                    "This trigger is discovered but its family serializer is unavailable.",
                    "UNAVAILABLE",
                )
            if triggers and triggers[0].get("id"):
                pending_deletions.setdefault("deletedTriggers", []).append(
                    {"id": triggers[0]["id"]}
                )
            triggers = [new_trigger(flow, definition, _inputs(edit))]
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
                catch.raw["connectedTo"] = step.uid
                tree.insert(roots, catch, parent=None, after=step.uid, before=None)
            summary.append(f"{index}. Insert {definition['name']}")
        elif op in {"move_step", "delete_step", "update_inputs"}:
            label = _str(edit, "step", required=True) or ""
            ref = _resolve_alias(roots, label) or ""
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
                moved = tree.detach(roots, ref)
                if moved.logic_type in {"TRY", "CATCH"}:
                    raise CapabilityError(
                        "Moving a Try/Catch pair is unavailable. Move its children explicitly.",
                        "UNAVAILABLE",
                    )
                if parent and any(parent == s.uid for s in tree.walk([moved])):
                    raise CapabilityError("A block cannot be moved into itself.")
                tree.insert(roots, moved, parent=parent, after=after, before=before)
                summary.append(f"{index}. Move step {label}")
            else:
                siblings, position, _ = tree.find(roots, ref)
                _update_inputs(siblings[position], _inputs(edit))
                summary.append(f"{index}. Update inputs of step {label}")
        elif op == "update_stage":
            _update_stage(working, edit)
            summary.append(f"{index}. Update stage metadata")
    for step in tree.walk(roots):
        step.raw.pop("_alias", None)
    payload = tree.flatten(working, roots)
    if omitted_subflows:
        _refresh_component_mirror(flow, payload)
    _preserve_stage_attachments(flow, payload, roots)
    payload["triggerInstances"] = triggers
    payload.update(pending_deletions)
    trigger = triggers[0] if triggers else None
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
                value = item.get("value")
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
                await resolver.validate(item, step)
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
    validate_payload(payload, field_name="flow", validate_keys=False)
    semantics.studio_semantics(payload, pending_deletions=pending_deletions)
    reference_evidence: dict[str, Any] = {
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
    result = {
        "payload": payload,
        "outline": trigger_lines(triggers) + tree.outline(roots),
        "summary": summary,
        "created": created,
        "pending_deletions": pending_deletions,
        "omitted_subflows": omitted_subflows,
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
    for step in tree.walk(roots):
        if step.raw.get("_alias") == ref:
            return step.uid
    siblings, index, _ = tree.find(roots, ref)
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
    flow = state["flow"]
    summary = {
        "sys_id": flow["id"],
        "name": flow["name"],
        "active": False,
        "status": "draft",
        "changes": result["summary"],
        "before": trigger_lines(flow["triggerInstances"])
        + tree.outline(tree.build_tree(flow)),
        "after": result["outline"],
        "data_pills": [entry["name"] for entry in result["payload"]["label_cache"]],
        "adapter": support.ADAPTER,
        "verification": "offline_regression_only",
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
            semantics.compare_saved(result["payload"], flow, generated_ids=set())
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
    return summary, fingerprint(
        {"state": state, "references": result["reference_revision"]}
    )


async def apply(
    command: FlowCommand,
    expected: str | None,
    client: ServiceNowClient,
    *,
    binding_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = await load_state(command, client)
    result = await plan(command, state, client)
    if (
        fingerprint({"state": state, "references": result["reference_revision"]})
        != expected
    ):
        raise CapabilityError(
            "The flow or its definitions changed since preview. Create a new preview.",
            "STALE_PREVIEW",
        )
    await capture.require_current(command, client, binding_context)
    saved = await semantics.save_draft(client, result["payload"])
    if (
        saved.get("id") != command.sys_id
        or saved.get("active") is not False
        or saved.get("status") != "draft"
    ):
        raise CapabilityError(
            "The saved flow differs from the draft request. Inspect it before retrying.",
            "UNKNOWN_OUTCOME",
        )
    saved = await semantics.verify_saved(
        client,
        result["payload"],
        saved,
        generated_ids={""},
        pending_deletions=result["pending_deletions"],
        omitted_subflows=result["omitted_subflows"],
    )
    actual_outline = trigger_lines(saved["triggerInstances"]) + tree.outline(
        tree.build_tree(saved)
    )
    return {
        "action": "edit",
        "table": "sys_hub_flow",
        "sys_id": saved["id"],
        "name": saved.get("name"),
        "active": False,
        "status": "draft",
        "outline": actual_outline,
    }
