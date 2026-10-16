"""Saved subflow calls are omitted from the complete draft, never deleted as definitions."""

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import editing, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.mcp.inputs import NativeMCPServer
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response, register_mutations
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_lifecycle import capture_routes

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


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


@pytest.mark.parametrize(
    "defect", [None, "retained", "wrong_call", "snapshot", "other_input", "opaque"]
)
async def test_saved_subflow_deletion_native_wire_and_independent_readback(
    settings: Settings, defect
):
    flow = two_calls()
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        assert [call["id"] for call in payload["subFlowInstances"]] == WIRE["after_ids"]
        remaining = payload["subFlowInstances"][0]
        original = before["subFlowInstances"][1]
        assert remaining["order"] == 4
        for key in (
            "id",
            "uiUniqueIdentifier",
            "subflowSysId",
            "subFlow",
            "inputs",
            "waitForCompletion",
        ):
            assert remaining[key] == original[key]
        assert all(not key.lower().startswith("deletedsub") for key in payload)
        assert payload["deletedActions"] == payload["deletedFlowLogicInstances"] == []
        assert "deleted" not in remaining
        actual = copy.deepcopy(payload)
        if defect == "retained":
            actual = copy.deepcopy(before)
        elif defect == "wrong_call":
            actual["subFlowInstances"][0] = copy.deepcopy(before["subFlowInstances"][0])
        elif defect == "snapshot":
            actual["subFlowInstances"][0]["subflowSysId"] = "9" * 32
            actual["subFlowInstances"][0]["subFlow"]["id"] = "9" * 32
        elif defect == "other_input":
            actual["subFlowInstances"][0]["inputs"][0]["value"] = "different"
        elif defect == "opaque":
            actual["vendorSetting"] = {"preserve": False}
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    settings.mcp_tool_package = "flow,flow_write"
    server = NativeMCPServer("subflow-deletion")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            preview = await session.call_tool(
                "flow_preview",
                {
                    "action": "edit",
                    "sys_id": FLOW,
                    "edits": [{"op": "delete_step", "step": CALL}],
                },
            )
            assert not preview.is_error, preview
            await session.validate_tool_result("flow_preview", preview)
            summary = preview.structured_content["data"]["summary"]
            assert summary["changes"] == [f"1. Delete step {CALL}"]
            assert summary["deleted_instances"]["subFlowInstances"] == [
                {"id": CALL, "uiUniqueIdentifier": "call-one"},
            ]
            assert (
                summary["deletion_methods"]["subFlowInstances"]
                == "omit_from_complete_list"
            )
            assert len(summary["before"]) - len(summary["after"]) == 1
            assert not write.called
            assert all(call.request.method == "GET" for call in http_mock.calls)
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert applied.is_error is bool(defect)
            if defect:
                assert applied.structured_content["error"]["code"] == "UNKNOWN_OUTCOME"
            assert write.call_count == 1
            assert http_mock.calls[-1].request.method == "GET"
            assert (
                http_mock.calls[-1].request.url.path
                == f"/api/now/processflow/flow/{FLOW}"
            )
            assert {call.request.method for call in http_mock.calls} <= {"GET", "PUT"}
            assert not any(
                "snapshot" in call.request.url.path for call in http_mock.calls
            )
            assert flow == before
            assert saved["inputs"] == before["inputs"]
            assert saved["outputs"] == before["outputs"]
            assert saved["flowVariables"] == before["flowVariables"]
            assert saved["vendorSetting"] == before["vendorSetting"]
        tasks.cancel_scope.cancel()


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


@pytest.mark.parametrize("defect", ["definition_changed", "context_changed", "policy"])
async def test_saved_subflow_delete_apply_revalidates_bound_state(
    settings, defect, monkeypatch
):
    flow = two_calls()
    coordinator, tools = fixtures(settings, flow)
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_update_set=update_set,
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
    elif defect == "context_changed":
        http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
            200,
            json={
                "result": {
                    "currentApplication": {"sysId": "global"},
                    "currentUpdateSet": {"sysId": "5" * 32},
                }
            },
        )
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
