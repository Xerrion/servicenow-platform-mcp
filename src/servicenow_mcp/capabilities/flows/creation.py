"""Complete creation through the existing planner and native property POST/full PUT.

Studio creates properties first (bundle:99463-99505). The persisted draft owns
server defaults and revision. A local planning seed never authorizes a write.
"""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import capture, editing, semantics, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


async def prepare(
    command: FlowCommand, properties: dict[str, Any], client: ServiceNowClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed = {
        **properties,
        "id": command.instance_id,
        "isPublished": False,
        "masterSnapshotId": "",
        "snapshot": False,
        "masterSnapshot": False,
        "flowEngineVersion": 2,
        "security": {"can_write": True},
        "stages": {},
        **{
            key: []
            for key in (
                "actionInstances",
                "subFlowInstances",
                "flowLogicInstances",
                "triggerInstances",
                "inputs",
                "outputs",
                "flowVariables",
            )
        },
    }
    state = await editing.load_state(command, client, initial_flow=seed)
    result = await editing.plan(command, state, client)
    if sum(1 for _ in tree.walk(tree.build_tree(result["payload"]))) > 200:
        raise CapabilityError(
            "Complete creation supports at most 200 nodes, including handler wrappers."
        )
    return state, result


async def preview(
    command: FlowCommand, properties: dict[str, Any], client: ServiceNowClient
) -> tuple[dict[str, Any], str]:
    state, result = await prepare(command, properties, client)
    summary = editing.preview_summary(state, result, command)
    summary.pop("sys_id")
    summary.update(
        type=properties["type"],
        creation="complete_definition",
        upstream_writes=["create_properties", "save_complete_definition"],
        atomic=False,
    )
    return summary, editing.plan_revision(state, result)


async def read_revision(client: ServiceNowClient, sys_id: str, scope: str) -> str:
    check_table_access("sys_hub_flow")
    header = await client.flow_record("sys_hub_flow", sys_id, capture.HEADER_FIELDS)
    revision = capture.scalar(header.get("sys_mod_count"))
    if (
        capture.scalar(header.get("sys_id")) != sys_id
        or capture.scalar(header.get("sys_scope")) != scope
        or not revision
        or not revision.isascii()
        or not revision.isdecimal()
        or not capture.scalar(header.get("sys_updated_on"))
    ):
        raise CapabilityError("The created flow revision is incomplete.", "UNAVAILABLE")
    return revision


async def apply(
    command: FlowCommand,
    properties: dict[str, Any],
    expected: str | None,
    client: ServiceNowClient,
    binding_context: dict[str, Any] | None,
) -> dict[str, Any]:
    # Validate the entire plan and all readable dependencies before the first write.
    state, planned = await prepare(command, properties, client)
    if editing.plan_revision(state, planned) != expected:
        raise CapabilityError(
            "Creation dependencies changed. Create a new preview.", "STALE_PREVIEW"
        )
    await capture.require_current(command, client, binding_context)
    sys_id = None
    header_state, definition_state, phase = "unknown", "not_sent", "create_properties"
    try:
        echo = await semantics.save_draft(client, properties, create=True)
        validate_sys_id(echo.get("id", ""))
        sys_id = echo["id"]
        header_state = "returned"
        phase = "verify_properties"
        created = await semantics.verify_created(client, properties, echo)
        editing.require_editable(created, sys_id)
        if any(
            created[key]
            for key in ("inputs", "outputs", "flowVariables", "stages", "label_cache")
        ):
            raise CapabilityError(
                "The new header contains unexpected authored content."
            )
        header_state = "verified"
        # Each upstream write has its own revision and Customer Update verification.
        # Never guess that header creation starts at revision zero or one.
        phase = "verify_header_capture"
        await capture.verify_after(command, client, sys_id, binding_context)
        phase = "read_created_revision"
        revision = await read_revision(client, sys_id, properties["scope"])
        edit_command = FlowCommand(
            action="edit",
            sys_id=sys_id,
            edits=command.edits,
            expected_scope=command.expected_scope,
            expected_update_set=command.expected_update_set,
            expected_revision=revision if command.expected_scope else None,
        )
        phase = "bind_created_revision"
        bound = await capture.read_binding(edit_command, client)
        phase = "prepare_complete_definition"
        if fingerprint(await client.studio_request(f"flow/{sys_id}")) != fingerprint(
            created
        ):
            raise CapabilityError(
                "The created definition changed before the complete save."
            )
        actual_state = await editing.load_state(command, client, initial_flow=created)
        actual_plan = await editing.plan(command, actual_state, client)
        if actual_plan["reference_revision"] != planned[
            "reference_revision"
        ] or fingerprint(
            [actual_state["logic"], actual_state["triggers"]]
        ) != fingerprint([state["logic"], state["triggers"]]):
            raise CapabilityError("Dependencies changed after header creation.")
        await capture.require_current(edit_command, client, bound)
        if await read_revision(client, sys_id, properties["scope"]) != revision:
            raise CapabilityError(
                "The created header changed before the complete save."
            )
        phase, definition_state = "save_complete_definition", "unknown"
        try:
            result = await editing.save_plan(
                edit_command, actual_plan, client, binding_context=bound
            )
        except CapabilityError as exc:
            if exc.code == "STALE_PREVIEW":
                definition_state = "not_sent"
            raise
        definition_state = "verified"
        phase = "verify_capture"
        result["capture"] = await capture.verify_after(
            edit_command, client, sys_id, bound
        )
        result["revision"] = await read_revision(client, sys_id, properties["scope"])
        if bound and result["revision"] != result["capture"]["revision"]:
            raise CapabilityError("The flow changed after capture verification.")
        result.update(
            action="create", type=properties["type"], creation="complete_definition"
        )
        return result
    except Exception as exc:  # noqa: BLE001 - preserve progress after an irreversible write
        # Do not include upstream exceptions, inputs, payloads, or authorization data.
        detail = (
            str(exc)
            if isinstance(exc, CapabilityError)
            and str(exc).startswith("Independent saved-flow verification failed (")
            else ""
        )
        raise CapabilityError(
            f"Complete creation stopped during {phase}. "
            + (
                f"Created flow sys_id={sys_id}. "
                if sys_id
                else "The created flow ID is unknown. "
            )
            + "Inspect the flow and Customer Updates before a new edit preview. Do not repeat creation. "
            + detail,
            "UNKNOWN_OUTCOME",
            outcome={
                "sys_id": sys_id,
                "phase": phase,
                "header": header_state,
                "definition": definition_state,
                "retry_safe": False,
                "recovery": "inspect_existing_flow_before_edit"
                if sys_id
                else "find_created_flow_before_any_write",
            },
        ) from None
