"""Paired moves, subflow creation, and one-shot lifecycle source-derived fixtures."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import tree
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_lifecycle import (
    capture_routes,
    status_header,
    validation_flow,
    validation_routes,
)


@pytest.fixture(autouse=True)
def no_update_set_reads():
    yield
    assert all(
        "sys_update_" not in call.request.url.path
        and "concoursepicker" not in call.request.url.path
        for call in http_mock.calls
    )


async def paired_flow(settings):
    flow = base_flow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    flow = (
        await plan(
            settings,
            flow,
            [
                {"op": "insert_logic", "logic": "Try", "after": "ui-1", "ref": "pair"},
                {"op": "move_step", "step": "ui-2", "parent": "pair"},
            ],
        )
    )["payload"]
    for index, row in enumerate(flow["flowLogicInstances"], 20):
        row["id"] = f"{index:032x}"
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    return flow


@pytest.mark.parametrize("placement", [{"before": "ui-1"}, {"after": "ui-4"}])
async def test_persisted_try_pair_moves_as_one_unit(settings, placement):
    flow = await paired_flow(settings)
    original = copy.deepcopy(flow["flowLogicInstances"])
    result = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "move_step",
                    "step": original[0]["uiUniqueIdentifier"],
                    **placement,
                }
            ],
        )
    )["payload"]
    pair = result["flowLogicInstances"]
    assert [row["id"] for row in pair] == [row["id"] for row in original]
    assert pair[1]["connectedTo"] == pair[0]["uiUniqueIdentifier"]
    assert pair[0]["children"] == ["ui-2"]
    assert int(pair[1]["order"]) == int(pair[0]["order"]) + 2


async def test_try_pair_moves_across_parent_and_rejects_independent_catch(settings):
    flow = await paired_flow(settings)
    roots = tree.build_tree(flow)
    pair = next(s for s in roots if s.logic_type == "TRY")
    catch = next(s for s in roots if s.logic_type == "CATCH")
    with pytest.raises(CapabilityError, match="paired Try"):
        await plan(
            settings, flow, [{"op": "move_step", "step": catch.uid, "before": "ui-1"}]
        )
    with pytest.raises(CapabilityError):
        await plan(
            settings, flow, [{"op": "move_step", "step": pair.uid, "parent": "ui-2"}]
        )
    result = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "inputs": {"condition": "true"},
                    "ref": "outer",
                },
                {"op": "move_step", "step": pair.uid, "parent": "outer"},
            ],
        )
    )["payload"]
    moved = [
        s
        for s in tree.walk(tree.build_tree(result))
        if s.logic_type in {"TRY", "CATCH"}
    ]
    assert moved[0].raw["parent"] == moved[1].raw["parent"]


@pytest.mark.parametrize("defect", [None, "type", "active", "echo"])
async def test_native_subflow_creation_is_triggerless_and_independently_verified(
    settings, defect
):
    _, tools = fixtures(settings, base_flow())
    http_mock.get(f"{BASE}/api/now/table/sys_scope/global").respond(
        200, json={"result": {"sys_id": "global", "name": "Global"}}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow").respond(
        200, json={"result": []}
    )

    def create(request):
        properties = json.loads(request.content)
        assert properties["type"] == "subflow" and properties["active"] is False
        saved = base_flow()
        saved.update(properties)
        saved.update(actionInstances=[], subFlowInstances=[])
        echo = copy.deepcopy(saved)
        if defect == "type":
            saved["type"] = "flow"
        elif defect == "active":
            saved["active"] = True
        elif defect == "echo":
            echo["type"] = "flow"
        persist(saved)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.post(f"{BASE}/api/now/processflow/flow").mock(side_effect=create)
    preview = decode_response(
        await tools["flow_preview"](
            action="create", flow_type="subflow", name="Created subflow", scope="global"
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["type"] == "subflow"
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        result["error"]["code"] == "UNKNOWN_OUTCOME"
        if defect
        else result["status"] == "success"
    ), result
    assert write.call_count == 1


def effect_routes(settings, action):
    flow = validation_flow()
    if action == "publish":
        flow["type"] = "subflow"
        flow["triggerInstances"] = []
    if action == "deactivate":
        flow.update(
            active=True, status="published", isPublished=True, masterSnapshotId="b" * 32
        )
    validation_routes(flow)
    coordinator, tools = fixtures(settings, flow)
    capture_routes()
    header = status_header()
    header.update(
        type=flow["type"],
        active="true" if flow["active"] else "false",
        status=flow["status"],
        master_snapshot="b" * 32 if action == "deactivate" else "",
        latest_snapshot="b" * 32 if action == "deactivate" else "",
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": header})
    )
    args = {
        "action": action,
        "sys_id": FLOW,
        "expected_scope": "global",
        "expected_revision": "7",
    }
    if action == "test":
        args["inputs"] = {"request_item": "f" * 32}
        http_mock.get(f"{BASE}/api/now/table/sc_req_item/{'f' * 32}").respond(
            200, json={"result": {"sys_id": "f" * 32, "sys_mod_count": "0"}}
        )
    return coordinator, tools, flow, header, args


@pytest.mark.parametrize("action", ["activate", "publish", "deactivate", "test"])
@pytest.mark.parametrize("definition_size", [None, 304455])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "timeout",
        "invalid_json",
        "wrong_identity",
        "echo_get",
        "content",
        "effect",
    ],
)
async def test_lifecycle_effect_is_one_shot_and_independently_reconciled(
    settings, action, defect, definition_size
):
    coordinator, tools, flow, header, args = effect_routes(settings, action)
    if definition_size:
        flow["componentInstances"] = ["x" * definition_size]
    route = (
        "test"
        if action == "test"
        else "subflow"
        if action == "publish"
        else "snapshot"
        if action == "activate"
        else "deactivate"
    )

    def effect(request):
        if defect == "timeout":
            raise httpx2.ReadTimeout("private-response", request=request)
        if defect == "invalid_json":
            return httpx2.Response(200, content=b"private-response")
        if action == "test":
            body = json.loads(request.content)
            assert (
                body["runOnThread"] is False
                and body["outputMap"]["request_item"] == "f" * 32
            )
            assert body["flow"] == flow
            execution = {
                "sys_id": "d" * 32,
                "flow": FLOW,
                "state": "QUEUED",
                "sys_created_on": "2026-10-14 10:00:00",
                "sys_updated_on": "2026-10-14 10:00:00",
            }
            if defect:
                execution["flow"] = "e" * 32
                http_mock.get(f"{BASE}/api/now/processflow/flow/{'e' * 32}").respond(
                    200,
                    json={
                        "result": {
                            "data": {
                                "id": "e" * 32,
                                "parentFlow": "e" * 32,
                                "snapshot": True,
                            }
                        }
                    },
                )
            http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
                200, json={"result": execution}
            )
            return httpx2.Response(200, json={"result": {"data": "d" * 32}})
        saved = copy.deepcopy(flow)
        if action == "deactivate":
            saved["active"] = False
        else:
            saved.update(
                active=True,
                status="published",
                isPublished=True,
                masterSnapshotId="b" * 32,
            )
            http_mock.get(f"{BASE}/api/now/processflow/flow/{'b' * 32}").respond(
                200,
                json={
                    "result": {
                        "data": {
                            "id": "b" * 32,
                            "parentFlow": FLOW,
                            "type": flow["type"],
                            "snapshot": True,
                            "masterSnapshot": True,
                        }
                    }
                },
            )
        echo = copy.deepcopy(saved)
        if defect == "wrong_identity":
            echo["id"] = "e" * 32
        elif defect == "echo_get":
            saved["masterSnapshotId"] = "e" * 32
        elif defect == "content":
            saved["actionInstances"][0]["inputs"][0]["value"] = "changed"
        elif defect == "effect":
            saved["active"] = not saved["active"]
        persist(saved)
        header.update(
            active=str(saved["active"]).lower(),
            status=saved["status"],
            master_snapshot=saved["masterSnapshotId"],
            latest_snapshot=saved["masterSnapshotId"],
        )
        header["sys_mod_count"] = "8"
        captured = {
            "sys_id": "8" * 32,
            "name": f"sys_hub_flow_{FLOW}",
            "update_set": "9" * 32,
            "application": "global",
            "sys_updated_on": "2026-10-14 10:00:00",
            "sys_mod_count": "8",
            "action": "INSERT_OR_UPDATE",
        }
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200, json={"result": [captured]}
        )
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(
        method="GET" if action == "deactivate" else "POST",
        url=f"{BASE}/api/now/processflow/flow/{FLOW}/{route}",
    ).mock(side_effect=effect)
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
        assert "private-response" not in str(result)
    else:
        assert result["status"] == "success", result
    assert write.call_count == 1 and len(coordinator.store) == 0
    retry = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert retry["status"] == "error" and write.call_count == 1


@pytest.mark.parametrize("action", ["activate", "publish", "deactivate", "test"])
@pytest.mark.parametrize(
    "defect", ["scope", "revision", "definition", "grant", "policy"]
)
async def test_lifecycle_binding_drift_never_dispatches_effect(
    settings, action, defect, monkeypatch
):
    from servicenow_mcp import policy

    coordinator, tools, flow, header, args = effect_routes(settings, action)
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    if defect == "scope":
        header["sys_scope"] = "e" * 32
    elif defect == "revision":
        header["sys_mod_count"] = "8"
    elif defect == "definition":
        flow["description"] = "changed"
    elif defect == "grant":
        coordinator.auth._generation += 1
    else:
        monkeypatch.setattr(
            policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_flow_snapshot"}
        )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert all(
        call.request.method == "GET"
        and not call.request.url.path.endswith("/deactivate")
        for call in http_mock.calls
    )


@pytest.mark.parametrize("action", ["activate", "publish", "deactivate", "test"])
async def test_lifecycle_requires_explicit_context_and_never_aliases_compile_or_run(
    settings, action
):
    coordinator, tools, _, _, _ = effect_routes(
        settings, "test" if action == "test" else "activate"
    )
    result = decode_response(await tools["flow_preview"](action=action, sys_id=FLOW))
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert not http_mock.calls


async def test_flow_publish_only_does_not_silently_activate(settings):
    coordinator, tools, _, _, args = effect_routes(settings, "activate")
    args["action"] = "publish"
    result = decode_response(await tools["flow_preview"](**args))
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"request_item": ""},
        {"request_item": None},
        {"request_item": "not-an-id"},
        {"request_item": "f" * 32, "unexpected": True},
        {"request_item": "{{producer.record}}"},
        {"request_item": {"value": "f" * 32}},
        {"table_name": "other"},
    ],
)
async def test_test_inputs_are_explicit_typed_static_and_schema_bound(settings, inputs):
    coordinator, tools, _, _, args = effect_routes(settings, "test")
    args["inputs"] = inputs
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "state", ["QUEUED", "WAITING", "COMPLETE", "ERROR", "CANCELLED", "UNKNOWN"]
)
async def test_asynchronous_test_result_reports_observed_state_without_polling_or_replay(
    settings, state
):
    _, tools, _flow, _, args = effect_routes(settings, "test")
    context = "d" * 32
    effect = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json={"result": {"data": context}}
    )
    read = http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{context}").respond(
        200,
        json={
            "result": {
                "sys_id": context,
                "flow": FLOW,
                "state": state,
                "sys_created_on": "2026-10-14 10:00:00",
                "sys_updated_on": "2026-10-14 10:00:00",
            }
        },
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if state == "UNKNOWN":
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
        assert context in result["error"]["message"]
    else:
        assert result["status"] == "success", result
        assert result["data"]["terminal"] == (
            state in {"COMPLETE", "ERROR", "CANCELLED"}
        )
        assert result["data"]["execution_succeeded"] is (
            state == "COMPLETE" if state in {"COMPLETE", "ERROR", "CANCELLED"} else None
        )
    assert effect.call_count == read.call_count == 1


@pytest.mark.parametrize("status", [202, 302, 403, 500])
@pytest.mark.parametrize("action", ["activate", "deactivate", "test"])
async def test_effect_http_and_incomplete_responses_remain_unknown(
    settings, action, status
):
    coordinator, tools, _, _, args = effect_routes(settings, action)
    suffix = "snapshot" if action == "activate" else action
    effect = http_mock.route(
        method="GET" if action == "deactivate" else "POST",
        url=f"{BASE}/api/now/processflow/flow/{FLOW}/{suffix}",
    ).respond(
        status,
        json={"result": {"data": {}}},
        headers={"Location": "https://blocked.example/"},
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert effect.call_count == 1 and len(coordinator.store) == 0


async def test_test_selected_record_revision_change_stales_preview(settings):
    _, tools, _, _, args = effect_routes(settings, "test")
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    http_mock.get(f"{BASE}/api/now/table/sc_req_item/{'f' * 32}").respond(
        200, json={"result": {"sys_id": "f" * 32, "sys_mod_count": "1"}}
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("defect", [None, "connected", "children", "parent", "id"])
async def test_saved_try_pair_move_put_keeps_all_native_identities(settings, defect):
    flow = await paired_flow(settings)
    _, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    pair = flow["flowLogicInstances"]

    def save(request):
        body = json.loads(request.content)
        assert [row["id"] for row in body["flowLogicInstances"]] == [
            row["id"] for row in pair
        ]
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        if defect == "connected":
            actual["flowLogicInstances"][1]["connectedTo"] = "other"
        elif defect == "children":
            actual["flowLogicInstances"][0]["children"] = []
        elif defect == "parent":
            next(
                row
                for row in actual["actionInstances"]
                if row["uiUniqueIdentifier"] == "ui-2"
            )["parent"] = ""
        elif defect == "id":
            actual["flowLogicInstances"][1]["id"] = "e" * 32
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "move_step",
                    "step": pair[0]["uiUniqueIdentifier"],
                    "after": "ui-4",
                }
            ],
        )
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


@pytest.mark.parametrize(
    "mutation",
    ["capture", "snapshot_owner", "snapshot_id", "header", "existing_step_id"],
)
async def test_activation_checks_snapshot_and_header_but_ignores_capture(
    settings, mutation
):
    _, tools, flow, header, args = effect_routes(settings, "activate")

    def effect(request):
        saved = copy.deepcopy(flow)
        saved.update(
            active=True, status="published", isPublished=True, masterSnapshotId="b" * 32
        )
        if mutation == "existing_step_id":
            saved["actionInstances"][0]["id"] = "e" * 32
        persist(saved)
        header.update(
            active="true",
            status="published",
            master_snapshot="b" * 32,
            latest_snapshot="b" * 32,
            sys_mod_count="8",
        )
        if mutation == "header":
            header["active"] = "false"
        if mutation != "capture":
            http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "8" * 32,
                            "name": f"sys_hub_flow_{FLOW}",
                            "update_set": "9" * 32,
                            "application": "global",
                            "sys_updated_on": "2026-10-14 10:00:00",
                            "sys_mod_count": "8",
                            "action": "INSERT_OR_UPDATE",
                        }
                    ]
                },
            )
        snapshot = {
            "id": "e" * 32 if mutation == "snapshot_id" else "b" * 32,
            "parentFlow": "e" * 32 if mutation == "snapshot_owner" else FLOW,
            "snapshot": True,
            "masterSnapshot": True,
            "type": "flow",
        }
        http_mock.get(f"{BASE}/api/now/processflow/flow/{'b' * 32}").respond(
            200, json={"result": {"data": snapshot}}
        )
        return httpx2.Response(200, json={"result": {"data": saved}})

    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/snapshot").mock(
        side_effect=effect
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if mutation == "capture":
        assert result["status"] == "success" and "capture" not in result["data"], result
    else:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert write.call_count == 1


async def test_subflow_scalar_test_uses_declarations_and_reports_real_effects(settings):
    _, tools, flow, _header, args = effect_routes(settings, "publish")
    flow["inputs"] = [
        {
            "id": "a" * 32,
            "name": "message",
            "type": "string",
            "mandatory": True,
            "maxsize": 50,
            "children": [],
        },
        {"id": "b" * 32, "name": "enabled", "type": "boolean", "children": []},
        {"id": "c" * 32, "name": "count", "type": "integer", "children": []},
    ]
    args.update(
        action="test", inputs={"message": "fixture", "enabled": True, "count": 3}
    )

    def effect(request):
        body = json.loads(request.content)
        assert body["outputMap"] == args["inputs"] and body["runOnThread"] is False
        assert body["flow"]["inputs"] == flow["inputs"]
        return httpx2.Response(200, json={"result": {"data": "d" * 32}})

    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").mock(
        side_effect=effect
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200, json={"result": {"sys_id": "d" * 32, "flow": FLOW, "state": "WAITING"}}
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    assert (
        preview["data"]["summary"]["effect"]
        == "execute_draft_with_real_action_side_effects"
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success" and result["data"]["state"] == "WAITING", (
        result
    )
    assert write.call_count == 1


@pytest.mark.parametrize("action", ["activate", "publish", "deactivate", "test"])
async def test_production_lifecycle_is_rejected_before_reads(prod_settings, action):
    coordinator, tools = fixtures(prod_settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](
            action=action,
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
        )
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert not http_mock.calls


async def test_subflow_can_receive_published_action_after_declaration(settings):
    from tests.test_flow_authoring import fixtures as action_fixtures

    coordinator, tools, flow, definition = action_fixtures(settings)
    flow["type"] = "subflow"
    flow["inputs"] = [{"name": "message", "type": "string", "children": []}]

    def save(request):
        body = json.loads(request.content)
        assert body["type"] == "subflow" and body["inputs"] == flow["inputs"]
        body["actionInstances"][-1]["id"] = "f" * 32
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=definition["id"]
        )
    )
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert write.call_count == 1 and len(coordinator.store) == 0


async def test_native_lifecycle_protocol_requires_preview_before_dispatch(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    _, _, _, _, args = effect_routes(settings, "test")
    server = NativeMCPServer("flow-lifecycle-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    context_id = "d" * 32
    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json={"result": {"data": context_id}}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{context_id}").respond(
        200,
        json={"result": {"sys_id": context_id, "flow": FLOW, "state": "IN_PROGRESS"}},
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
            preview = await session.call_tool("flow_preview", args)
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            assert not write.called
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert not applied.is_error, applied
            assert applied.structured_content["data"]["execution_sys_id"] == context_id
            assert applied.structured_content["data"]["execution_succeeded"] is None
            assert write.call_count == 1
        tasks.cancel_scope.cancel()


@pytest.mark.parametrize("subflow", [False, True])
@pytest.mark.parametrize(
    "defect", [None, "definition_drift", "unreadable", "echo_value", "id_race"]
)
async def test_structured_call_insertion_in_handler_uses_snapshot_and_native_pills(
    settings, subflow, defect
):
    from tests.test_flow_error_authoring import definitions

    flow = base_flow()
    _, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": definitions()}
    )
    definition_id = "b" * 32
    definition = {
        "id": definition_id,
        "name": "Fixture call",
        "scope": "global",
        "access": "public",
        "parentFlow": "c" * 32,
        "type": "subflow",
        "snapshot": True,
        "masterSnapshot": True,
        "state": "published",
        "is_snapshot": True,
        "security": {"can_read": defect != "unreadable"},
        "entitled": True,
        "isFlowEntitled": True,
        "inputs": [
            {"id": "d" * 32, "name": "message", "type": "string", "children": []}
        ],
        "outputs": [{"name": "text", "type": "string", "children": []}],
    }
    if subflow:
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{'c' * 32}").respond(
            200,
            json={
                "result": {
                    "sys_id": "c" * 32,
                    "type": "subflow",
                    "active": "true",
                    "master_snapshot": definition_id,
                }
            },
        )
        http_mock.get(f"{BASE}/api/now/processflow/flow/{definition_id}").mock(
            side_effect=lambda _: httpx2.Response(
                200, json={"result": {"data": definition}}
            )
        )
    else:
        http_mock.get(
            f"{BASE}/api/now/processflow/action/action_types/{definition_id}"
        ).mock(side_effect=lambda _: httpx2.Response(200, json={"result": definition}))

    def save(request):
        body = json.loads(request.content)
        added = next(
            row
            for row in body["subFlowInstances" if subflow else "actionInstances"]
            if not row.get("id")
        )
        assert added["parent"] == tree.build_tree(body)[1].uid
        assert body["active"] is False
        added["id"] = "e" * 32
        for index, row in enumerate(body["flowLogicInstances"], 20):
            row["id"] = f"{index:032x}"
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        if defect in {"echo_value", "id_race"}:
            row = next(
                row
                for row in actual["subFlowInstances" if subflow else "actionInstances"]
                if row["id"] == "e" * 32
            )
            if defect == "echo_value":
                row["inputs"][0]["value"] = "changed"
            else:
                row["id"] = "f" * 32
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    edits = [
        {"op": "set_error_handler", "enabled": True},
        {
            "op": "insert_subflow" if subflow else "insert_action",
            "subflow_sys_id" if subflow else "action_type_sys_id": "c" * 32
            if subflow
            else definition_id,
            "inputs": {"message": "{{error_handler.fixture_status.message}}"},
            "parent": "error_handler",
            "ref": "new_call",
        },
        {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
        {
            "op": "update_inputs",
            "step": "ui-3",
            "inputs": {"log_message": "{{new_call.text}}"},
        },
    ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    if defect == "unreadable":
        assert preview["status"] == "error" and not write.called
        return
    assert preview["status"] == "success", preview
    if defect == "definition_drift":
        definition["description"] = "changed"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect == "definition_drift":
        assert result["error"]["code"] == "STALE_PREVIEW" and not write.called
    elif defect:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME" and write.call_count == 1, (
            result
        )
    else:
        assert result["status"] == "success" and write.call_count == 1, result


async def test_deactivated_published_flow_can_preview_explicit_reactivation(settings):
    _, tools, flow, header, args = effect_routes(settings, "deactivate")
    flow["active"] = False
    header["active"] = "false"
    args["action"] = "activate"
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "success", result
    assert (
        result["data"]["summary"]["effect"] == "publish_snapshot_and_activate_triggers"
    )
    assert all(
        call.request.method == "GET"
        and not call.request.url.path.endswith("/deactivate")
        for call in http_mock.calls
    )


@pytest.mark.parametrize(
    "defect", [None, "return_type", "mandatory", "unknown", "dynamic"]
)
async def test_ordered_insert_action_reuses_native_complex_input_serializer(
    settings, defect
):
    flow = base_flow()
    _, tools = fixtures(settings, flow)
    child: dict[str, Any] = {"name": "count", "type": "integer", "children": []}
    if defect == "mandatory":
        child["mandatory"] = True
    definition: dict[str, Any] = {
        "id": "b" * 32,
        "name": "Complex fixture",
        "state": "published",
        "is_snapshot": True,
        "entitled": True,
        "security": {"can_read": True},
        "inputs": [
            {
                "name": "payload",
                "type": "object",
                "children": [
                    child,
                    {"name": "text", "type": "string", "children": []},
                ],
            }
        ],
        "outputs": [],
    }
    if defect == "dynamic":
        definition["inputs"][0]["dependent_on"] = "table"
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'b' * 32}").respond(
        200, json={"result": definition}
    )
    inputs = {
        "payload": {
            "count": "wrong" if defect == "return_type" else 3,
            "text": "fixture",
        }
    }
    if defect == "mandatory":
        inputs["payload"]["count"] = ""
    elif defect == "unknown":
        inputs["other"] = 3
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "insert_action",
                    "action_type_sys_id": "b" * 32,
                    "inputs": inputs,
                }
            ],
        )
    )
    assert result["status"] == ("error" if defect else "success"), result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("family", ["insert_action", "insert_subflow"])
async def test_structured_insertion_policy_gate_covers_native_step_table(
    settings, monkeypatch, family
):
    from servicenow_mcp import policy

    coordinator, tools = fixtures(settings, base_flow())
    target = (
        "sys_hub_action_instance_v2"
        if family == "insert_action"
        else "sys_hub_sub_flow_instance_v2"
    )
    monkeypatch.setattr(policy, "DENIED_TABLES", policy.DENIED_TABLES | {target})
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": family,
                    "action_type_sys_id"
                    if family == "insert_action"
                    else "subflow_sys_id": "b" * 32,
                }
            ],
        )
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert not http_mock.calls


@pytest.mark.parametrize("cancel", [False, True])
async def test_lifecycle_cancellation_and_concurrent_apply_do_not_duplicate_execution(
    settings, cancel
):
    import asyncio

    coordinator, tools, _, _, args = effect_routes(settings, "test")

    async def effect(request):
        if cancel:
            raise asyncio.CancelledError
        return httpx2.Response(200, json={"result": {"data": "d" * 32}})

    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").mock(
        side_effect=effect
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200, json={"result": {"sys_id": "d" * 32, "flow": FLOW, "state": "QUEUED"}}
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    token = preview["data"]["preview_token"]
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await tools["mutation_apply"](preview_token=token)
    else:
        results = await asyncio.gather(
            *(tools["mutation_apply"](preview_token=token) for _ in range(5))
        )
        assert sum(decode_response(r)["status"] == "success" for r in results) == 1
    assert write.call_count == 1 and len(coordinator.store) == 0
    repeated = decode_response(await tools["mutation_apply"](preview_token=token))
    assert repeated["status"] == "error" and write.call_count == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"result": {}},
        {"result": {"data": "d" * 32, "errorCode": False}},
        {"result": {"data": "d" * 32, "errorCode": 7}},
        {"result": {"data": "invalid"}},
        {"result": {"data": "d" * 32, "errorMessage": "private-marker"}},
    ],
)
async def test_incomplete_or_rejected_test_response_never_discloses_body_or_replays(
    settings, body
):
    coordinator, tools, _, _, args = effect_routes(settings, "test")
    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json=body
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert "private-marker" not in str(result)
    assert write.call_count == 1 and len(coordinator.store) == 0


async def test_test_context_snapshot_is_checked_by_exact_ownership(settings):
    _, tools, _, _, args = effect_routes(settings, "test")
    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json={"result": {"data": "d" * 32}}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200, json={"result": {"sys_id": "d" * 32, "flow": "e" * 32, "state": "WAITING"}}
    )
    snapshot = http_mock.get(f"{BASE}/api/now/processflow/flow/{'e' * 32}").respond(
        200,
        json={
            "result": {"data": {"id": "e" * 32, "parentFlow": FLOW, "snapshot": True}}
        },
    )
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert result["data"]["version_content_verified"] is False
    assert snapshot.call_count == write.call_count == 1


async def test_try_pair_move_rechecks_existing_consumer_dominance(settings):
    flow = await paired_flow(settings)
    producer = next(
        row for row in flow["actionInstances"] if row["uiUniqueIdentifier"] == "ui-1"
    )
    producer["actionType"]["outputs"] = [{"name": "text", "type": "string"}]
    consumer = next(
        row for row in flow["actionInstances"] if row["uiUniqueIdentifier"] == "ui-2"
    )
    consumer["inputs"][0]["value"] = "{{ui-1.text}}"
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    with pytest.raises(CapabilityError, match="precede"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "move_step",
                    "step": flow["flowLogicInstances"][0]["uiUniqueIdentifier"],
                    "before": "ui-1",
                }
            ],
        )


@pytest.mark.parametrize("valid_owner", [False, True])
async def test_readonly_status_checks_test_snapshot_ownership_without_effects(
    settings, valid_owner
):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from tests.test_flow import _register_and_get_tools

    effect_routes(settings, "test")
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200,
        json={
            "result": {
                "sys_id": "d" * 32,
                "flow": "e" * 32,
                "state": "WAITING",
                "sys_created_on": "2026-10-14 10:00:00",
                "sys_updated_on": "2026-10-14 10:00:00",
            }
        },
    )
    http_mock.get(f"{BASE}/api/now/processflow/flow/{'e' * 32}").respond(
        200,
        json={
            "result": {
                "data": {
                    "id": "e" * 32,
                    "snapshot": True,
                    "parentFlow": FLOW if valid_owner else "f" * 32,
                }
            }
        },
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id="d" * 32)
    )
    assert result["status"] == "success", result
    assert result["data"]["execution"]["state"] == ("WAITING" if valid_owner else None)
    assert result["data"]["execution"]["version_verified"] is False
    assert all(
        call.request.method == "GET"
        and not call.request.url.path.endswith("/deactivate")
        for call in http_mock.calls
    )
