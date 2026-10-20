"""Studio-source fixtures for handlers and conditional error stages, not live captures."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import stages, tree
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


async def test_handler_readonly_validation_integration(settings):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.capabilities.flows import lifecycle
    from servicenow_mcp.platform.client import ServiceNowClient
    from tests.test_flow_lifecycle import validation_flow, validation_routes

    flow = validation_flow()
    routes(settings, flow)
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
        200, json={"result": definitions()}
    )
    before = copy.deepcopy(payload)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(payload, client)
    assert result["complete"] is True and result["valid"] is True, result
    assert before == payload
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("defect", ["context", "revision", "authorization"])
async def test_error_handler_capture_and_authorization_binding_before_write(
    settings, defect
):
    from tests.test_flow_lifecycle import capture_routes

    coordinator, tools = routes(settings, base_flow())
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
            edits=[{"op": "set_error_handler", "enabled": True}],
        )
    )
    assert preview["status"] == "success", preview
    if defect == "context":
        http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
            200,
            json={
                "result": {
                    "currentApplication": {"sysId": "global"},
                    "currentUpdateSet": {"sysId": "e" * 32},
                }
            },
        )
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
