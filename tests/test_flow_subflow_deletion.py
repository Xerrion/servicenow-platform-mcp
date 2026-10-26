"""Saved subflow calls are omitted from the complete draft, never deleted as definitions."""

import copy
import json
from pathlib import Path

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import editing, semantics, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_children_readback import saved_branches
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist
from tests.test_flow_lifecycle import capture_routes

WIRE = json.loads(
    (Path(__file__).parent / "fixtures/flow_saved_subflow_deletion.json").read_text()
)
CALL = WIRE["before"][0]["id"]
OTHER = WIRE["before"][1]["id"]


def two_calls():
    flow = base_flow()
    flow["subFlowInstances"] = copy.deepcopy(WIRE["before"])
    for call in flow["subFlowInstances"]:
        call["subFlow"] = {
            "id": call["subflowSysId"],
            "userCanRead": True,
            "inputs": [{"name": "action", "type": "choice"}],
            "outputs": [{"name": "result", "type": "string", "children": []}],
        }
    flow["deletedActions"] = []
    flow["deletedFlowLogicInstances"] = []
    flow["vendorSetting"] = {"preserve": True}
    return flow


async def test_saved_subflow_omission_serializer(settings):
    command = FlowCommand(
        action="edit", sys_id=FLOW, edits=[{"op": "delete_step", "step": CALL}]
    )
    flow = two_calls()
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            command, {"flow": flow, "logic": [], "triggers": []}, client
        )
    assert [row["id"] for row in result["payload"]["subFlowInstances"]] == WIRE[
        "after_ids"
    ]
    assert (
        result["payload"]["deletedActions"]
        == result["payload"]["deletedFlowLogicInstances"]
        == []
    )
    assert "componentInstances" not in result["payload"]


def mirrored_branch_calls():
    """Synthetic untrimmed Studio projection, not a historical request or echo.

    removeActionFromFlow updates componentInstances (bundle:205202-205236).
    getUpdatedFlowToSaveOrPublish spreads that state (99104), splits its survivors
    (99193-99197), then saveFlowToServer uses cleanupFlowBeforePut (97521-97527).
    With TRIM_FLOW_PAYLOAD false, cleanup retains the updated mirror (101697-101712).
    """
    flow = saved_branches()
    flow["subFlowInstances"] = copy.deepcopy(two_calls()["subFlowInstances"])
    for order, call in enumerate(flow["subFlowInstances"], start=6):
        call["order"] = order
    for identity, logic in zip(("8", "9"), flow["flowLogicInstances"], strict=True):
        logic["id"] = identity * 32
    definition = editing.trigger_catalog(DEFS["trigger_categories"])["service_catalog"]
    flow["triggerInstances"] = [editing.new_trigger(flow, definition, {})]
    flow["flowLogicInstances"][0]["inputs"] = [
        {
            "name": "condition",
            "value": "{{Service Catalog_1.request_item.state}}=1",
            "parameter": {"type": "conditions"},
        }
    ]
    flow["aliasOverride"] = {"aliasOverrideMappings": []}
    flow["alerts"] = [{"message": "private-synthetic-alert"}]
    flow["deletedActions"] = []
    flow["deletedFlowLogicInstances"] = []
    flow["deletedTriggers"] = []
    flow["vendorSetting"] = {"opaque": ["preserve"]}
    flow = tree.flatten(flow, tree.build_tree(flow))
    flow["componentInstances"] = copy.deepcopy(
        sorted(
            [row for key in tree.COLLECTIONS.values() for row in flow[key]],
            key=lambda row: int(row["order"]),
        )
    )
    return flow


@pytest.mark.parametrize("delete_both", [False, True])
async def test_subflow_deletion_refreshes_only_proven_complete_component_mirror(
    settings, delete_both
):
    flow = mirrored_branch_calls()
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    removed = {CALL, OTHER} if delete_both else {CALL}
    command = FlowCommand(
        action="edit",
        sys_id=FLOW,
        edits=[
            {"op": "delete_step", "step": call, "children": "reject"}
            for call in (CALL, OTHER)
            if call in removed
        ],
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            command, {"flow": flow, "logic": [], "triggers": []}, client
        )
    payload = result["payload"]
    # The untrimmed native save must describe the same surviving instances twice.
    expected = copy.deepcopy(
        [row for row in before["componentInstances"] if row["id"] not in removed]
    )
    for order, row in enumerate(expected, start=1):
        row["order"] = order
    assert payload["componentInstances"] == expected
    assert len(payload["componentInstances"]) == 7 - len(removed)
    assert payload["componentInstances"] == sorted(
        [row for key in tree.COLLECTIONS.values() for row in payload[key]],
        key=lambda row: int(row["order"]),
    )
    for key in (
        "actionInstances",
        "flowLogicInstances",
        "triggerInstances",
        "inputs",
        "outputs",
        "flowVariables",
        "stages",
        "aliasOverride",
        "alerts",
        "deletedActions",
        "deletedFlowLogicInstances",
        "deletedTriggers",
        "vendorSetting",
    ):
        assert payload[key] == before[key], key
    assert [entry["name"] for entry in payload["label_cache"]] == [
        "Service Catalog_1.request_item.state"
    ]
    assert len(payload["actionInstances"]) == 3
    assert len(payload["flowLogicInstances"]) == 2
    assert len(payload["triggerInstances"]) == 1
    assert all(row["id"] not in removed for row in payload["subFlowInstances"])
    if not delete_both:
        remaining = copy.deepcopy(before["subFlowInstances"][1])
        remaining["order"] = 6
        assert payload["subFlowInstances"] == [remaining]
    assert flow == before
    semantics.compare_saved(
        payload,
        copy.deepcopy(payload),
        generated_ids=set(),
        omitted_subflows=result["omitted_subflows"],
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect", ["missing", "reordered", "opaque", "value", "type", "duplicate", "null"]
)
async def test_subflow_deletion_never_discards_unproven_mirror_content(
    settings, defect
):
    flow = mirrored_branch_calls()
    mirror = flow["componentInstances"]
    if defect == "missing":
        mirror.pop()
    elif defect == "reordered":
        mirror.reverse()
    elif defect == "opaque":
        mirror[0]["private-synthetic-key"] = "private-synthetic-value"
    elif defect == "value":
        mirror[0]["inputs"][0]["value"] = "private-synthetic-value"
    elif defect == "type":
        mirror[0]["order"] = True
    elif defect == "duplicate":
        mirror[-1] = copy.deepcopy(mirror[-2])
    else:
        flow["componentInstances"] = None
    before = copy.deepcopy(flow)
    command = FlowCommand(
        action="edit", sys_id=FLOW, edits=[{"op": "delete_step", "step": CALL}]
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await editing.plan(
                command, {"flow": flow, "logic": [], "triggers": []}, client
            )
    assert error.value.code == "UNAVAILABLE"
    assert "componentInstances mirror differs" in str(error.value)
    assert "private-synthetic" not in str(error.value)
    assert flow == before
    assert not http_mock.calls


@pytest.mark.parametrize(
    "defect", ["retained_mirror", "snapshot", "input", "opaque", "alerts"]
)
async def test_corrected_mirror_does_not_normalize_saved_differences(settings, defect):
    flow = mirrored_branch_calls()
    fixtures(settings, flow)
    command = FlowCommand(
        action="edit", sys_id=FLOW, edits=[{"op": "delete_step", "step": CALL}]
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        planned = await editing.plan(
            command, {"flow": flow, "logic": [], "triggers": []}, client
        )
    payload = planned["payload"]
    actual = copy.deepcopy(payload)
    if defect == "retained_mirror":
        actual["componentInstances"] = copy.deepcopy(flow["componentInstances"])
    elif defect == "snapshot":
        actual["subFlowInstances"][0]["subflowSysId"] = "e" * 32
        actual["subFlowInstances"][0]["subFlow"]["id"] = "e" * 32
    elif defect == "input":
        actual["actionInstances"][0]["inputs"][0]["value"] = "private-synthetic-value"
    elif defect == "alerts":
        actual["alerts"] = []
    else:
        actual["vendorSetting"]["opaque"] = []
    with pytest.raises(semantics.SemanticMismatch) as error:
        semantics.compare_saved(
            payload,
            actual,
            generated_ids=set(),
            omitted_subflows=planned["omitted_subflows"],
        )
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "private-synthetic" not in str(error.value)
    if defect == "retained_mirror":
        assert error.value.path == "flow.componentInstances"
        assert "expected_count=6 actual_count=7" in error.value.detail()


async def test_native_saved_subflow_deletion_sends_current_mirror_once(settings):
    flow = mirrored_branch_calls()
    before = copy.deepcopy(flow)
    _, tools = fixtures(settings, flow)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        assert len(payload["componentInstances"]) == 6
        assert [row["id"] for row in payload["subFlowInstances"]] == [OTHER]
        assert not any(row["id"] == CALL for row in payload["componentInstances"])
        saved.update(copy.deepcopy(payload))
        persist(payload)
        return httpx2.Response(200, json={"result": {"data": payload}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[{"op": "delete_step", "step": CALL}]
        )
    )
    assert preview["status"] == "success", preview
    assert not route.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert route.call_count == 1
    assert http_mock.calls[-1].request.method == "GET"
    assert http_mock.calls[-1].request.url.path == f"/api/now/processflow/flow/{FLOW}"
    assert not any("snapshot" in call.request.url.path for call in http_mock.calls)
    assert flow == before
    assert (
        saved["subFlowInstances"][0]["subFlow"]
        == before["subFlowInstances"][1]["subFlow"]
    )


async def test_mirror_correction_never_clears_a_dangling_input(settings):
    flow = mirrored_branch_calls()
    flow["subFlowInstances"][1]["inputs"][0].update(
        value="{{call-one.result}}", parameter={"type": "string"}
    )
    flow["componentInstances"][-1] = copy.deepcopy(flow["subFlowInstances"][1])
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    command = FlowCommand(
        action="edit", sys_id=FLOW, edits=[{"op": "delete_step", "step": CALL}]
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError):
            await editing.plan(
                command, {"flow": flow, "logic": [], "triggers": []}, client
            )
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "consumer",
    [
        "inputs",
        "dynamicInputs",
        "workflowInputs",
        "decisionTableInputs",
        "outputsToAssign",
    ],
)
async def test_saved_subflow_deletion_rejects_dangling_surviving_outputs(
    settings, consumer
):
    flow = two_calls()
    flow["actionInstances"][2]["order"] = 6
    flow["actionInstances"][2][consumer] = [
        {
            "name": "message",
            "value": "{{call-one.result}}",
            "parameter": {"type": "string"},
        },
    ]
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "delete_step", "step": CALL}],
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect", ["duplicate_ui", "duplicate_id", "missing_collection", "alias_overrides"]
)
async def test_saved_subflow_deletion_fails_closed_on_ambiguous_state(settings, defect):
    flow = two_calls()
    if defect == "duplicate_ui":
        flow["subFlowInstances"][1]["uiUniqueIdentifier"] = "call-one"
    elif defect == "duplicate_id":
        flow["subFlowInstances"][1]["id"] = CALL
    elif defect == "missing_collection":
        flow.pop("subFlowInstances")
    else:
        flow["aliasOverride"] = {
            "aliasOverrideMappings": [{"sourceAlias": "configured"}]
        }
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "delete_step", "step": CALL}],
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "kind,disposition", [("IF", "delete"), ("IF", "promote"), ("TRY", "delete")]
)
async def test_explicit_connected_cascade_handles_saved_child_calls(
    settings, kind, disposition
):
    flow = two_calls()
    first = copy.deepcopy(next(d for d in family_definitions() if d["type"] == kind))
    companion_kind = "ELSE" if kind == "IF" else "CATCH"
    companion = copy.deepcopy(
        next(d for d in family_definitions() if d["type"] == companion_kind)
    )
    roots = tree.build_tree(flow)
    parent = tree.new_logic_step(
        FLOW, first, {"condition": "true"} if kind == "IF" else {}
    )
    parent.raw["id"] = "7" * 32
    pair = tree.new_logic_step(FLOW, companion, {})
    pair.raw["id"] = "8" * 32
    if kind == "TRY":
        pair.raw["connectedTo"] = parent.uid
    # Put only the selected call under the first block. The second call remains at root.
    call = roots.pop(3)
    parent.children.append(call)
    roots[1:1] = [parent, pair]
    flow = tree.flatten(flow, roots)
    fixtures(settings, flow)
    edits = [{"op": "delete_step", "step": parent.raw["id"], "children": disposition}]
    if disposition == "delete":
        edits[0]["cascade"] = "connected"
    else:
        # No connected sibling when explicitly promoting a single block's children.
        roots.remove(pair)
        flow = tree.flatten(flow, roots)
    command = FlowCommand(action="edit", sys_id=FLOW, edits=edits)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        planned = await editing.plan(
            command, {"flow": flow, "logic": [], "triggers": []}, client
        )
    if disposition == "delete":
        assert planned["omitted_subflows"] == [
            {"id": CALL, "uiUniqueIdentifier": "call-one"}
        ]
        assert [c["id"] for c in planned["payload"]["subFlowInstances"]] == [OTHER]
        assert {
            r["id"] for r in planned["pending_deletions"]["deletedFlowLogicInstances"]
        } == {"7" * 32, "8" * 32}
    else:
        assert planned["omitted_subflows"] == []
        assert {c["id"] for c in planned["payload"]["subFlowInstances"]} == {
            CALL,
            OTHER,
        }
        assert all(not c["parent"] for c in planned["payload"]["subFlowInstances"])


async def test_saved_subflow_consumer_cache_is_rebuilt_for_only_survivors(settings):
    flow = two_calls()
    call = flow["subFlowInstances"][0]
    call["inputs"][0].update(
        value="{{flow_variable.operation}}", displayValue="old display"
    )
    flow["flowVariables"] = [{"name": "operation", "type": "string", "value": "KEEP"}]
    call["inputs"][0]["parameter"]["type"] = "string"
    flow["label_cache"] = [
        {"name": "flow_variable.operation", "usedInstances": {"call-one": ["action"]}}
    ]
    before = copy.deepcopy(flow)
    _, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "delete_step", "step": CALL}],
        )
    )
    assert result["status"] == "success", result
    assert result["data"]["summary"]["data_pills"] == []
    assert flow == before


@pytest.mark.parametrize("defect", ["definition_changed", "scope_changed", "policy"])
async def test_saved_subflow_delete_apply_revalidates_bound_state(
    settings, defect, monkeypatch
):
    flow = two_calls()
    coordinator, tools = fixtures(settings, flow)
    capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            edits=[{"op": "delete_step", "step": CALL}],
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["context"]["state"] == "bound"
    if defect == "definition_changed":
        changed = copy.deepcopy(flow)
        changed["subFlowInstances"][1]["subFlow"]["outputs"][0]["type"] = "integer"
        http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
            200, json={"result": {"data": changed}}
        )
    elif defect == "scope_changed":
        capture_routes(scope="5" * 32)
    else:
        from servicenow_mcp import policy

        monkeypatch.setattr(
            policy,
            "DENIED_TABLES",
            policy.DENIED_TABLES | {"sys_hub_sub_flow_instance_v2"},
        )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "error"
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert len(coordinator.store) == 0
