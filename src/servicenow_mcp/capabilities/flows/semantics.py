"""Persisted flow semantics shared by Studio planning and save verification.

Studio remains the serialization adapter. Display outlines are never evidence of
saved values. Unknown fields stay in the original payload, not in this view.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NoReturn

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    data,
    declarations,
    handlers,
    payloads,
    tree,
)
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


COLLECTIONS = {
    "actionInstances": "action",
    "flowLogicInstances": "flowlogic",
    "subFlowInstances": "subflow",
}
PRESERVED = ("inputs", "outputs", "flowVariables", "stages")
# Only application-declared field names can appear in public comparison paths.
_DIAGNOSTIC_FIELDS = frozenset(
    {
        "id",
        "type",
        "active",
        "status",
        "isPublished",
        "snapshot",
        "masterSnapshot",
        "masterSnapshotId",
        "flowEngineVersion",
        "scope",
        "name",
        "label",
        "inputs",
        "outputs",
        "flowVariables",
        "stages",
        "label_cache",
        "description",
        "security",
        "can_read",
        "can_write",
        "value",
        "displayValue",
        "children",
        "parameter",
        "mandatory",
        "choices",
        "maxsize",
        "readonly",
        "readOnly",
        "reference",
        "attributes",
        "order",
        "parent",
        "uiUniqueIdentifier",
        "actionTypeSysId",
        "actionType",
        "definitionId",
        "flowLogicDefinition",
        "subflowSysId",
        "subFlow",
        "waitForCompletion",
        "data",
        "script",
        "scriptActive",
        "depth",
        "ancestorUiId",
        "ancestorType",
        "isFirstFlowLogicChild",
        "isPrecededByThen",
        "sys_updated_on",
        "sys_mod_count",
        "updated",
        "updatedBy",
        "savedDate",
        "contentACLFilteredOrders",
        "nonCriticalErrors",
    },
)
# Fixed Studio settings, scoped to their schema location rather than arbitrary
# mapping keys. Source: docs/designer-bundle.min.js:203732-203750,204678-204690.
_DIAGNOSTIC_SETTING_FIELDS = {
    "flow": frozenset(
        {
            "alerts",
            "componentInstances",
            "deletedActions",
            "deletedFlowLogicInstances",
            "deletedTriggers",
            "aliasOverride",
        }
    ),
    "flow.aliasOverride": frozenset({"aliasOverrideMappings"}),
}


class SemanticMismatch(CapabilityError):
    """An equality failure with a value-free path and controlled invariant category."""

    def __init__(
        self,
        path: str,
        kind: str,
        side: str = "both",
        invariant: str = "",
        *,
        expected_count: int | None = None,
        actual_count: int | None = None,
    ) -> None:
        # Only native lengths reach diagnostic text. Never coerce payload values.
        self._counts = (
            f" expected_count={expected_count} actual_count={actual_count}"
            if kind == "COUNT_MISMATCH"
            and type(expected_count) is int
            and type(actual_count) is int
            and expected_count >= 0
            and actual_count >= 0
            else ""
        )
        super().__init__(
            f"Saved step topology, definitions, or values differ from the request ({side}:{path} {kind}{self._counts}). "
            "Inspect the flow.",
            "UNKNOWN_OUTCOME",
        )
        self.path, self.kind, self.side, self.invariant = path, kind, side, invariant

    def detail(self) -> str:
        return f"{self.side}:{self.path} {self.kind}{self._counts}" + (
            f". {self.invariant}" if self.invariant else ""
        )


def _difference(left: Any, right: Any, path: str) -> SemanticMismatch | None:
    if _json(left) == _json(right):
        return None
    if type(left) is not type(right):
        return SemanticMismatch(path, "TYPE_MISMATCH")
    if isinstance(left, dict):
        for index, key in enumerate(sorted(left.keys() | right.keys())):
            location = path + (
                f".{key}"
                if key in _DIAGNOSTIC_FIELDS
                or key in _DIAGNOSTIC_SETTING_FIELDS.get(path, ())
                else f"[field:{index}]"
            )
            if key not in left or key not in right:
                return SemanticMismatch(
                    location,
                    "MISSING_FIELD",
                    "expected" if key not in left else "actual",
                )
            difference = _difference(left[key], right[key], location)
            if difference:
                return difference
    elif isinstance(left, list):
        if len(left) != len(right):
            return SemanticMismatch(
                path,
                "COUNT_MISMATCH",
                expected_count=len(left),
                actual_count=len(right),
            )
        for index, (wanted, found) in enumerate(zip(left, right, strict=True)):
            difference = _difference(wanted, found, f"{path}[{index}]")
            if difference:
                return difference
    return SemanticMismatch(path, "VALUE_MISMATCH")


def _binding_view(encoded: str, originals: list[dict[str, Any]]) -> list[Any]:
    values = json.loads(encoded)
    names = [item["name"] for item in originals if item["name"] in values]
    names.extend(sorted(values.keys() - set(names)))
    return [values[name] for name in names]


def _semantic_difference(
    wanted: FlowSemantics, found: FlowSemantics, expected: dict[str, Any]
) -> SemanticMismatch:
    difference = _difference(wanted.identity, found.identity, "flow.id")
    if difference:
        return difference
    difference = _difference(
        json.loads(wanted.settings), json.loads(found.settings), "flow"
    )
    if difference:
        return difference
    trigger_views = []
    for encoded in (wanted.triggers, found.triggers):
        triggers = json.loads(encoded)
        for index, trigger in enumerate(triggers):
            originals = (
                expected["triggerInstances"][index]["inputs"]
                if index < len(expected["triggerInstances"])
                else []
            )
            trigger["inputs"] = _binding_view(trigger["inputs"], originals)
        trigger_views.append(triggers)
    difference = _difference(
        trigger_views[0], trigger_views[1], "flow.triggerInstances"
    )
    if difference:
        return difference
    if len(wanted.steps) != len(found.steps):
        return SemanticMismatch(
            "flow.steps",
            "COUNT_MISMATCH",
            expected_count=len(wanted.steps),
            actual_count=len(found.steps),
        )
    paths = {
        row["uiUniqueIdentifier"]: f"flow.{collection}[{index}]"
        for collection in COLLECTIONS
        for index, row in enumerate(expected[collection])
    }
    inputs = {
        row["uiUniqueIdentifier"]: row["inputs"]
        for collection in COLLECTIONS
        for row in expected[collection]
    }
    for left, right in zip(wanted.steps, found.steps, strict=True):
        path = paths[left.ui_id]
        for attribute, field in (
            ("kind", "type"),
            ("identity", "id"),
            ("ui_id", "uiUniqueIdentifier"),
            ("order", "order"),
            ("parent", "parent"),
            ("definition", "definition"),
        ):
            difference = _difference(
                getattr(left, attribute), getattr(right, attribute), path + "." + field
            )
            if difference:
                return difference
        difference = _difference(
            _binding_view(left.inputs, inputs[left.ui_id]),
            _binding_view(right.inputs, inputs[left.ui_id]),
            path + ".inputs",
        )
        if difference:
            return difference
        difference = _difference(
            json.loads(left.settings), json.loads(right.settings), path
        )
        if difference:
            return difference
    return SemanticMismatch("flow", "VALUE_MISMATCH")


def _json(value: Any) -> str:
    # JSON equality distinguishes false from 0 and true from 1.
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except TypeError, ValueError:
        _incomplete()


class IncompleteSemantics(CapabilityError):
    """A parser invariant with a safe schema path, never a source value."""

    def __init__(self, path: str, invariant: str) -> None:
        super().__init__(invariant, "UNKNOWN_OUTCOME")
        self.path = path


def _incomplete(
    path: str = "flow",
    invariant: str = "Studio returned incomplete or malformed flow semantics. Inspect the flow.",
) -> NoReturn:
    raise IncompleteSemantics(path, invariant)


def _identity(value: str, path: str = "flow.id") -> None:
    try:
        validate_sys_id(value)
    except ValueError:
        _incomplete(
            path,
            "Read a valid 32-character persisted identity. No identity is inferred or replaced.",
        )


def _bindings(raw: Any, path: str = "flow.inputs") -> str:
    if not isinstance(raw, list):
        _incomplete(
            path,
            "Read the complete binding list. Missing or non-list bindings cannot establish semantics.",
        )
    names: set[str] = set()
    values: dict[str, Any] = {}
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            _incomplete(item_path, "Each binding must be an object.")
        if not isinstance(item.get("name"), str):
            _incomplete(
                item_path + ".name", "Each binding requires a readable string name."
            )
        if "value" not in item:
            _incomplete(
                item_path + ".value",
                "Read the stored binding value. An absent value is not an empty default.",
            )
        name = item["name"]
        if not name or name in names:
            _incomplete(
                item_path + ".name",
                "Binding names must be nonempty and unique within the binding list.",
            )
        names.add(name)
        # Keep nested values and literal pill references without coercion or display substitution.
        values[name] = item
    return _json(values)


@dataclass(frozen=True)
class SemanticStep:
    kind: str
    identity: str
    ui_id: str
    order: int
    parent: str
    definition: str
    inputs: str
    settings: str


@dataclass(frozen=True)
class FlowSemantics:
    identity: str
    steps: tuple[SemanticStep, ...]
    triggers: str
    settings: str


def table_step(
    *,
    kind: str,
    identity: str,
    ui_id: str,
    order: str,
    parent: str,
    definition: str,
    inputs: Any,
    settings: dict[str, Any],
) -> SemanticStep:
    """Adapt a decoded V2 table row to the same semantic fields used by Studio."""
    if (
        not identity
        or not ui_id
        or not definition
        or not isinstance(inputs, list | dict)
    ):
        _incomplete()
    try:
        numeric_order = int(order)
    except ValueError:
        _incomplete()
    if numeric_order < 1:
        _incomplete()
    if isinstance(inputs, list):
        bindings = _bindings(inputs)
    else:
        # Logic blobs also contain non-binding settings. Preserve them in full.
        for key in (
            "inputs",
            "decisionTableInputs",
            "dynamicInputs",
            "workflowInputs",
            "outputsToAssign",
        ):
            if key in inputs:
                _bindings(inputs[key])
        bindings = _json(inputs)
    return SemanticStep(
        kind,
        identity,
        ui_id,
        numeric_order,
        parent,
        definition,
        bindings,
        _json(settings),
    )


def studio_semantics(
    flow: dict[str, Any],
    *,
    pending_deletions: dict[str, list[dict[str, str]]] | None = None,
) -> FlowSemantics:
    """Parse a complete Studio definition without changing its serialized payload."""
    try:
        payloads.validate_definition(flow)
    except CapabilityError as exc:
        _incomplete("flow", str(exc))
    if not isinstance(flow.get("id"), str) or not flow["id"]:
        _incomplete("flow.id", "Read the persisted flow identity.")
    _identity(flow["id"])
    if flow.get("type") not in {"flow", "subflow"}:
        _incomplete(
            "flow.type", "Local validation requires a flow or subflow definition."
        )
    if flow["type"] == "subflow" and flow.get("triggerInstances"):
        _incomplete(
            "flow.triggerInstances", "Subflow definitions cannot contain triggers."
        )
    prerequisites = {
        "active": (
            False,
            "Local validation requires the flow active field to be the boolean false. Trigger activation is separate.",
        ),
        "status": ("draft", "Local validation requires a saved draft status."),
        "isPublished": (
            False,
            "Read the boolean publication flag. Local validation requires an unpublished draft.",
        ),
        "snapshot": (
            False,
            "Read the boolean snapshot flag. Local validation does not accept snapshot definitions.",
        ),
        "masterSnapshot": (
            False,
            "Read the boolean master-snapshot flag. Local validation requires a root draft.",
        ),
        "masterSnapshotId": (
            "",
            "Read the master snapshot reference. This adapter requires an explicitly empty reference.",
        ),
        "flowEngineVersion": (
            2,
            "Read the engine version. Local validation requires V2.",
        ),
    }
    for key, (expected, invariant) in prerequisites.items():
        actual = flow.get(key)
        if actual != expected or (
            isinstance(expected, bool) and actual is not expected
        ):
            _incomplete(f"flow.{key}", invariant)
    for key in ("contentACLFilteredOrders", "nonCriticalErrors"):
        if flow.get(key):
            _incomplete(
                f"flow.{key}",
                "Studio reports filtered content or definition errors. Read a complete definition before validation.",
            )
    for key in ("deletedActions", "deletedFlowLogicInstances", "deletedTriggers"):
        tombstones = flow.get(key, [])
        authorized = (pending_deletions or {}).get(key, [])
        if tombstones and tombstones != authorized:
            _incomplete(
                f"flow.{key}",
                "The definition contains unapproved pending deletions. No deletion state is inferred.",
            )
    for key in (
        *COLLECTIONS,
        "triggerInstances",
        "inputs",
        "outputs",
        "flowVariables",
        "label_cache",
    ):
        if not isinstance(flow.get(key), list):
            _incomplete(
                f"flow.{key}",
                "Read the complete list. Missing or non-list collections cannot establish semantics.",
            )
    # Studio 27 returns keyed stage objects. Older local fixtures used arrays.
    if not isinstance(flow.get("stages"), list | dict):
        _incomplete(
            "flow.stages",
            "Read configured stages as an object or a legacy list. Missing stages are unknown.",
        )
    steps: list[SemanticStep] = []
    identities: set[str] = set()
    ui_ids: set[str] = set()
    orders: set[int] = set()
    for collection, kind in COLLECTIONS.items():
        for index, raw in enumerate(flow[collection]):
            path = f"flow.{collection}[{index}]"
            if not isinstance(raw, dict):
                _incomplete(path, "Each persisted step must be an object.")
            identity = raw.get("id", "")
            ui_id = raw.get("uiUniqueIdentifier", "")
            if not isinstance(identity, str):
                _incomplete(path + ".id", "Read the step identity as a string.")
            if not isinstance(ui_id, str) or not ui_id:
                _incomplete(
                    path + ".uiUniqueIdentifier", "Read a nonempty step UI identity."
                )
            if identity:
                _identity(identity, path + ".id")
            if identity and identity in identities:
                _incomplete(
                    path + ".id",
                    "Persisted step identities must be unique across all step collections.",
                )
            if ui_id in ui_ids:
                _incomplete(
                    path + ".uiUniqueIdentifier",
                    "Step UI identities must be unique across all step collections.",
                )
            identities.add(identity)
            ui_ids.add(ui_id)
            order_raw = raw.get("order")
            if isinstance(order_raw, bool) or not isinstance(order_raw, int | str):
                _incomplete(
                    path + ".order",
                    "Read a positive integer order or its numeric string representation. Booleans are invalid.",
                )
            try:
                order = int(order_raw)
            except ValueError:
                _incomplete(path + ".order", "The step order must parse as an integer.")
            top_try = (
                kind == "flowlogic"
                and raw.get("flowLogicDefinition", {}).get("type") == "TOP_LEVEL_TRY"
                and raw.get("parent") == ""
            )
            if order < (0 if top_try else 1) or order in orders:
                _incomplete(
                    path + ".order",
                    "Step orders must be positive and unique across all step collections.",
                )
            orders.add(order)
            parent = raw.get("parent")
            if not isinstance(parent, str):
                _incomplete(
                    path + ".parent",
                    "Read the parent UI identity as a string. Root steps require an explicitly empty string.",
                )
            definition_key = {
                "action": "actionTypeSysId",
                "flowlogic": "definitionId",
                "subflow": "subflowSysId",
            }[kind]
            definition = raw.get(definition_key)
            if not isinstance(definition, str) or not definition:
                _incomplete(
                    path + "." + definition_key,
                    "Read the persisted step definition reference.",
                )
            _identity(definition, path + "." + definition_key)
            if raw.get("type") != kind:
                _incomplete(
                    path + ".type",
                    "The step type must match its containing collection.",
                )
            nested_key = {
                "action": "actionType",
                "flowlogic": "flowLogicDefinition",
                "subflow": "subFlow",
            }[kind]
            nested = raw.get(nested_key)
            if nested_key in raw and not isinstance(nested, dict):
                _incomplete(
                    path + "." + nested_key,
                    "Nested definition metadata must be an object when present.",
                )
            if (
                isinstance(nested, dict)
                and nested.get("id")
                and nested["id"] != definition
            ):
                _incomplete(
                    path + "." + nested_key + ".id",
                    "The nested definition identity differs from the step definition reference.",
                )
            bindings = _bindings(raw.get("inputs"), path + ".inputs")
            logic_definition = raw.get("flowLogicDefinition")
            if "flowLogicDefinition" in raw and not isinstance(logic_definition, dict):
                _incomplete(
                    path + ".flowLogicDefinition",
                    "This adapter requires object metadata when the flow-logic definition field is present.",
                )
            if (
                isinstance(logic_definition, dict)
                and logic_definition.get("type") == "SETFLOWVARIABLES"
                and _json(raw.get("flowVariables")) != _json(raw["inputs"])
            ):
                _incomplete(
                    path + ".flowVariables",
                    "The variable mirror must match the stored input bindings.",
                )
            if (
                isinstance(logic_definition, dict)
                and logic_definition.get("type") == "ASSIGNSUBFLOWOUTPUTS"
            ):
                _bindings(raw.get("outputsToAssign"), path + ".outputsToAssign")
            if "data" in raw:
                values = raw.get("inputs")
                if not isinstance(values, list) or not isinstance(raw["data"], dict):
                    _incomplete(
                        path + ".data",
                        "The binding mirror must be an object with a complete input list.",
                    )
                mirror = {item["name"]: item["value"] for item in raw["inputs"]}
                if (
                    isinstance(logic_definition, dict)
                    and logic_definition.get("type") == "ASSIGNSUBFLOWOUTPUTS"
                ):
                    mirror.update(
                        {
                            item["name"]: data.output_assignment_mirror(item)
                            for item in raw["outputsToAssign"]
                        }
                    )
                if _json(raw["data"]) != _json(mirror):
                    _incomplete(
                        path + ".data",
                        "The binding mirror differs from the stored input values.",
                    )
            settings = {
                k: v
                for k, v in raw.items()
                if k
                not in {
                    "id",
                    "uiUniqueIdentifier",
                    "order",
                    "parent",
                    definition_key,
                    "inputs",
                }
                # GET reconstructs the UI family from the ordered parent tree.
                # Validate explicit caches below before excluding their presence.
                and k not in tree.UI_FIELDS | {"children"}
            }
            if kind == "subflow":
                wait = raw.get("waitForCompletion")
                if not isinstance(wait, dict) or not isinstance(
                    wait.get("value"), bool
                ):
                    _incomplete(
                        path + ".waitForCompletion.value",
                        "Read the subflow wait setting as a boolean inside its value object.",
                    )
            steps.append(
                SemanticStep(
                    kind,
                    identity,
                    ui_id,
                    order,
                    parent,
                    definition,
                    bindings,
                    _json(settings),
                ),
            )
    by_ui = {step.ui_id: step for step in steps if step.ui_id}
    for index, raw in enumerate(flow["flowLogicInstances"]):
        # docs/designer-bundle.min.js:92801 mapChildren accepts an absent cache.
        if "children" not in raw:
            continue
        children = raw.get("children")
        expected_children = [
            step.ui_id
            for step in sorted(steps, key=lambda s: s.order)
            if step.parent == raw["uiUniqueIdentifier"]
        ]
        if not isinstance(children, list) or children != expected_children:
            _incomplete(
                f"flow.flowLogicInstances[{index}].children",
                "Read the ordered child UI identities. They must match the persisted parent links exactly.",
            )
    paths = {
        raw["uiUniqueIdentifier"]: f"flow.{collection}[{index}]"
        for collection in COLLECTIONS
        for index, raw in enumerate(flow[collection])
    }
    for step in steps:
        seen = {step.ui_id}
        parent = step.parent
        while parent:
            if (
                parent in seen
                or parent not in by_ui
                or by_ui[parent].kind != "flowlogic"
            ):
                _incomplete(
                    paths[step.ui_id] + ".parent",
                    "The parent must reference existing flow logic without cycles. No parent is repaired or inferred.",
                )
            seen.add(parent)
            parent = by_ui[parent].parent
    # The client reconstructs this entire UI family after GET. Missing fields are
    # not missing topology, but explicit caches must agree before they are ignored.
    metadata = tree.ui_metadata(tree.build_tree(flow))
    if any(
        row.get("flowLogicDefinition", {}).get("type") == handlers.TRY
        for row in flow["flowLogicInstances"]
    ):
        handlers.validate(flow, tree.build_tree(flow))
    for collection in COLLECTIONS:
        for index, raw in enumerate(flow[collection]):
            expected = metadata[raw["uiUniqueIdentifier"]]
            for field in tree.UI_FIELDS | {"children"}:
                if field not in raw:
                    continue
                value = expected.get(field, [])
                if (
                    field in {"ancestorUiId", "ancestorType"}
                    and value is None
                    and raw[field] == ""
                ):
                    continue
                if _json(raw[field]) != _json(value):
                    _incomplete(
                        f"flow.{collection}[{index}].{field}",
                        "Explicit UI metadata must match the ordered parent tree. No contradictory cache is repaired.",
                    )
    triggers = []
    for index, trigger in enumerate(flow["triggerInstances"]):
        path = f"flow.triggerInstances[{index}]"
        if not isinstance(trigger, dict):
            _incomplete(path, "Each trigger must be an object.")
        if not isinstance(trigger.get("type"), str) or not trigger["type"]:
            _incomplete(path + ".type", "Read the stored trigger type.")
        if (
            not isinstance(trigger.get("triggerDefinitionId"), str)
            or not trigger["triggerDefinitionId"]
        ):
            _incomplete(
                path + ".triggerDefinitionId",
                "Read the persisted trigger definition reference.",
            )
        _identity(trigger["triggerDefinitionId"], path + ".triggerDefinitionId")
        if (
            trigger.get("label")
            and trigger.get("name")
            and trigger["label"] != trigger["name"]
        ):
            _incomplete(
                path + ".label",
                "Trigger label and name disagree. Do not infer the trigger pill prefix.",
            )
        name = trigger.get("label") or trigger.get("name")
        if not isinstance(name, str) or not name:
            _incomplete(
                path + ".name",
                "Read a nonempty trigger name or label for the pill prefix.",
            )
        triggers.append(
            {
                **{
                    k: v
                    for k, v in trigger.items()
                    if k not in {"name", "label", "inputs", "id"}
                },
                "name": name,
                "inputs": _bindings(trigger.get("inputs"), path + ".inputs"),
            },
        )
    # Unknown settings are opaque but must survive. Do not whitelist away a mismatch.
    settings = {
        k: v
        for k, v in flow.items()
        if k not in {*COLLECTIONS, "id", "triggerInstances"}
    }
    settings["label_cache"] = []
    for raw_entry in flow["label_cache"]:
        entry = raw_entry
        if isinstance(raw_entry, dict):
            # Studio maps absent cache attributes to null (92955, 92972, 101727).
            entry = {
                key: value
                for key, value in raw_entry.items()
                if key != "attributes" or value is not None
            }
            choices = entry.get("choices")
            if isinstance(choices, list) and all(
                isinstance(choice, dict) and "value" in choice for choice in choices
            ):
                # Cache consumers select by value, not position (bundle:96149-96154).
                # Keep every field and duplicate; only canonicalize list order.
                entry["choices"] = sorted(
                    choices, key=lambda choice: (_json(choice["value"]), _json(choice))
                )
        settings["label_cache"].append(entry)
    return FlowSemantics(
        flow["id"],
        tuple(sorted(steps, key=lambda s: s.order)),
        _json(triggers),
        _json(settings),
    )


def _updated_timestamp(flow: dict[str, Any], side: str) -> datetime | None:
    value = flow.get("updated", "")
    if value == "":
        return None
    if not isinstance(value, str):
        raise SemanticMismatch("flow.updated", "INVALID_SERVER_REVISION", side)
    try:
        if len(value) == 19:
            result = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if result.strftime("%Y-%m-%d %H:%M:%S") != value:
                raise ValueError
            return result
        if 1 <= len(value) <= 13 and value.isascii() and value.isdecimal():
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))
    except ValueError, OverflowError:
        pass
    raise SemanticMismatch("flow.updated", "INVALID_SERVER_REVISION", side)


def _compare_updated(
    expected: dict[str, Any], actual: dict[str, Any], *, allow_advance: bool
) -> None:
    # SaveIndicator reads flow.updated as the last-save UTC timestamp (bundle:9102,
    # 455477-455490). createMomentObjFromUTCDate accepts UTC text or epoch ms (108170).
    before = _updated_timestamp(expected, "expected")
    after = _updated_timestamp(actual, "actual")
    if before is not None and after is None:
        raise SemanticMismatch("flow.updated", "MISSING_SERVER_REVISION", "actual")
    if allow_advance:
        if before is not None and after is not None and after < before:
            raise SemanticMismatch(
                "flow.updated", "SERVER_REVISION_REGRESSION", "actual"
            )
    elif before != after:
        raise SemanticMismatch("flow.updated", "SERVER_REVISION_MISMATCH")


def _compare_component_mirror_ids(
    expected: dict[str, Any], actual: dict[str, Any], generated_ids: set[str]
) -> None:
    # The optional untrimmed mirror duplicates the three native collections
    # (bundle:99193-99197,130432-130460). Validate the complete duplicate first.
    # Normalize only new database IDs, exactly as for the authoritative lists.
    # Mirror-only metadata, existing IDs, UI IDs, and bindings stay strict.
    if not generated_ids:
        return
    new = {
        row["uiUniqueIdentifier"]: row
        for key in COLLECTIONS
        for row in expected[key]
        if row.get("id", "") in generated_ids
    }
    if not new:
        return

    def ordered(definition: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            (row for key in COLLECTIONS for row in definition[key]),
            key=lambda row: int(row["order"]),
        )

    if "componentInstances" in expected and _json(
        expected["componentInstances"]
    ) != _json(ordered(expected)):
        raise SemanticMismatch("flow.componentInstances", "MIRROR_MISMATCH", "expected")
    if "componentInstances" not in actual:
        return
    actual_rows = ordered(actual)
    by_ui = {row["uiUniqueIdentifier"]: row for row in actual_rows}
    mirror = actual["componentInstances"]
    if not isinstance(mirror, list) or any(not isinstance(row, dict) for row in mirror):
        raise SemanticMismatch("flow.componentInstances", "MIRROR_MISMATCH", "actual")
    # Untrimmed responses can retain the request's new-ID marker in the
    # duplicate. Only that marker or the authoritative assigned ID is allowed.
    for row in mirror:
        uid = row.get("uiUniqueIdentifier")
        if uid in new and row.get("id") not in (
            new[uid].get("id"),
            by_ui.get(uid, {}).get("id"),
        ):
            raise SemanticMismatch(
                "flow.componentInstances.id", "IDENTITY_MISMATCH", "actual"
            )
    comparable = copy.deepcopy(actual_rows)
    for row in [*mirror, *comparable]:
        if row.get("uiUniqueIdentifier") in new:
            wanted = new[row["uiUniqueIdentifier"]]
            if "id" in wanted:
                row["id"] = wanted["id"]
            else:
                row.pop("id", None)
    if _json(mirror) != _json(comparable):
        raise SemanticMismatch("flow.componentInstances", "MIRROR_MISMATCH", "actual")


def _field_ui_identity(row: dict[str, Any], path: str, side: str) -> str | None:
    copies = [row["uiUniqueId"]] if "uiUniqueId" in row else []
    for key in ("attributes", "fieldFacetMap"):
        metadata = row.get(key)
        if isinstance(metadata, dict) and "uiUniqueId" in metadata:
            copies.append(metadata["uiUniqueId"])
    if not copies:
        return None
    if any(not isinstance(value, str) or not value for value in copies):
        raise SemanticMismatch(path + ".uiUniqueId", "INVALID_UI_IDENTITY", side)
    if len(set(copies)) != 1:
        raise SemanticMismatch(path + ".uiUniqueId", "IDENTITY_MISMATCH", side)
    # Studio restores this UI cache from persisted metadata. Never generate an
    # identity, replace an explicit conflicting copy, or alter the storage copy.
    # appendPropertyToItem:22009-22035; getFlowVariables:232977-232978.
    uid = copies[0]
    assert isinstance(uid, str)
    row["uiUniqueId"] = uid
    return uid


def _variable_parameters(flow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for collection in ("flowLogicInstances", "componentInstances"):
        for index, step in enumerate(flow.get(collection, [])):
            if (
                not isinstance(step, dict)
                or step.get("flowLogicDefinition", {}).get("type") != "SETFLOWVARIABLES"
            ):
                continue
            for key in ("inputs", "flowVariables"):
                for position, row in enumerate(step.get(key, [])):
                    if isinstance(row, dict) and isinstance(row.get("parameter"), dict):
                        result.append(
                            (
                                f"flow.{collection}[{index}].{key}[{position}].parameter",
                                row["parameter"],
                            )
                        )
    return result


def _restore_field_ui_identities(flow: dict[str, Any], side: str) -> None:
    for section in ("inputs", "outputs", "flowVariables"):
        rows = flow.get(section)
        if not isinstance(rows, list):
            continue
        names, identities = set(), set()
        for index, (name, row) in enumerate(declarations.walk(rows)):
            path = f"flow.{section}[{index}]"
            uid = _field_ui_identity(row, path, side)
            if name in names or uid is not None and uid in identities:
                raise SemanticMismatch(path, "DUPLICATE_IDENTITY", side)
            names.add(name)
            if uid is not None:
                identities.add(uid)
    for path, parameter in _variable_parameters(flow):
        _field_ui_identity(parameter, path, side)


def _new_variable_parameter_id(
    expected: dict[str, Any], actual: dict[str, Any], name: str, uid: str, assigned: str
) -> None:
    # Set Flow Variables mirrors assignments in inputs (bundle:207311-207315).
    # The exact saved-flow identity projection proves that parameter.id refers
    # to its declaration. Only that owner's assigned ID can replace a new marker.
    for side, flow in (("expected", expected), ("actual", actual)):
        for path, parameter in _variable_parameters(flow):
            if parameter.get("uiUniqueId") != uid:
                continue
            if parameter.get("name") != name:
                raise SemanticMismatch(path + ".name", "IDENTITY_MISMATCH", side)
            if "id" not in parameter:
                continue
            identity = parameter["id"]
            if (
                side == "expected"
                and identity
                or side == "actual"
                and identity != assigned
            ):
                raise SemanticMismatch(path + ".id", "IDENTITY_MISMATCH", side)
            parameter.pop("id")


def _variable_type_labels(flow: dict[str, Any]) -> None:
    """Project the observed String display spellings in variable DTO slots only.

    Studio hydrates type_label from GLIDE_TYPES (232966-232973), copies it into
    uiTypeLabel in the picker (344082-344085), and renders that attribute as type
    text (491623-491638). Reference labels can select controls (164783), so this
    does not apply to other types, arbitrary labels, or unknown attributes.
    """
    rows = [
        *flow.get("flowVariables", []),
        *(row for _, row in _variable_parameters(flow)),
    ]
    for row in rows:
        attributes = row.get("attributes")
        if (
            row.get("type") != "string"
            or not isinstance(attributes, dict)
            or attributes.get("uiType") != "string"
        ):
            continue
        if row.get("type_label") in ("string", "String"):
            row["type_label"] = "string"
        if attributes.get("uiTypeLabel") in ("string", "String"):
            # Planning can share this dictionary with a label-cache entry.
            # Replacing the slot prevents changes outside the proven DTO path.
            row["attributes"] = {**attributes, "uiTypeLabel": "string"}


def compare_saved(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    generated_ids: set[str],
    pending_deletions: dict[str, list[dict[str, str]]] | None = None,
    allow_updated_advance: bool = False,
    omitted_subflows: list[dict[str, str]] | None = None,
    generated_stages: set[str] | None = None,
    generated_fields: list[dict[str, str]] | None = None,
    removed_fields: list[dict[str, str]] | None = None,
) -> None:
    """Allow server IDs only for explicitly new steps. Existing identities cannot change."""
    # Tombstones are request instructions, not persisted settings. Only planned IDs may disappear.
    expected = copy.deepcopy(expected)
    actual = copy.deepcopy(actual)
    _restore_field_ui_identities(expected, "expected")
    _restore_field_ui_identities(actual, "actual")
    _variable_type_labels(expected)
    _variable_type_labels(actual)
    for definition in (expected, actual):
        for step in [
            *definition.get("flowLogicInstances", []),
            *definition.get("componentInstances", []),
        ]:
            if (
                not isinstance(step, dict)
                or step.get("flowLogicDefinition", {}).get("type")
                != "ASSIGNSUBFLOWOUTPUTS"
            ):
                continue
            rows = step.get("outputsToAssign")
            _bindings(rows, "flow.flowLogicInstances.outputsToAssign")
            for row in rows:
                row["value"] = data.output_assignment_value(row)
    for removed in removed_fields or []:
        for side, definition in (("expected", expected), ("actual", actual)):
            for section in ("inputs", "outputs", "flowVariables"):
                for index, (_, row) in enumerate(
                    declarations.walk(definition.get(section, []))
                ):
                    if isinstance(row, dict) and any(
                        removed.get(key) and row.get(key) == removed[key]
                        for key in ("id", "uiUniqueId")
                    ):
                        raise SemanticMismatch(
                            f"flow.{section}[{index}]",
                            "DELETED_IDENTITY_RETAINED",
                            side,
                        )
    # New declarations carry client uiUniqueId in attributes or fieldFacetMap.
    # Database id is separate, including persisted Object children
    # (bundle:22032-22035,471965,472158,458913-458917).
    assigned_field_ids = {
        section: [
            row.get("id")
            for _, row in declarations.walk(actual.get(section, []))
            if isinstance(row, dict) and row.get("id")
        ]
        for section in ("inputs", "outputs", "flowVariables")
        if isinstance(actual.get(section), list)
    }
    for manifest in generated_fields or []:
        section, name, uid = (
            manifest[key] for key in ("collection", "name", "uiUniqueId")
        )
        if section not in {"inputs", "outputs", "flowVariables"}:
            raise SemanticMismatch("flow", "INVALID_NEW_FIELD_MANIFEST")
        wanted_rows, actual_rows = expected.get(section), actual.get(section)
        if not isinstance(wanted_rows, list) or not isinstance(actual_rows, list):
            raise SemanticMismatch(f"flow.{section}", "INCOMPLETE_SEMANTICS")
        wanted_field = [
            row
            for path, row in declarations.walk(wanted_rows)
            if isinstance(row, dict) and path == name and row.get("uiUniqueId") == uid
        ]
        found_field = [
            row
            for path, row in declarations.walk(actual_rows)
            if isinstance(row, dict) and path == name and row.get("uiUniqueId") == uid
        ]
        if len(wanted_field) != 1 or wanted_field[0].get("id") or len(found_field) != 1:
            raise SemanticMismatch(f"flow.{section}", "NEW_FIELD_IDENTITY_MISMATCH")
        if "id" in found_field[0]:
            identity = found_field[0]["id"]
            if not isinstance(identity, str):
                raise SemanticMismatch(
                    f"flow.{section}.id", "INVALID_SERVER_IDENTITY", "actual"
                )
            _identity(identity, f"flow.{section}.id")
            if assigned_field_ids[section].count(identity) != 1:
                raise SemanticMismatch(
                    f"flow.{section}.id", "DUPLICATE_IDENTITY", "actual"
                )
            if section == "flowVariables":
                _new_variable_parameter_id(expected, actual, name, uid, identity)
            found_field[0].pop("id")
    assigned_stage_ids = (
        [
            row.get("id")
            for row in actual.get("stages", {}).values()
            if isinstance(row, dict) and row.get("id")
        ]
        if generated_stages and isinstance(actual.get("stages"), dict)
        else []
    )
    # createStage supplies a stable stageId. Only new stages can receive a
    # separate database id (bundle:204158-204167,476178-476187).
    for key in generated_stages or set():
        wanted_stage = expected.get("stages", {}).get(key)
        found_stage = actual.get("stages", {}).get(key)
        if not isinstance(wanted_stage, dict) or wanted_stage.get("id"):
            raise SemanticMismatch("flow.stages", "INVALID_NEW_STAGE_MANIFEST")
        if isinstance(found_stage, dict) and "id" in found_stage:
            if not isinstance(found_stage["id"], str):
                raise SemanticMismatch(
                    "flow.stages.id", "INVALID_SERVER_IDENTITY", "actual"
                )
            _identity(found_stage["id"], "flow.stages.id")
            if assigned_stage_ids.count(found_stage["id"]) != 1:
                raise SemanticMismatch("flow.stages.id", "DUPLICATE_IDENTITY", "actual")
            found_stage.pop("id")
    removed_ids: set[str] = set()
    removed_ui: set[str] = set()
    for index, removed in enumerate(omitted_subflows or []):
        path = f"flow.subFlowInstances.removed[{index}]"
        if not isinstance(removed, dict) or set(removed) != {
            "id",
            "uiUniqueIdentifier",
        }:
            raise SemanticMismatch(path, "INVALID_DELETION_MANIFEST")
        _identity(removed["id"], path + ".id")
        uid = removed["uiUniqueIdentifier"]
        if (
            not isinstance(uid, str)
            or not uid
            or uid in removed_ui
            or removed["id"] in removed_ids
        ):
            raise SemanticMismatch(path, "INVALID_DELETION_MANIFEST")
        removed_ids.add(removed["id"])
        removed_ui.add(uid)
    for side, definition in (("expected", expected), ("actual", actual)):
        for collection in COLLECTIONS:
            for index, step in enumerate(definition.get(collection, [])):
                if isinstance(step, dict) and (
                    step.get("id") in removed_ids
                    or step.get("uiUniqueIdentifier") in removed_ui
                ):
                    raise SemanticMismatch(
                        f"flow.{collection}[{index}]", "DELETED_IDENTITY_RETAINED", side
                    )
    _compare_updated(expected, actual, allow_advance=allow_updated_advance)
    if allow_updated_advance:
        # Only request-to-postsave comparisons may advance. Never remove this
        # field from preview fingerprints or echo-to-independent-read equality.
        expected.pop("updated", None)
        actual.pop("updated", None)
    for key, rows in (pending_deletions or {}).items():
        if expected.get(key, []) not in ([], rows) or actual.get(key, []) not in (
            [],
            rows,
        ):
            raise SemanticMismatch("flow.deletions", "UNAPPROVED_DELETIONS")
        for row in rows:
            collections = (
                ["triggerInstances"] if key == "deletedTriggers" else list(COLLECTIONS)
            )
            if any(
                step.get("id") == row["id"]
                for collection in collections
                for step in actual[collection]
            ):
                raise SemanticMismatch("flow.deletions", "DELETED_IDENTITY_RETAINED")
        expected.pop(key, None)
        actual.pop(key, None)
    _compare_component_mirror_ids(expected, actual, generated_ids)
    parsed = []
    for side, definition in (("expected", expected), ("actual", actual)):
        try:
            parsed.append(studio_semantics(definition))
        except IncompleteSemantics as exc:
            raise SemanticMismatch(
                exc.path, "INCOMPLETE_SEMANTICS", side, str(exc)
            ) from None
    wanted, found = parsed
    if (
        wanted.identity != found.identity
        or wanted.triggers != found.triggers
        or wanted.settings != found.settings
    ):
        raise _semantic_difference(wanted, found, expected)
    normalized = copy.deepcopy(actual)
    expected_triggers = expected["triggerInstances"]
    actual_triggers = actual["triggerInstances"]
    if len(expected_triggers) != len(actual_triggers):
        raise SemanticMismatch(
            "flow.triggerInstances",
            "COUNT_MISMATCH",
            expected_count=len(expected_triggers),
            actual_count=len(actual_triggers),
        )
    for index, (wanted_trigger, found_trigger) in enumerate(
        zip(expected_triggers, actual_triggers, strict=True)
    ):
        if wanted_trigger.get("id") and wanted_trigger.get("id") != found_trigger.get(
            "id"
        ):
            raise SemanticMismatch(
                f"flow.triggerInstances[{index}].id", "IDENTITY_MISMATCH"
            )
        if found_trigger.get("id"):
            _identity(found_trigger["id"], f"flow.triggerInstances[{index}].id")
    for collection in COLLECTIONS:
        expected_rows = expected[collection]
        actual_rows = normalized[collection]
        if len(expected_rows) != len(actual_rows):
            raise SemanticMismatch(
                f"flow.{collection}",
                "COUNT_MISMATCH",
                expected_count=len(expected_rows),
                actual_count=len(actual_rows),
            )
        for index, row in enumerate(actual_rows):
            _identity(row.get("id", ""), f"flow.{collection}[{index}].id")
        by_ui = {
            row.get("uiUniqueIdentifier"): row
            for row in actual_rows
            if row.get("uiUniqueIdentifier")
        }
        for index, row in enumerate(expected_rows):
            if row.get("id", "") not in generated_ids:
                continue
            saved = by_ui.get(row.get("uiUniqueIdentifier"))
            if saved is None:
                raise SemanticMismatch(
                    f"flow.{collection}[{index}].uiUniqueIdentifier",
                    "IDENTITY_MISMATCH",
                )
            _identity(saved["id"], f"flow.{collection}[{index}].id")
            saved["id"] = row.get("id", "")
    normalized_semantics = studio_semantics(normalized)
    if normalized_semantics != wanted:
        raise _semantic_difference(wanted, normalized_semantics, expected)


async def _bind_new_variable_blocks(
    client: ServiceNowClient,
    expected: dict[str, Any],
    saved: dict[str, Any],
    generated_ids: set[str],
) -> dict[str, Any]:
    """Bind only server-created assignment blocks to independently read owners.

    New Studio instances omit flowBlockId (206651-206672,221783-221799).
    The observed dictionary maps logic-instance block to sys_hub_flow_block and
    inherited flow to sys_hub_flow_base. Existing block references remain strict.
    """
    bound = copy.deepcopy(expected)
    known_blocks = {
        row["flowBlockId"]
        for row in expected["flowLogicInstances"]
        if row.get("flowBlockId")
    }
    for index, row in enumerate(expected["flowLogicInstances"]):
        if (
            row.get("id", "") not in generated_ids
            or row.get("id")
            or "flowBlockId" in row
            or row.get("flowLogicDefinition", {}).get("type") != "SETFLOWVARIABLES"
        ):
            continue
        matches = [
            s
            for s in saved["flowLogicInstances"]
            if s.get("uiUniqueIdentifier") == row["uiUniqueIdentifier"]
        ]
        if len(matches) != 1 or "flowBlockId" not in matches[0]:
            continue  # The complete comparator diagnoses missing or duplicate steps.
        actual = matches[0]
        path = f"flow.flowLogicInstances[{index}].flowBlockId"
        identity, block = actual.get("id"), actual["flowBlockId"]
        _identity(identity, path + ".instance")
        _identity(block, path)
        if (
            block in known_blocks
            or actual.get("type") != "flowlogic"
            or actual.get("flowSysId") != expected["id"]
            or actual.get("definitionId") != row["definitionId"]
            or actual.get("flowLogicDefinition", {}).get("type") != "SETFLOWVARIABLES"
        ):
            raise SemanticMismatch(path, "BLOCK_OWNERSHIP_MISMATCH", "actual")
        check_table_access("sys_hub_flow_logic_instance_v2")
        record = await client.get_record(
            "sys_hub_flow_logic_instance_v2",
            identity,
            fields=[
                "sys_id",
                "flow",
                "ui_id",
                "sys_class_name",
                "logic_definition",
                "block",
                "block.sys_id",
            ],
        )
        wanted = {
            "sys_id": identity,
            "flow": expected["id"],
            "ui_id": row["uiUniqueIdentifier"],
            "sys_class_name": "sys_hub_flow_logic_instance_v2",
            "logic_definition": row["definitionId"],
            "block": block,
            "block.sys_id": block,
        }
        for key, value in wanted.items():
            observed = record.get(key)
            if isinstance(observed, dict):
                observed = observed.get("value")
            if observed != value:
                raise SemanticMismatch(path, "BLOCK_OWNERSHIP_MISMATCH", "actual")
        known_blocks.add(block)
        for collection in ("flowLogicInstances", "componentInstances"):
            for target in bound.get(collection, []):
                if target.get("uiUniqueIdentifier") == row["uiUniqueIdentifier"]:
                    target["flowBlockId"] = block
    return bound


async def verify_saved(
    client: ServiceNowClient,
    expected: dict[str, Any],
    echo: dict[str, Any],
    *,
    generated_ids: set[str],
    pending_deletions: dict[str, list[dict[str, str]]] | None = None,
    omitted_subflows: list[dict[str, str]] | None = None,
    generated_stages: set[str] | None = None,
    generated_fields: list[dict[str, str]] | None = None,
    removed_fields: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Verify the echo and a separate GET. Never retry a write or expose input values in errors."""
    phase = "request.echo"
    try:
        bound = await _bind_new_variable_blocks(client, expected, echo, generated_ids)
        compare_saved(
            bound,
            echo,
            generated_ids=generated_ids,
            pending_deletions=pending_deletions,
            allow_updated_advance=True,
            omitted_subflows=omitted_subflows,
            generated_stages=generated_stages,
            generated_fields=generated_fields,
            removed_fields=removed_fields,
        )
        phase = "readback.get"
        actual = await client.studio_request(f"flow/{expected['id']}")
        phase = "request.readback"
        bound = await _bind_new_variable_blocks(client, expected, actual, generated_ids)
        compare_saved(
            bound,
            actual,
            generated_ids=generated_ids,
            pending_deletions=pending_deletions,
            allow_updated_advance=True,
            omitted_subflows=omitted_subflows,
            generated_stages=generated_stages,
            generated_fields=generated_fields,
            removed_fields=removed_fields,
        )
        # Assigned IDs must also remain stable between the save response and independent read.
        phase = "echo.readback"
        compare_saved(
            echo,
            actual,
            generated_ids=set(),
            pending_deletions=pending_deletions,
            omitted_subflows=omitted_subflows,
        )
        return actual
    except SemanticMismatch as exc:
        detail = exc.detail()
    except IncompleteSemantics as exc:
        detail = f"{exc.path} INCOMPLETE_SEMANTICS. {exc}"
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        detail = (
            "flow UNAVAILABLE_READBACK"
            if phase == "readback.get"
            else "flow COMPARISON_UNAVAILABLE"
        )
    raise CapabilityError(
        f"Independent saved-flow verification failed ({phase}, {detail}). Inspect the flow. Do not replay the write.",
        "UNKNOWN_OUTCOME",
    ) from None


async def verify_created(
    client: ServiceNowClient, properties: dict[str, Any], echo: dict[str, Any]
) -> dict[str, Any]:
    """Read a newly created draft independently and verify the requested properties."""
    try:
        validate_sys_id(echo["id"])
        if properties.get("type") == "subflow" and any(
            key not in echo or _json(value) != _json(echo[key])
            for key, value in properties.items()
        ):
            _incomplete()
        actual = await client.studio_request(f"flow/{echo['id']}")
        parsed = studio_semantics(actual)
        if parsed.identity != echo["id"] or parsed.steps or actual["triggerInstances"]:
            _incomplete()
        if any(
            key not in actual or _json(value) != _json(actual[key])
            for key, value in properties.items()
        ):
            _incomplete()
        return actual
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        raise CapabilityError(
            "Independent created-flow verification is incomplete. Inspect the target before another preview.",
            "UNKNOWN_OUTCOME",
        ) from None


async def save_draft(
    client: ServiceNowClient, payload: dict[str, Any], *, create: bool = False
) -> dict[str, Any]:
    """A failed effect response cannot establish whether the save happened. Never retry here."""
    try:
        return await client.studio_request("flow", data=payload, create=create)
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        raise CapabilityError(
            "The draft save did not return a completed result. UNKNOWN_OUTCOME: inspect the flow "
            "before another preview. Do not replay the write.",
            "UNKNOWN_OUTCOME",
        ) from None
