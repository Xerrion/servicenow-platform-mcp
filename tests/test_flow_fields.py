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
from tests._compiled_snapshot_fixture import snapshot_storage
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_active_drafts import MAIN, SELECTED, ActiveModel
from tests.test_flow_data_edits import family_definitions, typed_flow
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist
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


@pytest.mark.parametrize(
    "kind,label,value",
    [
        ("glide_date_time", "Native UTC Date/Time", "2026-10-26 14:30:00"),
        ("decimal", "Decimal", "0012.500"),
        ("decimal", "Native decimal amount", 12.5),
        ("float", "Floating Point Number", "-1.25e2"),
        ("float", "Native floating point", -125.0),
    ],
)
async def test_native_variable_scalars_keep_typed_assignments_and_pills(
    settings, kind, label, value
):
    flow = typed_flow()
    flow["actionInstances"][0]["actionType"]["outputs"].append(
        {"name": "native_value", "label": "Native value", "type": kind}
    )
    for name in ("literal_value", "pill_value"):
        flow["actionInstances"][1]["inputs"].append(
            {"name": name, "value": "", "parameter": {"name": name, "type": kind}}
        )
    fixtures(settings, flow)
    field_types = [{"value": kind, "label": label, "vendorMetadata": {"revision": "2"}}]
    type_route = http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200, json={"result": {"data": copy.deepcopy(field_types)}}
    )
    catalog = copy.deepcopy(family_definitions())
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog}
    )
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": name,
                "label": name.replace("_", " ").title(),
                "type": kind,
            }
            for name in ("literal_value", "pill_value")
        ]
        + [
            {
                "op": "set_variables",
                "inputs": {
                    "literal_value": value,
                    "pill_value": "{{ui-1.native_value}}",
                },
                "after": "ui-1",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {
                    name: "{{flow_variable." + name + "}}"
                    for name in ("literal_value", "pill_value")
                },
            },
        ],
    )
    payload = planned["payload"]
    declarations = payload["flowVariables"][-2:]
    for position, field in enumerate(declarations, start=1):
        uid = field["uiUniqueId"]
        assert field == {
            "name": field["name"],
            "label": field["name"].replace("_", " ").title(),
            "type": kind,
            "type_label": label,
            "order": position,
            "path": position,
            "parent": "",
            "mandatory": False,
            "hint": "",
            "uiUniqueId": uid,
            "attributes": {
                "uiUniqueId": uid,
                "sourceUiUniqueId": "",
                "sourceType": "",
                "sourceId": "",
            },
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        }
        assert "defaultValue" not in field and "value" not in field
    assignment = payload["flowLogicInstances"][0]
    assert assignment["inputs"] == assignment["flowVariables"]
    for field, binding, assigned in zip(
        declarations,
        assignment["inputs"],
        (value, "{{ui-1.native_value}}"),
        strict=True,
    ):
        assert binding == {
            "id": "",
            "name": field["name"],
            "parameter": field,
            "children": [],
            "value": assigned,
            "scriptActive": False,
            "displayValue": "" if assigned == "{{ui-1.native_value}}" else assigned,
        }
        assert type(binding["value"]) is type(assigned)
    for binding in payload["actionInstances"][1]["inputs"][-2:]:
        assert binding["value"] == "{{flow_variable." + binding["name"] + "}}"
        assert binding["displayValue"] == ""
        assert binding["parameter"] == {"name": binding["name"], "type": kind}
    assert {row["name"]: row["type"] for row in payload["label_cache"]} == {
        "ui-1.native_value": kind,
        "flow_variable.literal_value": kind,
        "flow_variable.pill_value": kind,
    }
    assert type_route.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


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


@pytest.mark.parametrize("section", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "kind,label,value,updated",
    [
        ("glide_date_time", "Date/Time", "2026-10-26 14:30:00", "2026-10-27 09:00:00"),
        ("decimal", "Decimal", "0012.500", "-0.000"),
        ("decimal", "Native decimal amount", 12.5, 0),
        ("float", "Floating Point Number", "-1.25e2", "0.0"),
        ("float", "Native floating point", -125.0, 0.0),
        ("number", "Number", "1e3", "-0.00"),
        ("number", "Number", 1000, 0),
    ],
)
async def test_native_interface_scalars_preserve_defaults_and_input_pills(
    settings, section, kind, label, value, updated
):
    flow = typed_flow()
    flow["type"] = "subflow"
    flow["actionInstances"][1]["inputs"].append(
        {"name": "native_value", "value": "", "parameter": {"type": kind}}
    )
    fixtures(settings, flow)
    field_types = [{"value": kind, "label": label, "vendorMetadata": {"revision": "2"}}]
    type_route = http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200, json={"result": {"data": copy.deepcopy(field_types)}}
    )
    edits = [
        {
            "op": "create_field",
            "collection": section,
            "name": "amount",
            "label": "Amount",
            "type": kind,
            "default_value": value,
        },
        {
            "op": "create_field",
            "collection": section,
            "name": "unset_value",
            "label": "Unset value",
            "type": kind,
        },
        {
            "op": "update_field",
            "collection": section,
            "name": "amount",
            "label": "Current amount",
            "hint": "Native scalar",
            "default_value": updated,
        },
    ]
    if section == "inputs":
        edits.append(
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"native_value": "{{subflow.amount}}"},
            }
        )
    created = await plan(settings, flow, edits[:2])
    initial, initial_unset = created["payload"][section]
    assert initial["defaultValue"] == value
    assert type(initial["defaultValue"]) is type(value)
    assert initial["type"] == kind and initial["type_label"] == label
    assert "defaultValue" not in initial_unset
    planned = await plan(settings, flow, edits)
    field, unset = planned["payload"][section]
    uid = field["uiUniqueId"]
    assert field == {
        "name": "amount",
        "label": "Current amount",
        "type": kind,
        "type_label": label,
        "order": 1,
        "path": 0,
        "parent": "",
        "mandatory": False,
        "hint": "Native scalar",
        "uiUniqueId": uid,
        "attributes": {
            "uiUniqueId": uid,
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        },
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
        "defaultValue": updated,
    }
    assert type(field["defaultValue"]) is type(updated)
    assert unset["type"] == kind and unset["type_label"] == label
    assert "defaultValue" not in unset and "value" not in unset
    assert unset["attributes"]["uiUniqueId"] == unset["uiUniqueId"]
    if section == "inputs":
        binding = planned["payload"]["actionInstances"][1]["inputs"][-1]
        assert binding == {
            **flow["actionInstances"][1]["inputs"][-1],
            "value": "{{subflow.amount}}",
            "displayValue": "",
        }
        assert planned["payload"]["label_cache"][0]["name"] == "subflow.amount"
        assert planned["payload"]["label_cache"][0]["type"] == kind
    else:
        assert planned["payload"]["label_cache"] == []
    assert type_route.called is (kind in {"decimal", "float"})
    assert all(call.request.method == "GET" for call in http_mock.calls)


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
        "default_value",
        False
        if kind == "boolean"
        else ""
        if section == "flowVariables" and kind == "string"
        else None,
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
    _, row = capture_routes()
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
        assert applied["data"]["revision"] == "8" and "capture" not in applied["data"]
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
        ("glide_date_time", 0),
        ("decimal", True),
        ("float", "NaN"),
        ("number", {}),
    ],
)
async def test_interface_defaults_remain_native_and_typed(settings, kind, value):
    flow = base_flow()
    flow["type"] = "subflow"
    coordinator, tools = fixtures(settings, flow)
    if kind in {"decimal", "float"}:
        http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
            200, json={"result": {"data": [{"value": kind, "label": "Native numeric"}]}}
        )
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
    if kind in {"decimal", "float"}:
        assert "numeric" in preview["error"]["message"].lower()
    assert all(call.request.method == "GET" for call in http_mock.calls)


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
        binding = flow["actionInstances"][0]["inputs"][0]
        binding["scriptActive"] = True
        binding["script"] = {
            binding["name"]: {
                "script": "return fd_data.subflow_inputs.message;",
                "scriptActive": True,
                "showScript": True,
            }
        }
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
    removed = await plan(
        settings,
        flow,
        [{"op": "delete_field", "collection": "inputs", "name": "opaque_object"}],
    )
    assert removed["payload"]["inputs"] == []
    assert removed["payload"]["stages"] == flow["stages"]


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


@pytest.mark.parametrize("section", ["inputs", "outputs"])
async def test_active_published_v2_subflow_interface_crud_preserves_runtime(
    settings, monkeypatch, section
):
    model = ActiveModel(settings, monkeypatch)
    type_route = http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200, json={"result": {"data": [{"value": "decimal", "label": "Decimal"}]}}
    )
    catalog = copy.deepcopy(DEFS)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog["flow_logic"]}
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": catalog["trigger_categories"]}
    )
    model.flow.update(
        type="subflow", status="published", isPublished=True, remoteTriggerSysId=""
    )
    model.header.update(type="subflow", status="published", remote_trigger_id="")
    model.flow[section] = [
        {
            "id": "c" * 32,
            "name": "obsolete",
            "label": "Obsolete",
            "type": "string",
            "order": 1,
            "opaque": {"keep": "published only"},
        },
        {
            "id": "d" * 32,
            "name": "retained",
            "label": "Retained",
            "type": "glide_date_time",
            "defaultValue": "2026-10-26 14:30:00",
            "order": 2,
            "opaque": {"keep": [False, 0]},
        },
    ]
    model.flow["vendor"] = {"opaque": {"preserve": [False, 0, ""]}}
    model.snapshot = {
        **copy.deepcopy(model.flow),
        "id": MAIN,
        "snapshot": True,
        "jsonSnapshot": True,
        "parentFlow": FLOW,
        "security": {"can_read": True},
    }
    model.serialized_record, model.serialized_chunks = snapshot_storage(model.snapshot)
    http_mock.get(f"{BASE}/api/now/processflow/flow/{MAIN}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(model.snapshot)}}
        )
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{MAIN}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(model.serialized_record)}
        )
    )
    original = copy.deepcopy(model.flow)
    published = copy.deepcopy(
        (model.snapshot, model.serialized_record, model.serialized_chunks, model.main)
    )
    settings_before = copy.deepcopy((model.properties, model.plans, model.trigger))

    def assign_identity():
        model.flow[section][-1]["id"] = "e" * 32
        model.header["status"] = model.flow["status"]

    model.after_save = assign_identity
    preview = await model.preview(
        edits=[
            {"op": "delete_field", "collection": section, "name": "obsolete"},
            {
                "op": "create_field",
                "collection": section,
                "name": "amount",
                "label": "Amount",
                "type": "decimal",
                "default_value": "0012.500",
            },
            {
                "op": "update_field",
                "collection": section,
                "name": "amount",
                "label": "Working amount",
                "default_value": "-0.000",
                "hint": "Working draft only",
            },
        ]
    )
    assert preview["status"] == "success", preview
    assert model.flow == original
    assert not model.writes
    assert all(call.request.method == "GET" for call in http_mock.calls)
    applied = await model.apply(preview)
    assert applied["status"] == "success", applied
    assert applied["data"]["runtime_preservation"] == "verified"
    assert applied["data"]["revision"] == "8"
    assert len(model.writes) == 1
    wire = model.writes[0]
    assert wire["status"] == "modified" and wire["isPublished"] is False
    assert wire["active"] is True and wire["flowEngineVersion"] == 2
    assert wire["masterSnapshotId"] == MAIN and wire["latestSnapshot"] == SELECTED
    assert wire["remoteTriggerSysId"] == ""
    assert wire["vendor"] == original["vendor"]
    assert wire["flowVariables"] == original["flowVariables"]
    other = "outputs" if section == "inputs" else "inputs"
    assert wire[other] == original[other]
    assert [field["name"] for field in wire[section]] == ["retained", "amount"]
    assert wire[section][0] == original[section][1]
    field = wire[section][1]
    assert field["type"] == "decimal" and field["type_label"] == "Decimal"
    assert type_route.called
    assert field["defaultValue"] == "-0.000"
    assert type(field["defaultValue"]) is str
    assert field["label"] == "Working amount" and field["hint"] == "Working draft only"
    assert field["attributes"]["uiUniqueId"] == field["uiUniqueId"]
    assert not field.get("id")
    assert model.flow[section][1] == {**field, "id": "e" * 32}
    assert model.flow["status"] == "draft" and model.flow["isPublished"] is False
    assert (
        model.snapshot,
        model.serialized_record,
        model.serialized_chunks,
        model.main,
    ) == published
    assert (model.properties, model.plans, model.trigger) == settings_before
    assert model.snapshot[section] == original[section]
    assert all(
        call.request.method == "GET"
        or call.request.method == "PUT"
        and call.request.url.path == "/api/now/processflow/flow"
        for call in http_mock.calls
    )
    assert (await model.apply(preview))["status"] == "error"
    assert len(model.writes) == 1 and len(model.coordinator.store) == 0


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
@pytest.mark.parametrize(
    "change",
    [
        {"snapshot": True},
        {"snapshot": True, "jsonSnapshot": True},
        {"masterSnapshot": True},
        {"security": {"can_write": False}},
    ],
)
async def test_native_scalar_create_preserves_snapshot_and_readonly_owners(
    settings, collection, change
):
    flow = base_flow()
    if collection != "flowVariables":
        flow["type"] = "subflow"
    flow.update(copy.deepcopy(change))
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": collection,
                    "name": "amount",
                    "label": "Amount",
                    "type": "decimal",
                }
            ],
        )
    )
    assert preview["status"] == "error", preview
    assert flow == before
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "collection,kind",
    [
        (collection, kind)
        for collection in ("flowVariables", "inputs", "outputs")
        for kind in ("glide_date_time", "decimal", "float")
    ]
    + [("inputs", "number"), ("outputs", "number")],
)
@pytest.mark.parametrize("op", ["update_field", "delete_field"])
async def test_native_scalar_readonly_declarations_never_stage(
    settings, collection, kind, op
):
    flow = base_flow()
    if collection != "flowVariables":
        flow["type"] = "subflow"
    flow[collection] = [
        {
            "id": "c" * 32,
            "name": "amount",
            "label": "Amount",
            "type": kind,
            "readonly": True,
            "opaque": {"preserve": [False, 0]},
        }
    ]
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200,
        json={"result": {"data": [{"value": kind, "label": "Native readonly scalar"}]}},
    )
    edit = {"op": op, "collection": collection, "name": "amount"}
    if op == "update_field":
        edit["label"] = "Changed amount"
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "error", preview
    assert "read-only" in preview["error"]["message"].lower()
    assert flow == before
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("glide_date_time", "2026-10-26 14:30:00"),
        ("decimal", "0012.500"),
        ("float", -125.0),
    ],
)
async def test_native_variable_scalars_still_require_assignment_not_defaults(
    settings, kind, value
):
    flow = typed_flow()
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200,
        json={"result": {"data": [{"value": kind, "label": "Native assigned scalar"}]}},
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "flowVariables",
                    "name": "amount",
                    "label": "Amount",
                    "type": kind,
                    "default_value": value,
                }
            ],
        )
    )
    assert preview["status"] == "error", preview
    assert "set_variables" in preview["error"]["message"]
    assert flow == before
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
