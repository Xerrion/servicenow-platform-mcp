"""Source-derived standard-stage CRUD, not captured or live save evidence.

createStage: bundle:184105-184139; addNewFlowStage/detach/delete:204322-204398.
Stage indexes refer to component array positions, not flow-logic instances.
canAddStages ancestor exclusions: bundle:1575,16243-16250; sandwich is not excluded.
"""

import copy
import json
from uuid import UUID

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import editing, semantics, stages
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_children_readback import saved_branches
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_lifecycle import capture_routes, status_header

CREATE = {
    "op": "create_stage",
    "label": "Fulfilment",
    "value": "fulfilment",
    "ref": "work",
}


def stage_flow():
    flow = base_flow()
    flow["stages"] = {
        "saved-stage": {
            "stageId": "saved-stage",
            "id": "c" * 32,
            "type": "standard",
            "label": "Requested",
            "value": "requested",
            "order": 0,
            "componentIndexes": [],
            "duration": "1970-01-01 00:00:00",
            "alwaysShow": True,
            "states": dict(stages.STATES),
            "opaque": {"keep": True},
        }
    }
    flow["opaque"] = {"keep": [True, 1, "unchanged"]}
    return flow


async def plan(settings, flow, edits):
    before = copy.deepcopy(flow)
    command = FlowCommand(action="edit", sys_id=FLOW, edits=edits)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            command, {"flow": flow, "logic": [], "triggers": []}, client
        )
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result


async def test_stage_create_attach_update_move_detach_delete(settings):
    flow = stage_flow()
    edits = [
        CREATE,
        {"op": "attach_stage", "stage": "work", "step": "2"},
        {
            "op": "update_stage",
            "stage": "work",
            "always_show": False,
            "duration": "1970-01-02 02:03:04",
            "states": {**stages.STATES, "complete": "Done"},
            "order": 0,
        },
        {"op": "move_step", "step": "2", "before": "1"},
    ]
    planned = await plan(settings, flow, edits)
    repeated = await plan(settings, flow, edits)
    assert planned == repeated
    assert len(planned["created_stages"]) == 1
    key = planned["created_stages"][0]
    assert UUID(key).version == 4
    stage = planned["payload"]["stages"][key]
    assert stage == {
        "stageId": key,
        "label": "Fulfilment",
        "value": "fulfilment",
        "componentIndexes": [0],
        "type": "standard",
        "order": 0,
        "duration": "1970-01-02 02:03:04",
        "alwaysShow": False,
        "states": {**stages.STATES, "complete": "Done"},
    }
    assert planned["payload"]["stages"]["saved-stage"]["order"] == 1
    assert planned["payload"]["stages"]["saved-stage"]["opaque"] == {"keep": True}
    assert planned["payload"]["opaque"] == flow["opaque"]
    removed = await plan(
        settings,
        flow,
        [
            *edits,
            {"op": "detach_stage", "stage": "work", "step": "1"},
            {"op": "delete_stage", "stage": "work"},
        ],
    )
    assert removed["payload"]["stages"] == flow["stages"]
    assert removed["created_stages"] == []


async def test_stage_create_with_empty_legacy_list_and_delete_saved_stage(settings):
    flow = base_flow()
    planned = await plan(settings, flow, [CREATE])
    assert len(planned["payload"]["stages"]) == 1
    assert next(iter(planned["payload"]["stages"].values()))["order"] == 0
    removed = await plan(
        settings, stage_flow(), [{"op": "delete_stage", "stage": "saved-stage"}]
    )
    assert removed["payload"]["stages"] == {}


@pytest.mark.parametrize(
    "edit",
    [
        {**CREATE, "label": "Requested"},
        {**CREATE, "value": "requested"},
        {**CREATE, "label": ""},
        {**CREATE, "value": True},
        {**CREATE, "always_show": "false"},
        {**CREATE, "duration": "2 days"},
        {**CREATE, "duration": "1970-02-30 00:00:00"},
        {**CREATE, "duration": "1969-12-31 00:00:00"},
        {**CREATE, "states": {"complete": "Done"}},
        {**CREATE, "ref": "bad.ref"},
        {"op": "update_stage", "stage": "saved-stage", "order": True},
        {"op": "update_stage", "stage": "saved-stage", "order": 2},
        {"op": "update_stage", "stage": "saved-stage", "type": "error"},
        {"op": "delete_stage", "stage": "missing"},
        {"op": "detach_stage", "stage": "saved-stage", "step": "1"},
    ],
)
async def test_invalid_stage_requests_never_stage(settings, edit):
    coordinator, tools = fixtures(settings, stage_flow())
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        "attached",
        "error_dependency",
        "replace",
        "dangling_step",
        "cross_parent",
        "else",
    ],
)
async def test_stage_destructive_dependencies_are_explicit(settings, defect):
    flow = stage_flow()
    flow["stages"]["saved-stage"]["componentIndexes"] = [0]
    edits = [{"op": "delete_stage", "stage": "saved-stage"}]
    if defect == "error_dependency":
        flow["stages"]["saved-stage"]["componentIndexes"] = []
        flow["stages"]["err"] = {
            "stageId": "err",
            "type": "error",
            "ancestorStageId": "saved-stage",
            "componentIndexes": [],
            "order": 0,
        }
    elif defect == "replace":
        edits = [CREATE, {"op": "attach_stage", "stage": "work", "step": "1"}]
    elif defect == "dangling_step":
        edits = [CREATE, {"op": "delete_step", "step": "1"}]
    elif defect in {"cross_parent", "else"}:
        flow = saved_branches()
        flow["stages"] = stage_flow()["stages"]
        flow["stages"]["saved-stage"]["componentIndexes"] = [0]
        if defect == "else":
            edits = [{"op": "attach_stage", "stage": "saved-stage", "step": "else-ui"}]
        else:
            edits = [CREATE, {"op": "move_step", "step": "1", "parent": "if-ui"}]
    with pytest.raises(CapabilityError):
        await plan(settings, flow, edits)


@pytest.mark.parametrize("kind", ["FOREACH", "PARALLEL"])
@pytest.mark.parametrize("op", ["attach_stage", "update_stage"])
async def test_stage_attachment_rejects_excluded_scope(settings, kind, op):
    flow = stage_flow()
    definition = next(d for d in family_definitions() if d["type"] == kind)
    flow["flowLogicInstances"] = [
        {
            "id": "d" * 32,
            "uiUniqueIdentifier": "block",
            "definitionId": definition["id"],
            "flowLogicDefinition": definition,
            "type": "flowlogic",
            "parent": "",
            "order": 1,
            "inputs": [],
        }
    ]
    for action in flow["actionInstances"]:
        action["order"] += 1
    flow["actionInstances"][0]["parent"] = "block"
    flow["subFlowInstances"][0]["order"] += 1
    source_index = 1
    if kind == "PARALLEL":
        branch = next(d for d in family_definitions() if d["type"] == "PARALLELBLOCK")
        flow["flowLogicInstances"].append(
            {
                "id": "e" * 32,
                "uiUniqueIdentifier": "branch",
                "definitionId": branch["id"],
                "flowLogicDefinition": branch,
                "type": "flowlogic",
                "parent": "block",
                "order": 2,
                "inputs": [],
            }
        )
        for action in flow["actionInstances"]:
            action["order"] += 1
        flow["actionInstances"][0].update(parent="branch", order="2->3")
        flow["subFlowInstances"][0]["order"] += 1
        source_index = 2
    edit = {"op": op, "stage": "saved-stage"}
    if op == "attach_stage":
        edit["step"] = "ui-1"
    else:
        flow["stages"]["saved-stage"]["componentIndexes"] = [source_index]
        edit["label"] = "Renamed"
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [edit],
        )


@pytest.mark.parametrize("defect", [None, "stage_id", "database_id", "source_index"])
async def test_until_stage_attach_move_and_independent_readback(settings, defect):
    # Native DOUNTIL metadata; canAddStages excludes only FOREACH and PARALLEL
    # ancestors (bundle:1575,16243-16250), not display_as_sandwich blocks.
    flow = stage_flow()
    parameters = [
        {"name": "condition_name", "type": "string", "mandatory": False},
        {"name": "condition", "type": "string", "mandatory": True},
    ]
    definition = {
        "id": "eaeb040687210300e6c2c88fc0e3ec89",
        "name": "Do the following until",
        "type": "DOUNTIL",
        "connectedTo": "",
        "inputs": parameters,
        "variables": [],
        "userCanRead": True,
        "attributes": {
            "allow_children": "true",
            "allow_siblings": "true",
            "can_be_nested": "false",
            "decorator_is_top": "true",
            "display_as_sandwich": "true",
            "first_label": "Do",
            "line_type": "cc_curve",
            "pills_draggable_outside_block": "false",
            "second_label": "until",
        },
    }
    flow["flowLogicInstances"] = [
        {
            "id": "d" * 32,
            "uiUniqueIdentifier": "until-ui",
            "definitionId": definition["id"],
            "flowLogicDefinition": definition,
            "type": "flowlogic",
            "parent": "",
            "order": 2,
            "orderBottom": 5,
            "children": ["ui-2", "ui-3"],
            "inputs": [
                {"name": parameter["name"], "parameter": parameter, "value": value}
                for parameter, value in zip(
                    parameters, ["Complete", "true"], strict=True
                )
            ],
        }
    ]
    flow["actionInstances"][1].update(parent="until-ui", order=3)
    flow["actionInstances"][2].update(parent="until-ui", order=4)
    flow["subFlowInstances"][0]["order"] = 6
    before = copy.deepcopy(flow)
    expected_stage = {
        **flow["stages"]["saved-stage"],
        "componentIndexes": [2],
    }
    _, tools = fixtures(settings, flow)
    _, row = capture_routes()
    saved_definition = {}
    independent_reads = []

    def read_saved(request):
        independent_reads.append(copy.deepcopy(saved_definition))
        return httpx2.Response(200, json={"result": {"data": saved_definition}})

    def save(request):
        payload = json.loads(request.content)
        assert payload["stages"] == {"saved-stage": expected_stage}
        components = sorted(
            payload["actionInstances"]
            + payload["flowLogicInstances"]
            + payload["subFlowInstances"],
            key=lambda step: step["order"],
        )
        attached = components[expected_stage["componentIndexes"][0]]
        assert attached["uiUniqueIdentifier"] == "ui-3"
        assert attached["parent"] == "until-ui"
        assert attached["order"] == 3
        assert payload["flowLogicInstances"][0]["orderBottom"] == 5
        actual = copy.deepcopy(payload)
        if defect == "stage_id":
            actual["stages"]["saved-stage"]["stageId"] = "different"
        elif defect == "database_id":
            actual["stages"]["saved-stage"]["id"] = "e" * 32
        elif defect == "source_index":
            actual["stages"]["saved-stage"]["componentIndexes"] = [3]
        saved_definition.update(actual)
        http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").mock(
            side_effect=read_saved
        )
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200, json={"result": {**status_header(), "sys_mod_count": "8"}}
        )
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200, json={"result": [{**row, "sys_mod_count": "8"}]}
        )
        # Echo remains correct so only the independent GET can reveal corruption.
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            edits=[
                {"op": "attach_stage", "stage": "saved-stage", "step": "ui-3"},
                {"op": "move_step", "step": "ui-3", "before": "ui-2"},
            ],
        )
    )
    assert preview["status"] == "success", preview
    reviewed = preview["data"]["summary"]["stages"][0]
    assert reviewed["stage"] == "saved-stage"
    assert reviewed["componentIndexes"] == [2]
    assert flow == before
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert write.call_count == 1
    assert independent_reads
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
        assert applied["data"]["revision"] == "8"
        assert independent_reads[-1]["stages"] == {"saved-stage": expected_stage}
    assert flow == before
    assert all(call.request.method in {"GET", "PUT"} for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "stage_id",
        "assigned_id_race",
        "label",
        "attachment",
        "opaque",
        "retained_deleted",
    ],
)
async def test_bound_stage_edit_saves_once_and_verifies_independently(settings, defect):
    flow = stage_flow()
    _, tools = fixtures(settings, flow)
    _, row = capture_routes()
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        assert "saved-stage" not in payload["stages"]
        key = next(iter(payload["stages"]))
        echo = copy.deepcopy(payload)
        echo["stages"][key]["id"] = "e" * 32
        actual = copy.deepcopy(echo)
        if defect == "stage_id":
            actual["stages"][key]["stageId"] = "different"
        elif defect == "assigned_id_race":
            actual["stages"][key]["id"] = "f" * 32
        elif defect == "label":
            actual["stages"][key]["label"] = "changed"
        elif defect == "attachment":
            actual["stages"][key]["componentIndexes"] = [1]
        elif defect == "opaque":
            actual["opaque"]["keep"] = []
        elif defect == "retained_deleted":
            actual["stages"]["saved-stage"] = copy.deepcopy(
                flow["stages"]["saved-stage"]
            )
        persist(actual)
        saved.update(payload)
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200, json={"result": {**status_header(), "sys_mod_count": "8"}}
        )
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200, json={"result": [{**row, "sys_mod_count": "8"}]}
        )
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            edits=[
                CREATE,
                {"op": "attach_stage", "stage": "work", "step": "1"},
                {"op": "delete_stage", "stage": "saved-stage"},
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["context"]["state"] == "bound"
    assert len(preview["data"]["summary"]["stages"]) == 1
    assert preview["data"]["summary"]["stages_before"][0]["stage"] == "saved-stage"
    reviewed = preview["data"]["summary"]["stages"][0]
    assert reviewed["states"] == stages.STATES
    assert reviewed["duration"] == "1970-01-01 00:00:00"
    assert reviewed["alwaysShow"] is True
    assert reviewed["componentIndexes"] == [0]
    assert "opaque" not in preview["data"]["summary"]["stages_before"][0]
    key = preview["data"]["summary"]["stages"][0]["stage"]
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
        assert applied["data"]["revision"] == "8" and "capture" not in applied["data"]
        assert saved["stages"][key]["componentIndexes"] == [0]
    assert write.call_count == 1
    assert all(call.request.method in {"GET", "PUT"} for call in http_mock.calls)
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error"
    assert write.call_count == 1


async def test_stage_preview_rejects_stale_definition_before_write(settings):
    flow = stage_flow()
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[CREATE])
    )
    changed = copy.deepcopy(flow)
    changed["stages"]["saved-stage"]["opaque"]["keep"] = False
    persist(changed)
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_existing_stage_database_identity_never_normalizes(settings):
    expected = (
        await plan(
            settings,
            stage_flow(),
            [{"op": "update_stage", "stage": "saved-stage", "label": "Renamed"}],
        )
    )["payload"]
    changed = copy.deepcopy(expected)
    changed["stages"]["saved-stage"]["id"] = "e" * 32
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, changed, generated_ids=set())


@pytest.mark.parametrize("assigned", ["", "invalid", True, None, "c" * 32])
async def test_new_stage_invalid_or_duplicate_assigned_id_fails(settings, assigned):
    planned = await plan(settings, stage_flow(), [CREATE])
    actual = copy.deepcopy(planned["payload"])
    actual["stages"][planned["created_stages"][0]]["id"] = assigned
    with pytest.raises(CapabilityError):
        semantics.compare_saved(
            planned["payload"],
            actual,
            generated_ids=set(),
            generated_stages=set(planned["created_stages"]),
        )


async def test_two_new_stages_cannot_share_a_database_identity(settings):
    planned = await plan(
        settings,
        base_flow(),
        [CREATE, {"op": "create_stage", "label": "Delivery", "value": "delivery"}],
    )
    actual = copy.deepcopy(planned["payload"])
    for stage in actual["stages"].values():
        stage["id"] = "e" * 32
    with pytest.raises(semantics.SemanticMismatch, match="DUPLICATE_IDENTITY"):
        semantics.compare_saved(
            planned["payload"],
            actual,
            generated_ids=set(),
            generated_stages=set(planned["created_stages"]),
        )


@pytest.mark.parametrize("when", ["preview", "apply"])
async def test_stage_table_policy_is_checked_before_any_write(
    settings, monkeypatch, when
):
    from servicenow_mcp import policy

    coordinator, tools = fixtures(settings, stage_flow())
    token = None
    if when == "apply":
        preview = decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[CREATE])
        )
        assert preview["status"] == "success", preview
        token = preview["data"]["preview_token"]
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_flow_stage"}
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=token)
        if when == "apply"
        else await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[CREATE])
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_stage_crud_production_write_gate_is_unchanged(prod_settings):
    coordinator, tools = fixtures(prod_settings, stage_flow())
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[CREATE])
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert not http_mock.calls


async def test_stage_attachment_preserves_branch_topology_and_pills(settings):
    flow = saved_branches()
    flow["stages"] = stage_flow()["stages"]
    flow["flowLogicInstances"][0]["inputs"] = [
        {"name": "condition", "parameter": {"type": "string"}, "value": "true"}
    ]
    planned = await plan(
        settings,
        flow,
        [CREATE, {"op": "attach_stage", "stage": "work", "step": "ui-3"}],
    )
    assert planned["payload"]["stages"][planned["created_stages"][0]][
        "componentIndexes"
    ] == [2]
    assert [step["parent"] for step in planned["payload"]["actionInstances"]] == [
        "",
        "if-ui",
        "else-ui",
    ]
    assert (
        planned["payload"]["flowLogicInstances"][0]["inputs"]
        == flow["flowLogicInstances"][0]["inputs"]
    )


async def test_stage_detach_then_delete_attached_step_is_explicit(settings):
    flow = stage_flow()
    flow["stages"]["saved-stage"]["componentIndexes"] = [0]
    planned = await plan(
        settings,
        flow,
        [
            {"op": "detach_stage", "stage": "saved-stage", "step": "1"},
            {"op": "delete_step", "step": "1"},
            {"op": "delete_stage", "stage": "saved-stage"},
        ],
    )
    assert planned["payload"]["stages"] == {}
    assert len(planned["payload"]["actionInstances"]) == 2
    assert planned["pending_deletions"]["deletedActions"] == [{"id": "1" * 32}]


async def test_stage_update_keeps_unknown_state_metadata(settings):
    flow = stage_flow()
    flow["stages"]["saved-stage"]["states"]["vendor_state"] = {"opaque": "keep"}
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_stage",
                "stage": "saved-stage",
                "states": {**stages.STATES, "complete": "Done"},
            }
        ],
    )
    assert planned["payload"]["stages"]["saved-stage"]["states"] == {
        **stages.STATES,
        "complete": "Done",
        "vendor_state": {"opaque": "keep"},
    }
    assert "vendor_state" not in stages.summary(planned["payload"], [])[0]["states"]


async def test_stage_attachment_rejects_first_grouping_child(settings):
    flow = saved_branches()
    flow["stages"] = stage_flow()["stages"]
    flow["flowLogicInstances"][0]["flowLogicDefinition"].setdefault("attributes", {})[
        "is_dumb"
    ] = "true"
    with pytest.raises(CapabilityError, match="first child of a grouping block"):
        await plan(
            settings,
            flow,
            [{"op": "attach_stage", "stage": "saved-stage", "step": "ui-3"}],
        )
