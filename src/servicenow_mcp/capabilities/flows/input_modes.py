"""Native input script modes and identity-bound script references.

Studio 27:108593-108734,17135-17142,194042-194066,200180-200224,
205241-205324. JavaScript is authored text, never evaluated by this module.
"""

import copy
import json
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, native, script_refs, tree
from servicenow_mcp.capabilities.mutations.models import fingerprint

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver

COLLECTIONS = (
    "inputs",
    "outputsToAssign",
    "dynamicInputs",
    "workflowInputs",
    "decisionTableInputs",
)
STATIC_SELECTORS = {
    "GOBACKTO": {"go_back_to_step"},
    "DECISION": {
        "decision_table",
        "execution",
        "has_branches",
        "include_otherwise",
        "answer_table",
    },
    "DECISIONBLOCK": {"answer"},
    "RUNDYNAMICFLOW": {"flow_template", "wait_for_completion"},
    "GETFLOWOUTPUTS": {"flow_template"},
}


def readonly(item: dict[str, Any]) -> bool:
    return any(
        source.get(key) in (True, "true")
        for source in (item, data.spec(item))
        for key in ("readonly", "readOnly", "read_only")
    )


def input_binding(step: tree.Step, name: str) -> dict[str, Any]:
    matches = [
        item
        for collection in (
            "inputs",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        )
        for item in step.raw.get(collection, [])
        if item.get("name") == name
    ]
    if len(matches) != 1:
        raise CapabilityError(
            "The scripted input name is absent or ambiguous across native input collections."
        )
    return matches[0]


def scripts(value: Any) -> dict[str, str | None]:
    """Strings enable scripting; null disables it; empty text clears its source."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CapabilityError(
            "input_scripts must map input names to script text or null."
        )
    result: dict[str, str | None] = {}
    for name, code in value.items():
        if (
            not isinstance(name, str)
            or not name
            or not (code is None or isinstance(code, str))
        ):
            raise CapabilityError(
                "input_scripts must map input names to script text or null."
            )
        if isinstance(code, str):
            _references(code)
        result[name] = code
    return result


def _references(code: str) -> list[script_refs.ScriptReference]:
    try:
        return script_refs.references(code)
    except ValueError as exc:
        raise CapabilityError(str(exc)) from None


def _script_map(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("script")
    if result in (None, ""):
        if item.get("scriptActive"):
            raise CapabilityError(
                "An active scripted input has no native script map.", "UNAVAILABLE"
            )
        result = {}
    if not isinstance(result, dict) or any(
        not isinstance(name, str)
        or not isinstance(entry, dict)
        or not isinstance(entry.get("script", ""), str)
        or any(
            key in entry and type(entry[key]) is not bool
            for key in ("scriptActive", "showScript")
        )
        for name, entry in result.items()
    ):
        raise CapabilityError(
            "The saved input script map is incomplete or malformed.", "UNAVAILABLE"
        )
    if bool(item.get("scriptActive")) != any(
        entry.get("scriptActive") is True for entry in result.values()
    ):
        raise CapabilityError(
            "The input script status differs from its native script map.", "UNAVAILABLE"
        )
    mirror = item.get("scriptAsJsonString")
    if mirror not in (None, ""):
        try:
            parsed = json.loads(mirror)
        except ValueError, TypeError:
            raise CapabilityError(
                "The input script JSON mirror is malformed.", "UNAVAILABLE"
            ) from None
        if fingerprint(parsed) != fingerprint(result):
            raise CapabilityError(
                "The input script JSON mirror differs from its native script map.",
                "UNAVAILABLE",
            )
    return result


def _scriptable(item: dict[str, Any], logic_type: str | None) -> None:
    attributes = data.spec(item).get("attributes", {})
    if not isinstance(attributes, dict):
        raise CapabilityError(
            "Input scriptability metadata is malformed.", "UNAVAILABLE"
        )
    if attributes.get("is_scriptable", True) not in (True, "true"):
        raise CapabilityError("The input definition does not permit inline scripts.")
    if (
        data.value_type(item).kind
        in {
            "template_value",
            "dynamic_inputs",
            "dynamic_template",
            "dynamic_choice",
            "glide_variables",
            "name_values",
        }
        or attributes.get("fd_hide_inline_script_widget") == "true"
        or item.get("isFromDynamicInput") is True
        or item.get("canDrop") is False
    ):
        raise CapabilityError(
            "The native input widget does not expose whole-input script mode."
        )
    if item["name"] in STATIC_SELECTORS.get(logic_type or "", set()):
        raise CapabilityError(
            "Native structural target and schema selectors require static values."
        )


def _allowed(step: tree.Step, name: str, code: str | None) -> None:
    item = input_binding(step, name)
    if readonly(item):
        raise CapabilityError("Read-only input scripts cannot be changed.")
    if data.value_type(item).kind in {"template_value", "dynamic_template"}:
        raise CapabilityError(
            "Template field scripts require a qualified input.column selector."
        )
    if code:
        _scriptable(item, step.logic_type)


def prepare(step: tree.Step, modes: dict[str, str | None]) -> None:
    """Permit an explicit mode switch before the ordinary basic-value setter."""
    for name, code in modes.items():
        item = input_binding(step, name)
        _allowed(step, name, code)
        previous = copy.deepcopy(_script_map(item))
        key = item.get("field_name") or item["name"]
        if key in previous:
            previous[key].update(scriptActive=False, showScript=False)
        item["script"] = previous
        item["scriptActive"] = any(
            entry.get("scriptActive") is True for entry in previous.values()
        )
        if "scriptAsJsonString" in item:
            item["scriptAsJsonString"] = json.dumps(
                previous, ensure_ascii=False, separators=(",", ":")
            )


def value(item: dict[str, Any], supplied: Any, *, scripted: bool = False) -> None:
    """Validate a basic-mode value without interpreting authored script results."""
    if not scripted:
        data.set_value(item, supplied)
        return
    probe = copy.deepcopy(item)
    probe["scriptActive"] = False
    if supplied in ("", None):
        # Native mandatory checking exempts active scripts, not their saved basic values.
        probe["parameter"] = {**copy.deepcopy(data.spec(item)), "mandatory": False}
        probe["mandatory"] = False
    data.set_value(probe, supplied)
    for key in ("value", "displayValue", "children"):
        if key in probe:
            item[key] = probe[key]


def apply(
    step: tree.Step, modes: dict[str, str | None], values: dict[str, Any] | None = None
) -> None:
    for name, code in modes.items():
        item = input_binding(step, name)
        _allowed(step, name, code)
        if values is not None and name in values and code:
            value(item, values[name], scripted=True)
        previous = copy.deepcopy(_script_map(item))
        key = item.get("field_name") or item["name"]
        entry = copy.deepcopy(previous.get(key, {}))
        for old in previous.values():
            old["showScript"] = False
        if code is not None:
            entry["script"] = code
        entry.update(scriptActive=bool(code), showScript=bool(code))
        previous[key] = entry
        item["script"] = previous
        item["scriptActive"] = any(
            row.get("scriptActive") is True for row in previous.values()
        )
        if "scriptAsJsonString" in item:
            item["scriptAsJsonString"] = json.dumps(
                previous, ensure_ascii=False, separators=(",", ":")
            )
        if not code:
            data.set_value(item, item.get("value", ""))
    if "data" in step.raw:
        for item in step.raw.get("inputs", []):
            step.raw["data"][item["name"]] = item.get("value", "")


def bind_schema(step: tree.Step, definition: dict[str, Any], names: set[str]) -> None:
    """Mode writes cannot change or rely on stale independently read parameter schemas."""
    for name in names:
        item = data.named(step.raw.get("inputs"), name)
        declared = data.named(definition.get("inputs"), name)
        if fingerprint(native.parameter(data.spec(item))) != fingerprint(
            native.parameter(data.spec(declared))
        ):
            raise CapabilityError(
                "The authored input mode schema differs from its current definition.",
                "STALE_PREVIEW",
            )


def _bindings(
    roots: list[tree.Step],
) -> Iterator[tuple[tree.Step, str, tuple[str, ...], dict[str, Any]]]:
    def visit(
        rows: list[dict[str, Any]], prefix: tuple[str, ...]
    ) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
        for index, item in enumerate(rows):
            path = (*prefix, str(index), str(item.get("name", "")))
            yield path, item
            children = item.get("children", [])
            if not isinstance(children, list) or any(
                not isinstance(row, dict) for row in children
            ):
                raise CapabilityError(
                    "Scripted input child bindings are malformed.", "UNAVAILABLE"
                )
            yield from visit(children, path)

    for step in tree.walk(roots):
        for collection in COLLECTIONS:
            rows = step.raw.get(collection, [])
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) for row in rows
            ):
                raise CapabilityError(
                    "Scripted input bindings are malformed.", "UNAVAILABLE"
                )
            for path, item in visit(rows, ()):
                yield step, collection, path, item


def _label(step: tree.Step) -> str:
    if step.kind == "action":
        definition = step.raw.get("actionType", {})
        label = (
            definition.get("internal_name")
            or definition.get("name")
            or step.raw.get("name")
        )
    elif step.kind == "subflow":
        label = step.raw.get("subFlow", {}).get("name") or step.raw.get("name")
    else:
        label = step.raw.get("flowLogicDefinition", {}).get("name") or step.raw.get(
            "name"
        )
    if not isinstance(label, str):
        raise CapabilityError(
            "The script producer has no native label metadata.", "UNAVAILABLE"
        )
    label = label.split("-")[-1].strip().lower().replace(" ", "_")
    return re.sub(r"[&!*\u0024#]", "", label)


def _orders(roots: list[tree.Step], *, saved: bool = False) -> dict[str, tree.Step]:
    projected = tree.project_orders(roots)
    scope = data.Scope(roots)
    result: dict[str, tree.Step] = {}
    for step in tree.walk(roots):
        order = step.raw.get("order") if saved else projected[step.uid].order
        number = tree.order_key(order)
        if any(
            parent.logic_type == "TOP_LEVEL_CATCH" for parent in scope.parents[step.uid]
        ):
            number += 1
        key = "_" + str(number) + "__" + _label(step)
        if key in result:
            raise CapabilityError(
                "Native script producer labels are ambiguous.", "UNAVAILABLE"
            )
        result[key] = step
    return result


class Scripts:
    """Pin positional script producers before edits, then rewrite exact lexical spans."""

    def __init__(self, roots: list[tree.Step]) -> None:
        self.bound: dict[
            tuple[str, str, tuple[str, ...], str], tuple[str, dict[str, str]]
        ] = {}
        self.capture(roots, saved=True)

    def capture(
        self,
        roots: list[tree.Step],
        *,
        step_uid: str | None = None,
        names: set[str] | None = None,
        saved: bool = False,
    ) -> None:
        producers: dict[str, tree.Step] | None = None
        for step, collection, path, item in _bindings(roots):
            if step_uid is not None and step.uid != step_uid:
                continue
            if names is not None and item.get("name") not in names:
                continue
            for name, entry in _script_map(item).items():
                code = entry.get("script", "")
                pinned: dict[str, str] = {}
                for reference in _references(code):
                    if reference.producer in {"trigger", "flow_var", "subflow_inputs"}:
                        continue
                    if producers is None:
                        producers = _orders(roots, saved=saved)
                    if reference.producer not in producers:
                        raise CapabilityError(
                            "The script references an absent or stale native producer."
                        )
                    pinned[reference.producer] = producers[reference.producer].uid
                self.bound[(step.uid, collection, path, name)] = (code, pinned)

    def rebind(self, roots: list[tree.Step]) -> None:
        if not any(pinned for _, pinned in self.bound.values()):
            return
        producers = _orders(roots)
        by_uid = {step.uid: key for key, step in producers.items()}
        for step, collection, path, item in _bindings(roots):
            changed = False
            entry_replacements: dict[str, dict[str, str]] = {}
            for name, entry in _script_map(item).items():
                code = entry.get("script", "")
                captured = self.bound.get((step.uid, collection, path, name))
                if captured is None or captured[0] != code:
                    if _references(code):
                        raise CapabilityError(
                            "Script reference identities were not bound before the structural edit.",
                            "UNAVAILABLE",
                        )
                    continue
                replacements = {}
                for producer, uid in captured[1].items():
                    if uid not in by_uid:
                        raise CapabilityError(
                            "Remove script references explicitly before deleting their producer."
                        )
                    replacements[producer] = by_uid[uid]
                entry_replacements[name] = replacements
                try:
                    updated = script_refs.rewrite(code, replacements)
                except ValueError as exc:
                    raise CapabilityError(str(exc)) from None
                if updated != code:
                    entry["script"] = updated
                    changed = True
                self.bound[(step.uid, collection, path, name)] = (
                    updated,
                    {
                        replacements.get(producer, producer): uid
                        for producer, uid in captured[1].items()
                    },
                )
            if changed and item.get("scriptAsJsonString") not in (None, ""):
                try:
                    item["scriptAsJsonString"] = script_refs.rewrite_mirror(
                        item["scriptAsJsonString"],
                        {},
                        entry_replacements=entry_replacements,
                    )
                except (TypeError, ValueError) as exc:
                    raise CapabilityError(str(exc)) from None


async def validate(
    item: dict[str, Any], consumer: tree.Step, resolver: PillResolver
) -> None:
    """Resolve every script data access with the same producer/table/scope checks as pills."""
    if item.get("scriptActive") and data.value_type(item).kind not in {
        "template_value",
        "dynamic_template",
    }:
        if readonly(data.spec(item)):
            raise CapabilityError("The declared input is read-only.")
        _scriptable(item, consumer.logic_type)
        value(copy.deepcopy(item), item.get("value", ""), scripted=True)
    producer_map: dict[str, tree.Step] | None = None
    for entry in _script_map(item).values():
        for reference in _references(entry.get("script", "")):
            if not reference.path:
                raise CapabilityError(
                    "Script data accesses require a declared field path."
                )
            if reference.producer == "trigger":
                if not resolver.trigger:
                    raise CapabilityError("The script references a missing trigger.")
                prefix = (
                    str(resolver.trigger.get("name") or resolver.trigger.get("label"))
                    + "_1"
                )
            elif reference.producer in {"flow_var", "subflow_inputs"}:
                prefix = (
                    "flow_variable" if reference.producer == "flow_var" else "subflow"
                )
            else:
                if producer_map is None:
                    producer_map = _orders(
                        [
                            step
                            for uid, step in resolver.scope.steps.items()
                            if not resolver.scope.parents[uid]
                        ]
                    )
                producer = producer_map.get(reference.producer)
                if producer is None:
                    raise CapabilityError(
                        "The script references an absent or stale native producer."
                    )
                prefix = producer.uid
            producer_step = resolver.scope.steps.get(prefix)
            if producer_step is not None and producer_step.kind in {
                "action",
                "subflow",
            }:
                from servicenow_mcp.capabilities.flows import calls

                if (
                    producer_step.raw.get("actionType", {}).get("type")
                    == "GETCATALOGVARIABLES"
                ):
                    key = "catalog/" + producer_step.uid
                    if key not in resolver.definition_cache:
                        resolver.definition_cache[key] = await calls.catalog_selection(
                            producer_step, resolver.flow, resolver.client, changed=False
                        )
                    outputs = resolver.definition_cache[key]["schema"]
                else:
                    subflow = producer_step.kind == "subflow"
                    identity = (
                        (
                            producer_step.raw.get("subFlow", {}).get("parentFlow")
                            or producer_step.raw["subflowSysId"]
                        )
                        if subflow
                        else producer_step.raw["actionTypeSysId"]
                    )
                    key = producer_step.kind + "/" + identity
                    if key not in resolver.definition_cache:
                        resolver.definition_cache[key] = await calls.definition(
                            resolver.flow,
                            identity,
                            subflow=subflow,
                            client=resolver.client,
                        )
                    outputs = resolver.definition_cache[key].get("outputs", [])
                embedded = producer_step.raw.get("outputs")
                if not embedded:
                    embedded = producer_step.raw.get(
                        "subFlow" if producer_step.kind == "subflow" else "actionType",
                        {},
                    ).get("outputs", [])
                saved = data.named(embedded, reference.path[0])
                declared = data.named(outputs, reference.path[0])
                if fingerprint(native.parameter(data.spec(saved))) != fingerprint(
                    native.parameter(data.spec(declared))
                ):
                    raise CapabilityError(
                        "The script producer schema differs from its current definition.",
                        "UNAVAILABLE",
                    )
            await resolver.resolve(prefix + "." + ".".join(reference.path), consumer)
