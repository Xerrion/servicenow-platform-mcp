"""Read-only lifecycle evidence. Local validation does not compile, save, or execute a flow."""

import copy
import json
import re
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.flows import (
    capture,
    data,
    editing,
    semantics,
    support,
    tree,
)
from servicenow_mcp.capabilities.flows.capture import scalar
from servicenow_mcp.capabilities.mutations.models import fingerprint
from servicenow_mcp.policy import check_table_access, is_sensitive_field
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


HEADER_FIELDS = [
    "sys_id",
    "type",
    "sys_scope",
    "active",
    "status",
    "master_snapshot",
    "latest_snapshot",
    "sys_updated_on",
    "sys_mod_count",
    "sys_update_name",
]
EXECUTION_FIELDS = ["sys_id", "flow", "state", "sys_created_on", "sys_updated_on"]
EXECUTION_STATES = {
    "ERROR",
    "CONTINUE_SYNC",
    "PAUSED",
    "CANCELLED",
    "WAITING",
    "IN_PROGRESS",
    "PRESUMED_INTERRUPTED",
    "COMPLETE",
    "QUEUED",
    "PAUSED_IN_DEBUG",
}


def diagnostic(
    code: str, path: str, message: str, *, incomplete: bool = False
) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message, "incomplete": incomplete}


def check_bindings(items: Any, parameters: Any, path: str) -> list[dict[str, Any]]:
    """Validate actual values against declared parameters without rebuilding the saved structure."""
    diagnostics: list[dict[str, Any]] = []
    if (
        not isinstance(items, list)
        or not isinstance(parameters, list)
        or any(
            not isinstance(p, dict) or not isinstance(p.get("name"), str)
            for p in parameters
        )
    ):
        return [
            diagnostic(
                "INCOMPLETE_SCHEMA",
                path,
                "Read the complete declared input schema.",
                incomplete=True,
            ),
        ]
    names = [i.get("name") for i in items if isinstance(i, dict)]
    if len(names) != len(items) or len(set(names)) != len(names):
        return [
            diagnostic(
                "DUPLICATE_BINDING",
                path,
                "Input names must be present and unique.",
                incomplete=True,
            ),
        ]
    by_name = {i["name"]: i for i in items}
    parameter_names = [p["name"] for p in parameters]
    if len(set(parameter_names)) != len(parameter_names):
        return [
            diagnostic(
                "AMBIGUOUS_SCHEMA",
                path,
                "Declared input names must be unique.",
                incomplete=True,
            ),
        ]
    if set(names) - set(parameter_names):
        diagnostics.append(
            diagnostic(
                "UNDECLARED_BINDING",
                path,
                "An input has no matching declared parameter.",
                incomplete=True,
            ),
        )
    for parameter in parameters:
        item = by_name.get(parameter["name"])
        if item is None:
            diagnostics.append(
                diagnostic(
                    "MISSING_BINDING",
                    path,
                    "A declared input binding is absent. Read the complete saved inputs.",
                    incomplete=True,
                ),
            )
            continue
        if item.get("scriptActive"):
            diagnostics.append(
                diagnostic(
                    "UNVERIFIED_SCRIPT",
                    path,
                    "Local validation cannot establish script behavior.",
                    incomplete=True,
                ),
            )
            continue
        try:
            expected_type = data.value_type(parameter)
            if data.value_type(item) != expected_type:
                diagnostics.append(
                    diagnostic(
                        "SCHEMA_TYPE_MISMATCH",
                        path,
                        "The saved binding type differs from its declared parameter.",
                    ),
                )
                continue
            if item.get("children"):
                declared_children = data.children(parameter)
                if expected_type.kind == "object":
                    diagnostics.extend(
                        check_bindings(
                            item["children"], declared_children, path + ".children"
                        )
                    )
                elif (
                    expected_type.kind.startswith("array.")
                    and len(declared_children) == 1
                ):
                    for child in item["children"]:
                        diagnostics.extend(
                            check_bindings(
                                [child], declared_children, path + ".children"
                            )
                        )
                else:
                    diagnostics.append(
                        diagnostic(
                            "UNAVAILABLE_COMPLEX_SCHEMA",
                            path,
                            "The complex child layout is not supported by this validator.",
                            incomplete=True,
                        ),
                    )
            else:
                candidate = copy.deepcopy(item)
                candidate["parameter"] = copy.deepcopy(parameter)
                # Read-only is an authoring restriction, not a semantic defect in persisted values.
                candidate["parameter"].pop("readonly", None)
                candidate["parameter"].pop("readOnly", None)
                data.set_value(candidate, item.get("value"))
        except CapabilityError as exc:
            diagnostics.append(
                diagnostic(
                    "UNAVAILABLE_VALUE"
                    if exc.code == "UNAVAILABLE"
                    else "INVALID_VALUE",
                    path,
                    "Check the mandatory value, declared type, choices, length, and complex child schema.",
                    incomplete=exc.code == "UNAVAILABLE",
                ),
            )
        except ValueError, TypeError, KeyError:
            diagnostics.append(
                diagnostic(
                    "MALFORMED_VALUE",
                    path,
                    "The saved input does not match its declared schema.",
                    incomplete=True,
                ),
            )
    return diagnostics


class IncompleteSchema(ValueError):
    def __init__(self, path: str) -> None:
        super().__init__("Incomplete schema metadata")
        self.path = path


def schema_field_path(path: str, name: str, index: int) -> str:
    # Only schema identifiers reach diagnostics. Labels, values and unsafe names never do.
    safe = re.fullmatch(r"[a-z_][a-z0-9_]{0,63}", name) and not is_sensitive_field(name)
    return f"{path}[{name if safe else index}]"


def schema_choices(choices: Any, path: str) -> list[str] | None:
    """Compare typed choice contracts, not translated labels or presentation order."""
    if choices is None:
        return None
    if not isinstance(choices, list):
        raise IncompleteSchema(path)
    result = []
    for choice in choices:
        if not isinstance(choice, dict) or "value" not in choice:
            raise IncompleteSchema(path)
        # Studio separates label/value and sorts order for display (331055-331130).
        # Keep all other keys, including disabled, dependencies and unknown metadata.
        contract = {
            key: value for key, value in choice.items() if key not in {"label", "order"}
        }
        try:
            result.append(json.dumps(contract, sort_keys=True, allow_nan=False))
        except ValueError, TypeError:
            raise IncompleteSchema(path) from None
    if len(set(result)) != len(result):
        raise IncompleteSchema(path)
    return sorted(result)


def schema_types(
    fields: Any, *, path: str = "schema", named_paths: bool = True
) -> dict[str, Any]:
    if not isinstance(fields, list):
        raise IncompleteSchema(path)
    result: dict[str, Any] = {}
    for index, field in enumerate(fields):
        if (
            not isinstance(field, dict)
            or not isinstance(field.get("name"), str)
            or field["name"] in result
        ):
            raise IncompleteSchema(f"{path}[{index}].name")
        field_path = schema_field_path(
            path, field["name"] if named_paths else "", index
        )
        # Studio wraps parameters and can copy their fields to the UI value.
        # Read constraints from the same parameter that owns type/reference.
        parameter = data.spec(field)
        try:
            kind = data.value_type(field)
        except CapabilityError:
            raise IncompleteSchema(field_path + ".type") from None
        try:
            children = data.children(field)
        except CapabilityError:
            raise IncompleteSchema(field_path + ".children") from None
        result[field["name"]] = {
            "type": kind.kind,
            "reference": kind.reference,
            "children": schema_types(
                children, path=field_path + ".children", named_paths=named_paths
            ),
            "mandatory": parameter.get("mandatory"),
            "choices": schema_choices(
                parameter.get("choices"), field_path + ".choices"
            ),
            "maxsize": parameter.get("maxsize"),
            "readonly": parameter.get("readonly", parameter.get("readOnly")),
        }
    return result


def schema_difference(saved: Any, current: Any) -> dict[str, str] | None:
    """Locate the first strict signature difference without returning metadata values."""

    def compare(
        left: dict[str, Any], right: dict[str, Any], path: str
    ) -> dict[str, str] | None:
        for index, name in enumerate(sorted(left.keys() | right.keys())):
            # Use only names declared by the current dependency. Saved-only fields use an index.
            field_path = schema_field_path(path, name if name in right else "", index)
            if name not in left or name not in right:
                return {
                    "side": "saved" if name not in left else "current",
                    "path": field_path,
                    "kind": "MISSING_FIELD",
                }
            for prop, value in left[name].items():
                other = right[name][prop]
                if value == other:
                    continue
                if prop == "children":
                    return compare(value, other, field_path + ".children")
                side = "both"
                if value is None or other is None:
                    side = "saved" if value is None else "current"
                    kind = "METADATA_PRESENCE_MISMATCH"
                else:
                    kind = (
                        "REPRESENTATION_MISMATCH"
                        if type(value) is not type(other)
                        else "VALUE_MISMATCH"
                    )
                    if prop == "choices":
                        values = sorted(
                            json.dumps(json.loads(choice)["value"], sort_keys=True)
                            for choice in value
                        )
                        other_values = sorted(
                            json.dumps(json.loads(choice)["value"], sort_keys=True)
                            for choice in other
                        )
                        kind = (
                            "CHOICE_VALUE_SET_MISMATCH"
                            if values != other_values
                            else "CHOICE_METADATA_MISMATCH"
                        )
                return {"side": side, "path": field_path + "." + prop, "kind": kind}
        return None

    for collection in ("inputs", "outputs"):
        signatures = {}
        for side, definition in (("current", current), ("saved", saved)):
            try:
                fields = (
                    definition.get(collection) if isinstance(definition, dict) else None
                )
                signatures[side] = schema_types(
                    fields, path=collection, named_paths=side == "current"
                )
            except IncompleteSchema as exc:
                return {"side": side, "path": exc.path, "kind": "INCOMPLETE_SCHEMA"}
        mismatch = compare(signatures["saved"], signatures["current"], collection)
        if mismatch:
            return mismatch
    return None


async def validate_definition(
    flow: dict[str, Any], client: ServiceNowClient
) -> dict[str, Any]:
    """Check the saved representation before edits can renumber or repair it."""
    diagnostics: list[dict[str, Any]] = []
    dependency_evidence: dict[str, Any] = {}
    definition_cache: dict[str, dict[str, Any]] = {}
    try:
        semantics.studio_semantics(flow)
        roots = tree.build_tree(flow)
    except semantics.IncompleteSemantics as exc:
        return {
            "valid": None,
            "complete": False,
            "diagnostics": [
                diagnostic("INCOMPLETE_SEMANTICS", exc.path, str(exc), incomplete=True)
            ],
        }
    except CapabilityError, ValueError, TypeError, KeyError:
        return {
            "valid": None,
            "complete": False,
            "diagnostics": [
                diagnostic(
                    "INCOMPLETE_SEMANTICS",
                    "flow",
                    "Read a complete inactive V2 draft with unique identities, orders, and valid parent links.",
                    incomplete=True,
                ),
            ],
        }
    try:
        tree.validate_structure(roots)
    except CapabilityError:
        diagnostics.append(
            diagnostic(
                "INVALID_TOPOLOGY",
                "flowLogicInstances",
                "Check branch adjacency, Try/Catch pairing, parallel paths, loop-control placement, and terminal "
                "siblings.",
            ),
        )
    if flow.get("startingIndexOfErrorHandlingInstances", -1) != -1:
        diagnostics.append(
            diagnostic(
                "UNAVAILABLE_HANDLER",
                "flowLogicInstances",
                "Top-level handler wrapper validation is unavailable.",
                incomplete=True,
            ),
        )
    for collection in ("inputs", "outputs", "flowVariables"):
        try:
            schema_types(flow[collection])
        except ValueError, TypeError, CapabilityError:
            diagnostics.append(
                diagnostic(
                    "INCOMPLETE_INTERFACE",
                    collection,
                    "Declared interface/variable names, types, or child schemas are incomplete.",
                    incomplete=True,
                ),
            )
    try:
        stages = flow["stages"]
        count = len(list(tree.walk(roots)))
        if stages and not isinstance(stages, dict):
            raise ValueError("Legacy stages")
        for stage in stages.values() if isinstance(stages, dict) else []:
            if (
                not isinstance(stage, dict)
                or not isinstance(stage.get("componentIndexes"), list)
                or any(
                    not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= count
                    for i in stage["componentIndexes"]
                )
            ):
                raise ValueError("Stage attachment")
    except ValueError, TypeError:
        diagnostics.append(
            diagnostic(
                "UNAVAILABLE_STAGE_ATTACHMENTS",
                "stages",
                "Stage attachment indexes are incomplete or outside the saved topology.",
                incomplete=True,
            ),
        )
    triggers = flow["triggerInstances"]
    if len(triggers) != 1:
        diagnostics.append(
            diagnostic(
                "TRIGGER_COUNT",
                "triggerInstances",
                "A flow requires exactly one trigger.",
            ),
        )
    for trigger in triggers:
        if trigger.get("type") != "service_catalog":
            diagnostics.append(
                diagnostic(
                    "UNAVAILABLE_TRIGGER",
                    "triggerInstances",
                    "This trigger family requires a dedicated validation adapter.",
                    incomplete=True,
                ),
            )
        if any(
            tree.pills(i.get("value")) for i in data.bindings(trigger.get("inputs", []))
        ):
            diagnostics.append(
                diagnostic(
                    "TRIGGER_REFERENCE",
                    "triggerInstances.inputs",
                    "Trigger inputs cannot depend on flow outputs.",
                ),
            )
        if trigger.get("type") == "service_catalog":
            try:
                catalog = editing.trigger_catalog(
                    await client.studio_definitions("triggers")
                )
                definition = catalog.get("service_catalog")
                if not definition or definition.get("id") != trigger.get(
                    "triggerDefinitionId"
                ):
                    raise ValueError("Definition mismatch")
                parameters = [spec["parameter"] for spec in definition["inputs"]]
                dependency_evidence["trigger"] = definition
                if schema_types(trigger.get("outputs")) != schema_types(
                    definition.get("outputs")
                ):
                    diagnostics.append(
                        diagnostic(
                            "TRIGGER_SCHEMA_DRIFT",
                            "triggerInstances.outputs",
                            "Saved trigger output types differ from the instance definition.",
                            incomplete=True,
                        ),
                    )
                diagnostics.extend(
                    check_bindings(
                        trigger["inputs"], parameters, "triggerInstances.inputs"
                    )
                )
            except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
                diagnostics.append(
                    diagnostic(
                        "UNREADABLE_TRIGGER_SCHEMA",
                        "triggerInstances.inputs",
                        "The current trigger parameter schema could not be established.",
                        incomplete=True,
                    ),
                )
    resolver = editing.PillResolver(
        client, triggers[0] if len(triggers) == 1 else None, flow, roots
    )
    usage = tree.used_pills(roots)
    if len(usage) > 100:
        diagnostics.append(
            diagnostic(
                "REFERENCE_LIMIT",
                "flow",
                "Validation supports at most 100 distinct pills.",
                incomplete=True,
            ),
        )
        return {"valid": None, "complete": False, "diagnostics": diagnostics}
    for step in tree.walk(roots):
        path = f"{tree.COLLECTIONS[step.kind]}[{step.uid}]"
        if step.kind == "flowlogic" and step.logic_type not in support.LOGIC_TYPES | {
            "CATCH",
            "SETFLOWVARIABLES",
        }:
            diagnostics.append(
                diagnostic(
                    "UNAVAILABLE_FAMILY",
                    path,
                    "This flow-logic family requires a dedicated validation adapter.",
                    incomplete=True,
                ),
            )
        definition = step.raw.get(
            {
                "action": "actionType",
                "subflow": "subFlow",
                "flowlogic": "flowLogicDefinition",
            }[step.kind],
        )
        dependency_reason = "POLICY_READ_FAILED"
        schema_mismatch = None
        try:
            if step.kind == "action":
                check_table_access("sys_hub_action_type_definition")
                key = "action/" + step.raw["actionTypeSysId"]
                dependency_reason = "DEFINITION_READ_FAILED"
                if key not in definition_cache:
                    if len(definition_cache) >= 100:
                        raise ValueError("Definition limit")
                    definition_cache[key] = await client.studio_request(
                        "action/action_types/" + step.raw["actionTypeSysId"],
                    )
                current = definition_cache[key]
                dependency_reason = "IDENTITY_MISMATCH"
                if current.get("id") != step.raw["actionTypeSysId"]:
                    raise ValueError("Definition identity")
                dependency_reason = "READ_ACCESS_UNVERIFIED"
                if current.get("security", {}).get("can_read") is not True:
                    raise ValueError("Definition access")
                dependency_reason = "ENTITLEMENT_UNVERIFIED"
                if current.get("entitled") is not True:
                    raise ValueError("Definition entitlement")
                dependency_reason = "PUBLICATION_UNVERIFIED"
                if (
                    current.get("state") != "published"
                    or current.get("is_snapshot") is not True
                ):
                    raise ValueError("Unreadable definition")
                dependency_reason = "SCHEMA_MISMATCH"
                schema_mismatch = schema_difference(definition, current)
                if schema_mismatch:
                    raise ValueError("Schema drift")
                dependency_evidence[key] = current
            elif step.kind == "subflow":
                check_table_access("sys_hub_flow")
                key = "subflow/" + step.raw["subflowSysId"]
                dependency_reason = "DEFINITION_READ_FAILED"
                if key not in definition_cache:
                    if len(definition_cache) >= 100:
                        raise ValueError("Definition limit")
                    definition_cache[key] = await client.studio_request(
                        "flow/" + step.raw["subflowSysId"],
                        minimal=True,
                    )
                current = definition_cache[key]
                dependency_reason = "IDENTITY_MISMATCH"
                if (
                    current.get("id") != step.raw["subflowSysId"]
                    or current.get("type") != "subflow"
                ):
                    raise ValueError("Definition identity")
                dependency_reason = "READ_ACCESS_UNVERIFIED"
                if current.get("security", {}).get("can_read") is not True:
                    raise ValueError("Definition access")
                dependency_reason = "ENTITLEMENT_UNVERIFIED"
                if current.get("isFlowEntitled") is not True:
                    raise ValueError("Definition entitlement")
                dependency_reason = "PUBLICATION_UNVERIFIED"
                if (
                    current.get("snapshot") is not True
                    or current.get("masterSnapshot") is not True
                ):
                    raise ValueError("Unreadable definition")
                dependency_reason = "SCHEMA_MISMATCH"
                schema_mismatch = schema_difference(definition, current)
                if schema_mismatch:
                    raise ValueError("Schema drift")
                dependency_evidence[key] = current
            else:
                if "logic" not in dependency_evidence:
                    check_table_access("sys_hub_flow_logic_definition")
                    dependency_reason = "DEFINITION_READ_FAILED"
                    dependency_evidence["logic"] = await client.studio_definitions(
                        "flow_logic"
                    )
                dependency_reason = "LOGIC_DEFINITION_MISMATCH"
                current = next(
                    (
                        d
                        for d in dependency_evidence["logic"]
                        if d.get("id") == step.raw["definitionId"]
                    ),
                    None,
                )
                if (
                    not current
                    or not isinstance(definition, dict)
                    or current.get("type") != step.logic_type
                    or current.get("userCanRead") is not True
                    or schema_types(current.get("inputs"))
                    != schema_types(definition.get("inputs"))
                ):
                    raise ValueError("Logic definition drift")
        except Exception as exc:  # noqa: BLE001 — preserve safe fallback behavior for unexpected failures
            status = getattr(exc, "status_code", None)
            http_status = (
                status if type(status) is int and 400 <= status <= 599 else None
            )
            detail = dependency_reason + (
                f", HTTP {http_status}" if http_status is not None else ""
            )
            if schema_mismatch:
                detail += f", {schema_mismatch['side']}:{schema_mismatch['path']} {schema_mismatch['kind']}"
            issue = diagnostic(
                "UNVERIFIED_DEPENDENCY",
                path,
                f"Dependency verification failed ({detail}). No replacement version is inferred.",
                incomplete=True,
            )
            issue["reason"] = dependency_reason
            if schema_mismatch:
                issue["schema_mismatch"] = schema_mismatch
            if http_status is not None:
                issue["http_status"] = http_status
            diagnostics.append(issue)
        if (
            not isinstance(definition, dict)
            or definition.get("userCanRead") is not True
        ):
            diagnostics.append(
                diagnostic(
                    "UNREADABLE_DEFINITION",
                    path,
                    "Read access to the step definition is not established.",
                    incomplete=True,
                ),
            )
        elif step.logic_type != "SETFLOWVARIABLES":
            diagnostics.extend(
                check_bindings(
                    step.raw["inputs"], definition.get("inputs"), path + ".inputs"
                )
            )
        else:
            diagnostics.extend(
                check_bindings(
                    step.raw["inputs"],
                    [
                        p
                        for p in flow["flowVariables"]
                        if p.get("name") in {i.get("name") for i in step.raw["inputs"]}
                    ],
                    path + ".inputs",
                ),
            )
        for key in (
            "inputs",
            "outputsToAssign",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        ):
            try:
                for item in data.bindings(step.raw.get(key, [])):
                    if item.get("scriptActive"):
                        diagnostics.append(
                            diagnostic(
                                "UNVERIFIED_SCRIPT",
                                path + "." + key,
                                "Local validation cannot establish script behavior.",
                                incomplete=True,
                            ),
                        )
                        continue
                    try:
                        await resolver.validate(item, step)
                    except CapabilityError as exc:
                        diagnostics.append(
                            diagnostic(
                                "UNAVAILABLE_BINDING"
                                if exc.code == "UNAVAILABLE"
                                else "INVALID_BINDING",
                                path + "." + key,
                                "Check the declared type, mandatory value, producer path, visibility, and "
                                "reference-table compatibility.",
                                incomplete=exc.code == "UNAVAILABLE",
                            ),
                        )
                    except ValueError, TypeError, KeyError:
                        diagnostics.append(
                            diagnostic(
                                "MALFORMED_BINDING",
                                path + "." + key,
                                "The binding does not match its declared value schema.",
                                incomplete=True,
                            ),
                        )
            except CapabilityError, TypeError, KeyError:
                diagnostics.append(
                    diagnostic(
                        "INCOMPLETE_BINDINGS",
                        path + "." + key,
                        "Read the complete input child schema.",
                        incomplete=True,
                    ),
                )
        if step.logic_type == "FOREACH":
            item = next((i for i in step.raw["inputs"] if i.get("name") == "items"), {})
            if not tree.pills(item.get("value")):
                diagnostics.append(
                    diagnostic(
                        "UNAVAILABLE_LOOP_TYPE",
                        path,
                        "For Each requires a typed Records producer.",
                        incomplete=True,
                    ),
                )
    incomplete = any(d["incomplete"] for d in diagnostics)
    if len(diagnostics) > 100:
        diagnostics = [
            *diagnostics[:99],
            diagnostic(
                "DIAGNOSTIC_LIMIT",
                "flow",
                "Additional diagnostics were omitted. Resolve these issues and validate again.",
                incomplete=True,
            ),
        ]
        incomplete = True
    dependency_evidence["dictionary"] = {
        f"{t}.{c}": row for (t, c), row in resolver.dictionary_cache.items()
    }
    dependency_evidence["pills"] = resolver.cache
    dependency_evidence["compatibility"] = {
        f"{s}:{t}": ok for (s, t), ok in resolver.table_cache.items()
    }
    return {
        "valid": None if incomplete else not diagnostics,
        "complete": not incomplete,
        "diagnostics": diagnostics,
        "dependency_revision": fingerprint(dependency_evidence),
    }


async def validate(sys_id: str, client: ServiceNowClient) -> CapabilityResult:
    validate_sys_id(sys_id)
    check_table_access("sys_hub_flow")
    try:
        before = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        flow = await client.studio_request(f"flow/{sys_id}")
        if flow.get("id") != sys_id:
            raise ValueError("Unexpected identity")
        result = await validate_definition(flow, client)
        after = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if (
            any(scalar(before.get(k)) is None for k in HEADER_FIELDS)
            or before != after
            or scalar(after.get("sys_id")) != sys_id
        ):
            result.update(valid=None, complete=False)
            result["diagnostics"].append(
                diagnostic(
                    "READ_RACE",
                    "flow",
                    "Flow revision changed or was unreadable during validation. Read again; do not infer validity.",
                    incomplete=True,
                ),
            )
        revision = fingerprint(flow)
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        result = {
            "valid": None,
            "complete": False,
            "diagnostics": [
                diagnostic(
                    "UNREADABLE_DEFINITION",
                    "flow",
                    "The saved definition could not be read or validated completely. No compile request was sent.",
                    incomplete=True,
                ),
            ],
        }
        revision = None
    return CapabilityResult(
        data={
            "sys_id": sys_id,
            "mode": "local",
            "remote_compile": "not_performed",
            "revision": revision,
            **result,
        },
    )


async def status(
    sys_id: str, execution_sys_id: str | None, client: ServiceNowClient
) -> CapabilityResult:
    validate_sys_id(sys_id)
    if execution_sys_id:
        validate_sys_id(execution_sys_id)
    check_table_access("sys_hub_flow")
    diagnostics: list[dict[str, Any]] = []
    try:
        row = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if scalar(row.get("sys_id")) != sys_id:
            raise ValueError("Unexpected flow identity")
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        row = {}
        diagnostics.append(
            diagnostic(
                "UNREADABLE_FLOW",
                "flow",
                "Flow lifecycle state is unreadable.",
                incomplete=True,
            ),
        )
    missing = [field for field in HEADER_FIELDS if scalar(row.get(field)) is None]
    if missing:
        diagnostics.append(
            diagnostic(
                "PARTIAL_FLOW",
                "flow",
                "Lifecycle fields are incomplete. Missing: " + ", ".join(missing),
                incomplete=True,
            ),
        )
    revision = scalar(row.get("sys_mod_count"))
    if revision is not None and (not revision.isascii() or not revision.isdecimal()):
        diagnostics.append(
            diagnostic(
                "UNKNOWN_REVISION",
                "flow.revision",
                "The flow revision is malformed.",
                incomplete=True,
            ),
        )
    active = {"true": True, "false": False}.get(scalar(row.get("active")) or "")
    state = scalar(row.get("status"))
    if active is None or state not in {"draft", "published"}:
        diagnostics.append(
            diagnostic(
                "UNKNOWN_STATE",
                "flow",
                "The active flag or authoring status is unknown.",
                incomplete=True,
            ),
        )
    master = scalar(row.get("master_snapshot"))
    latest = scalar(row.get("latest_snapshot"))
    if scalar(row.get("type")) not in {"flow", "subflow"} or (
        state == "published" and not master
    ):
        diagnostics.append(
            diagnostic(
                "INCONSISTENT_LIFECYCLE",
                "flow",
                "Flow type or published snapshot evidence is incomplete.",
                incomplete=True,
            ),
        )
    for identity in (master, latest):
        if identity:
            try:
                validate_sys_id(identity)
            except ValueError:
                diagnostics.append(
                    diagnostic(
                        "MALFORMED_VERSION",
                        "flow",
                        "A snapshot version reference is malformed.",
                        incomplete=True,
                    ),
                )
    capture_state: dict[str, Any] = {
        "state": "unverified",
        "scope": "flow_header_metadata_only",
    }
    try:
        captured = await capture.read_capture(client, sys_id)
        capture_state.update(
            state="observed" if captured else "not_visible",
            metadata=captured,
            current_session_verified=False,
            payload_verified=False,
        )
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        capture_state["reason"] = (
            "Customer Update metadata could not be read completely. This does not prove missing capture."
        )
    session: dict[str, Any] = {"state": "unavailable", "changed": False}
    try:
        session.update(state="observed", **(await client.flow_session_context()))
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        session["reason"] = (
            "Current application/update-set context is unavailable through this authentication contract. No "
            "session switch was attempted."
        )
    execution: dict[str, Any] | None = None
    if execution_sys_id:
        check_table_access("sys_flow_context")
        execution = {
            "sys_id": execution_sys_id,
            "state": None,
            "terminal": None,
            "version_verified": False,
        }
        try:
            context = await client.flow_record(
                "sys_flow_context", execution_sys_id, EXECUTION_FIELDS
            )
            if (
                any(scalar(context.get(k)) is None for k in EXECUTION_FIELDS)
                or scalar(context["sys_id"]) != execution_sys_id
            ):
                raise ValueError("Incomplete context")
            if scalar(context["flow"]) not in {sys_id, master, latest}:
                raise ValueError("Unrelated context")
            context_state = scalar(context["state"])
            execution.update(
                state=context_state,
                terminal=context_state in {"ERROR", "COMPLETE", "CANCELLED"}
                if context_state in EXECUTION_STATES
                else None,
                sys_updated_on=scalar(context["sys_updated_on"]),
            )
            if context_state not in EXECUTION_STATES:
                raise ValueError("Unknown context state")
        except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
            diagnostics.append(
                diagnostic(
                    "UNKNOWN_EXECUTION",
                    "execution",
                    "Execution state is missing, unrelated, or unknown. This does not authorize replay.",
                    incomplete=True,
                ),
            )
    try:
        after = await client.flow_record("sys_hub_flow", sys_id, HEADER_FIELDS)
        if after != row:
            raise ValueError("Revision changed")
    except Exception:  # noqa: BLE001 — deliberately convert unexpected failures to safe diagnostics
        diagnostics.append(
            diagnostic(
                "READ_RACE",
                "flow",
                "Flow state changed or could not be rechecked during status assembly.",
                incomplete=True,
            ),
        )
    return CapabilityResult(
        data={
            "complete": not diagnostics,
            "completeness_scope": "flow_lifecycle_and_requested_execution_only",
            "flow": {
                "sys_id": sys_id,
                "scope": scalar(row.get("sys_scope")),
                "active": active,
                "status": state,
                "draft": state == "draft" if state in {"draft", "published"} else None,
                "has_published_snapshot": bool(master) if master is not None else None,
                "published_snapshot": master,
                "latest_snapshot": latest,
                "revision": {
                    "updated_on": scalar(row.get("sys_updated_on")),
                    "mod_count": scalar(row.get("sys_mod_count")),
                },
            },
            "execution": execution,
            "diagnostics": diagnostics,
            "replay_safe": False,
            "capture": capture_state,
            "session_context": session,
        },
    )
