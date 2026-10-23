"""Evidence-bound saves of active root drafts. No lifecycle effect endpoints.

The 2026-10-22 development capture used this exact Studio source with legacy
save enabled. Its trigger plan selected a JSON snapshot. Main-snapshot GET
representations changed during saving and are not executable-content evidence.
"""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import payloads, target
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

SOURCE_SHA256 = "797961b92db2caf0a1b924fe93a0c49c944b9f14a743426237cd0ace77201fc6"
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


def eligible(flow: dict[str, Any]) -> bool:
    """Recognize only the captured saved active-draft lifecycle profile."""
    expected = {
        "active": True,
        "type": "flow",
        "status": "draft",
        "isPublished": False,
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
    if not isinstance(flow.get("version"), str) or not flow["version"]:
        return False
    if flow.get("parentFlow"):
        return False
    try:
        for key in REFERENCES:
            if not isinstance(flow.get(key), str):
                return False
            validate_sys_id(flow[key])
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
) -> dict[str, Any]:
    """Read lifecycle and executable-version evidence, without acquiring edit access."""
    if not command.expected_scope or command.expected_revision is None:
        raise CapabilityError(
            "Active-draft edits require expected_scope and expected_revision."
        )
    _require(
        eligible(flow),
        "The active flow does not match the supported saved-draft profile.",
    )
    for table in (
        "sys_hub_flow",
        "sys_hub_flow_snapshot",
        "sys_flow_trigger_plan",
        "sys_flow_trigger",
        "sys_properties",
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
        "active": "true",
        "status": "draft",
        "type": "flow",
        "version": flow["version"],
        "master_snapshot": flow["masterSnapshotId"],
        "latest_snapshot": flow["latestSnapshot"],
        "remote_trigger_id": flow["remoteTriggerSysId"],
    }
    _require(
        all(target.scalar(header.get(k)) == v for k, v in expected_header.items()),
        "The Studio lifecycle and active-draft header disagree.",
    )
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
    source = await client.studio_source_digest()
    _require(
        source == SOURCE_SHA256,
        "The installed Studio source does not match the verified active-draft save profile.",
    )
    plans = (
        await client.query_records(
            "sys_flow_trigger_plan",
            f"trigger={flow['remoteTriggerSysId']}",
            limit=2,
        )
    )["records"]
    _require(
        len(plans) == 1, "The active flow requires one readable runtime trigger plan."
    )
    plan = plans[0]
    validate_sys_id(target.scalar(plan.get("sys_id")) or "")
    _record(plan, target.scalar(plan.get("sys_id")) or "")
    selected = target.scalar(plan.get("snapshot")) or ""
    validate_sys_id(selected)
    _require(
        target.scalar(plan.get("plan_id")) == flow["id"]
        and target.scalar(plan.get("trigger")) == flow["remoteTriggerSysId"]
        and selected in {flow["latestSnapshot"], flow["masterSnapshotId"]}
        and all(target.scalar(plan.get(k)) for k in ("plan", "binding_strategy")),
        "The trigger plan does not establish this root's executable snapshot.",
    )
    trigger = await client.get_record("sys_flow_trigger", flow["remoteTriggerSysId"])
    _record(trigger, flow["remoteTriggerSysId"])
    _require(
        target.scalar(trigger.get("active")) == "true"
        and bool(target.scalar(trigger.get("sys_class_name"))),
        "The bound runtime trigger is not readable and active.",
    )
    snapshot = await client.studio_request(f"flow/{selected}")
    payloads.validate_definition(snapshot, field_name="runtime_snapshot")
    _require(
        snapshot.get("id") == selected
        and snapshot.get("parentFlow") == flow["id"]
        and snapshot.get("type") == "flow"
        and snapshot.get("snapshot") is True
        and snapshot.get("jsonSnapshot") is True
        and snapshot.get("isPublished") is True
        and snapshot.get("status") == "published"
        and snapshot.get("active") is True
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
        "property": properties[0],
        "source": source,
        "plan": plan,
        "trigger": trigger,
        "snapshot": snapshot,
        "main": main,
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
