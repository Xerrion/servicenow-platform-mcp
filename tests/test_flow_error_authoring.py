"""Studio-source fixtures for handlers and conditional error stages, not live captures."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import handlers, semantics, stages, tree
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan


def definitions():
    # Variable names below are deliberately fixture-only. Production Error
    # Status fields come only from each readable instance catalog definition.
    result = family_definitions()
    result.extend(
        [
            {
                "id": "6" * 32,
                "type": "TOP_LEVEL_TRY",
                "name": "Top Level Try",
                "inputs": [],
                "variables": [],
                "userCanRead": True,
                "attributes": {"allow_children": "true"},
                "connectedTo": "",
                "displayOnUi": False,
            },
            {
                "id": "7" * 32,
                "type": "TOP_LEVEL_CATCH",
                "name": "Top Level Catch",
                "inputs": [
                    {
                        "id": "a" * 32,
                        "name": "enabled",
                        "type": "boolean",
                        "defaultValue": "1",
                    }
                ],
                "variables": [
                    {
                        "name": "fixture_status",
                        "type": "object",
                        "children": [
                            {"name": "code", "type": "integer", "children": []},
                            {"name": "message", "type": "string", "children": []},
                        ],
                    }
                ],
                "userCanRead": True,
                "attributes": {"allow_children": "true"},
                "connectedTo": "6" * 32,
            },
        ]
    )
    return result


def routes(settings, flow):
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": definitions()}
    )
    return coordinator, tools


async def test_create_populate_disable_enable_handler_preserves_steps(settings):
    flow = base_flow()
    routes(settings, flow)
    built = await plan(
        settings,
        flow,
        [
            {"op": "set_error_handler", "enabled": True},
            {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
        ],
    )
    payload = built["payload"]
    roots = tree.build_tree(payload)
    assert [s.logic_type for s in roots] == ["TOP_LEVEL_TRY", "TOP_LEVEL_CATCH"]
    assert roots[0].raw["order"] == 0
    assert [s.uid for s in roots[0].children] == ["ui-1", "ui-2", "ui-4"]
    assert [s.uid for s in roots[1].children] == ["ui-3"]
    assert payload["startOfErrorHandlingInstances"] == 4
    assert payload["active"] is False and payload["isPublished"] is False
    routes(settings, payload)
    disabled = (
        await plan(settings, payload, [{"op": "set_error_handler", "enabled": False}])
    )["payload"]
    catch = tree.build_tree(disabled)[1]
    assert catch.raw["inputs"][0]["value"] is False
    assert catch.children[0].uid == "ui-3" and catch.children[0].raw["readOnly"] is True
    routes(settings, disabled)
    enabled = (
        await plan(settings, disabled, [{"op": "set_error_handler", "enabled": True}])
    )["payload"]
    assert tree.build_tree(enabled)[1].children[0].raw["readOnly"] is False
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_native_error_stage_creation_under_conditional(settings):
    flow = base_flow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
            {"op": "attach_stage", "stage": "work", "step": "ui-1"},
            {
                "op": "insert_logic",
                "logic": "If",
                "inputs": {"condition": "true"},
                "after": "ui-1",
                "ref": "branch",
            },
            {"op": "move_step", "step": "ui-2", "parent": "branch"},
            {"op": "create_stage", "type": "error", "step": "ui-2", "ref": "failure"},
        ],
    )
    all_stages = result["payload"]["stages"]
    error = next(row for row in all_stages.values() if row["type"] == "error")
    standard = next(row for row in all_stages.values() if row["type"] == "standard")
    assert error["ancestorStageId"] == standard["stageId"]
    assert error["ancestorArrPos"] == 0
    assert error["componentIndexes"] == [2]
    assert (
        error["ancestralIfElseLogic"]
        == result["payload"]["flowLogicInstances"][0]["uiUniqueIdentifier"]
    )
    assert error["label"] == error["value"] == "Set current stage state to:  [Error]"
    assert error["order"] is None and error["states"] == stages.STATES


async def handler_flow(settings, populated=True):
    flow = base_flow()
    routes(settings, flow)
    edits = [{"op": "set_error_handler", "enabled": True}]
    if populated:
        edits.append({"op": "move_step", "step": "ui-3", "parent": "error_handler"})
    result = (await plan(settings, flow, edits))["payload"]
    for index, item in enumerate(result["flowLogicInstances"], 10):
        item["id"] = f"{index:032x}"
    return result


async def error_flow(settings):
    flow = base_flow()
    routes(settings, flow)
    result = (
        await plan(
            settings,
            flow,
            [
                {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
                {"op": "attach_stage", "stage": "work", "step": "ui-1"},
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "inputs": {"condition": "true"},
                    "after": "ui-1",
                    "ref": "branch",
                },
                {"op": "move_step", "step": "ui-2", "parent": "branch"},
                {
                    "op": "create_stage",
                    "type": "error",
                    "step": "ui-2",
                    "ref": "failure",
                },
            ],
        )
    )["payload"]
    for index, stage in enumerate(result["stages"].values(), 10):
        stage["id"] = f"{index:032x}"
        stage["opaque"] = {"keep": True}
    for index, logic in enumerate(result["flowLogicInstances"], 30):
        logic["id"] = f"{index:032x}"
    return result


@pytest.mark.parametrize("operation", ["disable", "remove"])
async def test_empty_handler_removal_native_unwrap_and_stage_reindex(
    settings, operation
):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
                {"op": "attach_stage", "stage": "work", "step": "ui-1"},
            ],
        )
    )["payload"]
    routes(settings, flow)
    enabled = (
        await plan(settings, flow, [{"op": "set_error_handler", "enabled": True}])
    )["payload"]
    for index, s in enumerate(enabled["flowLogicInstances"], 10):
        s["id"] = f"{index:032x}"
    assert next(iter(enabled["stages"].values()))["componentIndexes"] == [1]
    routes(settings, enabled)
    edit = (
        {"op": "set_error_handler", "enabled": False}
        if operation == "disable"
        else {"op": "remove_error_handler"}
    )
    result = await plan(settings, enabled, [edit])
    assert result["payload"]["flowLogicInstances"] == []
    assert all(item["parent"] == "" for item in result["payload"]["actionInstances"])
    assert next(iter(result["payload"]["stages"].values()))["componentIndexes"] == [0]
    assert len(result["pending_deletions"]["deletedFlowLogicInstances"]) == 2


async def test_populated_handler_removal_requires_explicit_child_deletion(settings):
    flow = await handler_flow(settings)
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="Delete handler steps explicitly"):
        await plan(settings, flow, [{"op": "remove_error_handler"}])
    result = await plan(
        settings,
        flow,
        [
            {"op": "delete_step", "step": "ui-3", "children": "reject"},
            {"op": "remove_error_handler"},
        ],
    )
    assert result["payload"]["flowLogicInstances"] == []
    assert result["pending_deletions"]["deletedActions"] == [{"id": "3" * 32}]


@pytest.mark.parametrize("consumer", ["ui-3", "ui-1"])
@pytest.mark.parametrize("field", ["message", "missing"])
async def test_handler_pills_use_catalog_schema_and_scope_only(
    settings, consumer, field
):
    flow = await handler_flow(settings)
    catch = tree.build_tree(flow)[1]
    routes(settings, flow)
    edits = [
        {
            "op": "update_inputs",
            "step": consumer,
            "inputs": {
                "log_message": "Error: {{"
                + catch.uid
                + ".fixture_status."
                + field
                + "}}"
            },
        }
    ]
    if consumer == "ui-3" and field == "message":
        result = await plan(settings, flow, edits)
        assert result["payload"]["label_cache"][0]["name"].endswith(
            "fixture_status.message"
        )
    else:
        with pytest.raises(CapabilityError):
            await plan(settings, flow, edits)


async def test_new_handler_catalog_leaf_pill_alias_serializes_native_uid(settings):
    flow = base_flow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "set_error_handler", "enabled": True},
            {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
            {
                "op": "update_inputs",
                "step": "ui-3",
                "inputs": {
                    "log_message": "Error: {{error_handler.fixture_status.message}}"
                },
            },
        ],
    )
    catch = tree.build_tree(result["payload"])[1]
    assert (
        catch.children[0].raw["inputs"][0]["value"]
        == "Error: {{" + catch.uid + ".fixture_status.message}}"
    )
    assert (
        result["payload"]["label_cache"][0]["name"]
        == catch.uid + ".fixture_status.message"
    )


async def test_main_outputs_cannot_cross_into_handler(settings):
    flow = await handler_flow(settings)
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "text", "type": "string"}
    ]
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="boundary"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {"log_message": "{{ui-1.text}}"},
                }
            ],
        )


async def test_error_stage_update_reorder_delete_and_dependency_guards(settings):
    flow = await error_flow(settings)
    error_key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
    standard_key = next(k for k, s in flow["stages"].items() if s["type"] == "standard")
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_stage",
                "stage": error_key,
                "always_show": False,
                "duration": "1970-01-01 00:10:00",
                "states": {**stages.STATES, "error": "Failed"},
            },
            {"op": "move_step", "step": "ui-3", "before": "ui-1"},
        ],
    )
    stage = result["payload"]["stages"][error_key]
    assert stage["ancestorArrPos"] == 1 and stage["componentIndexes"] == [3]
    assert stage["id"] == flow["stages"][error_key]["id"] and stage["opaque"] == {
        "keep": True
    }
    for edit in [
        {"op": "delete_stage", "stage": standard_key},
        {"op": "detach_stage", "stage": standard_key, "step": "ui-1"},
        {"op": "delete_step", "step": "ui-2"},
        {"op": "move_step", "step": "ui-1", "after": "ui-3"},
    ]:
        with pytest.raises(CapabilityError):
            await plan(settings, flow, [edit])
    removed = (
        await plan(
            settings,
            flow,
            [
                {"op": "detach_stage", "stage": error_key, "step": "ui-2"},
                {"op": "detach_stage", "stage": standard_key, "step": "ui-1"},
                {"op": "delete_stage", "stage": standard_key},
            ],
        )
    )["payload"]
    assert removed["stages"] == {}


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "enabled",
        "boundary",
        "order",
        "parent",
        "assigned_race",
        "opaque",
        "active",
        "stage_source",
        "stage_assigned_race",
    ],
)
async def test_handler_and_error_stage_save_once_echo_and_independent_get(
    settings, defect
):
    flow = base_flow()
    flow["opaque"] = {"keep": [True, 1]}
    coordinator, tools = routes(settings, flow)
    edits = [
        {"op": "set_error_handler", "enabled": True},
        {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
        {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
        {"op": "attach_stage", "stage": "work", "step": "ui-1"},
        {
            "op": "insert_logic",
            "logic": "If",
            "parent": "main",
            "after": "ui-1",
            "inputs": {"condition": "true"},
            "ref": "branch",
        },
        {"op": "move_step", "step": "ui-2", "parent": "branch"},
        {"op": "create_stage", "type": "error", "step": "ui-2"},
    ]

    def save(request):
        body = json.loads(request.content)
        assert (
            body["active"] is False
            and body["isPublished"] is False
            and body["status"] == "draft"
        )
        for index, logic in enumerate(body["flowLogicInstances"], 10):
            logic["id"] = f"{index:032x}"
        for index, stage in enumerate(body["stages"].values(), 20):
            stage["id"] = f"{index:032x}"
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        catch = next(
            s
            for s in actual["flowLogicInstances"]
            if s["flowLogicDefinition"]["type"] == "TOP_LEVEL_CATCH"
        )
        if defect == "enabled":
            catch["inputs"][0]["value"] = False
            catch["data"]["enabled"] = False
        elif defect == "boundary":
            actual["startOfErrorHandlingInstances"] += 1
        elif defect == "order":
            actual["flowLogicInstances"][0]["order"] = 1
        elif defect == "parent":
            actual["actionInstances"][0]["parent"] = ""
        elif defect == "assigned_race":
            catch["id"] = "e" * 32
        elif defect == "opaque":
            actual["opaque"]["keep"] = []
        elif defect == "active":
            actual["active"] = True
        elif defect == "stage_source":
            next(s for s in actual["stages"].values() if s["type"] == "error")[
                "ancestorStageId"
            ] = "wrong"
        elif defect == "stage_assigned_race":
            next(s for s in actual["stages"].values() if s["type"] == "error")["id"] = (
                "e" * 32
            )
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["error_handler"]["enabled"] is True
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        applied["error"]["code"] == "UNKNOWN_OUTCOME"
        if defect
        else applied["status"] == "success"
    ), applied
    assert write.call_count == 1
    repeated = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        repeated["status"] == "error"
        and write.call_count == 1
        and len(coordinator.store) == 0
    )


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "set_error_handler", "enabled": "false"},
        {"op": "set_error_handler", "enabled": 1},
        {"op": "set_error_handler", "enabled": None},
        {"op": "set_error_handler", "enabled": False},
        {"op": "remove_error_handler"},
        {"op": "set_error_handler", "enabled": True, "activate": True},
        {"op": "create_stage", "type": "error", "step": "ui-1"},
        {"op": "create_stage", "type": "error"},
        {"op": "create_stage", "type": "error", "step": "ui-1", "label": "Invented"},
    ],
)
async def test_malformed_or_unavailable_error_edits_never_stage(settings, edit):
    coordinator, tools = routes(settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect", ["missing", "unreadable", "duplicate", "input_contract"]
)
async def test_handler_creation_requires_exact_readable_catalog_contract(
    settings, defect
):
    coordinator, tools = routes(settings, base_flow())
    current = definitions()
    if defect == "missing":
        current.pop()
    elif defect == "unreadable":
        current[-1]["userCanRead"] = False
    elif defect == "duplicate":
        current.append(copy.deepcopy(current[-1]))
    else:
        current[-1]["inputs"][0]["name"] = "different"
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": current}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": True}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "defect",
    ["boundary", "enabled", "order", "region", "nested_wrapper", "missing_try"],
)
async def test_malformed_saved_handler_does_not_get_repaired(settings, defect):
    flow = await handler_flow(settings)
    if defect == "boundary":
        flow["startOfErrorHandlingInstances"] = 100
    elif defect == "enabled":
        tree.build_tree(flow)  # Keep the fixture's actual binding, not a tree copy.
        flow["flowLogicInstances"][-1]["inputs"][0]["value"] = "true"
        flow["flowLogicInstances"][-1]["data"]["enabled"] = "true"
    elif defect == "order":
        flow["flowLogicInstances"][0]["order"] = -1
    elif defect == "region":
        flow["actionInstances"][0]["errorHandlingInstance"] = True
    elif defect == "nested_wrapper":
        flow["flowLogicInstances"][-1]["parent"] = flow["flowLogicInstances"][0][
            "uiUniqueIdentifier"
        ]
    else:
        flow["flowLogicInstances"].pop(0)
    coordinator, tools = routes(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": False}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "delete_step", "step": "error_handler", "children": "delete"},
        {"op": "move_step", "step": "main", "parent": "error_handler"},
        {"op": "update_inputs", "step": "error_handler", "inputs": {"enabled": False}},
        {"op": "insert_logic", "logic": "End", "after": "main"},
    ],
)
async def test_hidden_wrapper_generic_edits_are_blocked(settings, edit):
    flow = await handler_flow(settings)
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit])


@pytest.mark.parametrize("op", ["delete_step", "update_inputs", "insert_logic"])
async def test_disabled_handler_preserves_readonly_contents(settings, op):
    flow = await handler_flow(settings)
    routes(settings, flow)
    flow = (
        await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])
    )["payload"]
    before = copy.deepcopy(flow)
    routes(settings, flow)
    edit = {"op": op, "step": "ui-3"}
    if op == "update_inputs":
        edit["inputs"] = {"log_message": "different"}
    elif op == "insert_logic":
        edit = {"op": op, "logic": "End", "parent": "error_handler"}
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit])
    assert flow == before


async def test_handler_structural_capacity_and_opaque_mirror_guards(settings):
    flow = await handler_flow(settings)
    routes(settings, flow)
    edits = [
        {
            "op": "insert_logic",
            "logic": "If",
            "inputs": {"condition": "true"},
            "parent": "error_handler",
        }
        for _ in range(10)
    ]
    with pytest.raises(CapabilityError, match="ten"):
        await plan(settings, flow, edits)
    flow["componentInstances"] = sorted(
        [row for key in tree.COLLECTIONS.values() for row in flow[key]],
        key=lambda row: row["order"],
    )
    flow["componentInstances"][0]["mirror_only"] = True
    # The three lists share objects only in this fixture. Break that alias to
    # model a contradictory independently serialized mirror.
    flow = copy.deepcopy(flow)
    flow["componentInstances"] = copy.deepcopy(flow["componentInstances"])
    flow["componentInstances"][0]["mirror_only"] = "different"
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="mirror differs"):
        await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])


@pytest.mark.parametrize("kind", ["set_error_handler", "error_stage"])
async def test_changed_handler_or_stage_definition_context_invalidates_preview(
    settings, kind
):
    flow = (
        await handler_flow(settings)
        if kind == "set_error_handler"
        else await error_flow(settings)
    )
    _, tools = routes(settings, flow)
    key = ""
    if kind == "set_error_handler":
        edits = [{"op": kind, "enabled": False}]
    else:
        key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
        edits = [{"op": "update_stage", "stage": key, "always_show": False}]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    if kind == "set_error_handler":
        current = definitions()
        current[-1]["variables"] = []
        http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
            200, json={"result": current}
        )
    else:
        flow["stages"][key]["opaque"] = {"changed": True}
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "error", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("phase", ["preview", "apply"])
@pytest.mark.parametrize(
    "table", ["sys_hub_flow_logic_instance_v2", "sys_hub_flow_stage"]
)
async def test_handler_policy_tables_rechecked_at_apply(
    settings, monkeypatch, phase, table
):
    from servicenow_mcp import policy

    coordinator, tools = routes(settings, base_flow())
    arguments = {
        "action": "edit",
        "sys_id": FLOW,
        "edits": [{"op": "set_error_handler", "enabled": True}],
    }
    preview = (
        decode_response(await tools["flow_preview"](**arguments))
        if phase == "apply"
        else None
    )
    monkeypatch.setattr(policy, "DENIED_TABLES", policy.DENIED_TABLES | {table})
    result = (
        decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        if preview
        else decode_response(await tools["flow_preview"](**arguments))
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("operation", ["disable", "enable", "remove"])
@pytest.mark.parametrize("defect", [None, "retained", "identity", "contents"])
async def test_saved_handler_toggle_and_empty_removal_round_trip(
    settings, operation, defect
):
    flow = await handler_flow(settings)
    if operation == "enable":
        routes(settings, flow)
        flow = (
            await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])
        )["payload"]
    _, tools = routes(settings, flow)
    edits = [{"op": "set_error_handler", "enabled": operation == "enable"}]
    if operation == "remove":
        edits = [{"op": "delete_step", "step": "ui-3"}, {"op": "remove_error_handler"}]

    def save(request):
        body = json.loads(request.content)
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        assert body["active"] is False and body["isPublished"] is False
        if operation == "remove":
            assert len(body["deletedFlowLogicInstances"]) == 2
        else:
            assert body["actionInstances"][-1]["id"] == "3" * 32
            assert (
                body["actionInstances"][-1]["inputs"]
                == flow["actionInstances"][-1]["inputs"]
            )
        if defect == "retained" and operation == "remove":
            actual["flowLogicInstances"] = copy.deepcopy(flow["flowLogicInstances"])
        elif defect == "identity" and actual["flowLogicInstances"]:
            actual["flowLogicInstances"][0]["id"] = "e" * 32
        elif defect:
            actual["actionInstances"][0]["inputs"][0]["value"] = "unexpected"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        result["error"]["code"] == "UNKNOWN_OUTCOME"
        if defect
        else result["status"] == "success"
    ), result
    assert write.call_count == 1


@pytest.mark.parametrize("operation", ["update", "detach", "delete"])
@pytest.mark.parametrize("defect", [None, "identity", "retained", "metadata"])
async def test_saved_error_stage_crud_independent_verification(
    settings, operation, defect
):
    flow = await error_flow(settings)
    key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
    _, tools = routes(settings, flow)
    edits = (
        [{"op": "update_stage", "stage": key, "duration": "1970-01-01 00:02:00"}]
        if operation == "update"
        else [{"op": "detach_stage", "stage": key, "step": "ui-2"}]
        if operation == "detach"
        else [{"op": "delete_stage", "stage": key}]
    )

    def save(request):
        body = json.loads(request.content)
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        if operation == "update":
            assert body["stages"][key]["id"] == flow["stages"][key]["id"]
            assert body["stages"][key]["opaque"] == {"keep": True}
        else:
            assert key not in body["stages"]
        if defect == "retained" and operation != "update":
            actual["stages"][key] = copy.deepcopy(flow["stages"][key])
        elif defect == "identity" and key in actual["stages"]:
            actual["stages"][key]["id"] = "e" * 32
        elif defect:
            next(iter(actual["stages"].values()))["opaque"] = {"changed": True}
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        result["error"]["code"] == "UNKNOWN_OUTCOME"
        if defect
        else result["status"] == "success"
    ), result
    assert write.call_count == 1


async def test_error_stage_recreation_is_explicit_and_creation_reorder_updates_source(
    settings,
):
    flow = await error_flow(settings)
    key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "delete_stage", "stage": key},
            {"op": "create_stage", "type": "error", "step": "ui-2"},
            {"op": "move_step", "step": "ui-3", "before": "ui-1"},
        ],
    )
    error = next(
        s for s in result["payload"]["stages"].values() if s["type"] == "error"
    )
    assert key not in result["payload"]["stages"] and error["ancestorArrPos"] == 1
    assert error["componentIndexes"] == [3]


@pytest.mark.parametrize(
    "defect",
    [
        "missing_ancestor",
        "wrong_branch",
        "wrong_position",
        "orphan_attachment",
        "duplicate_sibling",
    ],
)
async def test_error_stage_malformed_source_is_not_repaired(settings, defect):
    flow = await error_flow(settings)
    key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
    if defect == "missing_ancestor":
        flow["stages"][key]["ancestorStageId"] = "absent"
    elif defect == "wrong_branch":
        flow["stages"][key]["ancestralIfElseLogic"] = "absent"
    elif defect == "wrong_position":
        flow["stages"][key]["ancestorArrPos"] = 200
    elif defect == "orphan_attachment":
        flow["stages"][key]["componentIndexes"] = []
    else:
        flow["stages"]["other"] = {
            **copy.deepcopy(flow["stages"][key]),
            "stageId": "other",
        }
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings, flow, [{"op": "update_stage", "stage": key, "always_show": False}]
        )


@pytest.mark.parametrize("legacy", [False, True])
async def test_handler_readonly_validation_integration(settings, legacy):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.capabilities.flows import lifecycle
    from servicenow_mcp.platform.client import ServiceNowClient
    from tests.test_flow_lifecycle import validation_flow, validation_routes

    flow = validation_flow()
    routes(settings, flow)
    catalog = legacy_handler_definitions(upgraded=True) if legacy else definitions()
    if legacy:
        catalog = [row for row in catalog if row["type"] != handlers.TRY]
        http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
            200, json={"result": catalog}
        )
    payload = (
        await plan(
            settings,
            flow,
            [
                {"op": "set_error_handler", "enabled": True},
                {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
            ],
        )
    )["payload"]
    validation_routes(payload)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog}
    )
    before = copy.deepcopy(payload)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(payload, client)
    assert result["complete"] is True and result["valid"] is True, result
    assert before == payload
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("defect", ["scope", "revision", "authorization"])
async def test_error_handler_target_and_authorization_binding_before_write(
    settings, defect
):
    from tests.test_flow_lifecycle import capture_routes

    coordinator, tools = routes(settings, base_flow())
    capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            edits=[{"op": "set_error_handler", "enabled": True}],
        )
    )
    assert preview["status"] == "success", preview
    if defect == "scope":
        capture_routes(scope="e" * 32)
    elif defect == "revision":
        from tests.test_flow_lifecycle import status_header

        header = status_header()
        header["sys_mod_count"] = "8"
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200, json={"result": header}
        )
    else:
        coordinator.auth._generation += 1
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_handler_pills_reject_removal_with_pharmacy_or_interface_defaults(
    settings,
):
    flow = await handler_flow(settings, populated=False)
    catch = tree.build_tree(flow)[1]
    flow["pharmacy"] = {
        "prescriptions": [{"value": "{{" + catch.uid + ".fixture_status.message}}"}]
    }
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="transforms"):
        await plan(settings, flow, [{"op": "remove_error_handler"}])


@pytest.mark.parametrize("subflow", [False, True])
async def test_handler_insert_logic_in_handler_and_subflow_preserves_interfaces(
    settings, subflow
):
    flow = base_flow()
    if subflow:
        flow["type"] = "subflow"
        flow["inputs"] = [{"name": "text", "type": "string", "children": []}]
        flow["outputs"] = [{"name": "result", "type": "string", "children": []}]
    routes(settings, flow)
    built = (
        await plan(
            settings,
            flow,
            [
                {"op": "set_error_handler", "enabled": True},
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "inputs": {"condition": "true"},
                    "parent": "error_handler",
                    "ref": "branch",
                },
                {"op": "move_step", "step": "ui-3", "parent": "branch"},
            ],
        )
    )["payload"]
    roots = tree.build_tree(built)
    assert roots[1].children[0].logic_type == "IF"
    assert roots[1].children[0].children[0].uid == "ui-3"
    assert roots[1].children[0].children[0].raw["errorHandlingInstance"] is True
    assert built["inputs"] == flow["inputs"] and built["outputs"] == flow["outputs"]


async def test_duplicate_error_creation_and_unsupported_rename_are_rejected(settings):
    flow = await error_flow(settings)
    key = next(k for k, s in flow["stages"].items() if s["type"] == "error")
    routes(settings, flow)
    for edit in [
        {"op": "create_stage", "type": "error", "step": "ui-2"},
        {"op": "update_stage", "stage": key, "label": "changed"},
        {"op": "attach_stage", "stage": key, "step": "ui-3"},
    ]:
        with pytest.raises(CapabilityError):
            await plan(settings, flow, [edit])


@pytest.mark.parametrize("branch_kind", ["Else", "Else If"])
async def test_error_stage_uses_each_actual_conditional_branch(settings, branch_kind):
    flow = base_flow()
    routes(settings, flow)
    edits = [
        {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
        {"op": "attach_stage", "stage": "work", "step": "ui-1"},
        {
            "op": "insert_logic",
            "logic": "If",
            "inputs": {"condition": "true"},
            "after": "ui-1",
            "ref": "if_branch",
        },
        {
            "op": "insert_logic",
            "logic": branch_kind,
            "inputs": {"condition": "true"} if branch_kind == "Else If" else {},
            "after": "if_branch",
            "ref": "other_branch",
        },
        {"op": "move_step", "step": "ui-2", "parent": "other_branch"},
        {"op": "create_stage", "type": "error", "step": "ui-2"},
    ]
    built = (await plan(settings, flow, edits))["payload"]
    stage = next(s for s in built["stages"].values() if s["type"] == "error")
    branch = next(
        s
        for s in built["flowLogicInstances"]
        if s["flowLogicDefinition"]["type"]
        == ("ELSE" if branch_kind == "Else" else "ELSEIF")
    )
    assert stage["ancestralIfElseLogic"] == branch["uiUniqueIdentifier"]
    assert stage["ancestorArrPos"] == 0


async def test_error_stage_source_survives_handler_wrap_and_unwrap(settings):
    flow = await error_flow(settings)
    routes(settings, flow)
    built = (
        await plan(settings, flow, [{"op": "set_error_handler", "enabled": True}])
    )["payload"]
    stage = next(s for s in built["stages"].values() if s["type"] == "error")
    assert stage["ancestorArrPos"] == 1 and stage["componentIndexes"] == [3]
    routes(settings, built)
    unwrapped = (
        await plan(settings, built, [{"op": "set_error_handler", "enabled": False}])
    )["payload"]
    assert unwrapped["stages"] == flow["stages"]


async def test_handler_mirror_refreshes_only_exact_authoritative_projection(settings):
    flow = base_flow()
    flow["componentInstances"] = copy.deepcopy(
        sorted(
            [s for key in tree.COLLECTIONS.values() for s in flow[key]],
            key=lambda s: s["order"],
        )
    )
    routes(settings, flow)
    result = (
        await plan(settings, flow, [{"op": "set_error_handler", "enabled": True}])
    )["payload"]
    assert result["componentInstances"] == sorted(
        [s for key in tree.COLLECTIONS.values() for s in result[key]],
        key=lambda s: s["order"],
    )


async def test_handler_configuration_is_blocked_in_production_before_http(
    prod_settings,
):
    coordinator, tools = routes(prod_settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": True}],
        )
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert not http_mock.calls


async def test_saved_get_catch_region_flag_preserved_and_children_can_return_to_main(
    settings,
):
    flow = await handler_flow(settings)
    # onAnyFlowUpdateOK marks only rows after Catch, unlike creation.
    flow["flowLogicInstances"][-1]["errorHandlingInstance"] = False
    routes(settings, flow)
    same = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {"log_message": "updated"},
                }
            ],
        )
    )["payload"]
    assert same["flowLogicInstances"][-1]["errorHandlingInstance"] is False
    result = (
        await plan(
            settings,
            flow,
            [
                {"op": "move_step", "step": "ui-3", "parent": "main"},
                {"op": "remove_error_handler"},
            ],
        )
    )["payload"]
    assert result["flowLogicInstances"] == []
    assert all(
        row["errorHandlingInstance"] is False for row in result["actionInstances"]
    )


async def test_error_stage_policy_table_is_checked_before_structural_ancestry_changes(
    settings, monkeypatch
):
    from servicenow_mcp import policy

    flow = await error_flow(settings)
    coordinator, tools = routes(settings, flow)
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_flow_stage"}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "move_step", "step": "ui-3", "before": "ui-1"}],
        )
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_native_handler_error_stage_protocol_matches_reviewed_ancestry(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    flow = base_flow()
    routes(settings, flow)
    server = NativeMCPServer("native-handler-stages")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    reviewed = {}

    def save(request):
        body = json.loads(request.content)
        assert (
            next(s for s in body["stages"].values() if s["type"] == "error")[
                "ancestralIfElseLogic"
            ]
            == reviewed["ancestralIfElseLogic"]
        )
        assert tree.build_tree(body)[1].uid == reviewed["handler"]
        assert body["active"] is False and body["isPublished"] is False
        for index, s in enumerate(body["flowLogicInstances"], 10):
            s["id"] = f"{index:032x}"
        for index, stage in enumerate(body["stages"].values(), 20):
            stage["id"] = f"{index:032x}"
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    edits = [
        {"op": "set_error_handler", "enabled": True},
        {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
        {
            "op": "update_inputs",
            "step": "ui-3",
            "inputs": {
                "log_message": "Error: {{error_handler.fixture_status.message}}"
            },
        },
        {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
        {"op": "attach_stage", "stage": "work", "step": "ui-1"},
        {
            "op": "insert_logic",
            "logic": "If",
            "inputs": {"condition": "true"},
            "after": "ui-1",
            "ref": "branch",
        },
        {"op": "move_step", "step": "ui-2", "parent": "branch"},
        {"op": "create_stage", "type": "error", "step": "ui-2"},
    ]
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
                "flow_preview", {"action": "edit", "sys_id": FLOW, "edits": edits}
            )
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            summary = preview.structured_content["data"]["summary"]
            reviewed["handler"] = summary["error_handler"]["handler"]
            reviewed["ancestralIfElseLogic"] = next(
                s for s in summary["stages"] if s["type"] == "error"
            )["ancestralIfElseLogic"]
            assert not write.called
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert not applied.is_error, applied
            assert write.call_count == 1
        tasks.cancel_scope.cancel()


def complex_handler_definitions():
    """Source-derived catalog profile, not a captured instance catalog."""
    catalog = definitions()
    loop = next(row for row in catalog if row["type"] == "FOREACH")
    loop["attributes"]["display_as_sandwich"] = "true"
    return catalog


def complex_handler_projection(profile="nested"):
    # Literal Studio updateActionInstanceIndices labels (bundle:156494-156713),
    # with save's direct-wrapper parent reconstruction (163513-163524).
    # Bottom labels consume no instance/arrayPosition slot. Do not derive these
    # expectations from tree.project_orders or tree.renumber.
    main = [
        ("top-try", "", 0, None),
        ("m-source", "top-try", 1, None),
        ("m-loop", "top-try", 2, 5),
        ("m-if", "m-loop", 3, None),
        ("m-work", "m-if", 4, None),
        ("m-tail", "top-try", 6, None),
        ("m-stage-if", "top-try", 7, None),
        ("m-stage-work", "m-stage-if", 8, None),
        ("top-catch", "", 9, None),
    ]
    if profile == "nested":
        return main + [
            ("h-source", "top-catch", 10, None),
            ("h-loop", "top-catch", 11, 17),
            ("h-if", "h-loop", 12, None),
            ("h-try", "h-if", 13, None),
            ("h-work", "h-try", 14, None),
            ("h-catch", "h-if", 15, None),
            ("h-recover", "h-catch", 16, None),
            ("h-tail", "top-catch", 18, None),
        ]
    if profile == "parallel":
        return main + [
            ("h-source", "top-catch", 10, None),
            ("h-parallel", "top-catch", 11, None),
            ("h-left", "h-parallel", 12, None),
            ("h-loop", "h-left", "12->13", 16),
            ("h-if", "h-loop", 14, None),
            ("h-left-work", "h-if", 15, None),
            ("h-right", "h-parallel", 17, None),
            ("h-right-work", "h-right", "17->18", None),
            ("h-tail", "top-catch", 19, None),
        ]
    raise ValueError(f"Unknown complex handler profile: {profile}")


def complex_handler_flow(profile="nested"):
    """Deterministic source-native DTO for offline tests and parent MCP smoke.

    Uses real-shaped saved IDs, not an assertion that a live save was captured.
    The nested and Parallel cases keep their handler instances below ten.
    """
    flow = base_flow()
    template = copy.deepcopy(flow["actionInstances"][0])
    catalog = {row["type"]: row for row in complex_handler_definitions()}
    logic_types = {
        "top-try": "TOP_LEVEL_TRY",
        "top-catch": "TOP_LEVEL_CATCH",
        "m-loop": "FOREACH",
        "m-if": "IF",
        "m-stage-if": "IF",
        "h-loop": "FOREACH",
        "h-if": "IF",
        "h-try": "TRY",
        "h-catch": "CATCH",
        "h-parallel": "PARALLEL",
        "h-left": "PARALLELBLOCK",
        "h-right": "PARALLELBLOCK",
    }
    connections = {
        "top-catch": "top-try",
        "h-catch": "h-try",
        "h-left": "h-parallel",
        "h-right": "h-parallel",
    }
    projection = complex_handler_projection(profile)
    for collection in tree.COLLECTIONS.values():
        flow[collection] = []
    flow["opaque"] = {"studio_profile": profile, "preserve": [True, {"version": 27}]}
    flow["startOfErrorHandlingInstances"] = 8
    flow["startingIndexOfErrorHandlingInstances"] = 8
    flow["stages"] = {}
    for position, (uid, parent, order, bottom) in enumerate(projection):
        if uid in logic_types:
            kind = "flowlogic"
            logic_type = logic_types[uid]
            inputs = {}
            if logic_type == "FOREACH":
                producer = "m-source" if uid.startswith("m-") else "h-source"
                inputs = {"items": "{{" + producer + ".records}}"}
            elif logic_type == "IF":
                inputs = {"condition": "true"}
            elif logic_type in {"CATCH", "TOP_LEVEL_CATCH"}:
                inputs = {"enabled": True}
            raw = tree.new_logic_step(FLOW, catalog[logic_type], inputs).raw
            raw["children"] = [
                child for child, owner, _, _ in projection if owner == uid
            ]
            if uid in connections:
                raw["connectedTo"] = connections[uid]
        else:
            kind = "action"
            raw = copy.deepcopy(template)
            raw["inputs"][0]["value"] = uid
            raw["inputs"][0]["parameter"]["name"] = "log_message"
            raw["actionType"]["outputs"] = [
                {"name": "text", "type": "string", "children": []},
                {
                    "name": "records",
                    "type": "records",
                    "reference": "sc_req_item",
                    "children": [],
                },
            ]
        raw.update(
            id=f"{0x100 + position:032x}",
            uiUniqueIdentifier=uid,
            parent=parent,
            order=order,
            arrayPosition=position,
            isNew=False,
            errorHandlingInstance=position > 8,
            readOnly=False,
            opaque_instance={"identity": uid, "preserve": [True, {"revision": 27}]},
        )
        raw.setdefault("metadata", {})["opaque_studio"] = {"identity": uid}
        if bottom is not None:
            raw["orderBottom"] = bottom
        flow[tree.COLLECTIONS[kind]].append(raw)
    return flow


def complex_handler_routes(settings, flow=None, *, profile="nested", catalog=None):
    """Install mocked HTTP routes only; callers own the mock transport scope."""
    if flow is None:
        flow = complex_handler_flow(profile)
    coordinator, tools = routes(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200,
        json={
            "result": catalog if catalog is not None else complex_handler_definitions()
        },
    )
    return coordinator, tools


def complex_handler_edits():
    """Reusable safe edit plan: no activation, publication, or execution."""
    return [
        {"op": "update_inputs", "step": "h-tail", "inputs": {"log_message": "changed"}},
        {"op": "create_stage", "label": "Work", "value": "work", "ref": "work"},
        {"op": "attach_stage", "stage": "work", "step": "m-tail"},
        {"op": "create_stage", "type": "error", "step": "m-stage-work"},
    ]


def assert_complex_handler_projection(payload, expected):
    ordered = list(tree.walk(tree.build_tree(payload)))
    assert [
        (row.uid, row.raw["parent"], row.raw["order"], row.raw.get("orderBottom"))
        for row in ordered
    ] == expected
    assert [row.raw["arrayPosition"] for row in ordered] == list(range(len(expected)))
    for row in ordered:
        if row.kind == "flowlogic":
            assert row.raw["children"] == [
                uid for uid, owner, _, _ in expected if owner == row.uid
            ]


def assert_complex_handler_preserved(original, payload, *, changed_inputs=()):
    before = {step.uid: step.raw for step in tree.walk(tree.build_tree(original))}
    after = {step.uid: step.raw for step in tree.walk(tree.build_tree(payload))}
    assert after.keys() == before.keys()
    mutable = {
        "order",
        "arrayPosition",
        "orderBottom",
        "parent",
        "children",
        "readOnly",
        "errorHandlingInstance",
        *tree.UI_FIELDS,
    }
    for uid, row in before.items():
        for field, value in row.items():
            if field not in mutable and not (
                field in {"inputs", "data"} and uid in changed_inputs
            ):
                assert after[uid][field] == value, (uid, field)
        if uid in changed_inputs:
            for old, current in zip(row["inputs"], after[uid]["inputs"], strict=True):
                for field, value in old.items():
                    if field not in {"value", "displayValue"}:
                        assert current[field] == value, (uid, field)
    assert payload["opaque"] == original["opaque"]
    assert payload["active"] is False and payload["isPublished"] is False


async def save_complex_handler(
    settings, flow, edits, check_payload, *, defect=None, catalog=None
):
    coordinator, tools = complex_handler_routes(settings, flow, catalog=catalog)
    saved = {}

    def save(request):
        body = json.loads(request.content)
        check_payload(body)
        for index, row in enumerate(body["flowLogicInstances"], 0x300):
            row.setdefault("id", f"{index:032x}")
        for index, stage in enumerate(body["stages"].values(), 0x200):
            stage.setdefault("id", f"{index:032x}")
        saved.update(copy.deepcopy(body))
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        rows = {
            row["uiUniqueIdentifier"]: row
            for collection in tree.COLLECTIONS.values()
            for row in actual[collection]
        }
        if defect == "order":
            rows["h-tail"]["order"] += 1
        elif defect == "arrow_prefix":
            rows["h-loop"]["order"] = "99->13"
        elif defect == "arrayPosition":
            rows["h-tail"]["arrayPosition"] += 1
        elif defect == "orderBottom":
            rows["h-loop"]["orderBottom"] += 1
        elif defect == "parent":
            rows["h-tail"]["parent"] = "h-if"
        elif defect == "legacy_type":
            rows["top-try"]["flowLogicDefinition"]["type"] = "TOP_LEVEL_TRY"
        elif defect == "legacy_connection":
            rows["top-catch"]["connectedTo"] = "h-try"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert write.call_count == 1 and len(coordinator.store) == 0
    put_index = next(
        index
        for index, call in enumerate(http_mock.calls)
        if call.request.method == "PUT"
    )
    assert any(
        call.request.method == "GET"
        and call.request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for call in http_mock.calls[put_index + 1 :]
    )
    return saved


@pytest.mark.parametrize("profile", ["nested", "parallel"])
@pytest.mark.parametrize(
    "defect", [None, "order", "arrayPosition", "orderBottom", "parent"]
)
async def test_complex_handler_native_projection_save_and_independent_readback(
    settings, profile, defect
):
    flow = complex_handler_flow(profile)
    complex_handler_routes(settings, flow)
    expected = complex_handler_projection(profile)

    def check(payload):
        assert_complex_handler_projection(payload, expected)
        assert_complex_handler_preserved(flow, payload, changed_inputs={"h-tail"})
        tail = next(
            row
            for row in payload["actionInstances"]
            if row["uiUniqueIdentifier"] == "h-tail"
        )
        assert tail["inputs"][0]["value"] == "changed"
        assert payload["startOfErrorHandlingInstances"] == 8
        assert payload["startingIndexOfErrorHandlingInstances"] == 8
        standard = next(
            row for row in payload["stages"].values() if row["type"] == "standard"
        )
        error = next(
            row for row in payload["stages"].values() if row["type"] == "error"
        )
        assert standard["componentIndexes"] == [5]  # m-tail is label 6, instance 5.
        assert error["componentIndexes"] == [7]  # m-stage-work is label 8.
        assert error["ancestorArrPos"] == 5
        assert error["ancestorStageId"] == standard["stageId"]
        assert error["ancestralIfElseLogic"] == "m-stage-if"

    check((await plan(settings, flow, complex_handler_edits()))["payload"])
    await save_complex_handler(
        settings, flow, complex_handler_edits(), check, defect=defect
    )


async def test_complex_parallel_arrow_prefix_drift_is_unknown_outcome(settings):
    flow = complex_handler_flow("parallel")

    def check(payload):
        assert_complex_handler_projection(
            payload, complex_handler_projection("parallel")
        )
        assert_complex_handler_preserved(flow, payload, changed_inputs={"h-tail"})

    await save_complex_handler(
        settings, flow, complex_handler_edits(), check, defect="arrow_prefix"
    )


@pytest.mark.parametrize("destination", ["main", "handler"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_complex_handler_paired_try_moves_across_regions_and_saves(
    settings, destination, legacy
):
    flow = legacy_handler_flow() if legacy else complex_handler_flow()
    catalog = legacy_handler_definitions(upgraded=True) if legacy else None
    edits = [{"op": "move_step", "step": "h-try", "parent": "main"}]
    expected = [
        ("top-try", "", 0, None),
        ("m-source", "top-try", 1, None),
        ("m-loop", "top-try", 2, 5),
        ("m-if", "m-loop", 3, None),
        ("m-work", "m-if", 4, None),
        ("m-tail", "top-try", 6, None),
        ("m-stage-if", "top-try", 7, None),
        ("m-stage-work", "m-stage-if", 8, None),
        ("h-try", "top-try", 9, None),
        ("h-work", "h-try", 10, None),
        ("h-catch", "top-try", 11, None),
        ("h-recover", "h-catch", 12, None),
        ("top-catch", "", 13, None),
        ("h-source", "top-catch", 14, None),
        ("h-loop", "top-catch", 15, 17),
        ("h-if", "h-loop", 16, None),
        ("h-tail", "top-catch", 18, None),
    ]
    if destination == "handler":
        complex_handler_routes(settings, flow, catalog=catalog)
        flow = (await plan(settings, flow, edits))["payload"]
        edits = [{"op": "move_step", "step": "h-try", "parent": "h-if"}]
        expected = complex_handler_projection()

    def check(payload):
        assert_complex_handler_projection(payload, expected)
        assert_complex_handler_preserved(flow, payload)
        rows = {step.uid: step.raw for step in tree.walk(tree.build_tree(payload))}
        assert rows["h-catch"]["connectedTo"] == "h-try"
        assert rows["h-work"]["parent"] == "h-try"
        assert rows["h-recover"]["parent"] == "h-catch"
        assert all(
            rows[uid]["errorHandlingInstance"] is (destination == "handler")
            for uid in ("h-try", "h-work", "h-catch", "h-recover")
        )
        assert payload["startOfErrorHandlingInstances"] == (
            12 if destination == "main" else 8
        )

    await save_complex_handler(settings, flow, edits, check, catalog=catalog)


@pytest.mark.parametrize(
    ("profile", "consumer", "pill"),
    [
        ("nested", "h-work", "m-source.text"),
        ("nested", "m-tail", "h-source.text"),
        ("parallel", "h-right-work", "h-left-work.text"),
    ],
)
async def test_complex_handler_pills_cannot_cross_regions_or_parallel_branches(
    settings, profile, consumer, pill
):
    flow = complex_handler_flow(profile)
    coordinator, tools = complex_handler_routes(settings, flow)
    before = copy.deepcopy(flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": consumer,
                    "inputs": {"log_message": "{{" + pill + "}}"},
                }
            ],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0 and flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("profile", ["nested", "parallel"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_complex_handler_populated_toggle_preserves_projection_and_readonly(
    settings, profile, enabled
):
    flow = complex_handler_flow(profile)
    if enabled:
        complex_handler_routes(settings, flow)
        flow = (
            await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])
        )["payload"]

    def check(payload):
        assert_complex_handler_projection(payload, complex_handler_projection(profile))
        assert_complex_handler_preserved(flow, payload, changed_inputs={"top-catch"})
        rows = {step.uid: step.raw for step in tree.walk(tree.build_tree(payload))}
        catch = rows["top-catch"]
        assert catch["inputs"][0]["value"] is enabled
        assert catch["data"]["enabled"] is enabled
        for uid, row in rows.items():
            if uid == "top-catch" or uid.startswith("h-"):
                assert row["readOnly"] is not enabled
            else:
                assert row["readOnly"] is False

    await save_complex_handler(
        settings, flow, [{"op": "set_error_handler", "enabled": enabled}], check
    )


@pytest.mark.parametrize("operation", ["disable", "remove"])
async def test_complex_empty_handler_unwrap_keeps_gap_positions_and_stage_source(
    settings, operation
):
    flow = complex_handler_flow()
    for collection in tree.COLLECTIONS.values():
        flow[collection] = [
            row
            for row in flow[collection]
            if not row["uiUniqueIdentifier"].startswith("h-")
        ]
    next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "top-catch"
    )["children"] = []
    complex_handler_routes(settings, flow)
    flow = (await plan(settings, flow, complex_handler_edits()[1:]))["payload"]
    for index, stage in enumerate(flow["stages"].values(), 0x200):
        stage["id"] = f"{index:032x}"
        stage["opaque_stage"] = {"preserve": True}
    edit = (
        {"op": "remove_error_handler"}
        if operation == "remove"
        else {"op": "set_error_handler", "enabled": False}
    )

    def check(payload):
        assert_complex_handler_projection(
            payload,
            [
                ("m-source", "", 1, None),
                ("m-loop", "", 2, 5),
                ("m-if", "m-loop", 3, None),
                ("m-work", "m-if", 4, None),
                ("m-tail", "", 6, None),
                ("m-stage-if", "", 7, None),
                ("m-stage-work", "m-stage-if", 8, None),
            ],
        )
        assert payload["startOfErrorHandlingInstances"] == -1
        assert payload["startingIndexOfErrorHandlingInstances"] == -1
        assert {row["id"] for row in payload["deletedFlowLogicInstances"]} == {
            "00000000000000000000000000000100",
            "00000000000000000000000000000108",
        }
        standard = next(
            row for row in payload["stages"].values() if row["type"] == "standard"
        )
        error = next(
            row for row in payload["stages"].values() if row["type"] == "error"
        )
        assert standard["componentIndexes"] == [4]
        assert error["componentIndexes"] == [6] and error["ancestorArrPos"] == 4
        assert error["ancestralIfElseLogic"] == "m-stage-if"
        for key, stage in flow["stages"].items():
            assert payload["stages"][key]["id"] == stage["id"]
            assert payload["stages"][key]["opaque_stage"] == {"preserve": True}
        original = {step.uid: step.raw for step in tree.walk(tree.build_tree(flow))}
        for step in tree.walk(tree.build_tree(payload)):
            assert step.raw["id"] == original[step.uid]["id"]
            assert step.raw["opaque_instance"] == original[step.uid]["opaque_instance"]
            assert step.raw["errorHandlingInstance"] is False

    await save_complex_handler(settings, flow, [edit], check)


async def test_complex_profile_still_rejects_unknown_rome_first_try(settings):
    flow = complex_handler_flow()
    catalog = {row["type"]: row for row in complex_handler_definitions()}
    for row in flow["flowLogicInstances"]:
        if row["uiUniqueIdentifier"] in {"top-try", "top-catch"}:
            kind = "TRY" if row["uiUniqueIdentifier"] == "top-try" else "CATCH"
            replacement = tree.new_logic_step(
                FLOW, catalog[kind], {} if kind == "TRY" else {"enabled": True}
            ).raw
            row["flowLogicDefinition"] = replacement["flowLogicDefinition"]
            row["definitionId"] = replacement["definitionId"]
            row["inputs"] = replacement["inputs"]
            row["data"] = replacement["data"]
    coordinator, tools = complex_handler_routes(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": False}],
        )
    )
    assert result["status"] == "error" and result["error"]["code"] == "UNAVAILABLE", (
        result
    )
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_complex_parallel_handler_capacity_counts_instances_not_gap_labels(
    settings,
):
    flow = complex_handler_flow("parallel")
    complex_handler_routes(settings, flow)
    extra = {
        "op": "insert_logic",
        "logic": "If",
        "inputs": {"condition": "true"},
        "parent": "error_handler",
    }
    built = (await plan(settings, flow, [extra]))["payload"]
    catch = tree.build_tree(built)[1]
    assert len(list(tree.walk(catch.children))) == 10
    last = list(tree.walk(catch.children))[-1].raw
    assert last["order"] == 20
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [extra, extra])
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_complex_nested_sandwich_retains_saved_bottom_and_verifies_get(
    settings,
):
    flow = complex_handler_flow()
    catalog = complex_handler_definitions()
    try_definition = next(row for row in catalog if row["type"] == "TRY")
    try_definition["attributes"]["display_as_sandwich"] = "true"
    nested_try = next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "h-try"
    )
    nested_try["flowLogicDefinition"] = copy.deepcopy(try_definition)
    # A supplied saved cache, not a reconstructed label: Studio's one active
    # sandwich counter does not overwrite the nested populated Try's bottom.
    nested_try["orderBottom"] = 15
    expected = [
        (uid, owner, order, 15 if uid == "h-try" else bottom)
        for uid, owner, order, bottom in complex_handler_projection()
    ]
    complex_handler_routes(settings, flow, catalog=catalog)

    def check(payload):
        assert_complex_handler_projection(payload, expected)
        assert_complex_handler_preserved(flow, payload, changed_inputs={"h-tail"})

    check((await plan(settings, flow, complex_handler_edits()))["payload"])
    await save_complex_handler(
        settings, flow, complex_handler_edits(), check, catalog=catalog
    )


@pytest.mark.parametrize("cache", [None, "15", True])
async def test_complex_nested_sandwich_missing_or_noninteger_bottom_is_unavailable(
    settings, cache
):
    flow = complex_handler_flow()
    catalog = complex_handler_definitions()
    try_definition = next(row for row in catalog if row["type"] == "TRY")
    try_definition["attributes"]["display_as_sandwich"] = "true"
    nested_try = next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "h-try"
    )
    nested_try["flowLogicDefinition"] = copy.deepcopy(try_definition)
    if cache is not None:
        nested_try["orderBottom"] = cache
    coordinator, tools = complex_handler_routes(settings, flow, catalog=catalog)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=complex_handler_edits()
        )
    )
    assert preview["status"] == "error" and preview["error"]["code"] == "UNAVAILABLE", (
        preview
    )
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


def legacy_handler_definitions(*, upgraded=False):
    """Source fallback TRY and metadata-associated TOP_LEVEL_CATCH, not CATCH."""
    catalog = complex_handler_definitions()
    main = next(row for row in catalog if row["type"] == "TRY")
    catch = next(row for row in catalog if row["type"] == "TOP_LEVEL_CATCH")
    modern = next(row for row in catalog if row["type"] == "TOP_LEVEL_TRY")
    catch["connectedTo"] = main["id"] + "," + modern["id"]
    return catalog if upgraded else [row for row in catalog if row is not modern]


def legacy_handler_flow(profile="nested"):
    flow = complex_handler_flow(profile)
    definition = next(
        row for row in legacy_handler_definitions() if row["type"] == "TRY"
    )
    main = next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "top-try"
    )
    main["flowLogicDefinition"] = copy.deepcopy(definition)
    main["definitionId"] = definition["id"]
    catch = next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "top-catch"
    )
    catch["flowLogicDefinition"] = copy.deepcopy(
        next(
            row
            for row in legacy_handler_definitions()
            if row["type"] == "TOP_LEVEL_CATCH"
        )
    )
    return flow


@pytest.mark.parametrize("profile", ["nested", "parallel"])
@pytest.mark.parametrize("upgraded", [False, True])
@pytest.mark.parametrize("defect", [None, "legacy_type", "legacy_connection"])
async def test_legacy_handler_edits_save_and_independent_readback(
    settings, profile, upgraded, defect
):
    flow = legacy_handler_flow(profile)
    catalog = legacy_handler_definitions(upgraded=upgraded)

    def check(payload):
        assert_complex_handler_projection(payload, complex_handler_projection(profile))
        assert_complex_handler_preserved(flow, payload, changed_inputs={"h-tail"})
        roots = tree.build_tree(payload)
        assert roots[0].logic_type == handlers.TRY
        assert roots[0].raw["flowLogicDefinition"]["type"] == "TRY"
        assert (
            roots[0].raw["definitionId"]
            == flow["flowLogicInstances"][0]["definitionId"]
        )
        assert roots[1].raw["connectedTo"] == roots[0].uid
        assert payload["startOfErrorHandlingInstances"] == 8
        assert (
            next(
                row
                for row in payload["actionInstances"]
                if row["uiUniqueIdentifier"] == "m-source"
            )["ancestorType"]
            == "TRY"
        )
        assert handlers.summary(payload)["handler"] == "top-catch"
        semantics.studio_semantics(payload)

    await save_complex_handler(
        settings, flow, complex_handler_edits(), check, catalog=catalog, defect=defect
    )


async def test_legacy_catalog_fallback_creates_native_pair_without_ordinary_catch(
    settings,
):
    flow = base_flow()
    flow["stages"] = {}
    catalog = legacy_handler_definitions()

    def check(payload):
        roots = tree.build_tree(payload)
        assert [row.raw["flowLogicDefinition"]["type"] for row in roots] == [
            "TRY",
            "TOP_LEVEL_CATCH",
        ]
        assert [row.raw["order"] for row in roots] == [0, 4]
        assert [row.uid for row in roots[0].children] == ["ui-1", "ui-2", "ui-4"]
        assert [row.uid for row in roots[1].children] == ["ui-3"]
        assert payload["startOfErrorHandlingInstances"] == 4
        assert len(payload["flowLogicInstances"]) == 2
        assert roots[0].raw["definitionId"] == next(
            row["id"] for row in catalog if row["type"] == "TRY"
        )

    await save_complex_handler(
        settings,
        flow,
        [
            {"op": "set_error_handler", "enabled": True},
            {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
        ],
        check,
        catalog=catalog,
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_legacy_populated_toggle_keeps_identity_contents_and_readonly(
    settings, enabled
):
    flow = legacy_handler_flow()
    catalog = legacy_handler_definitions(upgraded=True)
    if enabled:
        complex_handler_routes(settings, flow, catalog=catalog)
        flow = (
            await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])
        )["payload"]

    def check(payload):
        assert_complex_handler_projection(payload, complex_handler_projection())
        assert_complex_handler_preserved(flow, payload, changed_inputs={"top-catch"})
        main, catch = tree.build_tree(payload)
        assert main.raw["flowLogicDefinition"]["type"] == "TRY"
        assert handlers.enabled(catch) is enabled
        assert all(
            row.raw["readOnly"] is not enabled for row in tree.walk(catch.children)
        )

    await save_complex_handler(
        settings,
        flow,
        [{"op": "set_error_handler", "enabled": enabled}],
        check,
        catalog=catalog,
    )


@pytest.mark.parametrize("operation", ["remove", "disable"])
async def test_legacy_empty_handler_unwrap_preserves_stages_and_main(
    settings, operation
):
    flow = legacy_handler_flow()
    catalog = legacy_handler_definitions()
    complex_handler_routes(settings, flow, catalog=catalog)
    flow = (await plan(settings, flow, complex_handler_edits()[1:]))["payload"]
    for index, stage in enumerate(flow["stages"].values(), 0x200):
        stage["id"] = f"{index:032x}"
    main, catch = tree.build_tree(flow)
    deleted = [row.raw["id"] for row in tree.walk(catch.children)]
    edits: list[dict[str, Any]] = [
        {"op": "delete_step", "step": row.uid, "children": "delete"}
        for row in catch.children
    ]
    edits.append(
        {"op": "remove_error_handler"}
        if operation == "remove"
        else {"op": "set_error_handler", "enabled": False}
    )
    original_ids = {row.uid: row.raw["id"] for row in tree.walk(main.children)}

    def check(payload):
        assert handlers.summary(payload)["present"] is False
        assert payload["startOfErrorHandlingInstances"] == -1
        roots = tree.build_tree(payload)
        assert {row.uid: row.raw["id"] for row in tree.walk(roots)} == original_ids
        assert all(
            row.raw["errorHandlingInstance"] is False for row in tree.walk(roots)
        )
        tombstones = {row["id"] for row in payload["deletedFlowLogicInstances"]}
        assert {main.raw["id"], catch.raw["id"]} <= tombstones
        assert not any(row["id"] in deleted for row in payload["actionInstances"])
        stage = next(
            row for row in payload["stages"].values() if row["type"] == "error"
        )
        assert stage["ancestralIfElseLogic"] == "m-stage-if"

    await save_complex_handler(settings, flow, edits, check, catalog=catalog)


@pytest.mark.parametrize(
    "defect",
    [
        "ordinary_catch",
        "definition_link",
        "instance_link",
        "input",
        "sandwich",
        "first_array",
        "region",
    ],
)
async def test_unknown_legacy_profile_never_stages_or_normalizes(settings, defect):
    flow = legacy_handler_flow()
    catalog = legacy_handler_definitions()
    main = flow["flowLogicInstances"][0]
    catch = next(
        row
        for row in flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "top-catch"
    )
    if defect == "ordinary_catch":
        catch["flowLogicDefinition"]["type"] = "CATCH"
    elif defect == "definition_link":
        catch["flowLogicDefinition"]["connectedTo"] = "f" * 32
    elif defect == "instance_link":
        catch["connectedTo"] = "h-try"
    elif defect == "input":
        main["inputs"] = [
            {"name": "custom", "value": "x", "parameter": {"type": "string"}}
        ]
    elif defect == "sandwich":
        main["flowLogicDefinition"]["attributes"]["display_as_sandwich"] = "true"
    elif defect == "first_array":
        flow["flowLogicInstances"].reverse()
    else:
        flow["actionInstances"][0]["errorHandlingInstance"] = True
    before = copy.deepcopy(flow)
    coordinator, tools = complex_handler_routes(settings, flow, catalog=catalog)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": False}],
        )
    )
    assert result["status"] == "error" and result["error"]["code"] == "UNAVAILABLE", (
        result
    )
    assert flow == before and len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("enabled", [False, True])
async def test_legacy_handler_pills_and_capacity_keep_native_scope_guards(
    settings, enabled
):
    flow = legacy_handler_flow("parallel")
    catalog = legacy_handler_definitions()
    complex_handler_routes(settings, flow, catalog=catalog)
    if not enabled:
        flow = (
            await plan(settings, flow, [{"op": "set_error_handler", "enabled": False}])
        )["payload"]
        complex_handler_routes(settings, flow, catalog=catalog)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "h-tail",
                    "inputs": {"log_message": "{{m-source.text}}"},
                }
            ],
        )
    if enabled:
        with pytest.raises(CapabilityError, match="at most ten"):
            await plan(
                settings,
                flow,
                [
                    {
                        "op": "insert_logic",
                        "logic": "If",
                        "inputs": {"condition": "true"},
                        "parent": "error_handler",
                    }
                ]
                * 2,
            )
        saved = await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "h-tail",
                    "inputs": {
                        "log_message": "{{error_handler.fixture_status.message}}"
                    },
                }
            ],
        )
        tail = next(
            row
            for row in saved["payload"]["actionInstances"]
            if row["uiUniqueIdentifier"] == "h-tail"
        )
        assert tail["inputs"][0]["value"] == "{{top-catch.fixture_status.message}}"


async def test_ordinary_first_try_remains_ordinary_without_handler_evidence(settings):
    flow = base_flow()
    flow["stages"] = {}
    catalog = legacy_handler_definitions()

    def check(payload):
        roots = tree.build_tree(payload)
        assert [row.logic_type for row in roots[:2]] == ["TRY", "CATCH"]
        assert roots[0].raw["order"] == 1
        assert roots[1].raw["connectedTo"] == roots[0].uid
        assert roots[0].children[0].uid == "ui-1"
        assert handlers.summary(payload)["present"] is False
        assert not roots[0].handler_try

    await save_complex_handler(
        settings,
        flow,
        [
            {"op": "insert_logic", "logic": "TRY", "before": "ui-1", "ref": "attempt"},
            {"op": "move_step", "step": "ui-1", "parent": "attempt"},
        ],
        check,
        catalog=catalog,
    )
