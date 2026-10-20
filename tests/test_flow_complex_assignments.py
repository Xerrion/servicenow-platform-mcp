"""Complex output wire fixtures follow Studio source. They are not live captures."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations, semantics
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, persist
from tests.test_flow_fields import plan
from tests.test_flow_output_assignments import routes, subflow


def declaration_edits():
    return [
        {
            "op": "create_field",
            "collection": "outputs",
            "name": "payload",
            "label": "Payload",
            "type": "object",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "payload",
            "name": "count",
            "label": "Count",
            "type": "integer",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "payload",
            "name": "message",
            "label": "Message",
            "type": "string",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "payload",
            "name": "tags",
            "label": "Tags",
            "type": "array.string",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "name": "rows",
            "label": "Rows",
            "type": "array.object",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0",
            "name": "count",
            "label": "Count",
            "type": "integer",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0",
            "name": "enabled",
            "label": "Enabled",
            "type": "boolean",
        },
    ]


async def test_native_object_values_and_leaf_pills_use_children_not_json(settings):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "assign_outputs",
                "outputs": {
                    "payload": {
                        "count": "{{ui-1.count}}",
                        "message": "Count {{ui-1.count}}",
                        "tags": ["first", "{{ui-1.details.message}}"],
                    }
                },
            }
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == row["displayValue"] == ""
    assert [child["name"] for child in row["children"]] == ["count", "message", "tags"]
    assert row["children"][0]["value"] == "{{ui-1.count}}"
    assert [child["value"] for child in row["children"][2]["children"]] == [
        "first",
        "{{ui-1.details.message}}",
    ]
    assert result["payload"]["flowLogicInstances"][0]["data"] == {"payload": ""}
    assert {entry["name"] for entry in result["payload"]["label_cache"]} == {
        "ui-1.count",
        "ui-1.details.message",
    }


@pytest.mark.parametrize(
    "kind,values",
    [
        ("array.string", ["", "text"]),
        ("array.integer", [0, 2]),
        ("array.boolean", [False, True]),
    ],
)
async def test_native_primitive_array_assignment(settings, kind, values):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "items",
                "label": "Items",
                "type": kind,
            },
            {"op": "assign_outputs", "outputs": {"items": values}},
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == row["displayValue"] == ""
    assert [child["value"] for child in row["children"]] == values
    assert all(child["type"] == kind.split(".")[1] for child in row["children"])
    assert result["payload"]["flowLogicInstances"][0]["data"]["items"] == values


async def test_array_objects_have_per_element_object_nodes_with_typed_children(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "assign_outputs",
                "outputs": {
                    "rows": [
                        {"count": 0, "enabled": False},
                        {"count": "{{ui-1.count}}", "enabled": True},
                    ]
                },
            }
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == row["displayValue"] == ""
    assert len(row["children"]) == 2
    assert all(
        item["type"] == "object" and item["value"] == "" for item in row["children"]
    )
    assert [child["value"] for child in row["children"][0]["children"]] == [0, False]
    assert [child["value"] for child in row["children"][1]["children"]] == [
        "{{ui-1.count}}",
        True,
    ]
    assert result["payload"]["flowLogicInstances"][0]["data"]["rows"] == row["children"]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("object", {}),
        ("array.string", []),
        ("array.integer", []),
        ("array.boolean", []),
        ("array.object", []),
    ],
)
async def test_empty_native_complex_values_roundtrip_without_schema_values(
    settings, kind, value
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "empty",
                "label": "Empty",
                "type": kind,
            },
            {"op": "assign_outputs", "outputs": {"empty": value}},
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == row["displayValue"] == ""
    assert row["children"] == []
    semantics.studio_semantics(result["payload"])


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "leaf",
        "array_order",
        "count",
        "type",
        "unknown",
        "identity",
        "mirror",
        "display",
        "missing_field",
    ],
)
async def test_native_complex_save_and_independent_get_fail_closed(settings, defect):
    flow = subflow()
    coordinator, tools = routes(settings, flow)

    def save(request):
        body = json.loads(request.content)
        for i, (_, node) in enumerate(declarations.walk(body["outputs"][1:]), start=10):
            node["id"] = f"{i:032x}"
        persist(body)
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(echo)
        row = actual["flowLogicInstances"][0]["outputsToAssign"][0]
        if defect == "leaf":
            row["children"][0]["value"] = 99
        elif defect == "array_order":
            row["children"][2]["children"].reverse()
        elif defect == "count":
            row["children"][2]["children"].pop()
        elif defect == "type":
            row["children"][0]["type"] = "string"
        elif defect == "unknown":
            row["children"][0]["vendor"] = {"keep": False}
        elif defect == "identity":
            row["children"][0]["uiUniqueId"] = "changed"
        elif defect == "mirror":
            actual["flowLogicInstances"][0]["data"]["payload"] = {"wrong": True}
        elif defect == "display":
            row["children"][0]["displayValue"] = "changed"
        elif defect == "missing_field":
            row["children"].pop()
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    outputs = {
        "payload": {
            "count": "{{ui-1.count}}",
            "message": "text",
            "tags": ["first", "second"],
        },
        "rows": [{"count": 0, "enabled": False}, {"count": 1, "enabled": True}],
    }
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=declaration_edits() + [{"op": "assign_outputs", "outputs": outputs}],
        )
    )
    assert preview["status"] == "success", preview
    summary = preview["data"]["summary"]["output_assignments"][0]
    assert {row["name"]: row["value"] for row in summary["outputs"]} == outputs
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert write.call_count == 1
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error"
    assert write.call_count == 1 and len(coordinator.store) == 0


async def test_saved_complex_row_update_preserves_opaque_leaf_and_row_metadata(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            declaration_edits()
            + [
                {
                    "op": "assign_outputs",
                    "outputs": {
                        "payload": {"count": 1, "message": "old", "tags": ["old"]},
                        "total": 7,
                    },
                }
            ],
        )
    )["payload"]
    step = flow["flowLogicInstances"][0]
    step["id"] = "d" * 32
    step["outputsToAssign"][0]["vendor"] = {"keep": [False, 0]}
    step["outputsToAssign"][0]["children"][0]["vendor"] = {"keep": True}
    step["outputsToAssign"][0]["children"][2]["children"][0]["vendor"] = {"index": 0}
    before = copy.deepcopy(step)
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "assign_outputs",
                "step": "d" * 32,
                "outputs": {
                    "payload": {"count": 2, "message": "new", "tags": ["new", "second"]}
                },
            }
        ],
    )
    after = result["payload"]["flowLogicInstances"][0]
    assert after["id"] == before["id"]
    assert after["outputsToAssign"][1] == before["outputsToAssign"][1]
    assert (
        after["outputsToAssign"][0]["vendor"] == before["outputsToAssign"][0]["vendor"]
    )
    assert after["outputsToAssign"][0]["children"][0]["vendor"] == {"keep": True}
    assert after["outputsToAssign"][0]["children"][2]["children"][0]["vendor"] == {
        "index": 0
    }
    assert (
        after["outputsToAssign"][0]["children"][2]["children"][1]["value"] == "second"
    )


@pytest.mark.parametrize(
    "outputs",
    [
        {"payload": '{"count":1}'},
        {"payload": {"unknown": 1}},
        {"payload": {"count": True}},
        {"payload": {"count": "{{ui-1.records}}"}},
        {"payload": "{{ui-1.details}}"},
        {"payload": {"tags": "{{ui-1.records}}"}},
        {"payload": {"tags": [1]}},
        {"rows": [{"count": "no integer"}]},
        {"rows": [1]},
        {"rows": "[]"},
        {"payload.count": 1},
    ],
)
async def test_invalid_complex_request_never_stages_or_writes(settings, outputs):
    coordinator, tools = routes(settings, subflow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=declaration_edits() + [{"op": "assign_outputs", "outputs": outputs}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_clear_complex_assignment_then_delete_declaration_is_explicit(settings):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "assign_outputs",
                "outputs": {"rows": [{"count": 1, "enabled": True}]},
                "ref": "return",
            },
            {"op": "clear_output_assignment", "step": "return", "name": "rows"},
            {"op": "delete_field", "collection": "outputs", "name": "rows"},
        ],
    )
    step = result["payload"]["flowLogicInstances"][0]
    assert step["outputsToAssign"] == [] and step["data"] == {}
    assert all(row["name"] != "rows" for row in result["payload"]["outputs"])


@pytest.mark.parametrize(
    "op,path",
    [
        ("delete_field", "payload.count"),
        ("delete_field", "payload"),
        ("update_field", "payload.message"),
    ],
)
async def test_complex_assignments_block_destructive_declaration_changes(
    settings, op, path
):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            declaration_edits()
            + [
                {"op": "assign_outputs", "outputs": {"payload": {"count": 1}}},
                {
                    "op": op,
                    "collection": "outputs",
                    "name": path,
                    **({"type": "integer"} if op == "update_field" else {}),
                },
            ],
        )


async def test_object_in_object_and_arrays_in_object_rows_are_recursive(settings):
    flow = subflow()
    routes(settings, flow)
    edits = declaration_edits() + [
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0",
            "name": "details",
            "label": "Details",
            "type": "object",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0.details",
            "name": "tags",
            "label": "Tags",
            "type": "array.string",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0.details",
            "name": "note",
            "label": "Note",
            "type": "string",
        },
        {
            "op": "assign_outputs",
            "outputs": {
                "rows": [
                    {
                        "count": 0,
                        "enabled": False,
                        "details": {
                            "tags": ["a", "{{ui-1.details.message}}"],
                            "note": "Count {{ui-1.count}}",
                        },
                    }
                ]
            },
        },
    ]
    result = await plan(settings, flow, edits)
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    details = row["children"][0]["children"][2]
    assert details["name"] == "details" and details["value"] == ""
    assert details["children"][0]["children"][1]["value"] == "{{ui-1.details.message}}"
    assert {item["name"] for item in result["payload"]["label_cache"]} == {
        "ui-1.details.message",
        "ui-1.count",
    }


async def test_object_values_replace_named_row_and_fill_declared_defaults(settings):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "update_field",
                "collection": "outputs",
                "name": "payload.message",
                "default_value": "Default message",
            },
            {
                "op": "assign_outputs",
                "outputs": {"payload": {"count": 9, "message": "old", "tags": ["old"]}},
                "ref": "return",
            },
            {
                "op": "assign_outputs",
                "step": "return",
                "outputs": {"payload": {"count": 0}},
            },
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert [child["value"] for child in row["children"]] == [0, "Default message", ""]
    assert row["children"][2]["children"] == []


@pytest.mark.parametrize(
    "defect",
    [
        "leaf_script",
        "leaf_readonly",
        "missing_schema",
        "wrong_type",
        "duplicate_leaf",
        "missing_leaf",
        "unknown_leaf",
        "bad_parent_value",
    ],
)
async def test_malformed_saved_complex_values_cannot_be_repaired_implicitly(
    settings, defect
):
    flow = subflow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            declaration_edits()
            + [{"op": "assign_outputs", "outputs": {"payload": {"count": 1}}}],
        )
    )["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    row = flow["flowLogicInstances"][0]["outputsToAssign"][0]
    if defect == "leaf_script":
        row["children"][0]["scriptActive"] = True
    elif defect == "leaf_readonly":
        row["children"][0]["readonly"] = True
    elif defect == "missing_schema":
        flow["outputs"][1]["children"].pop()
    elif defect == "wrong_type":
        row["children"][0]["type"] = "string"
    elif defect == "duplicate_leaf":
        row["children"].append(copy.deepcopy(row["children"][0]))
    elif defect == "missing_leaf":
        row["children"].pop()
    elif defect == "unknown_leaf":
        row["children"].append(
            {"name": "unknown", "type": "string", "value": "unknown"}
        )
    else:
        row["value"] = {"wrong": 1}
    coordinator, tools = routes(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "assign_outputs",
                    "step": "d" * 32,
                    "outputs": {"payload": {"count": 2}},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "edits",
    [
        [
            {
                "op": "assign_outputs",
                "outputs": {"payload": {"count": "{{ui-1.count}}"}},
                "before": "ui-1",
            }
        ],
        [
            {
                "op": "assign_outputs",
                "outputs": {"payload": {"count": "{{missing.count}}"}},
            }
        ],
        [
            {
                "op": "assign_outputs",
                "outputs": {"payload": {"message": "{{ui-1.records}}"}},
            }
        ],
        [
            {
                "op": "assign_outputs",
                "outputs": {"payload": {"tags": ["{{ui-1.count}}"]}},
            }
        ],
    ],
)
async def test_complex_leaf_pills_follow_scalar_type_cardinality_and_visibility(
    settings, edits
):
    flow = subflow()
    coordinator, tools = routes(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=declaration_edits() + edits
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


async def test_complex_assignment_preview_masks_nested_sensitive_keys(settings):
    flow = subflow()
    _, tools = routes(settings, flow)
    edits = declaration_edits() + [
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "rows.rows_child0",
            "name": "password",
            "label": "Password",
            "type": "string",
        },
        {
            "op": "assign_outputs",
            "outputs": {"rows": [{"password": "private-sensitive-marker"}]},
        },
    ]
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert result["status"] == "success", result
    assert "private-sensitive-marker" not in str(result)


async def test_native_complex_mcp_preview_apply_and_result_schemas(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    flow = subflow()
    routes(settings, flow)
    server = NativeMCPServer("complex-output-wire")
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
                    "edits": declaration_edits()
                    + [
                        {
                            "op": "assign_outputs",
                            "outputs": {
                                "payload": {
                                    "count": "{{ui-1.count}}",
                                    "message": "Message",
                                    "tags": [],
                                },
                                "rows": [{"count": 1, "enabled": True}],
                            },
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


async def test_nested_array_object_values_use_item_schema_not_schema_fields_as_values(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    edits = declaration_edits() + [
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "payload",
            "name": "rows",
            "label": "Rows",
            "type": "array.object",
        },
        {
            "op": "create_field",
            "collection": "outputs",
            "parent": "payload.rows.rows_child0",
            "name": "count",
            "label": "Count",
            "type": "integer",
        },
        {
            "op": "assign_outputs",
            "outputs": {
                "payload": {"rows": [{"count": 1}, {"count": "{{ui-1.count}}"}]}
            },
        },
    ]
    result = await plan(settings, flow, edits)
    array = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0][
        "children"
    ][-1]
    assert len(array["children"]) == 2
    assert all(item["type"] == "object" for item in array["children"])


async def test_complex_output_local_validation_uses_recursive_current_schema(settings):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.capabilities.flows import lifecycle
    from servicenow_mcp.platform.client import ServiceNowClient
    from tests.test_flow_lifecycle import validation_routes

    flow = subflow()
    flow["subFlowInstances"] = []
    for step in flow["actionInstances"]:
        step["actionType"]["inputs"] = [
            {"name": item["name"], **item["parameter"]} for item in step["inputs"]
        ]
        step["actionType"].setdefault("outputs", [])
    routes(settings, flow)
    built = (
        await plan(
            settings,
            flow,
            declaration_edits()
            + [
                {
                    "op": "assign_outputs",
                    "outputs": {
                        "payload": {"count": 1, "tags": ["a"]},
                        "rows": [{"count": 0, "enabled": False}],
                    },
                }
            ],
        )
    )["payload"]
    persist(built)
    validation_routes(built)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(built, client)
    assert result["complete"] is True, result
    assert result["valid"] is True, result


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_complex_output_policy_rechecks_native_logic_table(
    settings, monkeypatch, phase
):
    from servicenow_mcp import policy

    coordinator, tools = routes(settings, subflow())
    edits = declaration_edits() + [
        {"op": "assign_outputs", "outputs": {"payload": {"count": 1}}}
    ]
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


async def test_complex_output_production_write_gate_rejects_before_reads(prod_settings):
    coordinator, tools = routes(prod_settings, subflow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=declaration_edits()
            + [{"op": "assign_outputs", "outputs": {"rows": []}}],
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0 and not http_mock.calls


async def test_complex_output_context_change_prevents_save(settings):
    from tests.test_flow_lifecycle import capture_routes

    coordinator, tools = routes(settings, subflow())
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
            edits=declaration_edits()
            + [{"op": "assign_outputs", "outputs": {"rows": []}}],
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


async def test_complex_output_schema_change_invalidates_held_preview(settings):
    flow = subflow()
    routes(settings, flow)
    flow = (await plan(settings, flow, declaration_edits()))["payload"]
    _, tools = routes(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "assign_outputs", "outputs": {"payload": {"count": 1}}}],
        )
    )
    assert preview["status"] == "success", preview
    changed = copy.deepcopy(flow)
    changed["outputs"][1]["children"][0]["hint"] = "changed schema"
    persist(changed)
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_complex_array_element_metadata_uses_schema_identity_without_new_database_records(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    flow = (await plan(settings, flow, declaration_edits()))["payload"]
    row = flow["outputs"][-1]
    row["id"] = "b" * 32
    row["children"][0]["id"] = "c" * 32
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "assign_outputs", "outputs": {"rows": [{"count": 1}, {"count": 2}]}}],
    )
    assigned = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert assigned["id"] == "b" * 32
    assert [item["children"][0]["id"] for item in assigned["children"]] == [
        "c" * 32,
        "c" * 32,
    ]
    assert len({item["uiUniqueId"] for item in assigned["children"]}) == 1
    assert result["payload"]["outputs"] == flow["outputs"]


async def test_complex_leaves_check_current_declared_constraints(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            declaration_edits()
            + [
                {
                    "op": "update_field",
                    "collection": "outputs",
                    "name": "payload.message",
                    "maxsize": 3,
                },
                {
                    "op": "assign_outputs",
                    "outputs": {"payload": {"message": "too long"}},
                },
            ],
        )


async def test_saved_complex_array_value_shrink_removes_values_only_when_explicit(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "assign_outputs",
                "outputs": {"rows": [{"count": 1}, {"count": 2}]},
                "ref": "return",
            },
            {"op": "assign_outputs", "step": "return", "outputs": {"rows": []}},
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["children"] == []
    assert result["payload"]["outputs"][-1]["children"]


@pytest.mark.parametrize("side", ["echo", "readback"])
async def test_complex_nested_array_values_are_verified_on_each_save_surface(
    settings, side
):
    flow = subflow()
    _, tools = routes(settings, flow)

    def save(request):
        body = json.loads(request.content)
        persist(body)
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        target = echo if side == "echo" else actual
        target["flowLogicInstances"][0]["outputsToAssign"][0]["children"][1][
            "children"
        ][0]["value"] = False
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=declaration_edits()
            + [
                {
                    "op": "assign_outputs",
                    "outputs": {
                        "rows": [
                            {"count": 1, "enabled": False},
                            {"count": 2, "enabled": True},
                        ]
                    },
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    assert write.call_count == 1


@pytest.mark.parametrize("defect", ["value", "order", "count", "unknown"])
async def test_array_data_mirror_is_checked_without_ignoring_complex_values(
    settings, defect
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [{"op": "assign_outputs", "outputs": {"rows": [{"count": 1}, {"count": 2}]}}],
    )
    actual = copy.deepcopy(result["payload"])
    mirror = actual["flowLogicInstances"][0]["data"]["rows"]
    if defect == "value":
        mirror[0]["children"][0]["value"] = 99
    elif defect == "order":
        mirror.reverse()
    elif defect == "count":
        mirror.pop()
    else:
        mirror[0]["vendor"] = False
    with pytest.raises(CapabilityError):
        semantics.compare_saved(result["payload"], actual, generated_ids={""})


async def test_nested_array_object_data_mirror_retains_pre_serialization_arrays(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        declaration_edits()
        + [
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "rows.rows_child0",
                "name": "tags",
                "label": "Tags",
                "type": "array.string",
            },
            {
                "op": "assign_outputs",
                "outputs": {"rows": [{"count": 0, "tags": ["first", "second"]}]},
            },
        ],
    )
    step = result["payload"]["flowLogicInstances"][0]
    assert step["outputsToAssign"][0]["children"][0]["children"][-1]["value"] == ""
    assert step["data"]["rows"][0]["children"][-1]["value"] == ["first", "second"]
    semantics.studio_semantics(result["payload"])


async def test_existing_reference_output_leaf_uses_native_sys_id_and_keeps_facets(
    settings,
):
    flow = subflow()
    flow["outputs"] = [
        {
            "name": "payload",
            "type": "object",
            "children": [
                {
                    "name": "record",
                    "type": "reference",
                    "reference": "sc_req_item",
                    "fieldFacetMap": {"vendor": {"keep": True}},
                }
            ],
        }
    ]
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "assign_outputs", "outputs": {"payload": {"record": "a" * 32}}}],
    )
    child = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0][
        "children"
    ][0]
    assert child["value"] == "a" * 32
    assert child["reference"] == "sc_req_item"
    assert child["fieldFacetMap"] == {"vendor": {"keep": True}}
    assert result["payload"]["outputs"] == flow["outputs"]
