"""Source-derived output assignment fixtures, not live save captures."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import lifecycle, semantics
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import family_definitions, typed_flow
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_lifecycle import capture_routes, status_header, validation_routes


def definitions():
    # The assignment family is runtime-catalog supplied. The fixture provides
    # the minimal catalog contract, not a copied instance definition ID.
    return family_definitions() + [
        {
            "id": "f" * 32,
            "type": "ASSIGNSUBFLOWOUTPUTS",
            "name": "Assign Subflow Outputs",
            "inputs": [],
            "variables": [],
            "userCanRead": True,
            "connectedTo": "",
            "attributes": {"allow_children": "false"},
        }
    ]


def subflow():
    flow = typed_flow()
    flow["type"] = "subflow"
    flow["outputs"] = [
        {
            "id": "b" * 32,
            "name": "total",
            "label": "Total",
            "type": "integer",
            "opaque": {"keep": True},
        }
    ]
    return flow


def routes(settings, flow):
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": definitions()}
    )
    return coordinator, tools


async def test_create_output_and_assign_typed_pill_in_one_preview(settings):
    flow = subflow()
    routes(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "assign_outputs",
                "outputs": {"count": "{{ui-1.count}}", "total": 0},
                "after": "ui-4",
                "ref": "return",
            },
        ],
    )
    assigned = planned["payload"]["flowLogicInstances"][0]
    assert assigned["flowLogicDefinition"]["type"] == "ASSIGNSUBFLOWOUTPUTS"
    assert assigned["inputs"] == []
    assert [row["name"] for row in assigned["outputsToAssign"]] == ["count", "total"]
    assert [row["value"] for row in assigned["outputsToAssign"]] == [
        "{{ui-1.count}}",
        0,
    ]
    assert assigned["data"] == {"count": "{{ui-1.count}}", "total": 0}
    assert assigned["outputsToAssign"][1]["opaque"] == {"keep": True}
    assert planned["payload"]["outputs"][0] == flow["outputs"][0]
    assert planned["payload"]["label_cache"][0]["name"] == "ui-1.count"
    semantics.studio_semantics(planned["payload"])


async def test_assignment_update_clear_and_delete_declaration_are_explicit(settings):
    flow = subflow()
    routes(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "assign_outputs",
                "outputs": {"total": "{{ui-1.count}}"},
                "after": "ui-4",
                "ref": "return",
            },
            {"op": "assign_outputs", "step": "return", "outputs": {"total": 5}},
            {"op": "clear_output_assignment", "step": "return", "name": "total"},
            {"op": "delete_field", "collection": "outputs", "name": "total"},
        ],
    )
    assigned = planned["payload"]["flowLogicInstances"][0]
    assert assigned["outputsToAssign"] == []
    assert assigned["data"] == {}
    assert planned["payload"]["outputs"] == []
    assert planned["payload"]["label_cache"] == []


async def test_assignment_target_blocks_implicit_output_deletion(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="referenced or assigned"):
        await plan(
            settings,
            flow,
            [
                {"op": "assign_outputs", "outputs": {"total": 1}},
                {"op": "delete_field", "collection": "outputs", "name": "total"},
            ],
        )


@pytest.mark.parametrize(
    "value,kind",
    [
        (0, "integer"),
        (False, "boolean"),
        ("", "string"),
        ("text", "string"),
        ('{"ok":true}', "json"),
        ("c" * 32, "reference"),
    ],
)
async def test_native_scalar_assignment_values(settings, value, kind):
    flow = subflow()
    flow["outputs"][0].update(
        type=kind, **({"reference": "sc_req_item"} if kind == "reference" else {})
    )
    routes(settings, flow)
    result = await plan(
        settings, flow, [{"op": "assign_outputs", "outputs": {"total": value}}]
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == value and type(row["value"]) is type(value)
    assert row["displayValue"] == value
    assert row["type"] == kind


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "element_mapping",
        "value",
        "display",
        "opaque",
        "missing",
        "mirror",
        "id_race",
    ],
)
async def test_assignment_save_echo_and_get_are_independent_and_never_replayed(
    settings, defect
):
    flow = subflow()
    flow["componentInstances"] = copy.deepcopy(
        sorted(
            flow["actionInstances"] + flow["subFlowInstances"],
            key=lambda row: row["order"],
        )
    )
    coordinator, tools = routes(settings, flow)
    update_set, capture_row = capture_routes()

    def save(request):
        body = json.loads(request.content)
        echo = copy.deepcopy(body)
        persist(echo)
        actual = copy.deepcopy(echo)
        row = actual["flowLogicInstances"][0]["outputsToAssign"][0]
        if defect == "element_mapping":
            row["value"] = ""
        elif defect == "value":
            row["value"] = 9
        elif defect == "display":
            row["displayValue"] = 9
        elif defect == "opaque":
            row["opaque"]["keep"] = False
        elif defect == "missing":
            actual["flowLogicInstances"][0]["outputsToAssign"] = []
        elif defect == "mirror":
            actual["flowLogicInstances"][0]["data"]["total"] = 9
        elif defect == "id_race":
            actual["flowLogicInstances"][0]["id"] = "d" * 32
        # componentInstances is an explicit opaque mirror. Both representations
        # carry the same mapping on this fixture, without relaxing comparison.
        if defect == "element_mapping":
            next(s for s in actual["componentInstances"] if s["type"] == "flowlogic")[
                "outputsToAssign"
            ][0]["value"] = ""
        persist(actual)
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200,
            json={
                "result": {**status_header(), "type": "subflow", "sys_mod_count": "8"}
            },
        )
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200, json={"result": [{**capture_row, "sys_mod_count": "8"}]}
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
            expected_update_set=update_set,
            expected_revision="7",
            edits=[
                {
                    "op": "assign_outputs",
                    "outputs": {"total": "{{ui-1.count}}"},
                    "after": "ui-4",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["output_assignments_before"] == []
    after = preview["data"]["summary"]["output_assignments"]
    assert after[0]["outputs"] == [
        {"name": "total", "type": "integer", "value": "{{ui-1.count}}"}
    ]
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect in (None, "element_mapping"):
        assert applied["status"] == "success", applied
    else:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    assert write.call_count == 1
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error"
    assert write.call_count == 1 and len(coordinator.store) == 0


async def test_saved_assignment_update_preserves_identity_order_and_other_rows(
    settings,
):
    flow = subflow()
    flow["outputs"].append({"name": "message", "type": "string", "label": "Message"})
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [{"op": "assign_outputs", "outputs": {"total": 3, "message": "Keep"}}],
        )
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    flow["flowLogicInstances"][0]["outputsToAssign"][0]["vendor"] = {"opaque": True}
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "assign_outputs", "step": "d" * 32, "outputs": {"total": 7}}],
    )
    assigned = result["payload"]["flowLogicInstances"][0]
    assert assigned["id"] == "d" * 32
    assert (
        assigned["outputsToAssign"][1]
        == flow["flowLogicInstances"][0]["outputsToAssign"][1]
    )
    assert assigned["outputsToAssign"][0]["vendor"] == {"opaque": True}
    assert assigned["data"] == {"total": 7, "message": "Keep"}


@pytest.mark.parametrize(
    "edits",
    [
        [{"op": "assign_outputs", "outputs": {}}],
        [{"op": "assign_outputs", "outputs": {"missing": 1}}],
        [{"op": "assign_outputs", "outputs": {"total": True}}],
        [{"op": "assign_outputs", "outputs": {"total": "{{ui-1.records}}"}}],
        [
            {
                "op": "assign_outputs",
                "outputs": {"total": "{{ui-1.count}}"},
                "before": "ui-1",
            }
        ],
        [{"op": "assign_outputs", "step": "ui-1", "outputs": {"total": 1}}],
        [{"op": "clear_output_assignment", "name": "total"}],
        [
            {"op": "assign_outputs", "outputs": {"total": 1}, "ref": "return"},
            {"op": "clear_output_assignment", "step": "return", "name": "missing"},
        ],
        [
            {"op": "assign_outputs", "outputs": {"total": 1}, "ref": "return"},
            {
                "op": "assign_outputs",
                "step": "return",
                "outputs": {"total": 2},
                "after": "ui-1",
            },
        ],
    ],
)
async def test_invalid_output_assignment_never_stages(settings, edits):
    coordinator, tools = routes(settings, subflow())
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_output_assignment_local_validation_accepts_triggerless_subflow(settings):
    flow = subflow()
    flow["subFlowInstances"] = []
    for step in flow["actionInstances"]:
        step["actionType"]["inputs"] = [
            {"name": i["name"], **i["parameter"]} for i in step["inputs"]
        ]
        step["actionType"].setdefault("outputs", [])
    routes(settings, flow)
    built = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 2}}])
    )["payload"]
    persist(built)
    validation_routes(built)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(built, client)
    assert result["valid"] is True, result["diagnostics"]
    assert result["complete"] is True


async def test_output_assignment_type_change_is_rejected_without_clear(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="in use"):
        await plan(
            settings,
            flow,
            [
                {"op": "assign_outputs", "outputs": {"total": 1}},
                {
                    "op": "update_field",
                    "collection": "outputs",
                    "name": "total",
                    "type": "string",
                },
            ],
        )


@pytest.mark.parametrize(
    "defect",
    [
        "catalog",
        "readonly",
        "flow",
        "script",
        "type",
        "duplicate",
        "missing_value",
        "mirror",
    ],
)
async def test_unsupported_or_contradictory_saved_assignment_fails_closed(
    settings, defect
):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 2}}])
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    step = flow["flowLogicInstances"][0]
    if defect == "readonly":
        flow["outputs"][0]["readonly"] = True
    elif defect == "flow":
        flow["type"] = "flow"
    elif defect == "script":
        step["outputsToAssign"][0]["scriptActive"] = True
    elif defect == "type":
        step["outputsToAssign"][0]["type"] = "string"
    elif defect == "duplicate":
        step["outputsToAssign"].append(copy.deepcopy(step["outputsToAssign"][0]))
    elif defect == "missing_value":
        step["outputsToAssign"][0].pop("value")
    elif defect == "mirror":
        step["data"]["total"] = 7
    coordinator, tools = routes(settings, flow)
    if defect == "catalog":
        http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
            200, json={"result": family_definitions()}
        )
    edits = (
        [{"op": "assign_outputs", "outputs": {"total": 4}}]
        if defect == "catalog"
        else [{"op": "assign_outputs", "step": "d" * 32, "outputs": {"total": 4}}]
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("region", ["FOREACH", "PARALLELBLOCK"])
async def test_output_assignment_cannot_be_moved_into_loop_or_parallel_region(
    settings, region
):
    flow = subflow()
    routes(settings, flow)
    built = await plan(
        settings,
        flow,
        [
            {
                "op": "insert_logic",
                "logic": "FOREACH" if region == "FOREACH" else "PARALLEL",
                "after": "ui-4",
                "ref": "region",
                **(
                    {"inputs": {"items": "{{ui-1.records}}"}}
                    if region == "FOREACH"
                    else {}
                ),
            },
            *(
                [
                    {
                        "op": "insert_logic",
                        "logic": "PARALLELBLOCK",
                        "parent": "region",
                        "ref": "branch",
                    },
                    {"op": "move_step", "step": "ui-3", "parent": "branch"},
                ]
                if region == "PARALLELBLOCK"
                else []
            ),
        ],
    )
    flow = built["payload"]
    persist(flow)
    routes(settings, flow)
    parent = next(
        row["uiUniqueIdentifier"]
        for row in flow["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == region
    )
    with pytest.raises(CapabilityError, match="loops or parallel"):
        await plan(
            settings,
            flow,
            [{"op": "assign_outputs", "outputs": {"total": 4}, "parent": parent}],
        )


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_output_assignment_checks_native_logic_table_policy(
    settings, monkeypatch, phase
):
    from servicenow_mcp import policy

    coordinator, tools = routes(settings, subflow())
    edits = [{"op": "assign_outputs", "outputs": {"total": 1}}]
    preview = None
    if phase == "apply":
        preview = decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
        )
        assert preview["status"] == "success", preview
    monkeypatch.setattr(
        policy,
        "DENIED_TABLES",
        policy.DENIED_TABLES | {"sys_hub_flow_logic_instance_v2"},
    )
    if preview:
        result = decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
    else:
        result = decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
        )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_output_assignment_production_is_rejected_before_reads(prod_settings):
    coordinator, tools = routes(prod_settings, subflow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "assign_outputs", "outputs": {"total": 1}}],
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert not http_mock.calls


async def test_output_assignment_definition_change_blocks_apply_before_write(settings):
    _, tools = routes(settings, subflow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "assign_outputs", "outputs": {"total": 1}}],
        )
    )
    assert result["status"] == "success", result
    changed = definitions()
    changed[-1]["attributes"]["vendor"] = True
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": changed}
    )
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_assignment_metadata_update_preserves_values_and_unknown_settings(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "assign_outputs", "outputs": {"total": 2}, "ref": "return"},
            {
                "op": "update_field",
                "collection": "outputs",
                "name": "total",
                "label": "New total",
            },
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["label"] == "New total" and row["value"] == 2
    assert row["opaque"] == {"keep": True}
    assert row["id"] == flow["outputs"][0]["id"]


async def test_assignment_get_element_mapping_is_hydrated_for_later_pill_checks(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [{"op": "assign_outputs", "outputs": {"total": "{{ui-1.count}}"}}],
        )
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    flow["flowLogicInstances"][0]["outputsToAssign"][0]["value"] = ""
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {"count": 5}}],
    )
    assert (
        result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]["value"]
        == "{{ui-1.count}}"
    )
    assert result["payload"]["label_cache"][0]["name"] == "ui-1.count"


async def test_clear_saved_assignment_and_delete_output_keep_logic_identity(settings):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 3}}])
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "clear_output_assignment", "step": "d" * 32, "name": "total"},
            {"op": "delete_field", "collection": "outputs", "name": "total"},
        ],
    )
    assert result["payload"]["flowLogicInstances"][0]["id"] == "d" * 32
    assert result["payload"]["flowLogicInstances"][0]["outputsToAssign"] == []
    assert result["payload"]["outputs"] == []


async def test_saved_assignment_deletion_uses_logic_tombstone(settings):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 3}}])
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {"op": "delete_step", "step": "d" * 32},
            {"op": "delete_field", "collection": "outputs", "name": "total"},
        ],
    )
    assert result["pending_deletions"] == {
        "deletedFlowLogicInstances": [{"id": "d" * 32}]
    }
    assert result["payload"]["flowLogicInstances"] == []
    assert result["payload"]["outputs"] == []


async def test_output_assignment_summary_masks_sensitive_values(settings):
    flow = subflow()
    flow["outputs"] = [{"name": "password", "type": "string", "label": "Password"}]
    _, tools = routes(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "assign_outputs",
                    "outputs": {"password": "private-sensitive-marker"},
                }
            ],
        )
    )
    assert result["status"] == "success", result
    assert "private-sensitive-marker" not in str(result)


async def test_output_assignment_native_mcp_preview_apply_and_result_schemas(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    flow = subflow()
    routes(settings, flow)
    server = NativeMCPServer("output-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )

    def save(request):
        body = json.loads(request.content)
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
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
                    "edits": [
                        {
                            "op": "assign_outputs",
                            "outputs": {"total": "{{ui-1.count}}"},
                            "after": "ui-4",
                        }
                    ],
                },
            )
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            assert not write.called
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert not applied.is_error, applied
            assert write.call_count == 1
        tasks.cancel_scope.cancel()


async def test_assignment_context_change_before_apply_prevents_write(settings):
    coordinator, tools = routes(settings, subflow())
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
            edits=[{"op": "assign_outputs", "outputs": {"total": 1}}],
        )
    )
    assert preview["status"] == "success", preview
    http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
        200,
        json={
            "result": {
                "currentApplication": {"sysId": "global"},
                "currentUpdateSet": {"sysId": "e" * 32},
            }
        },
    )
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "error", applied
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_used_output_cannot_silently_change_binding_after_producer_move(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "assign_outputs",
                    "outputs": {"total": "{{ui-1.count}}"},
                    "after": "ui-4",
                    "ref": "return",
                },
                {"op": "move_step", "step": "ui-1", "after": "return"},
            ],
        )


async def test_assignment_inaccessible_current_catalog_blocks_existing_row_update(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 1}}])
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    coordinator, tools = routes(settings, flow)
    current = definitions()
    current[-1]["userCanRead"] = False
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": current}
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "assign_outputs", "step": "d" * 32, "outputs": {"total": 2}}],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_new_assignment_catalog_type_is_not_selected_ambiguously(settings):
    coordinator, tools = routes(settings, subflow())
    current = definitions()
    current.append({**copy.deepcopy(current[-1]), "id": "1" * 32})
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": current}
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "assign_outputs", "outputs": {"total": 1}}],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0


async def test_output_assignment_pill_cannot_escape_conditional_producer(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "insert_logic",
                    "logic": "IF",
                    "inputs": {"condition": "true"},
                    "before": "ui-1",
                    "ref": "branch",
                },
                {"op": "move_step", "step": "ui-1", "parent": "branch"},
                {
                    "op": "assign_outputs",
                    "outputs": {"total": "{{ui-1.count}}"},
                    "after": "ui-4",
                },
            ],
        )


async def test_new_output_declaration_and_assignment_save_keep_ui_identity(settings):
    flow = subflow()
    _, tools = routes(settings, flow)

    def save(request):
        body = json.loads(request.content)
        new_field = body["outputs"][-1]
        assigned = body["flowLogicInstances"][0]["outputsToAssign"][0]
        assert assigned["uiUniqueId"] == new_field["uiUniqueId"]
        assert assigned["value"] == "{{ui-1.count}}"
        new_field["id"] = "c" * 32
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "outputs",
                    "name": "new_total",
                    "label": "New total",
                    "type": "integer",
                },
                {"op": "assign_outputs", "outputs": {"new_total": "{{ui-1.count}}"}},
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1


@pytest.mark.parametrize("mismatch", ["display_value", "opaque", "type", "target"])
async def test_element_mapping_fallback_does_not_hide_other_row_changes(
    settings, mismatch
):
    flow = subflow()
    routes(settings, flow)
    payload = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 2}}])
    )["payload"]
    actual = copy.deepcopy(payload)
    row = actual["flowLogicInstances"][0]["outputsToAssign"][0]
    row["value"] = ""
    if mismatch == "display_value":
        row["displayValue"] = 3
    elif mismatch == "opaque":
        row["opaque"]["keep"] = False
    elif mismatch == "type":
        row["type"] = "string"
    else:
        row["name"] = "other"
    with pytest.raises(CapabilityError):
        semantics.compare_saved(payload, actual, generated_ids={""})


async def test_output_assignments_do_not_normalize_ordinary_action_values(settings):
    flow = subflow()
    routes(settings, flow)
    payload = (
        await plan(settings, flow, [{"op": "assign_outputs", "outputs": {"total": 2}}])
    )["payload"]
    actual = copy.deepcopy(payload)
    actual["actionInstances"][0]["inputs"][0].update(value="", displayValue="one")
    with pytest.raises(CapabilityError):
        semantics.compare_saved(payload, actual, generated_ids={""})
