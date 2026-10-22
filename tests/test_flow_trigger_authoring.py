"""Native trigger fixtures derive from local Studio source, not captured saves."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan


def categories():
    result = copy.deepcopy(DEFS["trigger_categories"])
    record = next(c for c in result if c["name"] == "Record")
    original = next(iter(record["triggers"].values()))
    # Definition IDs/choices are instance-catalog inputs, not hardcoded runtime
    # contracts. These two fixtures exercise the shared record serializer.
    for index, (kind, label) in enumerate(
        [
            ("record_update", "Updated"),
            ("record_create_or_update", "Created or Updated"),
        ],
        start=1,
    ):
        item = copy.deepcopy(original)
        item.update(id=f"{index:032x}", type=kind, label=label)
        record["triggers"][kind] = item

    def input_spec(name, kind, index, choices=None):
        parameter = {
            "id": f"{index:032x}",
            "name": name,
            "label": name,
            "type": kind,
            "children": [],
            "mandatory": True,
            "readonly": False,
            "attributes": {},
        }
        if choices:
            parameter["choices"] = [
                {"label": label, "value": value} for value, label in choices
            ]
        return {
            "name": name,
            "label": name,
            "type": kind,
            "order": index,
            "mandatory": True,
            "default_value": "",
            "parameter": parameter,
        }

    schedule = {"name": "Date", "triggers": {}}
    for index, (kind, label, specs) in enumerate(
        [
            ("daily", "Daily", [("glide_time", "glide_time")]),
            (
                "weekly",
                "Weekly",
                [("day_of_week", "day_of_week"), ("glide_time", "glide_time")],
            ),
            (
                "monthly",
                "Monthly",
                [("integer", "integer"), ("glide_time", "glide_time")],
            ),
            ("run_once", "Run Once", [("glide_date_time", "glide_date_time")]),
            ("repeat", "Repeat", [("glide_duration", "glide_duration")]),
        ],
        start=10,
    ):
        inputs = [
            input_spec(name, value_type, index * 10 + offset)
            for offset, (name, value_type) in enumerate(specs)
        ]
        for item in inputs:
            if item["name"] == "day_of_week":
                item["choiceList"] = [
                    {"value": str(day), "label": str(day)} for day in range(1, 8)
                ]
        schedule["triggers"][kind] = {
            "id": f"{index:032x}",
            "type": kind,
            "label": label,
            "description": "Source-derived schedule fixture",
            "inputs": inputs,
            "outputs": copy.deepcopy(original["outputs"][2:]),
        }
    result.append(schedule)
    return result


@pytest.mark.parametrize(
    "defect",
    ["readonly", "minimum", "weekday_choices", "extra_timezone", "duplicate_catalog"],
)
async def test_runtime_catalog_constraints_are_not_bypassed(settings, defect):
    coordinator, tools = routes(settings, base_flow())
    catalog = categories()
    kind = "daily"
    inputs = {"glide_time": "1970-01-01 09:00:00"}
    if defect == "readonly":
        catalog[-1]["triggers"]["daily"]["inputs"][0]["parameter"]["readonly"] = True
    elif defect == "minimum":
        kind, inputs = "repeat", {"glide_duration": "1970-01-01 00:00:30"}
        catalog[-1]["triggers"]["repeat"]["inputs"][0]["parameter"]["attributes"][
            "minimum_seconds"
        ] = 60
    elif defect == "weekday_choices":
        kind, inputs = (
            "weekly",
            {"day_of_week": "1", "glide_time": "1970-01-01 09:00:00"},
        )
        catalog[-1]["triggers"]["weekly"]["inputs"][0].pop("choiceList")
    elif defect == "extra_timezone":
        catalog[-1]["triggers"]["daily"]["inputs"].append(
            {
                "name": "time_zone",
                "type": "string",
                "parameter": {"id": "f" * 32, "type": "string", "name": "time_zone"},
            }
        )
    else:
        catalog[-1]["triggers"]["duplicate"] = copy.deepcopy(
            catalog[-1]["triggers"]["daily"]
        )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": catalog}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "type": kind, "inputs": inputs}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_trigger_native_instance_table_policy_rechecked(
    settings, monkeypatch, phase
):
    from servicenow_mcp import policy

    coordinator, tools = routes(settings, base_flow())
    edits = [
        {
            "op": "set_trigger",
            "type": "daily",
            "inputs": {"glide_time": "1970-01-01 09:00:00"},
        }
    ]
    preview = None
    if phase == "apply":
        preview = decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
        )
        assert preview["status"] == "success", preview
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_trigger_instance_v2"}
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
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_schedule_production_save_gate_rejects_before_reads(prod_settings):
    coordinator, tools = routes(prod_settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "type": "run_once",
                    "inputs": {"glide_date_time": "2027-01-01 00:00:00"},
                }
            ],
        )
    )
    assert result["status"] == "error" and len(coordinator.store) == 0
    assert not http_mock.calls


async def test_trigger_context_change_prevents_apply_save(settings):
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
            edits=[
                {
                    "op": "set_trigger",
                    "type": "daily",
                    "inputs": {"glide_time": "1970-01-01 09:00:00"},
                }
            ],
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


async def test_changed_table_descriptor_invalidates_preview(settings):
    _, tools = routes(settings, base_flow())
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {"table": "incident"},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    http_mock.get(f"{BASE}/api/now/processflow/table/incident/descriptor").respond(
        200, json={"result": {"data": {"displayField": "short_description"}}}
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["daily", "record_create"])
async def test_trigger_families_are_supported_by_readonly_local_validation(
    settings, kind
):
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.capabilities.flows import lifecycle
    from servicenow_mcp.platform.client import ServiceNowClient
    from tests.test_flow_lifecycle import validation_flow, validation_routes

    flow = validation_flow()
    flow["triggerInstances"] = []
    routes(settings, flow)
    inputs = (
        {"table": "incident"}
        if kind == "record_create"
        else {"glide_time": "1970-01-01 09:00:00"}
    )
    built = (
        await plan(
            settings, flow, [{"op": "set_trigger", "type": kind, "inputs": inputs}]
        )
    )["payload"]
    validation_routes(built)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(built, client)
    assert result["valid"] is True and result["complete"] is True, result


async def test_native_trigger_protocol_preview_apply_and_result_schemas(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    routes(settings, base_flow())
    server = NativeMCPServer("native-trigger-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )

    def save(request):
        body = json.loads(request.content)
        body["triggerInstances"][0]["id"] = "d" * 32
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
                            "op": "set_trigger",
                            "type": "daily",
                            "inputs": {"glide_time": "1970-01-01 09:00:00"},
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


def routes(settings, flow):
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": categories()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200,
        json={
            "result": [{"name": "incident", "label": "Incident", "sys_id": "b" * 32}]
        },
    )
    http_mock.get(f"{BASE}/api/now/processflow/table/incident/descriptor").respond(
        200, json={"result": {"data": {"displayField": "number"}}}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
        200,
        json={
            "result": [
                {
                    "name": "incident",
                    "element": "active",
                    "column_label": "Active",
                    "internal_type": "boolean",
                    "reference": "",
                    "choice": "0",
                }
            ]
        },
    )
    return coordinator, tools


@pytest.mark.parametrize(
    "kind", ["record_create", "record_update", "record_create_or_update"]
)
async def test_record_create_and_new_current_record_pill(settings, kind):
    flow = base_flow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "set_trigger",
                "type": kind,
                "inputs": {
                    "table": "incident",
                    "condition": "^EQ",
                    "run_flow_in": "background",
                },
            }
        ],
    )
    trigger = result["payload"]["triggerInstances"][0]
    table = next(row for row in trigger["inputs"] if row["name"] == "table")
    condition = next(row for row in trigger["inputs"] if row["name"] == "condition")
    assert table["reference"] == "incident" and table["displayField"] == "number"
    assert condition["table"] == condition["data_format"] == "incident"
    definition = next(
        t for c in categories() for t in c["triggers"].values() if t["type"] == kind
    )
    assert [row["name"] for row in trigger["outputs"]] == [
        row["name"] for row in definition["outputs"]
    ]
    assert [row["parameter"] for row in trigger["outputs"]] == [
        row["parameter"] for row in definition["outputs"]
    ]
    assert (
        result["payload"]["active"] is False
        and result["payload"]["isPublished"] is False
    )


@pytest.mark.parametrize(
    "kind,inputs",
    [
        ("daily", {"glide_time": "1970-01-01 09:30:00"}),
        ("weekly", {"day_of_week": "1", "glide_time": "1970-01-01 09:30:00"}),
        ("monthly", {"integer": 15, "glide_time": "1970-01-01 09:30:00"}),
        ("run_once", {"glide_date_time": "2027-01-02 09:30:00"}),
        ("repeat", {"glide_duration": "1970-01-02 00:00:00"}),
    ],
)
async def test_standard_schedules_are_inactive_native_draft_configuration(
    settings, kind, inputs
):
    flow = base_flow()
    routes(settings, flow)
    result = await plan(
        settings, flow, [{"op": "set_trigger", "type": kind, "inputs": inputs}]
    )
    trigger = result["payload"]["triggerInstances"][0]
    assert {row["name"]: row["value"] for row in trigger["inputs"]} == inputs
    assert result["payload"]["active"] is False
    assert result["payload"]["status"] == "draft"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_update_trigger_preserves_identity_and_opaque_settings_then_remove(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "daily",
                    "inputs": {"glide_time": "1970-01-01 09:30:00"},
                }
            ],
        )
    )["payload"]
    flow["triggerInstances"][0].update(id="c" * 32, vendor={"keep": True})
    routes(settings, flow)
    updated = await plan(
        settings,
        flow,
        [{"op": "update_trigger", "inputs": {"glide_time": "1970-01-01 10:00:00"}}],
    )
    assert updated["payload"]["triggerInstances"][0]["id"] == "c" * 32
    assert updated["payload"]["triggerInstances"][0]["vendor"] == {"keep": True}
    removed = await plan(settings, flow, [{"op": "remove_trigger"}])
    assert removed["payload"]["triggerInstances"] == []
    assert removed["pending_deletions"] == {"deletedTriggers": [{"id": "c" * 32}]}


@pytest.mark.parametrize("operation", ["update_trigger", "remove_trigger", "replace"])
@pytest.mark.parametrize("defect", [None, "identity", "opaque", "retained_deleted"])
async def test_saved_trigger_update_remove_and_replace_have_guarded_save_contract(
    settings, operation, defect
):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "daily",
                    "inputs": {"glide_time": "1970-01-01 09:00:00"},
                }
            ],
        )
    )["payload"]
    flow["triggerInstances"][0].update(id="c" * 32, vendor={"preserve": True})
    _, tools = routes(settings, flow)

    def save(request):
        body = json.loads(request.content)
        if operation == "update_trigger":
            assert body["triggerInstances"][0]["id"] == "c" * 32
            assert body["triggerInstances"][0]["vendor"] == {"preserve": True}
        else:
            assert body["deletedTriggers"] == [{"id": "c" * 32}]
        if operation == "replace":
            body["triggerInstances"][0]["id"] = "d" * 32
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        if defect == "retained_deleted" and operation != "update_trigger":
            actual["triggerInstances"].append(
                copy.deepcopy(flow["triggerInstances"][0])
            )
        elif defect == "identity" and actual["triggerInstances"]:
            actual["triggerInstances"][0]["id"] = "e" * 32
        elif defect:
            actual["description"] = "unexpected change"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    edit = (
        {"op": "update_trigger", "inputs": {"glide_time": "1970-01-01 10:00:00"}}
        if operation == "update_trigger"
        else {"op": "remove_trigger"}
        if operation == "remove_trigger"
        else {
            "op": "set_trigger",
            "type": "repeat",
            "replace": True,
            "inputs": {"glide_duration": "1970-01-01 00:01:00"},
        }
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert (
        applied["error"]["code"] == "UNKNOWN_OUTCOME"
        if defect
        else applied["status"] == "success"
    ), applied
    assert write.call_count == 1


async def test_record_table_change_requires_explicit_conditions_and_fresh_descriptor(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {"table": "incident", "condition": "active=true"},
                }
            ],
        )
    )["payload"]
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="explicit"):
        await plan(
            settings, flow, [{"op": "update_trigger", "inputs": {"table": "task"}}]
        )
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "task", "label": "Task", "sys_id": "a" * 32}]}
    )
    http_mock.get(f"{BASE}/api/now/processflow/table/task/descriptor").respond(
        200, json={"result": {"data": {"displayField": "number"}}}
    )
    updated = await plan(
        settings,
        flow,
        [{"op": "update_trigger", "inputs": {"table": "task", "condition": ""}}],
    )
    values = {
        item["name"]: item
        for item in updated["payload"]["triggerInstances"][0]["inputs"]
    }
    assert values["table"]["reference"] == "task"
    assert (
        values["condition"]["value"] == "^EQ" and values["condition"]["table"] == "task"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "table_missing",
        "descriptor_missing",
        "unknown_condition",
        "definition_drift",
        "condition_metadata",
        "template",
    ],
)
async def test_incomplete_record_contract_never_stages(settings, defect):
    flow = base_flow()
    coordinator, tools = routes(settings, flow)
    edit = {
        "op": "set_trigger",
        "type": "record_create",
        "inputs": {"table": "incident", "condition": "active=true"},
    }
    if defect == "table_missing":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": []}
        )
    elif defect == "descriptor_missing":
        http_mock.get(f"{BASE}/api/now/processflow/table/incident/descriptor").respond(
            200, json={"result": {"data": {}}}
        )
    elif defect == "unknown_condition":
        http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
            200, json={"result": []}
        )
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").mock(
            side_effect=lambda request: httpx2.Response(
                200,
                json={
                    "result": [
                        {"name": "incident", "label": "Incident", "sys_id": "b" * 32}
                    ]
                },
            )
        )
    else:
        flow = (await plan(settings, flow, [edit]))["payload"]
        coordinator, tools = routes(settings, flow)
        edit = {"op": "update_trigger", "inputs": {"condition": "active=false"}}
        if defect == "definition_drift":
            flow["triggerInstances"][0]["inputs"][0]["parameter"]["hint"] = "unproven"
        elif defect == "condition_metadata":
            flow["triggerInstances"][0]["metadata"] = {"predicates": [{"opaque": True}]}
        else:
            flow["triggerInstances"][0]["fromTemplate"] = True
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_schedule_catalog_change_invalidates_preview_before_save(settings):
    _, tools = routes(settings, base_flow())
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "type": "daily",
                    "inputs": {"glide_time": "1970-01-01 09:00:00"},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    current = categories()
    current[-1]["triggers"]["daily"]["description"] = "changed"
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": current}
    )
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("surface", ["pharmacy", "default", "script"])
async def test_trigger_removal_checks_hidden_consumers_without_clearing(
    settings, surface
):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "daily",
                    "inputs": {"glide_time": "1970-01-01 09:00:00"},
                }
            ],
        )
    )["payload"]
    if surface == "pharmacy":
        flow["pharmacy"] = {
            "prescriptions": [{"prescription": "{{Daily_1.run_start_time}}"}]
        }
    elif surface == "default":
        flow["outputs"] = [
            {
                "name": "time",
                "type": "string",
                "defaultValue": "{{Daily_1.run_start_time}}",
            }
        ]
    else:
        flow["actionInstances"][0]["inputs"][0].update(
            scriptActive=True, script="return fd_data.trigger.run_start_time;"
        )
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [{"op": "remove_trigger"}])


async def test_record_trigger_current_pill_resolves_internal_link_without_rewriting_output(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
        200,
        json={
            "result": [
                {
                    "name": "incident",
                    "element": "number",
                    "column_label": "Number",
                    "internal_type": "string",
                    "reference": "",
                    "choice": "0",
                }
            ]
        },
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "set_trigger",
                "type": "record_create",
                "inputs": {"table": "incident", "condition": "active=true"},
            },
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "Record {{Created_1.current.number}}"},
            },
        ],
    )
    assert result["payload"]["label_cache"][0]["name"] == "Created_1.current.number"
    assert (
        result["payload"]["triggerInstances"][0]["outputs"][0]["type"] == "document_id"
    )
    assert (
        result["payload"]["triggerInstances"][0]["outputs"][0]["parameter"]["reference"]
        == ""
    )


@pytest.mark.parametrize(
    "kind,inputs",
    [
        ("record_create", {"table": "incident", "condition": "active=true"}),
        ("record_update", {"table": "incident", "condition": "active=true"}),
        ("record_create_or_update", {"table": "incident", "condition": "active=true"}),
        ("daily", {"glide_time": "1970-01-01 09:30:00"}),
        ("weekly", {"day_of_week": "1", "glide_time": "1970-01-01 09:30:00"}),
        ("monthly", {"integer": 15, "glide_time": "1970-01-01 09:30:00"}),
        ("run_once", {"glide_date_time": "2027-01-02 09:30:00"}),
        ("repeat", {"glide_duration": "1970-01-02 00:00:00"}),
    ],
)
@pytest.mark.parametrize(
    "defect",
    [None, "value", "type", "outputs", "metadata", "active", "assigned_id_race"],
)
async def test_all_trigger_families_save_only_draft_with_independent_get(
    settings, kind, inputs, defect
):
    flow = base_flow()
    coordinator, tools = routes(settings, flow)

    def save(request):
        body = json.loads(request.content)
        assert body["active"] is False and body["isPublished"] is False
        assert body["status"] == "draft"
        assert (
            body["actionInstances"][0]["inputs"] == flow["actionInstances"][0]["inputs"]
        )
        body["triggerInstances"][0]["id"] = "d" * 32
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        trigger = actual["triggerInstances"][0]
        if defect == "value":
            trigger["inputs"][0]["value"] = "changed"
        elif defect == "type":
            trigger["type"] = "unexpected"
        elif defect == "outputs":
            trigger["outputs"].pop()
        elif defect == "metadata":
            trigger["vendor"] = True
        elif defect == "active":
            actual["active"] = True
        elif defect == "assigned_id_race":
            trigger["id"] = "e" * 32
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "type": kind, "inputs": inputs}],
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["triggers_before"] == []
    assert preview["data"]["summary"]["triggers"][0]["type"] == kind
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
    assert replay["status"] == "error" and write.call_count == 1
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "kind,inputs",
    [
        ("daily", {"glide_time": "09:30:00"}),
        ("daily", {"glide_time": "1970-01-01 25:30:00"}),
        ("daily", {"glide_time": "2027-01-01 09:30:00"}),
        ("weekly", {"day_of_week": "9", "glide_time": "1970-01-01 09:30:00"}),
        ("monthly", {"integer": 32, "glide_time": "1970-01-01 09:30:00"}),
        ("monthly", {"integer": "15", "glide_time": "1970-01-01 09:30:00"}),
        ("monthly", {"integer": True, "glide_time": "1970-01-01 09:30:00"}),
        ("run_once", {"glide_date_time": "2027-02-30 09:30:00"}),
        ("repeat", {"glide_duration": "1970-01-01 00:00:00"}),
        ("daily", {}),
        ("daily", {"glide_time": "{{Created_1.current}}"}),
        (
            "daily",
            {"glide_time": "1970-01-01 09:30:00", "timezone": "Europe/Copenhagen"},
        ),
        ("record_create", {"table": "sys_user"}),
        ("record_create", {"table": "sc_req_item"}),
        (
            "record_create",
            {"table": "incident", "condition": "active=javascript:gs.getUserID()"},
        ),
        ("record_create", {"table": "incident", "run_when_user_setting": "one_of"}),
        ("record_create", {"table": "incident", "run_flow_in": "unknown"}),
        ("record_create", {"table": "incident", "condition": False}),
        ("record_create", {"table": "incident", "condition": None}),
    ],
)
async def test_invalid_trigger_input_cannot_stage_or_write(settings, kind, inputs):
    coordinator, tools = routes(settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "type": kind, "inputs": inputs}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_record_run_options_follow_catalog_choices_and_keep_native_user_list(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "set_trigger",
                "type": "record_update",
                "inputs": {
                    "table": "incident",
                    "condition": "",
                    "run_on_extended": "true",
                    "run_when_setting": "interactive",
                    "run_flow_in": "foreground",
                    "run_when_user_setting": "one_of",
                    "run_when_user_list": "a" * 32 + "," + "b" * 32,
                },
            }
        ],
    )
    values = {
        row["name"]: row["value"]
        for row in result["payload"]["triggerInstances"][0]["inputs"]
    }
    assert values["condition"] == "^EQ"
    assert (
        values["run_on_extended"] == "true"
        and values["run_when_user_setting"] == "one_of"
    )
    assert values["run_when_user_list"] == "a" * 32 + "," + "b" * 32


@pytest.mark.parametrize("op", ["remove_trigger", "set_trigger"])
async def test_trigger_removal_and_replacement_reject_surviving_pills(settings, op):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {"table": "incident"},
                },
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "Record {{Created_1.current}}"},
                },
            ],
        )
    )["payload"]
    routes(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": op,
                    **(
                        {
                            "type": "daily",
                            "replace": True,
                            "inputs": {"glide_time": "1970-01-01 09:30:00"},
                        }
                        if op == "set_trigger"
                        else {}
                    ),
                }
            ],
        )
    edits = [
        {
            "op": "update_inputs",
            "step": "ui-1",
            "inputs": {"log_message": "No trigger pill"},
        },
        {"op": "remove_trigger"},
    ]
    removed = await plan(settings, flow, edits)
    assert removed["payload"]["triggerInstances"] == []


async def test_same_family_replacement_rejects_existing_trigger_consumers(settings):
    flow = base_flow()
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {"table": "incident"},
                },
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "Record {{Created_1.current}}"},
                },
            ],
        )
    )["payload"]
    flow["triggerInstances"][0]["id"] = "c" * 32
    routes(settings, flow)
    with pytest.raises(CapabilityError, match="still referenced"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "replace": True,
                    "type": "record_create",
                    "inputs": {"table": "incident"},
                }
            ],
        )


async def test_choice_list_on_native_string_parameter_is_enforced_at_create(settings):
    coordinator, tools = routes(settings, base_flow())
    catalog = categories()
    # Studio explicitly copies choiceList independently of the underlying
    # parameter type (createTriggerInstances:58156-58158). The name/values here
    # are fixture-only so this test does not invent a production run-mode key.
    record = next(c for c in catalog if c["name"] == "Record")["triggers"][
        "record_update"
    ]
    record["inputs"].append(
        {
            "name": "fixture_run_mode",
            "type": "string",
            "default_value": "fixture_once",
            "parameter": {
                "id": "f" * 32,
                "name": "fixture_run_mode",
                "type": "string",
                "label": "Fixture run mode",
            },
            "choiceList": [{"value": "fixture_once", "label": "Fixture once"}],
        }
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": catalog}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "type": "record_update",
                    "inputs": {"table": "incident", "fixture_run_mode": "unlisted"},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


async def test_schedule_creation_does_not_select_a_catalog_clock_default(settings):
    coordinator, tools = routes(settings, base_flow())
    catalog = categories()
    catalog[-1]["triggers"]["daily"]["inputs"][0]["default_value"] = (
        "1970-01-01 03:00:00"
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": catalog}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "type": "daily", "inputs": {}}],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


@pytest.mark.parametrize("kind", ["record_update", "record_create_or_update"])
async def test_catalog_run_mode_choice_list_round_trips_without_a_hardcoded_key(
    settings, kind
):
    flow = base_flow()
    routes(settings, flow)
    catalog = categories()
    definition = next(c for c in catalog if c["name"] == "Record")["triggers"][kind]
    definition["inputs"].append(
        {
            "name": "fixture_run_mode",
            "label": "Fixture run mode",
            "type": "string",
            "order": "99",
            "default_value": "fixture_once",
            "parameter": {
                "id": "f" * 32,
                "name": "fixture_run_mode",
                "type": "string",
                "label": "Fixture run mode",
            },
            "choiceList": [
                {"value": "fixture_once", "label": "Fixture once"},
                {"value": "fixture_always", "label": "Fixture always"},
            ],
        }
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": catalog}
    )
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": kind,
                    "inputs": {
                        "table": "incident",
                        "fixture_run_mode": "fixture_always",
                    },
                }
            ],
        )
    )["payload"]
    flow["triggerInstances"][0]["id"] = "c" * 32
    persist(flow)
    result = await plan(
        settings,
        flow,
        [{"op": "update_trigger", "inputs": {"fixture_run_mode": "fixture_once"}}],
    )
    actual = next(
        row
        for row in result["payload"]["triggerInstances"][0]["inputs"]
        if row["name"] == "fixture_run_mode"
    )
    assert actual["value"] == "fixture_once"
    assert actual["choiceList"] == definition["inputs"][-1]["choiceList"]
    assert result["payload"]["triggerInstances"][0]["id"] == "c" * 32


async def test_trigger_condition_and_unrelated_settings_round_trip_preservation(
    settings,
):
    flow = base_flow()
    flow.update(
        runAs="user",
        vendor_settings={"preserve": [1, {"unknown": True}]},
        inputs=[],
        outputs=[],
    )
    routes(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {
                        "table": "incident",
                        "condition": "active=true^ORactive=false^EQ",
                    },
                }
            ],
        )
    )["payload"]
    flow["triggerInstances"][0].update(
        id="c" * 32, metadata={"predicates": [], "unknown": {"keep": ["opaque"]}}
    )
    routes(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "update_trigger", "inputs": {"condition": "active=false^EQ"}}],
    )
    payload = result["payload"]
    assert (
        payload["runAs"] == flow["runAs"]
        and payload["vendor_settings"] == flow["vendor_settings"]
    )
    for key in (
        "inputs",
        "outputs",
        "flowVariables",
        "stages",
        "actionInstances",
        "flowLogicInstances",
        "subFlowInstances",
    ):
        assert payload[key] == flow[key]
    assert (
        payload["triggerInstances"][0]["metadata"]
        == flow["triggerInstances"][0]["metadata"]
    )
    assert (
        next(
            row
            for row in payload["triggerInstances"][0]["inputs"]
            if row["name"] == "condition"
        )["value"]
        == "active=false^EQ"
    )
