"""Source-derived whole complex bindings with independent producer schema reads."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, persist
from tests.test_flow_fields import plan
from tests.test_flow_output_assignments import routes, subflow


def schema(kind="object"):
    if kind == "object":
        return {
            "name": "payload",
            "type": "object",
            "children": [
                {"name": "count", "type": "integer", "children": []},
                {"name": "text", "type": "string", "children": []},
            ],
        }
    item = kind.split(".")[1]
    return {
        "name": "payload",
        "type": kind,
        **({"reference": "sc_req_item"} if kind == "array.reference" else {}),
        "children": [
            {
                "name": "item",
                "type": item,
                "children": schema()["children"] if item == "object" else [],
            }
        ],
    }


async def test_object_matching_uses_local_names_not_field_order_or_labels(settings):
    flow, _, _ = prepared(settings)
    target = flow["actionInstances"][1]["inputs"][-1]["parameter"]
    target["children"].reverse()
    for row in target["children"]:
        row["name"] = "payload." + row["name"]
        row["label"] = "Target label"
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            }
        ],
    )
    assert result["payload"]["actionInstances"][1]["inputs"][-1]["parameter"] == target


async def test_whole_reference_schema_without_table_fails_closed(settings):
    flow, tools, current = prepared(settings)
    for row in (
        flow["actionInstances"][0]["actionType"]["outputs"][-1],
        current["outputs"][-1],
        flow["actionInstances"][1]["inputs"][-1]["parameter"],
    ):
        row["children"][0]["type"] = "reference"
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result


def prepared(settings, kind="object", consumer="action"):
    flow = subflow()
    source = schema(kind)
    flow["actionInstances"][0]["actionType"]["outputs"].append(copy.deepcopy(source))
    # Distinct action definitions keep the producer's schema independent.
    flow["actionInstances"][0]["actionTypeSysId"] = "a" * 32
    flow["actionInstances"][0]["actionType"]["id"] = "a" * 32
    if consumer == "action":
        flow["actionInstances"][1]["inputs"].append(
            {
                "name": "payload",
                "value": "",
                "children": [],
                "parameter": copy.deepcopy(source),
            }
        )
    elif consumer == "subflow":
        flow["subFlowInstances"][0]["inputs"].append(
            {
                "name": "payload",
                "value": "",
                "children": [],
                "parameter": copy.deepcopy(source),
            }
        )
    else:
        flow["outputs"].append(copy.deepcopy(source))
    _, tools = routes(settings, flow)
    definition = {
        **copy.deepcopy(flow["actionInstances"][0]["actionType"]),
        "id": "a" * 32,
        "security": {"can_read": True},
        "entitled": True,
        "state": "published",
        "is_snapshot": True,
    }
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": definition}
    )
    return flow, tools, definition


@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "array.string",
        "array.integer",
        "array.boolean",
        "array.object",
        "array.reference",
    ],
)
@pytest.mark.parametrize("consumer", ["action", "subflow", "assignment"])
async def test_whole_binding_is_one_native_pill_with_no_literal_children(
    settings, kind, consumer
):
    flow, _, _ = prepared(settings, kind, consumer)
    edit = (
        {"op": "assign_outputs", "outputs": {"payload": "{{ui-1.payload}}"}}
        if consumer == "assignment"
        else {
            "op": "update_inputs",
            "step": "ui-2" if consumer == "action" else "ui-4",
            "inputs": {"payload": "{{ui-1.payload}}"},
        }
    )
    result = await plan(settings, flow, [edit])
    row = (
        result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
        if consumer == "assignment"
        else result["payload"][
            "actionInstances" if consumer == "action" else "subFlowInstances"
        ][1 if consumer == "action" else 0]["inputs"][-1]
    )
    assert row["value"] == "{{ui-1.payload}}"
    assert row["children"] == []
    assert result["payload"]["label_cache"][0]["name"] == "ui-1.payload"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_new_declared_object_input_can_bind_new_declared_output(settings):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "source",
                "label": "Source",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "source",
                "name": "text",
                "label": "Text",
                "type": "string",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "target",
                "label": "Target",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "target",
                "name": "text",
                "label": "Text",
                "type": "string",
            },
            {"op": "assign_outputs", "outputs": {"target": "{{subflow.source}}"}},
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]
    assert row["value"] == row["displayValue"] == "{{subflow.source}}"
    assert row["children"] == []


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "extra",
        "type",
        "cardinality",
        "duplicate",
        "unknown_schema",
        "wrong_item",
    ],
)
async def test_whole_structural_mismatch_never_stages(settings, defect):
    flow, tools, _ = prepared(
        settings, "array.object" if defect == "wrong_item" else "object"
    )
    target = flow["actionInstances"][1]["inputs"][-1]["parameter"]
    if defect == "missing":
        target["children"].pop()
    elif defect == "extra":
        target["children"].append({"name": "extra", "type": "string", "children": []})
    elif defect == "type":
        target["children"][0]["type"] = "string"
    elif defect == "cardinality":
        target.update(
            type="array.string",
            children=[{"name": "item", "type": "string", "children": []}],
        )
    elif defect == "duplicate":
        target["children"].append(copy.deepcopy(target["children"][0]))
    elif defect == "unknown_schema":
        target.pop("children")
    else:
        target["children"][0]["type"] = "string"
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "reference,allowed", [("sc_req_item", True), ("task", True), ("sys_user", False)]
)
async def test_reference_fields_check_table_compatibility_recursively(
    settings, reference, allowed
):
    flow, _, current = prepared(settings)
    for row in (
        flow["actionInstances"][0]["actionType"]["outputs"][-1],
        current["outputs"][-1],
    ):
        row["children"][0].update(type="reference", reference="sc_req_item")
    flow["actionInstances"][1]["inputs"][-1]["parameter"]["children"][0].update(
        type="reference", reference=reference
    )
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    if allowed:
        result = await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
        assert result["payload"]["actionInstances"][1]["inputs"][-1]["children"] == []
    else:
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                flow,
                [
                    {
                        "op": "update_inputs",
                        "step": "ui-2",
                        "inputs": {"payload": "{{ui-1.payload}}"},
                    }
                ],
            )


@pytest.mark.parametrize(
    "defect",
    [
        "unreadable",
        "unpublished",
        "not_entitled",
        "identity",
        "schema_drift",
        "incomplete",
    ],
)
async def test_independent_producer_definition_is_required(settings, defect):
    _, tools, current = prepared(settings)
    if defect == "unreadable":
        current["security"]["can_read"] = False
    elif defect == "unpublished":
        current["is_snapshot"] = False
    elif defect == "not_entitled":
        current["entitled"] = False
    elif defect == "identity":
        current["id"] = "b" * 32
    elif defect == "schema_drift":
        current["outputs"][-1]["children"][0]["type"] = "string"
    else:
        current["outputs"][-1].pop("children")
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("consumer", ["action", "subflow", "assignment"])
@pytest.mark.parametrize(
    "defect", [None, "path", "children", "schema", "unknown", "display", "id"]
)
async def test_whole_pill_save_verifies_echo_and_independent_get(
    settings, consumer, defect
):
    _, tools, _ = prepared(settings, "array.object", consumer)

    def binding(body):
        return (
            body["flowLogicInstances"][0]["outputsToAssign"][0]
            if consumer == "assignment"
            else body[
                "actionInstances" if consumer == "action" else "subFlowInstances"
            ][1 if consumer == "action" else 0]["inputs"][-1]
        )

    def save(request):
        body = json.loads(request.content)
        persist(body)
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        row = binding(actual)
        if defect == "path":
            row["value"] = "{{other.payload}}"
        elif defect == "children":
            row["children"] = [{"name": "item", "type": "object", "value": ""}]
        elif defect == "schema":
            if consumer == "assignment":
                row["attributes"] = {"unexpected_schema": True}
            else:
                row["parameter"]["children"][0]["type"] = "integer"
        elif defect == "unknown":
            row["vendor"] = {"change": True}
        elif defect == "display":
            row["displayValue"] = "changed"
        elif defect == "id":
            row["id"] = "b" * 32
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    edit = (
        {"op": "assign_outputs", "outputs": {"payload": "{{ui-1.payload}}"}}
        if consumer == "assignment"
        else {
            "op": "update_inputs",
            "step": "ui-2" if consumer == "action" else "ui-4",
            "inputs": {"payload": "{{ui-1.payload}}"},
        }
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
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
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error" and write.call_count == 1


async def test_published_subflow_output_can_produce_whole_object(settings):
    flow, _, _ = prepared(settings)
    producer = flow["subFlowInstances"][0]
    producer["subFlow"] = {
        "id": producer["subflowSysId"],
        "outputs": [schema()],
        "userCanRead": True,
    }
    current = {
        **copy.deepcopy(producer["subFlow"]),
        "type": "subflow",
        "security": {"can_read": True},
        "isFlowEntitled": True,
        "snapshot": True,
        "masterSnapshot": True,
    }
    http_mock.get(
        f"{BASE}/api/now/processflow/flow/{producer['subflowSysId']}"
    ).respond(200, json={"result": {"data": current}})
    result = await plan(
        settings,
        flow,
        [
            {"op": "move_step", "step": "ui-4", "before": "ui-2"},
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-4.payload}}"},
            },
        ],
    )
    assert (
        result["payload"]["actionInstances"][1]["inputs"][-1]["value"]
        == "{{ui-4.payload}}"
    )


async def test_complex_binding_source_schema_change_invalidates_preview(settings):
    _, tools, current = prepared(settings)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "success", result
    current["outputs"][-1]["hint"] = "changed current definition"
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_whole_array_can_be_nested_in_literal_output_object(settings):
    flow, _, _ = prepared(settings, "array.string", "assignment")
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "container",
                "label": "Container",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "container",
                "name": "items",
                "label": "Items",
                "type": "array.string",
            },
            {
                "op": "assign_outputs",
                "outputs": {"container": {"items": "{{ui-1.payload}}"}},
            },
        ],
    )
    row = result["payload"]["flowLogicInstances"][0]["outputsToAssign"][0]["children"][
        0
    ]
    assert row["value"] == "{{ui-1.payload}}" and row["children"] == []


async def test_nested_object_producer_schema_supports_whole_path_binding(settings):
    flow, _, current = prepared(settings)
    nested = {"name": "wrapper", "type": "object", "children": [schema()]}
    flow["actionInstances"][0]["actionType"]["outputs"].append(copy.deepcopy(nested))
    current["outputs"].append(nested)
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.wrapper.payload}}"},
            }
        ],
    )
    assert result["payload"]["label_cache"][0]["name"] == "ui-1.wrapper.payload"


async def test_whole_pill_replacement_clears_literals_but_preserves_target_metadata(
    settings,
):
    flow, _, _ = prepared(settings)
    target = flow["actionInstances"][1]["inputs"][-1]
    target["vendor"] = {"keep": False}
    target["children"] = [
        {"name": "count", "value": 9, "parameter": {"type": "integer"}},
        {"name": "text", "value": "old", "parameter": {"type": "string"}},
    ]
    parameter = copy.deepcopy(target["parameter"])
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            }
        ],
    )
    row = result["payload"]["actionInstances"][1]["inputs"][-1]
    assert row["children"] == []
    assert row["parameter"] == parameter and row["vendor"] == {"keep": False}


@pytest.mark.parametrize("op", ["delete_step", "move_step"])
async def test_whole_pill_references_revalidated_after_tree_edit(settings, op):
    flow, _, _ = prepared(settings)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                },
                {
                    "op": op,
                    "step": "ui-1",
                    **({"after": "ui-2"} if op == "move_step" else {}),
                },
            ],
        )


async def test_whole_pills_obey_branch_dominance(settings):
    flow, _, _ = prepared(settings)
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
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                },
            ],
        )


async def test_saved_whole_pill_with_stale_children_is_rejected(settings):
    flow, tools, _ = prepared(settings)
    target = flow["actionInstances"][1]["inputs"][-1]
    target.update(
        value="{{ui-1.payload}}",
        children=[{"name": "count", "value": 9, "parameter": {"type": "integer"}}],
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "update_inputs", "step": "ui-2", "inputs": {"count": 1}}],
        )
    )
    assert result["status"] == "error", result


async def test_held_preview_binds_current_producer_schema_and_reads_once(settings):
    flow, _, _ = prepared(settings)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "target",
                "label": "Target",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "target",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "target",
                "name": "text",
                "label": "Text",
                "type": "string",
            },
            {"op": "assign_outputs", "outputs": {"target": "{{ui-1.payload}}"}},
        ],
    )
    reads = [
        call
        for call in http_mock.calls
        if call.request.url.path.endswith("/action/action_types/" + "a" * 32)
    ]
    assert len(reads) == 1
    assert result["reference_revision"]


async def test_whole_pill_native_mcp_preview_apply_schemas(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    prepared(settings, "array.object", "assignment")
    server = NativeMCPServer("whole-pill-wire")
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
                            "outputs": {"payload": "{{ui-1.payload}}"},
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


async def test_whole_pill_to_literal_transition_uses_preserved_target_schema(settings):
    flow, _, _ = prepared(settings)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": {"count": 0, "text": "literal"}},
            },
        ],
    )
    row = result["payload"]["actionInstances"][1]["inputs"][-1]
    assert row["value"] == "" and [child["value"] for child in row["children"]] == [
        0,
        "literal",
    ]
    assert result["payload"]["label_cache"] == []


async def test_whole_input_pill_updates_only_proven_component_mirror(settings):
    flow, _, _ = prepared(settings)
    flow["componentInstances"] = copy.deepcopy(
        sorted(
            flow["actionInstances"] + flow["subFlowInstances"],
            key=lambda row: row["order"],
        )
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            }
        ],
    )
    mirrored = next(
        row
        for row in result["payload"]["componentInstances"]
        if row["uiUniqueIdentifier"] == "ui-2"
    )
    assert mirrored["inputs"][-1]["value"] == "{{ui-1.payload}}"
    assert mirrored["inputs"][-1]["children"] == []
    saved = result["payload"]
    # The reverse transition must also refresh a proven component mirror.
    routes(settings, saved)
    literal = await plan(
        settings,
        saved,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": {"count": 0, "text": "literal"}},
            }
        ],
    )
    restored = next(
        row
        for row in literal["payload"]["componentInstances"]
        if row["uiUniqueIdentifier"] == "ui-2"
    )
    assert restored["inputs"][-1]["value"] == ""
    assert [child["value"] for child in restored["inputs"][-1]["children"]] == [
        0,
        "literal",
    ]
    flow["componentInstances"][0]["vendor"] = "unproven mirror metadata"
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="mirror"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_whole_pill_policy_gates_and_no_writes(settings, monkeypatch, phase):
    from servicenow_mcp import policy

    _, tools, _ = prepared(settings)
    edits = [
        {
            "op": "update_inputs",
            "step": "ui-2",
            "inputs": {"payload": "{{ui-1.payload}}"},
        }
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
        policy.DENIED_TABLES | {"sys_hub_action_type_definition"},
    )
    result = (
        decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        if preview
        else decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
        )
    )
    assert result["status"] == "error"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_whole_pill_production_rejected_before_reads(prod_settings):
    _, tools, _ = prepared(prod_settings)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error" and not http_mock.calls


async def test_whole_pill_target_scope_changes_block_apply(settings):
    from tests.test_flow_lifecycle import capture_routes

    _, tools, _ = prepared(settings)
    capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    capture_routes(scope="e" * 32)
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "error", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_whole_pill_readonly_target_is_not_bypassed(settings):
    flow, tools, _ = prepared(settings)
    flow["actionInstances"][1]["inputs"][-1]["parameter"]["readonly"] = True
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error"


@pytest.mark.parametrize(
    "defect", ["schema_missing", "dynamic", "unsupported_leaf", "array_type"]
)
async def test_missing_or_unproven_schemas_remain_unavailable(settings, defect):
    flow, tools, current = prepared(
        settings, "array.string" if defect == "array_type" else "object"
    )
    producer = flow["actionInstances"][0]["actionType"]["outputs"][-1]
    target = flow["actionInstances"][1]["inputs"][-1]["parameter"]
    if defect == "schema_missing":
        producer.pop("children")
        current["outputs"][-1].pop("children")
    elif defect == "dynamic":
        producer["dynamic"] = {"definition": "unproven"}
        current["outputs"][-1]["dynamic"] = {"definition": "unproven"}
    elif defect == "unsupported_leaf":
        for row in (producer, current["outputs"][-1], target):
            row["children"][0]["type"] = "opaque-special-type"
    else:
        for row in (producer, current["outputs"][-1], target):
            row["children"][0]["type"] = "integer"
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error", result


async def test_packed_declared_array_input_can_supply_whole_array_output(settings):
    flow = subflow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "source",
                "label": "Source",
                "type": "array.object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "source.source_child0",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "target",
                "label": "Target",
                "type": "array.object",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "target.target_child0",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {"op": "assign_outputs", "outputs": {"target": "{{subflow.source}}"}},
        ],
    )
    step = result["payload"]["flowLogicInstances"][0]
    assert step["data"] == {"target": "{{subflow.source}}"}
    assert step["outputsToAssign"][0]["children"] == []


async def test_whole_pill_local_validation_uses_published_producer_schema(settings):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.capabilities.flows import lifecycle
    from servicenow_mcp.platform.client import ServiceNowClient
    from tests.test_flow_lifecycle import validation_routes

    flow, _, _ = prepared(settings)
    flow["subFlowInstances"] = []
    for step in flow["actionInstances"]:
        step["actionType"]["inputs"] = [
            {"name": item["name"], **item["parameter"]} for item in step["inputs"]
        ]
        step["actionType"].setdefault("outputs", [])
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{ui-1.payload}}"},
            }
        ],
    )
    built = result["payload"]
    validation_routes(built)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        checked = await lifecycle.validate_definition(built, client)
    assert checked["valid"] is True, checked
    assert checked["complete"] is True


@pytest.mark.parametrize("valid", [True, False])
async def test_nested_array_object_schema_is_checked_recursively(settings, valid):
    flow, _, current = prepared(settings)
    nested = schema("array.object")
    nested["name"] = "rows"
    for row in (
        flow["actionInstances"][0]["actionType"]["outputs"][-1],
        current["outputs"][-1],
        flow["actionInstances"][1]["inputs"][-1]["parameter"],
    ):
        row["children"].append(copy.deepcopy(nested))
    if not valid:
        flow["actionInstances"][1]["inputs"][-1]["parameter"]["children"][-1][
            "children"
        ][0]["children"][0]["type"] = "string"
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    if valid:
        result = await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
        assert result["payload"]["actionInstances"][1]["inputs"][-1]["children"] == []
    else:
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                flow,
                [
                    {
                        "op": "update_inputs",
                        "step": "ui-2",
                        "inputs": {"payload": "{{ui-1.payload}}"},
                    }
                ],
            )


async def test_new_declared_whole_producer_cannot_be_deleted_while_referenced(settings):
    flow = subflow()
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="referenced or assigned"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "source",
                    "label": "Source",
                    "type": "object",
                },
                {
                    "op": "create_field",
                    "collection": "outputs",
                    "name": "target",
                    "label": "Target",
                    "type": "object",
                },
                {"op": "assign_outputs", "outputs": {"target": "{{subflow.source}}"}},
                {"op": "delete_field", "collection": "inputs", "name": "source"},
            ],
        )


@pytest.mark.parametrize("kind", ["object", "array.string"])
async def test_exact_complex_pills_cannot_be_interpolated(settings, kind):
    _, tools, _ = prepared(settings, kind)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "prefix {{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("valid", [True, False])
async def test_choice_leaf_sets_are_compatible_without_ignoring_values(settings, valid):
    flow, _, current = prepared(settings)
    for row in (
        flow["actionInstances"][0]["actionType"]["outputs"][-1],
        current["outputs"][-1],
    ):
        row["children"][0].update(
            type="choice", choices=[{"value": "one", "label": "One"}]
        )
    flow["actionInstances"][1]["inputs"][-1]["parameter"]["children"][0].update(
        type="choice",
        choices=[{"value": "one" if valid else "other", "label": "Target"}],
    )
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    if valid:
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    else:
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                flow,
                [
                    {
                        "op": "update_inputs",
                        "step": "ui-2",
                        "inputs": {"payload": "{{ui-1.payload}}"},
                    }
                ],
            )


@pytest.mark.parametrize("consumer", ["action", "subflow", "assignment"])
@pytest.mark.parametrize(
    "reference,allowed",
    [
        ("sc_req_item", True),
        ("task", True),
        ("sys_user", False),
    ],
)
async def test_reference_array_whole_pills_check_parent_and_item_tables(
    settings, consumer, reference, allowed
):
    flow, _, _ = prepared(settings, "array.reference", consumer)
    target = (
        flow["outputs"][-1]
        if consumer == "assignment"
        else flow["actionInstances" if consumer == "action" else "subFlowInstances"][
            1 if consumer == "action" else 0
        ]["inputs"][-1]["parameter"]
    )
    target["reference"] = reference
    edit = (
        {"op": "assign_outputs", "outputs": {"payload": "{{ui-1.payload}}"}}
        if consumer == "assignment"
        else {
            "op": "update_inputs",
            "step": "ui-2" if consumer == "action" else "ui-4",
            "inputs": {"payload": "{{ui-1.payload}}"},
        }
    )
    if allowed:
        planned = await plan(settings, flow, [edit])
        assert planned["payload"]["label_cache"][0]["reference"] == "sc_req_item"
        assert target["children"][0].get("reference") is None
    else:
        with pytest.raises(CapabilityError, match="incompatible table"):
            await plan(settings, flow, [edit])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_table",
        "invalid_table",
        "missing_item",
        "extra_item",
        "wrong_type",
        "wrong_table",
        "item_children",
        "scalar_cardinality",
        "collection_traversal",
    ],
)
async def test_reference_array_whole_pills_reject_incomplete_or_conflicting_schema(
    settings, defect
):
    flow, tools, _ = prepared(settings, "array.reference")
    target = flow["actionInstances"][1]["inputs"][-1]["parameter"]
    if defect == "missing_table":
        target.pop("reference")
    elif defect == "invalid_table":
        target["reference"] = "task.field"
    elif defect == "missing_item":
        target["children"] = []
    elif defect == "extra_item":
        target["children"].append(copy.deepcopy(target["children"][0]))
    elif defect == "wrong_type":
        target["children"][0]["type"] = "string"
    elif defect == "wrong_table":
        target["children"][0]["reference"] = "sys_user"
    elif defect == "item_children":
        target["children"][0]["children"] = [{"name": "extra", "type": "string"}]
    elif defect == "scalar_cardinality":
        target.update(type="reference", children=[])
    pill = (
        "{{ui-1.payload.item}}"
        if defect == "collection_traversal"
        else "{{ui-1.payload}}"
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {"op": "update_inputs", "step": "ui-2", "inputs": {"payload": pill}}
            ],
        )
    )
    assert result["status"] == "error", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("defect", ["table", "item_type", "item_table", "missing_item"])
async def test_reference_array_whole_pill_reads_independent_producer_schema(
    settings, defect
):
    _flow, tools, current = prepared(settings, "array.reference")
    independent = current["outputs"][-1]
    if defect == "table":
        independent["reference"] = "sys_user"
    elif defect == "item_type":
        independent["children"][0]["type"] = "string"
    elif defect == "item_table":
        independent["children"][0]["reference"] = "sys_user"
    else:
        independent["children"] = []
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": current}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"payload": "{{ui-1.payload}}"},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert all(call.request.method == "GET" for call in http_mock.calls)
