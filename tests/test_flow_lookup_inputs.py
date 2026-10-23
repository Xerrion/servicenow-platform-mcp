"""Sanitized native lookup inputs and linked record output contracts. No live writes."""

import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, data
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan

LOOKUP = "e" * 32


def definition():
    return {
        "id": LOOKUP,
        "name": "Fixture lookup",
        "type": "",
        "state": "published",
        "is_snapshot": True,
        "security": {"can_read": True},
        "entitled": True,
        "opaque": {"retain": True},
        "inputs": [
            {
                "name": "table",
                "type": "table_name",
                "defaultValue": "",
                "children": [],
                "maxsize": 80,
            },
            {
                "name": "conditions",
                "type": "conditions",
                "reference": "",
                "dependent_on": "table",
                "use_dependent": True,
                "defaultValue": "",
                "children": [],
                "maxsize": 4000,
            },
            {
                "name": "sort_column",
                "type": "field_name",
                "dependent_on": "table",
                "defaultValue": "",
                "children": [],
            },
            {
                "name": "sort_type",
                "type": "choice",
                "defaultValue": "sort_asc",
                "choices": [
                    {"value": "sort_asc", "label": "Ascending"},
                    {"value": "sort_desc", "label": "Descending"},
                ],
            },
            {
                "name": "if_multiple_records_are_found_action",
                "type": "choice",
                "defaultValue": "use_first_record",
                "choices": [
                    {"value": "error", "label": "Fail"},
                    {"value": "use_first_record", "label": "First"},
                ],
            },
            {
                "name": "__snc_dont_fail_on_error",
                "type": "boolean",
                "defaultValue": "",
                "children": [],
            },
        ],
        "outputs": [
            {
                "id": "f" * 32,
                "name": "Record",
                "label": "Record",
                "type": "document_id",
                "reference": "",
                "internal_link": "table",
                "children": [],
                "opaque": {"retain": True},
            },
            {
                "name": "Table",
                "type": "table_name",
                "reference": "",
                "internal_link": "table",
                "children": [],
            },
        ],
    }


def routes(schema=None):
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{LOOKUP}").respond(
        200, json={"result": schema or definition()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200,
        json={
            "result": [
                {
                    "sys_id": "c" * 32,
                    "name": "question_choice",
                    "label": "Fixture Choice",
                }
            ]
        },
    )
    http_mock.get(
        f"{BASE}/api/now/processflow/table/question_choice/descriptor"
    ).respond(200, json={"result": {"data": {"displayField": "text"}}})

    def dictionary(request):
        column = request.url.params["sysparm_query"].split("element=")[-1]
        return httpx2.Response(
            200,
            json={
                "result": [
                    {
                        "name": "question_choice",
                        "element": column,
                        "column_label": column,
                        "internal_type": "boolean"
                        if column == "inactive"
                        else "string",
                        "reference": "",
                        "choice": "0",
                    }
                ]
            },
        )

    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").mock(side_effect=dictionary)


def insert(
    conditions="inactive=false^valueINADD,REMOVE^value={{flow_variable.requested_action}}",
):
    return {
        "op": "insert_action",
        "action_type_sys_id": LOOKUP,
        "before": "ui-1",
        "ref": "action_choice",
        "inputs": {
            "table": "question_choice",
            "conditions": conditions,
            "if_multiple_records_are_found_action": "error",
            "__snc_dont_fail_on_error": False,
        },
    }


def flow_fixture():
    flow = base_flow()
    flow["flowVariables"] = [
        {"name": "requested_action", "type": "string", "children": []}
    ]
    return flow


def test_lookup_static_conditions_reproduce_native_insertion_contract():
    schema = definition()
    row = calls.step(
        flow_fixture(), schema, insert()["inputs"], subflow=False, uid="fixture-new"
    )
    assert (
        data.named(row.raw["inputs"], "conditions")["value"]
        == insert()["inputs"]["conditions"]
    )
    assert row.raw["actionType"] == schema


async def test_lookup_linked_record_uses_table_and_dictionary_not_query_inferred_enum(
    settings,
):
    flow = flow_fixture()
    fixtures(settings, flow)
    routes()
    edits = [
        insert(),
        {
            "op": "update_inputs",
            "step": "ui-1",
            "inputs": {"log_message": "Selected {{action_choice.Record.value}}"},
        },
    ]
    result = await plan(settings, flow, edits)
    row = result["payload"]["actionInstances"][0]
    assert row["actionType"] == definition()
    condition = data.named(row["inputs"], "conditions")
    assert condition["value"] == insert()["inputs"]["conditions"]
    assert condition["reference"] == condition["data_format"] == "question_choice"
    entry = next(
        row
        for row in result["payload"]["label_cache"]
        if row["name"].endswith(".Record.value")
    )
    assert entry["type"] == "string" and not entry["choices"]
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_lookup_literal_conditions_have_supported_native_scalar_shape(settings):
    flow = flow_fixture()
    fixtures(settings, flow)
    routes()
    result = await plan(settings, flow, [insert("inactive=false^valueINADD,REMOVE")])
    assert (
        result["payload"]["actionInstances"][0]["inputs"][1]["value"]
        == "inactive=false^valueINADD,REMOVE"
    )


@pytest.mark.parametrize(
    "condition",
    [
        "value=javascript:bad()",
        "value=ADD^NQinactive=true",
        "value=ADD^ORDERBYvalue",
        "value=ADD^GROUPBYvalue",
        "not an encoded condition",
        "inactiveISEMPTYfalse",
        "value=ADD^",
        "value=ADD^" * 51,
    ],
)
async def test_lookup_rejects_unsupported_conditions_without_writes(
    settings, condition
):
    flow = flow_fixture()
    fixtures(settings, flow)
    routes()
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [insert(condition)])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_table",
        "no_descriptor",
        "missing_field",
        "dynamic_table",
        "schema_drift",
        "no_link",
    ],
)
async def test_linked_record_output_requires_readable_table_schema(settings, defect):
    flow = flow_fixture()
    schema = definition()
    row = calls.step(
        flow,
        schema,
        {**insert()["inputs"], "conditions": ""},
        subflow=False,
        uid="lookup",
    )
    row.raw.update(id="b" * 32, order=1, parent="")
    flow["actionInstances"][0] = row.raw
    if defect == "dynamic_table":
        row.raw["inputs"][0]["value"] = "{{flow_variable.requested_action}}"
    if defect == "no_link":
        row.raw["actionType"]["outputs"][0]["internal_link"] = ""
    fixtures(settings, flow)
    routes(schema)
    if defect == "unknown_table":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": []}
        )
    elif defect == "no_descriptor":
        http_mock.get(
            f"{BASE}/api/now/processflow/table/question_choice/descriptor"
        ).respond(200, json={"result": {"data": {}}})
    elif defect == "missing_field":
        http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
            200, json={"result": []}
        )
    elif defect == "schema_drift":
        schema["outputs"][0]["internal_link"] = "missing"
        routes(schema)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"log_message": "{{lookup.Record.value}}"},
                }
            ],
        )


async def test_lookup_string_output_cannot_supply_connector_choice_even_with_in_filter(
    settings,
):
    flow = flow_fixture()
    flow["subFlowInstances"][0]["inputs"][0]["parameter"]["choices"] = [
        {"value": "ADD"},
        {"value": "REMOVE"},
    ]
    fixtures(settings, flow)
    routes()
    with pytest.raises(CapabilityError, match="declared producer choice set"):
        await plan(
            settings,
            flow,
            [
                insert(),
                {
                    "op": "update_inputs",
                    "step": "ui-4",
                    "inputs": {"action": "{{action_choice.Record.value}}"},
                },
            ],
        )


@pytest.mark.parametrize("drift", [None, "definition", "dictionary", "descriptor"])
async def test_lookup_preview_apply_binds_definition_dictionary_and_table_descriptor(
    settings, drift
):
    flow = flow_fixture()
    _, tools = fixtures(settings, flow)
    routes()

    def save(request):
        payload = json.loads(request.content)
        for row in payload["actionInstances"]:
            if not row.get("id"):
                row["id"] = "b" * 32
        persist(payload)
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                insert(),
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "{{action_choice.Record.value}}"},
                },
            ],
        )
    )
    assert preview["status"] == "success", preview
    if drift == "definition":
        schema = definition()
        schema["opaque"]["retain"] = False
        routes(schema)
    elif drift == "dictionary":
        http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
            200,
            json={
                "result": [
                    {
                        "name": "question_choice",
                        "element": "value",
                        "internal_type": "integer",
                        "column_label": "Changed",
                        "reference": "",
                        "choice": "0",
                    }
                ]
            },
        )
    elif drift == "descriptor":
        http_mock.get(
            f"{BASE}/api/now/processflow/table/question_choice/descriptor"
        ).respond(200, json={"result": {"data": {"displayField": "value"}}})
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == ("error" if drift else "success"), result
    assert write.call_count == (0 if drift else 1)


@pytest.mark.parametrize("defect", ["echo", "get", "opaque"])
async def test_lookup_round_trip_does_not_ignore_condition_or_schema_changes(
    settings, defect
):
    flow = flow_fixture()
    _, tools = fixtures(settings, flow)
    routes()

    def save(request):
        payload = json.loads(request.content)
        payload["actionInstances"][0]["id"] = "b" * 32
        actual = json.loads(json.dumps(payload))
        if defect == "echo":
            payload["actionInstances"][0]["inputs"][1]["value"] = "inactive=true"
        elif defect == "get":
            actual["actionInstances"][0]["inputs"][1]["value"] = "inactive=true"
        else:
            actual["actionInstances"][0]["actionType"]["outputs"][0]["opaque"] = {
                "retain": False
            }
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[insert()])
    )
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME" and write.call_count == 1, (
        result
    )


async def test_condition_dotwalks_and_output_order_remain_checked(settings):
    flow = flow_fixture()
    fixtures(settings, flow)
    routes()
    # A string dictionary field is not a reference that can be dot-walked.
    with pytest.raises(CapabilityError, match="not a reference"):
        await plan(settings, flow, [insert("value.active=true")])
    with pytest.raises(CapabilityError, match="before|preced|later|region"):
        await plan(
            settings,
            flow,
            [
                {**insert(), "before": "ui-3"},
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "{{action_choice.Record.value}}"},
                },
            ],
        )
