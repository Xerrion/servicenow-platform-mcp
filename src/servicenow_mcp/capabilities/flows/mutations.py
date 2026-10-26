"""Experimental draft authoring through the observed Workflow Studio contract."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue, TypeAdapter, ValidationError

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    creation,
    dynamic_schemas,
    editing,
    effects,
    semantics,
    target,
)
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.validation import sanitize_query_value

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

_SCHEMA_EVIDENCE: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class SchemaResolution:
    """Internal apply result whose evidence must be retained before issuing a token."""

    evidence: dict[str, JsonValue]
    summary: dict[str, JsonValue]


def validate_schema_evidence(evidence: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        return _SCHEMA_EVIDENCE.validate_python(
            dynamic_schemas.validate_evidence(evidence)
        )
    except ValidationError:
        raise CapabilityError(
            "Schema evidence must contain native JSON values."
        ) from None


async def _verify_schema_evidence(
    command: FlowCommand, client: ServiceNowClient
) -> None:
    for evidence in command.schema_evidence:
        await dynamic_schemas.verify(validate_schema_evidence(evidence), client)


async def create_properties(
    command: FlowCommand, client: ServiceNowClient
) -> dict[str, Any]:
    check_table_access("sys_scope")
    scope = await client.get_record(
        "sys_scope", command.scope or "", fields=["sys_id", "name"]
    )
    if scope.get("sys_id") != command.scope:
        raise CapabilityError("The requested application scope could not be resolved.")
    matches = await client.query_records(
        "sys_hub_flow",
        f"name={sanitize_query_value(command.name or '')}^sys_scope={command.scope}",
        fields=["sys_id", "name"],
        limit=1,
    )
    if matches["records"]:
        raise CapabilityError(
            "A flow with this name already exists in the requested scope.",
            "DUPLICATE_FLOW",
        )
    return {
        "name": command.name,
        "description": command.description or "",
        "scope": command.scope,
        "type": command.flow_type or "flow",
        "active": False,
        "status": "draft",
        "runAs": "user",
        "access": "public",
        "flowPriority": "MEDIUM",
        "label_cache": [],
    }


async def _append_plan(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[FlowCommand, dict[str, Any], dict[str, Any]]:
    subflow = command.action == "add_subflow"
    adapted = command.model_copy(
        update={
            "edits": [
                {
                    "op": "insert_subflow" if subflow else "insert_action",
                    "subflow_sys_id" if subflow else "action_type_sys_id": (
                        command.subflow_sys_id
                        if subflow
                        else command.action_type_sys_id
                    ),
                    "inputs": command.inputs or {},
                    "ref": "appended_step",
                }
            ]
        },
        deep=True,
    )
    # Keep the convenience action so shared loading cannot opt into active-draft edits.
    state = await editing.load_state(adapted, client)
    result = await editing.plan(adapted, state, client)
    return adapted, state, result


async def preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str | None]:
    if command.action == "resolve_schema":
        plan, summary = await dynamic_schemas.preview(command, client)
        return summary, fingerprint(plan)
    await _verify_schema_evidence(command, client)
    context = await target.read_binding(command, client)
    summary, revision = await _preview(command, client)
    if command.schema_evidence:
        summary["resolved_schemas"] = [
            dynamic_schemas.evidence_summary(evidence)
            for evidence in command.schema_evidence
        ]
    summary["context"] = {
        "state": "bound" if context else "unverified",
        "expected_scope": command.expected_scope,
        "expected_revision": command.expected_revision,
        "scope_semantics": "target_application",
    }
    return summary, fingerprint({"operation": revision, "context": context})


async def _preview(
    command: FlowCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str | None]:
    if command.action in effects.OPS:
        return await effects.preview(command, client)
    if command.action == "edit":
        return await editing.preview(command, client)
    if command.action == "create":
        payload = await create_properties(command, client)
        if command.edits:
            return await creation.preview(command, payload, client)
        return mask_sensitive_fields(payload), fingerprint(payload)
    _, state, result = await _append_plan(command, client)
    flow = state["flow"]
    collection = (
        "subFlowInstances" if command.action == "add_subflow" else "actionInstances"
    )
    instance = next(
        row
        for row in result["payload"][collection]
        if row["uiUniqueIdentifier"] == result["refs"]["appended_step"]
    )
    return {
        "sys_id": flow["id"],
        "name": flow["name"],
        "active": False,
        "status": "draft",
        **(
            {
                "subflow_sys_id": command.subflow_sys_id,
                "snapshot_sys_id": instance["subflowSysId"],
                "wait_for_completion": instance["waitForCompletion"]["value"],
            }
            if command.action == "add_subflow"
            else {"action_type_sys_id": instance["actionTypeSysId"]}
        ),
        "action_name": instance["name"],
        "inputs": mask_sensitive_fields(
            {item["name"]: item["value"] for item in instance["inputs"]}
        ),
        "existing_action_count": len(flow["actionInstances"]),
        "existing_subflow_count": len(flow["subFlowInstances"]),
        "proposed_order": instance["order"],
    }, editing.plan_revision(state, result)


async def apply(
    command: FlowCommand, expected: str | None, client: ServiceNowClient
) -> dict[str, Any] | SchemaResolution:
    if command.action == "resolve_schema":
        plan, summary = await dynamic_schemas.preview(command, client)
        if fingerprint(plan) != expected:
            raise CapabilityError(
                "The schema provider or its dependencies changed. Create a new preview.",
                "STALE_PREVIEW",
            )
        raw_evidence = await dynamic_schemas.apply(plan, client)
        try:
            evidence = validate_schema_evidence(raw_evidence)
            return SchemaResolution(
                evidence=evidence,
                summary={
                    **summary,
                    "resolved_schemas": [dynamic_schemas.evidence_summary(evidence)],
                },
            )
        except Exception:  # noqa: BLE001 - provider effect already occurred
            raise CapabilityError(
                "The provider executed, but its schema evidence could not be retained. "
                "No schema token is available. Do not replay the action automatically.",
                "SCHEMA_EVIDENCE_NOT_RETAINED",
                outcome={"provider_executed": True, "schema_retained": False},
            ) from None
    await _verify_schema_evidence(command, client)
    context = await target.read_binding(command, client)
    _, revision = await _preview(command, client)
    if fingerprint({"operation": revision, "context": context}) != expected:
        raise CapabilityError(
            "The flow, dependencies, target scope, or revision changed. Create a new preview.",
            "STALE_PREVIEW",
        )
    result = await _apply(command, revision, client, binding_context=context)
    if command.action == "create" and command.edits:
        return result
    if command.action == "test":
        return result
    revision = await target.verify_after(command, client, result["sys_id"], context)
    if revision is not None:
        result["revision"] = revision
    return result


async def _apply(
    command: FlowCommand,
    expected: str | None,
    client: ServiceNowClient,
    *,
    binding_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if command.action in effects.OPS:
        return await effects.apply(command, expected, client, binding_context)
    if command.action == "edit":
        return await editing.apply(
            command, expected, client, binding_context=binding_context
        )
    if command.action in {"add_action", "add_subflow"}:
        adapted, state, result = await _append_plan(command, client)
        if editing.plan_revision(state, result) != expected:
            raise CapabilityError(
                "The flow or its definitions changed since preview. Create a new preview.",
                "STALE_PREVIEW",
            )
        saved = await editing.save_plan(
            adapted, result, client, binding_context=binding_context
        )
        return {
            "action": command.action,
            "table": saved["table"],
            "sys_id": saved["sys_id"],
            "name": saved["name"],
            "active": saved["active"],
            "status": saved["status"],
            "subflow_instance_sys_id"
            if command.action == "add_subflow"
            else "action_sys_id": saved["refs"]["appended_step"]["sys_id"],
        }
    payload = await create_properties(command, client)
    if command.edits:
        return await creation.apply(command, payload, expected, client, binding_context)
    if fingerprint(payload) != expected:
        raise CapabilityError(
            "The creation properties changed. Create a new preview.",
            "STALE_PREVIEW",
        )
    await target.require_current(command, client, binding_context)
    saved = await semantics.save_draft(client, payload, create=True)
    if (
        saved.get("active") is not False
        or saved.get("status") != "draft"
        or saved.get("scope") != command.scope
        or saved.get("name") != command.name
    ):
        raise CapabilityError(
            "The saved flow differs from the draft request. Check ServiceNow before retrying.",
            "UNKNOWN_OUTCOME",
        )
    saved = await semantics.verify_created(client, payload, saved)
    return {
        "action": command.action,
        "table": "sys_hub_flow",
        "sys_id": saved["id"],
        "name": saved.get("name"),
        "active": False,
        "status": "draft",
    }
