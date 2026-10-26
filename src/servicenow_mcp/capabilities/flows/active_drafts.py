"""Evidence-bound working drafts of versioned owners. No lifecycle effects.

The native 27800/engine-2 profile supports published-to-draft saves. Runtime
selection comes from an active trigger plan or the callable master reference.
JSON runtimes compare in full. Native getter facades use version-owned Table
API content instead. Tests can create unrelated published non-master versions.
"""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import compile_verification, payloads, target
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

NATIVE_RELEASE = 27800
REFERENCES = ("masterSnapshotId", "latestSnapshot", "remoteTriggerSysId")
LIFECYCLE = (
    "id",
    "scope",
    "type",
    "active",
    "status",
    "isPublished",
    "snapshot",
    "jsonSnapshot",
    "masterSnapshot",
    *REFERENCES,
    "flowEngineVersion",
    "version",
)
HEADER_FIELDS = [
    *target.HEADER_FIELDS,
    "active",
    "status",
    "type",
    "version",
    "master_snapshot",
    "latest_snapshot",
    "remote_trigger_id",
]
MAIN_FIELDS = [
    "sys_id",
    "parent_flow",
    "master",
    "active",
    "status",
    "version",
    "sys_mod_count",
    "sys_updated_on",
    "sys_scope",
]
RUNTIME_TABLES = {
    "actionInstances": ("sys_hub_action_instance_v2", "flow"),
    "flowLogicInstances": ("sys_hub_flow_logic_instance_v2", "flow"),
    "subFlowInstances": ("sys_hub_sub_flow_instance_v2", "flow"),
    "triggerInstances": ("sys_hub_trigger_instance_v2", "flow"),
    "inputs": ("sys_hub_flow_input", "model"),
    "outputs": ("sys_hub_flow_output", "model"),
    "flowVariables": ("sys_hub_flow_variable", "model"),
    "stages": ("sys_hub_flow_stage", "flow"),
}
# Native 27800 dictionary fields, not the UI getter's projected settings. A
# populated field hidden by ACL cannot silently disappear from preservation.
COMPONENT_REQUIRED = {
    "order",
    "parent_ui_id",
    "ui_id",
    "attributes",
    "comment",
    "display_text",
    "generation_source",
}
RUNTIME_REQUIRED = {
    "actionInstances": COMPONENT_REQUIRED
    | {
        "action_type",
        "action_type_parent",
        "compiled_snapshot",
        "values",
    },
    "flowLogicInstances": COMPONENT_REQUIRED
    | {
        "logic_definition",
        "values",
        "flow_variables_assigned",
        "outputs_assigned",
        "block",
        "connected_to",
        "workflow_reference",
        "decision_table",
    },
    "subFlowInstances": COMPONENT_REQUIRED
    | {
        "subflow",
        "subflow_inputs",
        "wait_for_completion",
    },
    "triggerInstances": {
        "trigger_type",
        "trigger_inputs",
        "trigger_outputs",
        "trigger_definition",
        "name",
        "remote_sys_id",
        "published_version",
        "comment",
        "display_text",
        "generation_source",
    },
    "inputs": {"element", "internal_type", "mandatory", "default_value", "attributes"},
    "outputs": {"element", "internal_type", "attributes"},
    "flowVariables": {"element", "internal_type", "default_value", "attributes"},
    "stages": {"label", "value", "order", "component_indexes"},
}


async def persisted_content(identity: str, client: ServiceNowClient) -> dict[str, Any]:
    """Read the selected native version's persisted content, never its GET facade.

    Native master GETs can expose owner/editor fields. The version-owned V2
    instances and declarations retain the published configuration during saves.
    Complete records keep unknown settings strict. A full page fails closed.
    """
    result: dict[str, Any] = {}
    for section, (table, owner) in RUNTIME_TABLES.items():
        check_table_access(table)
        rows = (
            await client.query_records(
                table, f"{owner}={identity}^ORDERBYsys_id", limit=1000
            )
        )["records"]
        _require(
            len(rows) < 1000,
            "Published version content exceeds the preservation read bound.",
        )
        _require(
            len({target.scalar(row.get("sys_id")) for row in rows}) == len(rows),
            "Published version content has duplicate record identities.",
        )
        for row in rows:
            row_id = target.scalar(row.get("sys_id")) or ""
            validate_sys_id(row_id)
            _record(row, row_id)
            _require(
                RUNTIME_REQUIRED[section] <= row.keys(),
                "Published content is missing configuration fields. Check field read access.",
            )
            _require(
                target.scalar(row.get(owner)) == identity,
                "Published content has an unrelated owner.",
            )
        result[section] = sorted(
            rows, key=lambda row: target.scalar(row["sys_id"]) or ""
        )
    _require(
        bool(
            result["actionInstances"]
            or result["flowLogicInstances"]
            or result["subFlowInstances"]
        ),
        "The published version has no readable executable content.",
    )
    # Blocks can be shared with the editor owner. Read only exact independently
    # referenced identities, without inventing a model=<block> variable owner.
    # A shared block changing during a draft save remains fail-closed.
    blocks = []
    block_ids = sorted(
        {target.scalar(row.get("block")) or "" for row in result["flowLogicInstances"]}
        - {""}
    )
    for block_id in block_ids:
        validate_sys_id(block_id)
        check_table_access("sys_hub_flow_block")
        block = await client.get_record("sys_hub_flow_block", block_id)
        _record(block, block_id)
        _require(
            {"name", "label_cache", "sys_class_name"} <= block.keys()
            and target.scalar(block.get("sys_class_name")) == "sys_hub_flow_block",
            "The referenced published block content is incomplete or unsupported.",
        )
        blocks.append(block)
    result["blocks"] = blocks
    return result


def eligible(flow: dict[str, Any]) -> bool:
    """Recognize the captured versioned, editable native owner profile."""
    expected = {
        "snapshot": False,
        "jsonSnapshot": False,
        "masterSnapshot": False,
        "flowEngineVersion": 2,
    }
    if any(
        type(flow.get(k)) is not type(v) or flow.get(k) != v
        for k, v in expected.items()
    ):
        return False
    if type(flow.get("active")) is not bool or flow.get("type") not in {
        "flow",
        "subflow",
    }:
        return False
    if type(flow.get("isPublished")) is not bool or (
        flow.get("status"),
        flow.get("isPublished"),
    ) not in {
        ("draft", False),
        ("published", True),
    }:
        return False
    if not isinstance(flow.get("version"), str) or not flow["version"]:
        return False
    if (
        type(flow.get("authoredOnReleaseVersion")) is not int
        or flow["authoredOnReleaseVersion"] != NATIVE_RELEASE
    ):
        return False
    if flow.get("parentFlow"):
        return False
    try:
        for key in ("masterSnapshotId", "latestSnapshot"):
            if not isinstance(flow.get(key), str):
                return False
            validate_sys_id(flow[key])
        remote = flow.get("remoteTriggerSysId")
        if not isinstance(remote, str):
            return False
        if remote:
            validate_sys_id(remote)
        elif flow["type"] == "flow" and flow["active"]:
            return False
    except ValueError:
        return False
    return True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CapabilityError(message, "UNAVAILABLE")


def _record(row: dict[str, Any], identity: str) -> None:
    revision = target.scalar(row.get("sys_mod_count"))
    _require(
        target.scalar(row.get("sys_id")) == identity
        and isinstance(revision, str)
        and revision.isascii()
        and revision.isdecimal()
        and bool(target.scalar(row.get("sys_updated_on"))),
        "Active-draft runtime evidence has no complete identity or revision.",
    )


async def read(
    command: FlowCommand,
    flow: dict[str, Any],
    client: ServiceNowClient,
    *,
    revision: str | None = None,
    require_save_profile: bool = True,
) -> dict[str, Any]:
    """Read lifecycle and executable-version evidence, without acquiring edit access."""
    if not command.expected_scope or command.expected_revision is None:
        raise CapabilityError(
            "Versioned-owner checks require expected_scope and expected_revision."
        )
    _require(
        eligible(flow),
        "The owner does not match the supported native versioned-draft profile.",
    )
    for table in (
        "sys_hub_flow",
        "sys_hub_flow_snapshot",
        "sys_flow_trigger_plan",
        "sys_flow_trigger",
        *(["sys_properties"] if require_save_profile else []),
    ):
        check_table_access(table)
    header = await client.flow_record("sys_hub_flow", flow["id"], HEADER_FIELDS)
    _record(header, flow["id"])
    if target.scalar(header.get("sys_mod_count")) != (
        revision or command.expected_revision
    ):
        raise CapabilityError(
            "The active-draft target revision changed.", "STALE_PREVIEW"
        )
    if (
        flow.get("scope") != command.expected_scope
        or target.scalar(header.get("sys_scope")) != command.expected_scope
    ):
        raise CapabilityError(
            "The active-draft target scope changed.", "CONTEXT_MISMATCH"
        )
    expected_header = {
        "active": str(flow["active"]).lower(),
        "status": flow["status"],
        "type": flow["type"],
        "version": flow["version"],
        "master_snapshot": flow["masterSnapshotId"],
        "latest_snapshot": flow["latestSnapshot"],
        "remote_trigger_id": flow.get("remoteTriggerSysId", ""),
    }
    _require(
        all(target.scalar(header.get(k)) == v for k, v in expected_header.items()),
        "The Studio lifecycle and active-draft header disagree.",
    )
    properties = []
    if require_save_profile:
        properties = (
            await client.query_records(
                "sys_properties",
                "name=sn_flow_designer.save_as_you_go_enabled",
                fields=["sys_id", "name", "value", "sys_mod_count", "sys_updated_on"],
                limit=2,
            )
        )["records"]
        _require(
            len(properties) == 1
            and properties[0].get("name") == "sn_flow_designer.save_as_you_go_enabled"
            and properties[0].get("value") == "false",
            "Active-draft saves require explicitly disabled save-as-you-go. No edit lock is acquired.",
        )
        validate_sys_id(properties[0].get("sys_id", ""))
        _record(properties[0], properties[0]["sys_id"])
    # Bind the native DTO's release/engine contract, not a fetched UI asset.
    # Runtime safety is established independently by the selected JSON snapshot
    # or persisted native version, trigger plan and published owner records.
    source = {
        "native_release": flow["authoredOnReleaseVersion"],
        "engine": flow["flowEngineVersion"],
    }
    plan = trigger = None
    snapshot_record = None
    serialized_runtime = None
    if flow["type"] == "flow" and flow["active"]:
        plans = (
            await client.query_records(
                "sys_flow_trigger_plan",
                f"trigger={flow['remoteTriggerSysId']}",
                limit=2,
            )
        )["records"]
        _require(
            len(plans) == 1,
            "The active flow requires one readable runtime trigger plan.",
        )
        plan = plans[0]
        validate_sys_id(target.scalar(plan.get("sys_id")) or "")
        _record(plan, target.scalar(plan.get("sys_id")) or "")
        selected = target.scalar(plan.get("snapshot")) or ""
        _require(
            target.scalar(plan.get("plan_id")) == flow["id"]
            and target.scalar(plan.get("trigger")) == flow["remoteTriggerSysId"]
            and isinstance(target.scalar(plan.get("plan")), str)
            and bool(target.scalar(plan.get("binding_strategy"))),
            "The trigger plan does not establish this root's executable snapshot.",
        )
        trigger = await client.get_record(
            "sys_flow_trigger", flow["remoteTriggerSysId"]
        )
        _record(trigger, flow["remoteTriggerSysId"])
        _require(
            target.scalar(trigger.get("active")) == "true"
            and bool(target.scalar(trigger.get("sys_class_name"))),
            "The bound runtime trigger is not readable and active.",
        )
    else:
        # Tests also create non-master records with status=published and the
        # same version. They are not the current callable version. The root's
        # explicit master reference owns that selection.
        selected = flow["masterSnapshotId"]
    validate_sys_id(selected)
    snapshot = await client.studio_request(f"flow/{selected}")
    payloads.validate_definition(snapshot, field_name="runtime_snapshot")
    _require(
        snapshot.get("id") == selected
        and snapshot.get("parentFlow") == flow["id"]
        and snapshot.get("type") == flow["type"]
        and snapshot.get("snapshot") is True
        and type(snapshot.get("jsonSnapshot")) is bool
        and snapshot.get("version") == flow["version"]
        and snapshot.get("scope") == command.expected_scope
        and type(snapshot.get("flowEngineVersion")) is int
        and snapshot.get("flowEngineVersion") == 2
        and snapshot.get("security", {}).get("can_read") is True
        and not snapshot.get("contentACLFilteredOrders")
        and not snapshot.get("nonCriticalErrors")
        and isinstance(snapshot.get("stages"), list | dict)
        and all(
            isinstance(snapshot.get(k), list)
            for k in (
                "actionInstances",
                "flowLogicInstances",
                "subFlowInstances",
                "triggerInstances",
                "inputs",
                "outputs",
                "flowVariables",
                "label_cache",
            )
        ),
        "The selected executable JSON snapshot is incomplete, unpublished, or unrelated.",
    )
    if snapshot["jsonSnapshot"]:
        _require(
            snapshot.get("isPublished") is True
            and snapshot.get("status") == "published"
            and snapshot.get("active") is True,
            "The selected JSON runtime is not published and active.",
        )
        try:
            serialized_runtime = await compile_verification.serialized_snapshot(
                selected,
                flow["id"],
                client,
            )
            document = serialized_runtime[0]
            _require(
                document.get("version") == snapshot["version"]
                and document.get("type") == snapshot["type"]
                and document.get("scope") == snapshot["scope"],
                "Serialized runtime ownership or version disagrees with its native reader.",
            )
        except Exception:  # noqa: BLE001 - incomplete storage evidence cannot authorize a save
            raise CapabilityError(
                "The selected JSON runtime storage is incomplete or unsupported. No save was sent.",
                "UNAVAILABLE",
            ) from None
    else:
        if snapshot_record is None:
            snapshot_record = await client.get_record("sys_hub_flow_snapshot", selected)
        _record(snapshot_record, selected)
        _require(
            all(
                target.scalar(snapshot_record.get(k)) == v
                for k, v in {
                    "sys_id": selected,
                    "parent_flow": flow["id"],
                    "master": "true"
                    if selected == flow["masterSnapshotId"]
                    else "false",
                    "status": "published",
                    "version": flow["version"],
                    "sys_scope": command.expected_scope,
                }.items()
            ),
            "The immutable published version ownership is incomplete.",
        )
        snapshot = {
            "id": selected,
            "content": await persisted_content(selected, client),
        }
    main = await client.get_record(
        "sys_hub_flow_snapshot", flow["masterSnapshotId"], fields=MAIN_FIELDS
    )
    _record(main, flow["masterSnapshotId"])
    _require(
        target.scalar(main.get("parent_flow")) == flow["id"]
        and target.scalar(main.get("master")) == "true"
        and target.scalar(main.get("active")) == "true"
        and target.scalar(main.get("status")) == "published"
        and target.scalar(main.get("version")) == flow["version"]
        and target.scalar(main.get("sys_scope")) == command.expected_scope,
        "The main snapshot ownership or version metadata is incomplete.",
    )
    if header != await client.flow_record("sys_hub_flow", flow["id"], HEADER_FIELDS):
        raise CapabilityError(
            "The active flow changed during runtime evidence reads.", "STALE_PREVIEW"
        )
    return {
        "header": header,
        "lifecycle": {k: flow[k] for k in LIFECYCLE},
        "property": properties[0] if properties else None,
        "source": source,
        "plan": plan,
        "trigger": trigger,
        "snapshot": snapshot,
        "serialized_runtime": serialized_runtime,
        "main": main,
        "snapshot_record": snapshot_record,
    }


async def require_current(
    command: FlowCommand,
    client: ServiceNowClient,
    flow: dict[str, Any],
    before: dict[str, Any],
) -> None:
    current = await client.studio_request(f"flow/{flow['id']}")
    if fingerprint(current) != fingerprint(flow) or fingerprint(
        await read(command, current, client)
    ) != fingerprint(before):
        raise CapabilityError(
            "The active draft or its executable version changed before saving.",
            "STALE_PREVIEW",
        )


async def verify_after(
    command: FlowCommand,
    client: ServiceNowClient,
    actual: dict[str, Any],
    before: dict[str, Any],
) -> None:
    """Require unchanged runtime records and exactly one target revision increment."""
    try:
        revision = str(
            int(target.scalar(before["header"]["sys_mod_count"]) or "-1") + 1
        )
        after = await read(command, actual, client, revision=revision)
        if (
            before["lifecycle"]["status"] == "published"
            and actual.get("status") == "draft"
            and actual.get("isPublished") is False
        ):
            after["header"]["status"] = before["header"]["status"]
            after["lifecycle"]["status"] = before["lifecycle"]["status"]
            after["lifecycle"]["isPublished"] = before["lifecycle"]["isPublished"]
        for key in ("sys_mod_count", "sys_updated_on"):
            after["header"][key] = before["header"][key]
        _require(
            fingerprint(after) == fingerprint(before),
            "Active-draft runtime evidence changed.",
        )
    except Exception:  # noqa: BLE001 - contain an ambiguous save without retrying
        raise CapabilityError(
            "Active-draft runtime preservation could not be verified. Inspect the flow and runtime version. Do not replay the save.",
            "UNKNOWN_OUTCOME",
            outcome={"runtime_preservation": "unknown", "configuration": "unverified"},
        ) from None
