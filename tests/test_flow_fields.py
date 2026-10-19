"""Source-derived scalar declaration CRUD. Fixtures are not captured live saves."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import editing, semantics
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import family_definitions, typed_flow
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_lifecycle import capture_routes, status_header


async def plan(settings, flow, edits):
    before = copy.deepcopy(flow)
    command = FlowCommand(action="edit", sys_id=FLOW, edits=edits)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            command, await editing.load_state(command, client), client
        )
    assert flow == before
    return result


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
async def test_scalar_declaration_create_update_delete(settings, collection):
    flow = base_flow()
    if collection != "flowVariables":
        flow["type"] = "subflow"
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "counter",
                "label": "Counter",
                "type": "integer",
            },
            {
                "op": "update_field",
                "collection": collection,
                "name": "counter",
                "label": "Current count",
                "hint": "Count of items",
            },
        ],
    )
    field = planned["payload"][collection][0]
    assert field["name"] == "counter"
    assert field["type"] == "integer"
    assert field["label"] == "Current count"
    assert field["hint"] == "Count of items"
    assert field["parent"] == ""
    assert field.get("children", []) == []
    assert field["attributes"]["uiUniqueId"] == field["uiUniqueId"]
    assert not field.get("id")
    removed = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "temporary",
                "label": "Temporary",
                "type": "string",
            },
            {"op": "delete_field", "collection": collection, "name": "temporary"},
        ],
    )
    assert removed["payload"][collection] == []
    assert removed["created_fields"] == []


async def test_new_variable_is_a_typed_pill_and_assignment_in_same_request(settings):
    flow = typed_flow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "total",
                "label": "Total",
                "type": "integer",
            },
            {
                "op": "set_variables",
                "inputs": {"total": "{{ui-1.count}}"},
                "after": "1",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"count": "{{flow_variable.total}}"},
            },
        ],
    )
    assignment = planned["payload"]["flowLogicInstances"][0]
    assert assignment["inputs"] == assignment["flowVariables"]
    assert (
        assignment["inputs"][0]["parameter"]["uiUniqueId"]
        == planned["payload"]["flowVariables"][-1]["uiUniqueId"]
    )
    assert {row["name"] for row in planned["payload"]["label_cache"]} == {
        "ui-1.count",
        "flow_variable.total",
    }


async def test_subflow_input_is_available_to_later_native_pill_edit(settings):
    flow = typed_flow()
    flow["type"] = "subflow"
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "amount",
                "label": "Amount",
                "type": "integer",
                "default_value": 2,
                "mandatory": True,
            },
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"count": "{{subflow.amount}}"},
            },
        ],
    )
    assert planned["payload"]["inputs"][0]["defaultValue"] == 2
    assert planned["payload"]["label_cache"][0]["name"] == "subflow.amount"


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
async def test_deletion_rejects_surviving_variable_uses(settings, consumer):
    flow = typed_flow()
    flow["actionInstances"][1][consumer] = [
        {
            "name": "count",
            "value": "{{flow_variable.counter}}",
            "parameter": {"type": "integer"},
        }
    ]
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": "flowVariables", "name": "counter"}],
        )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "section,kind,extra",
    [
        ("flowVariables", "string", {}),
        ("flowVariables", "integer", {}),
        ("flowVariables", "boolean", {}),
        ("flowVariables", "json", {}),
        ("flowVariables", "reference", {"reference": "sc_req_item"}),
        ("inputs", "boolean", {"default_value": False}),
        (
            "outputs",
            "reference",
            {"reference": "sc_req_item", "default_value": "b" * 32},
        ),
    ],
)
async def test_supported_scalar_wire_types_and_preserved_settings(
    settings, section, kind, extra
):
    flow = base_flow()
    flow["type"] = "subflow" if section != "flowVariables" else "flow"
    flow["vendor"] = {"preserve": [False, 0, "opaque"]}
    fixtures(settings, flow)
    if kind == "reference":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": [{"name": "sc_req_item"}]}
        )
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": section,
                "name": "item",
                "label": "Item",
                "type": kind,
                **extra,
            }
        ],
    )
    field = planned["payload"][section][0]
    assert field["type"] == kind
    for key in (
        "actionInstances",
        "subFlowInstances",
        "flowLogicInstances",
        "triggerInstances",
        "stages",
        "vendor",
    ):
        if key in {"actionInstances", "subFlowInstances"}:
            for before, after in zip(flow[key], planned["payload"][key], strict=True):
                assert all(after[field] == value for field, value in before.items())
        else:
            assert planned["payload"][key] == flow[key]
    assert field.get("defaultValue") == extra.get(
        "default_value", False if kind == "boolean" else None
    )


@pytest.mark.parametrize(
    "edit",
    [
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "bad.name",
            "label": "Bad",
            "type": "string",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "X",
            "type": "object",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "X",
            "type": "array.string",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "X",
            "type": "reference",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "X",
            "type": "string",
            "default_value": "text",
        },
        {
            "op": "create_field",
            "collection": "inputs",
            "name": "x",
            "label": "X",
            "type": "string",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "",
            "type": "string",
        },
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "x",
            "label": "X",
            "type": "string",
            "id": "f" * 32,
        },
        {"op": "delete_field", "collection": "flowVariables", "name": "missing"},
        {
            "op": "update_field",
            "collection": "flowVariables",
            "name": "counter",
            "type": "string",
        },
    ],
)
async def test_invalid_declaration_request_never_stages(settings, edit):
    coordinator, tools = fixtures(settings, typed_flow())
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_unused_saved_declarations_delete_without_tombstones(settings):
    flow = typed_flow()
    flow["flowVariableNames"] = ["counter"]
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [{"op": "delete_field", "collection": "flowVariables", "name": "counter"}],
    )
    assert planned["payload"]["flowVariables"] == []
    assert planned["payload"]["flowVariableNames"] == []
    assert planned["pending_deletions"] == {}


@pytest.mark.parametrize(
    "use", ["assignment", "script", "transform", "default", "metadata_source", "nested"]
)
async def test_destructive_variable_edits_reject_real_dependencies(settings, use):
    flow = typed_flow()
    if use == "assignment":
        definition = next(
            d for d in family_definitions() if d["type"] == "SETFLOWVARIABLES"
        )
        flow["flowLogicInstances"] = [
            {
                "id": "d" * 32,
                "uiUniqueIdentifier": "setter",
                "definitionId": definition["id"],
                "flowLogicDefinition": definition,
                "type": "flowlogic",
                "order": 5,
                "parent": "",
                "inputs": [
                    {
                        "name": "counter",
                        "value": 1,
                        "parameter": copy.deepcopy(flow["flowVariables"][0]),
                    }
                ],
            }
        ]
        flow["flowLogicInstances"][0]["flowVariables"] = copy.deepcopy(
            flow["flowLogicInstances"][0]["inputs"]
        )
    elif use == "script":
        flow["actionInstances"][0]["inputs"][0].update(
            script="return fd_data.flow_var.counter;", scriptActive=True
        )
    elif use == "transform":
        flow["pharmacy"] = {"prescriptions": [{"value": "{{flow_variable.counter}}"}]}
    elif use == "default":
        flow["outputs"] = [
            {
                "name": "result",
                "type": "integer",
                "defaultValue": "{{flow_variable.counter}}",
            }
        ]
    elif use == "metadata_source":
        flow["actionInstances"][0]["inputs"][0]["sourceId"] = flow["flowVariables"][0][
            "id"
        ]
    else:
        flow["actionInstances"][0]["inputs"][0]["children"] = [
            {"name": "nested", "value": "{{flow_variable.counter}}"}
        ]
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": "flowVariables", "name": "counter"}],
        )


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
@pytest.mark.parametrize(
    "defect", [None, "id_race", "name", "type", "opaque", "retain_deleted", "ui"]
)
async def test_native_declaration_save_bound_context_and_independent_identity(
    settings, collection, defect
):
    flow = base_flow()
    flow["type"] = "subflow" if collection != "flowVariables" else "flow"
    flow[collection] = [
        {
            "id": "c" * 32,
            "name": "obsolete",
            "label": "Obsolete",
            "type": "string",
            "opaque": {"keep": True},
        }
    ]
    flow["vendor"] = {"keep": True}
    coordinator, tools = fixtures(settings, flow)
    update_set, row = capture_routes()
    saved = {}

    def save(request):
        body = json.loads(request.content)
        assert [field["name"] for field in body[collection]] == ["new_field"]
        saved.update(copy.deepcopy(body))
        echo = copy.deepcopy(body)
        echo[collection][0]["id"] = "e" * 32
        actual = copy.deepcopy(echo)
        if defect == "id_race":
            actual[collection][0]["id"] = "f" * 32
        elif defect == "name":
            actual[collection][0]["name"] = "different"
        elif defect == "type":
            actual[collection][0]["type"] = "boolean"
        elif defect == "opaque":
            actual["vendor"]["keep"] = False
        elif defect == "retain_deleted":
            actual[collection].append(copy.deepcopy(flow[collection][0]))
        elif defect == "ui":
            actual[collection][0]["uiUniqueId"] = "different"
        persist(actual)
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200,
            json={
                "result": {
                    **status_header(),
                    "sys_mod_count": "8",
                    "type": flow["type"],
                }
            },
        )
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200, json={"result": [{**row, "sys_mod_count": "8"}]}
        )
        return httpx2.Response(200, json={"result": {"data": echo}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
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
                {"op": "delete_field", "collection": collection, "name": "obsolete"},
                {
                    "op": "create_field",
                    "collection": collection,
                    "name": "new_field",
                    "label": "New",
                    "type": "string",
                },
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["context"]["state"] == "bound"
    assert (
        preview["data"]["summary"]["fields_before"][collection][0]["name"] == "obsolete"
    )
    assert preview["data"]["summary"]["fields"][collection][0]["name"] == "new_field"
    assert not route.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
        assert applied["data"]["capture"]["state"] == "metadata_verified"
        assert (
            saved[collection][0]["uiUniqueId"]
            == preview["data"]["summary"]["fields"][collection][0]["uiUniqueId"]
        )
    assert route.call_count == 1
    assert all(call.request.method in {"GET", "PUT"} for call in http_mock.calls)
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error"
    assert len(coordinator.store) == 0


async def test_existing_field_database_identity_does_not_normalize(settings):
    flow = typed_flow()
    actual = copy.deepcopy(flow)
    actual["flowVariables"][0]["id"] = "e" * 32
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(flow, actual, generated_ids=set())


async def test_unused_declaration_type_default_and_metadata_update_preserves_opaque_fields(
    settings,
):
    flow = base_flow()
    flow["type"] = "subflow"
    flow["inputs"] = [
        {
            "id": "c" * 32,
            "name": "amount",
            "label": "Amount",
            "type": "string",
            "opaque": {"retain": True},
            "defaultValue": "old",
        }
    ]
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "amount",
                "type": "integer",
                "default_value": 7,
                "mandatory": True,
                "maxsize": 64,
                "hint": "Enter count",
            }
        ],
    )
    assert planned["payload"]["inputs"][0] == {
        **flow["inputs"][0],
        "type": "integer",
        "type_label": "Integer",
        "defaultValue": 7,
        "mandatory": True,
        "maxsize": 64,
        "hint": "Enter count",
    }


@pytest.mark.parametrize(
    "kind,value",
    [
        ("integer", True),
        ("boolean", "false"),
        ("json", "invalid"),
        ("reference", "not-a-sys-id"),
    ],
)
async def test_interface_defaults_remain_native_and_typed(settings, kind, value):
    flow = base_flow()
    flow["type"] = "subflow"
    coordinator, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "value",
                    "label": "Value",
                    "type": kind,
                    "default_value": value,
                    **({"reference": "sc_req_item"} if kind == "reference" else {}),
                }
            ],
        )
    )
    assert preview["status"] == "error"
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "section,use", [("inputs", "pill"), ("inputs", "script"), ("outputs", "target")]
)
async def test_subflow_interface_consumers_block_delete(settings, section, use):
    flow = base_flow()
    flow["type"] = "subflow"
    flow[section] = [
        {"id": "c" * 32, "name": "message", "label": "Message", "type": "string"}
    ]
    if use == "pill":
        flow["actionInstances"][0]["inputs"][0]["value"] = "{{subflow.message}}"
    elif use == "script":
        flow["actionInstances"][0]["inputs"][0].update(
            scriptActive=True, script="return fd_data.subflow_inputs.message;"
        )
    else:
        flow["actionInstances"][0]["outputsToAssign"] = [
            {"name": "message", "value": "result"}
        ]
    fixtures(settings, flow)
    with pytest.raises(CapabilityError, match="referenced or assigned"):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": section, "name": "message"}],
        )


async def test_exact_pill_matching_does_not_block_similarly_named_variable(settings):
    flow = typed_flow()
    flow["flowVariables"].append(
        {"name": "counter_extra", "label": "Extra", "type": "integer"}
    )
    flow["actionInstances"][1]["inputs"][1]["value"] = "{{flow_variable.counter_extra}}"
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [{"op": "delete_field", "collection": "flowVariables", "name": "counter"}],
    )
    assert [row["name"] for row in planned["payload"]["flowVariables"]] == [
        "counter_extra"
    ]
    assert planned["payload"]["label_cache"][0]["name"] == "flow_variable.counter_extra"


async def test_explicit_consumer_update_before_removal_does_not_clear_other_values(
    settings,
):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"][1]["value"] = "{{flow_variable.counter}}"
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {"op": "update_inputs", "step": "ui-2", "inputs": {"count": 5}},
            {"op": "delete_field", "collection": "flowVariables", "name": "counter"},
        ],
    )
    assert planned["payload"]["flowVariables"] == []
    assert planned["payload"]["actionInstances"][1]["inputs"][1]["value"] == 5
    assert (
        planned["payload"]["actionInstances"][0]["inputs"]
        == flow["actionInstances"][0]["inputs"]
    )


@pytest.mark.parametrize(
    "table", ["sys_hub_flow_input", "sys_hub_flow_output", "sys_hub_flow_variable"]
)
@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_declaration_policy_is_rechecked(settings, monkeypatch, table, phase):
    from servicenow_mcp import policy

    section = {
        "sys_hub_flow_input": "inputs",
        "sys_hub_flow_output": "outputs",
        "sys_hub_flow_variable": "flowVariables",
    }[table]
    flow = base_flow()
    flow["type"] = "subflow" if section != "flowVariables" else "flow"
    coordinator, tools = fixtures(settings, flow)
    edits = [
        {
            "op": "create_field",
            "collection": section,
            "name": "sample",
            "label": "Sample",
            "type": "string",
        }
    ]
    preview = None
    if phase == "apply":
        preview = decode_response(
            await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
        )
        assert preview["status"] == "success", preview
    monkeypatch.setattr(policy, "DENIED_TABLES", policy.DENIED_TABLES | {table})
    if phase == "apply":
        assert preview is not None
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


async def test_declaration_changed_since_preview_blocks_save(settings):
    flow = typed_flow()
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "flowVariables",
                    "name": "total",
                    "label": "Total",
                    "type": "integer",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    changed = copy.deepcopy(flow)
    changed["flowVariables"][0]["label"] = "Different"
    persist(changed)
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("defect", ["duplicate_id", "invalid_id", "removed_id_reused"])
async def test_assigned_declaration_ids_fail_closed(settings, defect):
    flow = typed_flow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {"op": "delete_field", "collection": "flowVariables", "name": "counter"},
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "one",
                "label": "One",
                "type": "integer",
            },
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "two",
                "label": "Two",
                "type": "integer",
            },
        ],
    )
    actual = copy.deepcopy(planned["payload"])
    for index, row in enumerate(actual["flowVariables"]):
        row["id"] = ("e" if index == 0 else "f") * 32
    if defect == "duplicate_id":
        actual["flowVariables"][1]["id"] = "e" * 32
    elif defect == "invalid_id":
        actual["flowVariables"][0]["id"] = True
    else:
        actual["flowVariables"][0]["id"] = flow["flowVariables"][0]["id"]
    with pytest.raises(CapabilityError):
        semantics.compare_saved(
            planned["payload"],
            actual,
            generated_ids=set(),
            generated_fields=planned["created_fields"],
            removed_fields=planned["removed_fields"],
        )


async def test_preview_masks_sensitive_default_and_does_not_copy_opaque_metadata(
    settings,
):
    flow = base_flow()
    flow["type"] = "subflow"
    flow["inputs"] = [
        {
            "name": "password",
            "label": "Password",
            "type": "string",
            "defaultValue": "private-sensitive-marker",
            "opaque": {"private-key": "private-value"},
        }
    ]
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "password",
                    "hint": "Use a vault",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert "private-sensitive-marker" not in str(preview)
    assert "private-key" not in str(preview)


async def test_reference_table_missing_blocks_preview(settings):
    flow = base_flow()
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": []}
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "flowVariables",
                    "name": "record",
                    "label": "Record",
                    "type": "reference",
                    "reference": "missing_table",
                }
            ],
        )
    )
    assert preview["error"]["code"] == "UNAVAILABLE"
    assert len(coordinator.store) == 0


async def test_reference_variable_dotwalk_uses_new_declared_table(settings):
    flow = typed_flow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "sc_req_item"}]}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
        200,
        json={
            "result": [
                {
                    "name": "sc_req_item",
                    "element": "state",
                    "column_label": "State",
                    "internal_type": "integer",
                    "reference": "",
                    "choice": "3",
                }
            ]
        },
    )
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "request",
                "label": "Request",
                "type": "reference",
                "reference": "sc_req_item",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"log_message": "State: {{flow_variable.request.state}}"},
            },
        ],
    )
    assert planned["payload"]["label_cache"][0]["name"] == "flow_variable.request.state"
    assert planned["payload"]["label_cache"][0]["column_name"] == "state"


async def test_used_variable_label_update_preserves_assignment_value_and_opaque_parameter(
    settings,
):
    flow = typed_flow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    built = await plan(
        settings,
        flow,
        [{"op": "set_variables", "inputs": {"counter": 3}, "after": "1"}],
    )
    flow = built["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    for key in ("inputs", "flowVariables"):
        flow["flowLogicInstances"][0][key][0]["parameter"]["opaque"] = {"keep": True}
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "flowVariables",
                "name": "counter",
                "label": "Current counter",
                "hint": "Keep count",
            }
        ],
    )
    assignment = planned["payload"]["flowLogicInstances"][0]
    assert assignment["inputs"] == assignment["flowVariables"]
    assert assignment["inputs"][0]["value"] == 3
    assert assignment["inputs"][0]["parameter"]["opaque"] == {"keep": True}
    assert assignment["inputs"][0]["parameter"]["label"] == "Current counter"


async def test_unrelated_complex_interface_and_stage_are_not_rewritten(settings):
    flow = typed_flow()
    flow["type"] = "subflow"
    nested = {
        "id": "c" * 32,
        "name": "opaque_object",
        "label": "Object",
        "type": "object",
        "children": [{"name": "child", "type": "string", "opaque": {"keep": True}}],
    }
    flow["inputs"] = [copy.deepcopy(nested)]
    flow["stages"] = {
        "saved-stage": {
            "stageId": "saved-stage",
            "componentIndexes": [],
            "opaque": {"keep": 1},
        }
    }
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "new_input",
                "label": "New input",
                "type": "string",
            }
        ],
    )
    assert planned["payload"]["inputs"][0] == nested
    assert planned["payload"]["stages"] == flow["stages"]
    with pytest.raises(CapabilityError, match="scalar"):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": "inputs", "name": "opaque_object"}],
        )


async def test_new_declaration_native_protocol_preview_and_apply(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    flow = typed_flow()
    fixtures(settings, flow)
    server = NativeMCPServer("field-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )

    def save(request):
        body = json.loads(request.content)
        body["flowVariables"][-1]["id"] = "e" * 32
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
                            "op": "create_field",
                            "collection": "flowVariables",
                            "name": "message",
                            "label": "Message",
                            "type": "string",
                        },
                        {
                            "op": "update_inputs",
                            "step": "ui-1",
                            "inputs": {"log_message": "{{flow_variable.message}}"},
                        },
                    ],
                },
            )
            assert not preview.is_error, preview
            await session.validate_tool_result("flow_preview", preview)
            assert not write.called
            token = preview.structured_content["data"]["preview_token"]
            applied = await session.call_tool(
                "mutation_apply", {"preview_token": token}
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert not applied.is_error, applied
            assert write.call_count == 1
        tasks.cancel_scope.cancel()


async def test_scalar_delete_rejects_flattened_child_registration(settings):
    flow = typed_flow()
    flow["flowVariables"].append(
        {"name": "counter.child", "parent": "counter", "type": "string"}
    )
    fixtures(settings, flow)
    with pytest.raises(CapabilityError, match="scalar"):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": "flowVariables", "name": "counter"}],
        )


async def test_assignment_metadata_refresh_changes_only_requested_settings(settings):
    flow = typed_flow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    built = await plan(
        settings,
        flow,
        [{"op": "set_variables", "inputs": {"counter": 3}, "after": "1"}],
    )
    flow = built["payload"]
    flow["flowLogicInstances"][0]["id"] = "d" * 32
    flow["flowVariables"][0]["hint"] = "Declaration hint"
    for key in ("inputs", "flowVariables"):
        flow["flowLogicInstances"][0][key][0]["parameter"]["hint"] = "Assignment hint"
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "flowVariables",
                "name": "counter",
                "label": "New counter",
            }
        ],
    )
    assignment = planned["payload"]["flowLogicInstances"][0]
    assert assignment["inputs"] == assignment["flowVariables"]
    assert assignment["inputs"][0]["parameter"]["label"] == "New counter"
    assert assignment["inputs"][0]["parameter"]["hint"] == "Assignment hint"


@pytest.mark.parametrize("collection", ["inputs", "outputs", "flowVariables"])
async def test_declarations_cannot_write_in_production(prod_settings, collection):
    coordinator, tools = fixtures(prod_settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": collection,
                    "name": "sample",
                    "label": "Sample",
                    "type": "string",
                }
            ],
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert not http_mock.calls


async def test_declaration_save_then_read_edit_retains_assigned_identity(settings):
    flow = typed_flow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "total",
                "label": "Total",
                "type": "integer",
            }
        ],
    )
    saved = copy.deepcopy(planned["payload"])
    saved["flowVariables"][-1]["id"] = "e" * 32
    persist(saved)
    after = await plan(
        settings,
        saved,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"count": "{{flow_variable.total}}"},
            }
        ],
    )
    assert after["payload"]["flowVariables"][-1] == saved["flowVariables"][-1]
    assert after["payload"]["label_cache"][0]["name"] == "flow_variable.total"
    semantics.compare_saved(after["payload"], after["payload"], generated_ids=set())
