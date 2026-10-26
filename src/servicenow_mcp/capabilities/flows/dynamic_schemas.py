"""Explicit, one-shot schema-provider effects bound to read-only Studio evidence."""

import copy
import json
import re
from asyncio import CancelledError
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.capabilities.flows import calls, data, declarations
from servicenow_mcp.capabilities.flows.target import scalar
from servicenow_mcp.capabilities.mutations.models import fingerprint
from servicenow_mcp.policy import (
    MASK_VALUE,
    check_table_access,
    is_sensitive_field,
    mask_sensitive_fields,
)
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.mutations.models import FlowCommand
    from servicenow_mcp.platform.client import ServiceNowClient

Mode = Literal["dynamic_inputs", "dynamic_choice", "dynamic_template"]
MODES = {"dynamic_inputs", "dynamic_choice", "dynamic_template"}
SNAPSHOT_TABLE = "sys_hub_action_type_snapshot"
PARENT_TABLE = "sys_hub_action_type_definition"
REVISION_FIELDS = ["sys_id", "sys_mod_count", "sys_updated_on"]
ALIAS_FIELDS = [*REVISION_FIELDS, "parent", "type"]
SNAPSHOT_FIELDS = [
    *REVISION_FIELDS,
    "parent_action",
    "master",
    "internal_name",
    "sys_scope",
]
PARENT_FIELDS = [
    *REVISION_FIELDS,
    "master_snapshot",
    "latest_snapshot",
    "state",
    "active",
    "internal_name",
    "sys_scope",
]
LINK = re.compile(r"\{\{action\.([a-z0-9_]+)\}\}")
NAME = re.compile(r"[a-z][a-z0-9_]*")
ALIAS_INPUT = "snc_input_alias_override"


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)


class _ActionBinding(_Shape):
    snapshot: dict[str, JsonValue]
    parent: dict[str, JsonValue]
    scope: dict[str, JsonValue] | None
    definition_fingerprint: str


class _Provider(_Shape):
    descriptor: dict[str, JsonValue]
    scoped_name: str
    binding: _ActionBinding


class _TableBinding(_Shape):
    record: dict[str, JsonValue]
    descriptor: dict[str, JsonValue]


class _RecordBinding(_Shape):
    table: str
    record: dict[str, JsonValue]


class _ConnectionTarget(_Shape):
    source_alias: str
    target_alias: str | None
    metadata: dict[str, JsonValue] | None


class _ConnectionBinding(_Shape):
    owner_definition_id: str
    connection_configurations_present: bool
    connection_configurations: list[dict[str, JsonValue]] | None
    targets: list[_ConnectionTarget]


class _ConnectionContext(_Shape):
    effective_overrides: dict[str, str]
    additional_sources: list[str]
    binding: _ConnectionBinding
    alias_records: list[_RecordBinding]


class _DependencyBinding(_Shape):
    sibling: str | None
    parameter: dict[str, JsonValue]
    descriptor: dict[str, JsonValue]
    value: JsonValue


class _Plan(_Shape):
    action_type_sys_id: str
    input_name: str
    expected_scope: str | None
    inputs: dict[str, JsonValue]
    requested_inputs: dict[str, JsonValue]
    connection_overrides: dict[str, str | None] | None
    connection_context: _ConnectionContext
    mode: Mode
    owner: _ActionBinding
    owner_fingerprint: str
    provider: _Provider
    dependencies: dict[str, JsonValue]
    sibling_inputs: dict[str, JsonValue]
    dependency_bindings: dict[str, _DependencyBinding]
    reference_bindings: dict[str, _TableBinding]
    record_bindings: list[_RecordBinding]


class _Evidence(_Shape):
    action_type_sys_id: str
    input_name: str
    mode: Mode
    owner_fingerprint: str
    provider: _Provider
    dependencies: dict[str, JsonValue]
    sibling_inputs: dict[str, JsonValue]
    connection_overrides: dict[str, str | None] | None
    connection_context: _ConnectionContext
    schema_data: list[dict[str, JsonValue]] = Field(alias="schema")
    schema_reference_bindings: dict[str, _TableBinding]
    plan: _Plan
    observed_binding_verified: Literal[True]
    exact_execution_version_verified: Literal[False]


def _shape(model: type[_Shape], value: Any) -> dict[str, Any]:
    validate_payload(value, field_name="schema evidence", validate_keys=False)
    try:
        return model.model_validate(value).model_dump(mode="json", by_alias=True)
    except ValidationError:
        raise CapabilityError(
            "Schema provider evidence has an invalid shape."
        ) from None


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise CapabilityError("Schema providers require single native identifiers.")
    validate_identifier(value)
    return value


def _revision(record: Any, identity: str) -> dict[str, Any]:
    validate_sys_id(identity)
    validate_payload(record, field_name="schema binding", validate_keys=False)
    count = scalar(record.get("sys_mod_count"))
    if (
        scalar(record.get("sys_id")) != identity
        or not count
        or not count.isascii()
        or not count.isdecimal()
        or not scalar(record.get("sys_updated_on"))
    ):
        raise CapabilityError(
            "Schema binding identity or revision is incomplete.", "UNAVAILABLE"
        )
    return record


async def _record(
    client: ServiceNowClient, table: str, identity: str, fields: list[str]
) -> dict[str, Any]:
    check_table_access(table)
    validate_sys_id(identity)
    return _revision(await client.get_record(table, identity, fields=fields), identity)


async def _action_binding(
    client: ServiceNowClient, identity: str, *, current_master: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = await _record(client, SNAPSHOT_TABLE, identity, SNAPSHOT_FIELDS)
    parent_id = scalar(snapshot.get("parent_action"))
    if not parent_id:
        raise CapabilityError(
            "The action snapshot has no independently readable parent.", "UNAVAILABLE"
        )
    parent = await _record(client, PARENT_TABLE, parent_id, PARENT_FIELDS)
    scope_id = scalar(snapshot.get("sys_scope"))
    if not scope_id or current_master and scope_id != scalar(parent.get("sys_scope")):
        raise CapabilityError(
            "The action snapshot and parent scopes disagree.", "UNAVAILABLE"
        )
    scope = None
    scope_name = scope_id
    if scope_id != "global":
        scope = await _record(
            client, "sys_scope", scope_id, [*REVISION_FIELDS, "scope"]
        )
        scope_name = _identifier(scalar(scope.get("scope")))
    if current_master and (
        snapshot.get("master") not in (True, "true")
        or scalar(parent.get("master_snapshot")) != identity
        or parent.get("active") not in (True, "true")
    ):
        raise CapabilityError(
            "The provider has no active current published master snapshot.",
            "UNAVAILABLE",
        )
    # Parent state can be draft while its active published master remains callable.
    definition = await calls.definition({}, identity, subflow=False, client=client)
    internal_name = scalar(snapshot.get("internal_name"))
    if (
        not internal_name
        or current_master
        and internal_name != scalar(parent.get("internal_name"))
        or definition.get("internal_name", definition.get("internalName"))
        != internal_name
        or definition.get("scope") not in {scope_id, scope_name}
        or "parent_action" in definition
        and definition["parent_action"] != parent_id
    ):
        raise CapabilityError(
            "The action definition identity differs from its publication records.",
            "UNAVAILABLE",
        )
    _identifier(internal_name)
    return {
        "snapshot": snapshot,
        "parent": parent,
        "scope": scope,
        "definition_fingerprint": fingerprint(definition),
    }, definition


async def _provider(
    client: ServiceNowClient, descriptor: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = scalar(descriptor.get("actionId"))
    if not identity:
        raise CapabilityError(
            "The provider descriptor has no action identity.", "UNAVAILABLE"
        )
    validate_sys_id(identity)
    scope = _identifier(descriptor.get("actionScope"))
    internal_name = _identifier(descriptor.get("actionInternalName"))
    check_table_access(SNAPSHOT_TABLE)
    selected = (
        await client.query_records(
            SNAPSHOT_TABLE, f"sys_id={identity}", fields=SNAPSHOT_FIELDS, limit=2
        )
    ).get("records")
    if not isinstance(selected, list) or len(selected) > 1:
        raise CapabilityError(
            "The provider selection identity is ambiguous.", "UNAVAILABLE"
        )
    if selected:
        selected_snapshot = _revision(selected[0], identity)
        parent_id = scalar(selected_snapshot.get("parent_action"))
        if not parent_id:
            raise CapabilityError("The provider snapshot has no parent.", "UNAVAILABLE")
    else:
        parent_id = identity
    parent = await _record(client, PARENT_TABLE, parent_id, PARENT_FIELDS)
    master_id = scalar(parent.get("master_snapshot"))
    if not master_id or selected and master_id != identity:
        raise CapabilityError(
            "The selected provider is not its current published master.", "UNAVAILABLE"
        )
    binding, definition = await _action_binding(client, master_id, current_master=True)
    if binding["parent"] != parent or selected and binding["snapshot"] != selected[0]:
        raise CapabilityError(
            "The provider changed while resolving publication.", "STALE_PREVIEW"
        )
    scope_record = binding["scope"]
    bound_scope = scalar(scope_record.get("scope")) if scope_record else "global"
    if scope != bound_scope or internal_name != scalar(
        binding["snapshot"].get("internal_name")
    ):
        raise CapabilityError(
            "The provider scoped name differs from its declared owner binding.",
            "UNAVAILABLE",
        )
    return {
        "descriptor": copy.deepcopy(descriptor),
        "scoped_name": f"{scope}.{internal_name}",
        "binding": binding,
    }, definition


def _static(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and ("{{" in item or "}}" in item):
            raise CapabilityError(
                "Schema provider dependencies must be static, without pills."
            )
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _native_value(parameter: dict[str, Any], value: Any) -> Any:
    if any(parameter.get(key) for key in ("dynamic", "dependent_on", "scriptActive")):
        raise CapabilityError(
            "Schema provider dependencies cannot have nested dynamic or scripted bindings.",
            "UNAVAILABLE",
        )
    _static(value)
    binding = {**copy.deepcopy(parameter), "parameter": copy.deepcopy(parameter)}
    data.set_value(binding, value)
    if isinstance(value, dict | list):
        raise CapabilityError(
            "Provider dependencies require scalar native input values.", "UNAVAILABLE"
        )
    return binding["value"]


def _parameter_signature(parameter: dict[str, Any]) -> dict[str, Any]:
    if any(parameter.get(key) for key in ("dynamic", "dependent_on", "scriptActive")):
        raise CapabilityError(
            "Provider input metadata contains an unresolved nested dependency.",
            "UNAVAILABLE",
        )
    kind = data.value_type(parameter)
    return {
        "type": kind.kind,
        "reference": kind.reference,
        "mandatory": parameter.get("mandatory", False),
        "choices": declarations.choices(parameter) if kind.kind == "choice" else None,
    }


def _same_js_scalar(left: Any, right: Any) -> bool:
    # JavaScript strict equality does not conflate false with zero, nor compare objects by value.
    if isinstance(left, bool):
        return isinstance(right, bool) and left == right
    if isinstance(right, bool):
        return False
    if isinstance(left, int | float):
        return isinstance(right, int | float) and left == right
    if isinstance(left, str):
        return isinstance(right, str) and left == right
    return left is None and right is None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


async def _table_binding(client: ServiceNowClient, table: str) -> dict[str, Any]:
    _identifier(table)
    check_table_access(table)
    check_table_access("sys_db_object")
    result = await client.query_records(
        "sys_db_object",
        f"name={table}",
        fields=[*REVISION_FIELDS, "name", "label"],
        limit=2,
    )
    rows = result.get("records")
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("name") != table:
        raise CapabilityError(
            "A schema reference table is absent or ambiguous.", "UNAVAILABLE"
        )
    record = _revision(rows[0], scalar(rows[0].get("sys_id")) or "")
    descriptor = await client.studio_table_descriptor(table)
    validate_payload(
        descriptor, field_name="schema reference descriptor", validate_keys=False
    )
    _identifier(descriptor.get("displayField"))
    return {"record": record, "descriptor": descriptor}


async def _bind_reference(
    client: ServiceNowClient,
    table: str,
    value: Any,
    tables: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    if table not in tables:
        tables[table] = await _table_binding(client, table)
    if value in ("", None):
        return
    validate_sys_id(value)
    if any(
        item["table"] == table and scalar(item["record"].get("sys_id")) == value
        for item in records
    ):
        return
    record = await _record(client, table, value, REVISION_FIELDS)
    records.append({"table": table, "record": record})


def _connection_request(overrides: Any) -> tuple[dict[str, str | None], bool]:
    from servicenow_mcp.capabilities.flows.connection_inputs import parse

    if overrides is None:
        return {}, False
    if not isinstance(overrides, dict):
        raise CapabilityError("connection_overrides must be a native alias map.")
    marker = "" in overrides
    if marker and overrides[""] != "":
        raise CapabilityError(
            "The native empty connection marker must be exactly an empty key and value."
        )
    actual = {source: target for source, target in overrides.items() if source != ""}
    return parse(actual), marker


def _owner_connection_subset(
    definition: dict[str, Any], requested: dict[str, str | None]
) -> dict[str, str | None]:
    configurations = definition.get("connection_configurations", [])
    if not isinstance(configurations, list) or any(
        not isinstance(row, dict) for row in configurations
    ):
        raise CapabilityError(
            "Native owner connection metadata is malformed.", "UNAVAILABLE"
        )
    offered = {
        row.get("alias_id")
        for row in configurations
        if isinstance(row.get("alias_id"), str)
    }
    return {source: target for source, target in requested.items() if source in offered}


def _validate_alias_records(
    requested: dict[str, str | None], records: list[dict[str, Any]]
) -> None:
    expected = set(requested) | {
        target for target in requested.values() if target is not None
    }
    observed = {}
    for alias in records:
        identity = scalar(alias["record"].get("sys_id")) or ""
        if alias["table"] != "sys_alias" or identity in observed:
            raise CapabilityError(
                "Schema provider alias record bindings are ambiguous."
            )
        record = _revision(alias["record"], identity)
        if "parent" not in record or scalar(record.get("type")) != "connection":
            raise CapabilityError(
                "Schema providers require independently readable connection aliases, not credentials.",
                "UNAVAILABLE",
            )
        if record["parent"] is not None and scalar(record["parent"]) is None:
            raise CapabilityError(
                "A connection alias parent identity is unreadable.", "UNAVAILABLE"
            )
        observed[identity] = record
    if set(observed) != expected:
        raise CapabilityError(
            "Schema provider connection alias identities are incomplete."
        )
    for source, target in requested.items():
        if scalar(observed[source].get("parent")) not in (None, ""):
            raise CapabilityError(
                "A schema provider connection source must be a root alias."
            )
        if target is not None and (
            target == source or scalar(observed[target].get("parent")) != source
        ):
            raise CapabilityError(
                "A schema provider target must be a child of its source alias."
            )


async def _build_plan(
    client: ServiceNowClient,
    action_type_sys_id: str,
    input_name: str,
    inputs: dict[str, Any],
    expected_scope: str | None,
    connection_overrides: dict[str, str | None] | None,
) -> dict[str, Any]:
    validate_sys_id(action_type_sys_id)
    _identifier(input_name)
    validate_payload(inputs, field_name="inputs")
    owner, definition = await _action_binding(
        client, action_type_sys_id, current_master=False
    )
    if (
        expected_scope is not None
        and scalar(owner["snapshot"].get("sys_scope")) != expected_scope
    ):
        raise CapabilityError(
            "The schema owner scope differs from expected_scope.", "CONTEXT_MISMATCH"
        )
    parameters = definition.get("inputs")
    if not isinstance(parameters, list) or any(
        not isinstance(parameter, dict) or not isinstance(parameter.get("name"), str)
        for parameter in parameters
    ):
        raise CapabilityError(
            "The schema owner input metadata is incomplete.", "UNAVAILABLE"
        )
    target = data.spec(data.named(parameters, input_name))
    mode = data.value_type(target).kind
    descriptor = target.get("dynamic")
    if mode not in MODES or not isinstance(descriptor, dict):
        raise CapabilityError(
            "The selected input has no supported dynamic schema provider.",
            "UNAVAILABLE",
        )
    provider, provider_definition = await _provider(client, descriptor)
    from servicenow_mcp.capabilities.flows.connection_inputs import provider_context

    requested_connections, tombstone_marker = _connection_request(connection_overrides)
    owner_connections = _owner_connection_subset(definition, requested_connections)
    _, connection_binding = provider_context(definition, owner_connections)
    effective_overrides = {
        source: target
        for source, target in requested_connections.items()
        if target is not None
    }
    alias_records = []
    alias_ids = set(requested_connections) | set(effective_overrides.values())
    for identity in sorted(alias_ids):
        record = await _record(client, "sys_alias", identity, ALIAS_FIELDS)
        alias_records.append({"table": "sys_alias", "record": record})
    if tombstone_marker:
        effective_overrides[""] = ""
    _validate_alias_records(requested_connections, alias_records)
    connection_context = {
        "effective_overrides": effective_overrides,
        "additional_sources": sorted(
            set(requested_connections) - set(owner_connections)
        ),
        "binding": connection_binding,
        "alias_records": alias_records,
    }
    provider_inputs = provider_definition.get("inputs")
    declared = descriptor.get("inputs")
    if (
        not isinstance(provider_inputs, list)
        or not isinstance(declared, list)
        or any(not isinstance(item, dict) for item in [*provider_inputs, *declared])
        or any(
            not isinstance(item.get("name"), str)
            for item in [*provider_inputs, *declared]
        )
        or len({item.get("name") for item in provider_inputs}) != len(provider_inputs)
        or len({item.get("name") for item in declared}) != len(declared)
        or {item.get("name") for item in provider_inputs}
        != {item.get("name") for item in declared}
    ):
        raise CapabilityError(
            "Provider dependency declarations are incomplete or ambiguous.",
            "UNAVAILABLE",
        )
    if any(item.get("name") == ALIAS_INPUT for item in declared):
        raise CapabilityError(
            "Provider inputs cannot replace the reserved native connection context.",
            "UNAVAILABLE",
        )
    native_inputs = {}
    sibling_parameters = {}
    tables: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    for name in inputs:
        _identifier(name)
        if name == input_name:
            raise CapabilityError(
                "The dynamic target cannot supply its own provider dependency."
            )
        data.named(parameters, name)
    for raw_parameter in parameters:
        parameter = data.spec(raw_parameter)
        name = _identifier(parameter.get("name"))
        if name == input_name:
            continue
        supplied = name in inputs
        kind = data.value_type(parameter)
        if not supplied and (
            any(
                parameter.get(key)
                for key in ("dynamic", "dependent_on", "scriptActive")
            )
            or kind.kind == "object"
            or kind.many
        ):
            if kind.kind == "reference":
                raise CapabilityError(
                    "Unresolved reference siblings cannot supply a provider's native value context.",
                    "UNAVAILABLE",
                )
            continue
        value = inputs[name] if supplied else data.default_value(parameter)
        if not supplied and kind.kind == "boolean":
            value = value in ("true", "1", True)
        validation_parameter = parameter
        if not supplied:
            validation_parameter = {
                key: val
                for key, val in parameter.items()
                if key not in {"readonly", "readOnly", "mandatory"}
            }
        native_inputs[name] = _native_value(validation_parameter, value)
        sibling_parameters[name] = parameter
        if kind.reference:
            await _bind_reference(
                client, kind.reference, native_inputs[name], tables, records
            )
        if kind.kind == "table_name" and native_inputs[name]:
            table = _identifier(native_inputs[name])
            if table not in tables:
                tables[table] = await _table_binding(client, table)
    sibling_inputs = {}
    dependencies = {}
    dependency_bindings = {}
    for item in declared:
        name = _identifier(item.get("name"))
        parameter = data.spec(data.named(provider_inputs, name))
        if _parameter_signature(item) != _parameter_signature(parameter):
            raise CapabilityError(
                "Provider dependency metadata differs from the published provider input.",
                "UNAVAILABLE",
            )
        if "value" not in item:
            raise CapabilityError(
                "The provider descriptor has no explicit dependency value.",
                "UNAVAILABLE",
            )
        source = item["value"]
        linked = LINK.fullmatch(source) if isinstance(source, str) else None
        sibling = linked.group(1) if linked else None
        if sibling:
            if sibling not in inputs or sibling not in native_inputs:
                raise CapabilityError(
                    f"Schema provider dependency '{sibling}' requires an explicit static sibling input."
                )
            source_parameter = sibling_parameters[sibling]
            if data.value_type(source_parameter) != data.value_type(parameter):
                raise CapabilityError(
                    "A linked schema dependency has incompatible native type or reference identity."
                )
            value = _native_value(parameter, native_inputs[sibling])
            sibling_inputs[sibling] = value
            # getDynamicActionInputValueReference:158921-158936.
            value = (
                {"table": item["reference"], "sys_id": value}
                if source_parameter.get("reference") and item.get("reference")
                else value
            )
        else:
            value = _native_value(parameter, source)
        if parameter.get("mandatory") and (
            value is None or isinstance(value, str) and value == ""
        ):
            raise CapabilityError("The provider has an empty mandatory dependency.")
        if not sibling and data.value_type(parameter).reference:
            await _bind_reference(
                client, data.value_type(parameter).reference, value, tables, records
            )
        processed = value
        # prepDynamicInputsDataForFetching:158873-158897 matches the first sibling by value.
        dependee = next(
            (
                sibling_parameters[key]
                for key, native in native_inputs.items()
                if _same_js_scalar(native, value)
            ),
            None,
        )
        if dependee is not None and data.value_type(dependee).kind == "reference":
            if not isinstance(value, str):
                raise CapabilityError(
                    "A native reference transformation requires text."
                )
            try:
                parsed = json.loads(value)
                if type(parsed) is int and abs(parsed) > 2**53 - 1:
                    raise CapabilityError(
                        "Native JSON normalization would lose a numeric reference identity."
                    )
                processed = _json(parsed)
            except ValueError:
                processed = _json({"value": value})
        dependencies[name] = processed
        dependency_bindings[name] = {
            "sibling": sibling,
            "parameter": copy.deepcopy(parameter),
            "descriptor": copy.deepcopy(item),
            "value": value,
        }
    if effective_overrides:
        dependencies[ALIAS_INPUT] = copy.deepcopy(effective_overrides)
    return _shape(
        _Plan,
        {
            "action_type_sys_id": action_type_sys_id,
            "input_name": input_name,
            "expected_scope": expected_scope,
            "inputs": copy.deepcopy(native_inputs),
            "requested_inputs": copy.deepcopy(inputs),
            "connection_overrides": copy.deepcopy(connection_overrides),
            "connection_context": connection_context,
            "mode": mode,
            "owner": owner,
            "owner_fingerprint": fingerprint(definition),
            "provider": provider,
            "dependencies": dependencies,
            "sibling_inputs": sibling_inputs,
            "dependency_bindings": dependency_bindings,
            "reference_bindings": tables,
            "record_bindings": records,
        },
    )


async def preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve declarations and revisions using GETs only; never execute a provider."""
    if not command.action_type_sys_id or not command.input_name:
        raise CapabilityError(
            "Schema resolution requires an action snapshot and input name."
        )
    plan = await _build_plan(
        client,
        command.action_type_sys_id,
        command.input_name,
        command.inputs or {},
        command.expected_scope,
        command.connection_overrides,
    )
    summary = mask_sensitive_fields(
        {
            "action_type_sys_id": plan["action_type_sys_id"],
            "input_name": plan["input_name"],
            "mode": plan["mode"],
            "provider": _provider_summary(plan["provider"]),
            "dependencies": plan["dependencies"],
            "sibling_inputs": plan["sibling_inputs"],
            "connection_overrides": plan["connection_overrides"],
            "connection_context": _connection_summary(plan["connection_context"]),
            "effect": "execute_schema_provider_action_once",
            "provider_executed": False,
            "exact_execution_version_verified": False,
            "execution_version_limitation": "Execution uses a scoped name, not a snapshot ID. Publication binding is observed before and after, not atomically verified at execution.",
        }
    )
    return plan, summary


async def _require_current(plan: dict[str, Any], client: ServiceNowClient) -> None:
    try:
        current = await _build_plan(
            client,
            plan["action_type_sys_id"],
            plan["input_name"],
            plan["requested_inputs"],
            plan["expected_scope"],
            plan["connection_overrides"],
        )
        if fingerprint(current) != fingerprint(plan):
            raise ValueError("Schema provider binding changed")
    except Exception:  # noqa: BLE001 - every incomplete binding must prevent execution/adoption
        raise CapabilityError(
            "The schema owner, provider, dependency, or reference binding changed or could not be re-established. Create a new preview.",
            "STALE_PREVIEW",
        ) from None


async def _schema_references(
    schema: list[dict[str, Any]], client: ServiceNowClient
) -> dict[str, Any]:
    tables = {}
    pending = list(schema)
    while pending:
        field = data.spec(pending.pop())
        reference = field.get("reference")
        if reference:
            table = _identifier(reference)
            if table not in tables:
                tables[table] = await _table_binding(client, table)
        children = field.get("children", [])
        if isinstance(children, list):
            pending.extend(child for child in children if isinstance(child, dict))
    return tables


def validate_evidence(evidence: Any) -> dict[str, Any]:
    """Parse the exact server-held artifact before storage, readback, or adoption."""
    from servicenow_mcp.capabilities.flows.dynamic_inputs import validate_schema

    result = _shape(_Evidence, evidence)
    if (
        evidence.get("observed_binding_verified") is not True
        or evidence.get("exact_execution_version_verified") is not False
    ):
        raise CapabilityError(
            "Schema evidence must retain its exact non-atomic verification claims."
        )
    plan = result["plan"]
    validate_sys_id(result["action_type_sys_id"])
    _identifier(result["input_name"])
    for key in (
        "action_type_sys_id",
        "input_name",
        "mode",
        "owner_fingerprint",
        "provider",
        "dependencies",
        "sibling_inputs",
        "connection_overrides",
        "connection_context",
    ):
        if result[key] != plan[key]:
            raise CapabilityError(
                "Schema evidence does not match its retained provider plan."
            )
    if result["owner_fingerprint"] != plan["owner"]["definition_fingerprint"]:
        raise CapabilityError("Schema evidence owner fingerprint is inconsistent.")
    if not re.fullmatch(r"[0-9a-f]{64}", result["owner_fingerprint"]):
        raise CapabilityError("Schema evidence fingerprint is incomplete.")
    for binding in (plan["owner"], plan["provider"]["binding"]):
        _revision(binding["snapshot"], scalar(binding["snapshot"].get("sys_id")) or "")
        _revision(binding["parent"], scalar(binding["parent"].get("sys_id")) or "")
        if scalar(binding["snapshot"].get("parent_action")) != scalar(
            binding["parent"].get("sys_id")
        ) or not re.fullmatch(r"[0-9a-f]{64}", binding["definition_fingerprint"]):
            raise CapabilityError(
                "Schema evidence publication identities are inconsistent."
            )
        if binding["scope"] is not None:
            _revision(
                binding["scope"], scalar(binding["snapshot"].get("sys_scope")) or ""
            )
    if scalar(plan["owner"]["snapshot"].get("sys_id")) != result["action_type_sys_id"]:
        raise CapabilityError("Schema evidence owner identity is inconsistent.")
    from servicenow_mcp.capabilities.flows.connection_inputs import provider_context

    requested_map, tombstone_marker = _connection_request(plan["connection_overrides"])
    context = plan["connection_context"]
    effective_connections = {
        source: target for source, target in requested_map.items() if target is not None
    }
    if tombstone_marker:
        effective_connections[""] = ""
    if context["effective_overrides"] != effective_connections:
        raise CapabilityError(
            "Schema evidence connection overrides differ from the reviewed request."
        )
    binding = context["binding"]
    if binding["owner_definition_id"] != result["action_type_sys_id"]:
        raise CapabilityError(
            "Schema evidence connection context belongs to another action owner."
        )
    owner_definition = {"id": binding["owner_definition_id"]}
    if binding["connection_configurations_present"]:
        if binding["connection_configurations"] is None:
            raise CapabilityError(
                "Schema evidence declared connection metadata is incomplete."
            )
        owner_definition["connection_configurations"] = binding[
            "connection_configurations"
        ]
    elif binding["connection_configurations"] is not None:
        raise CapabilityError(
            "Schema evidence absent connection metadata is inconsistent."
        )
    owner_connections = _owner_connection_subset(owner_definition, requested_map)
    rebound_map, rebound_binding = provider_context(owner_definition, owner_connections)
    expected_owner_map = {
        source: target
        for source, target in owner_connections.items()
        if target is not None
    }
    if rebound_map != expected_owner_map or rebound_binding != binding:
        raise CapabilityError(
            "Schema evidence connection targets differ from the owner declaration."
        )
    if context["additional_sources"] != sorted(
        set(requested_map) - set(owner_connections)
    ):
        raise CapabilityError(
            "Schema evidence full-root connection context is inconsistent."
        )
    if ALIAS_INPUT in plan["dependency_bindings"]:
        raise CapabilityError(
            "Schema evidence cannot replace reserved connection context with a provider dependency."
        )
    if effective_connections:
        if plan["dependencies"].get(ALIAS_INPUT) != effective_connections:
            raise CapabilityError(
                "Schema evidence native connection input is inconsistent."
            )
    elif ALIAS_INPUT in plan["dependencies"]:
        raise CapabilityError(
            "Schema evidence cannot retain an empty native connection override input."
        )
    _validate_alias_records(requested_map, context["alias_records"])
    expected_dependencies = set(plan["dependency_bindings"])
    if plan["connection_context"]["effective_overrides"]:
        expected_dependencies.add(ALIAS_INPUT)
    if set(plan["dependencies"]) != expected_dependencies:
        raise CapabilityError("Schema evidence dependency bindings are incomplete.")
    if any(
        name not in plan["inputs"] or value != plan["inputs"][name]
        for name, value in plan["sibling_inputs"].items()
    ):
        raise CapabilityError("Schema evidence sibling input binding is inconsistent.")
    if any(
        name not in plan["inputs"] or value != plan["inputs"][name]
        for name, value in plan["requested_inputs"].items()
    ):
        raise CapabilityError("Schema evidence request context is inconsistent.")
    provider = plan["provider"]
    descriptor = provider["descriptor"]
    provider_snapshot = provider["binding"]["snapshot"]
    provider_parent = provider["binding"]["parent"]
    if (
        provider["scoped_name"]
        != f"{_identifier(descriptor.get('actionScope'))}.{_identifier(descriptor.get('actionInternalName'))}"
        or scalar(provider_snapshot.get("internal_name"))
        != descriptor["actionInternalName"]
        or scalar(provider_parent.get("internal_name"))
        != descriptor["actionInternalName"]
        or scalar(provider_snapshot.get("sys_scope"))
        != scalar(provider_parent.get("sys_scope"))
        or scalar(provider_parent.get("master_snapshot"))
        != scalar(provider_snapshot.get("sys_id"))
        or provider_snapshot.get("master") not in (True, "true")
        or provider_parent.get("active") not in (True, "true")
        or descriptor.get("actionId")
        not in {
            scalar(provider_snapshot.get("sys_id")),
            scalar(provider_parent.get("sys_id")),
        }
    ):
        raise CapabilityError(
            "Schema evidence provider publication binding is inconsistent."
        )
    linked_siblings = {
        row["sibling"]
        for row in plan["dependency_bindings"].values()
        if row["sibling"] is not None
    }
    if linked_siblings != set(plan["sibling_inputs"]):
        raise CapabilityError("Schema evidence linked sibling bindings are incomplete.")
    for table, binding in plan["reference_bindings"].items():
        _identifier(table)
        if binding["record"].get("name") != table:
            raise CapabilityError(
                "Schema evidence dependency table identity is inconsistent."
            )
        _revision(binding["record"], scalar(binding["record"].get("sys_id")) or "")
        _identifier(binding["descriptor"].get("displayField"))
    for binding in plan["record_bindings"]:
        if binding["table"] not in plan["reference_bindings"]:
            raise CapabilityError(
                "Schema evidence record has no bound reference table."
            )
        _revision(binding["record"], scalar(binding["record"].get("sys_id")) or "")
    schema = validate_schema(result["mode"], {"data": result["schema"]})
    if schema != result["schema"]:
        raise CapabilityError("Schema evidence is not in its validated native shape.")
    schema_tables = {field["reference"] for field in schema if field.get("reference")}
    if schema_tables != set(result["schema_reference_bindings"]):
        raise CapabilityError(
            "Schema evidence reference table bindings are incomplete."
        )
    for table, binding in result["schema_reference_bindings"].items():
        _identifier(table)
        if binding["record"].get("name") != table:
            raise CapabilityError("Schema evidence reference identity is inconsistent.")
        _revision(binding["record"], scalar(binding["record"].get("sys_id")) or "")
        _identifier(binding["descriptor"].get("displayField"))
    return result


async def apply(plan: dict[str, Any], client: ServiceNowClient) -> dict[str, Any]:
    """Rebind, execute exactly once, validate output, and independently rebind again."""
    from servicenow_mcp.capabilities.flows.dynamic_inputs import validate_schema

    plan = _shape(_Plan, plan)
    await _require_current(plan, client)
    returned = False
    try:
        output = await client.studio_schema_provider(
            plan["provider"]["scoped_name"], copy.deepcopy(plan["dependencies"])
        )
        returned = True
        schema = validate_schema(plan["mode"], output)
        schema_references = await _schema_references(schema, client)
        await _require_current(plan, client)
        # Read new schema table descriptors again, rather than retaining a torn read.
        if await _schema_references(schema, client) != schema_references:
            raise ValueError("Schema reference metadata changed after execution")
        return validate_evidence(
            {
                "action_type_sys_id": plan["action_type_sys_id"],
                "input_name": plan["input_name"],
                "mode": plan["mode"],
                "owner_fingerprint": plan["owner_fingerprint"],
                "provider": plan["provider"],
                "dependencies": plan["dependencies"],
                "sibling_inputs": plan["sibling_inputs"],
                "connection_overrides": plan["connection_overrides"],
                "connection_context": plan["connection_context"],
                "schema": schema,
                "schema_reference_bindings": schema_references,
                "plan": plan,
                "observed_binding_verified": True,
                "exact_execution_version_verified": False,
            }
        )
    except Exception, CancelledError:  # noqa: BLE001 - the effect may have occurred; never replay
        raise CapabilityError(
            "Schema provider execution or independent readback is inconclusive. The action may have performed real work. Do not replay automatically; no schema was retained.",
            "UNKNOWN_OUTCOME",
            outcome={
                "provider_executed": True if returned else None,
                "provider_execution_attempted": True,
                "schema_retained": False,
            },
        ) from None


async def verify(evidence: dict[str, Any], client: ServiceNowClient) -> None:
    """Recheck all retained publication/dependency/schema bindings without effects."""
    evidence = validate_evidence(evidence)
    await _require_current(evidence["plan"], client)
    try:
        if (
            await _schema_references(evidence["schema"], client)
            != evidence["schema_reference_bindings"]
        ):
            raise ValueError("Schema reference binding changed")
    except Exception:  # noqa: BLE001 - a missing reference binding makes adoption stale
        raise CapabilityError(
            "Schema reference metadata changed or could not be re-established.",
            "STALE_PREVIEW",
        ) from None


def _provider_summary(provider: dict[str, Any]) -> dict[str, Any]:
    binding = provider["binding"]
    return {
        "name": provider["scoped_name"],
        "action_type_sys_id": scalar(binding["snapshot"].get("sys_id")),
        "parent_action_sys_id": scalar(binding["parent"].get("sys_id")),
        "snapshot_revision": scalar(binding["snapshot"].get("sys_mod_count")),
        "parent_revision": scalar(binding["parent"].get("sys_mod_count")),
        "definition_fingerprint": binding["definition_fingerprint"],
    }


def _connection_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "effective_overrides": copy.deepcopy(context["effective_overrides"]),
        "additional_sources": copy.deepcopy(context["additional_sources"]),
        "alias_revisions": [
            {
                "sys_id": scalar(alias["record"].get("sys_id")),
                "sys_mod_count": scalar(alias["record"].get("sys_mod_count")),
                "sys_updated_on": scalar(alias["record"].get("sys_updated_on")),
                "parent": scalar(alias["record"].get("parent")),
                "type": scalar(alias["record"].get("type")),
            }
            for alias in context["alias_records"]
        ],
    }


def evidence_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    """Expose validated, masked schema evidence without the private retained plan."""
    evidence = validate_evidence(evidence)
    public_schema = copy.deepcopy(evidence["schema"])
    for field in public_schema:
        if is_sensitive_field(field["name"]) or field.get("type") == "password2":
            for key in (
                "value",
                "defaultValue",
                "defaultDisplayValue",
                "display_value",
            ):
                if key in field:
                    field[key] = MASK_VALUE
    return mask_sensitive_fields(
        {
            "action_type_sys_id": evidence["action_type_sys_id"],
            "input_name": evidence["input_name"],
            "mode": evidence["mode"],
            "provider": _provider_summary(evidence["provider"]),
            "owner_fingerprint": evidence["owner_fingerprint"],
            "dependencies": evidence["dependencies"],
            "sibling_inputs": evidence["sibling_inputs"],
            "connection_overrides": evidence["connection_overrides"],
            "connection_context": _connection_summary(evidence["connection_context"]),
            "schema": public_schema,
            "observed_binding_verified": True,
            "exact_execution_version_verified": False,
        }
    )
