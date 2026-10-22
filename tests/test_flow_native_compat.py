"""Sanitized live-shaped scalar widgets and two-input native Catch definitions.

No target scripts, request records, person identities, or runtime effects.
"""

import copy
import json

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, handlers, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_error_authoring import definitions
from tests.test_flow_fields import plan


def task_template():
    return (
        'assignment_group={"display":"Fixture group","value":"'
        + "9" * 32
        + '","table":"sys_user_group","sys_id":"'
        + "9" * 32
        + '"}^description=<p data-pillvalue="{{fd_transform:fixture-transform}}">Status={{fd_transform:fixture-transform}}</p>^^kept=literal'
    )


def template_flow():
    flow = base_flow()
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "status", "type": "string", "children": []}
    ]
    flow["actionInstances"][1]["inputs"] = [
        {
            "name": "table",
            "value": "sc_task",
            "parameter": {"name": "table", "type": "table_name", "readonly": True},
        },
        {
            "name": "fields",
            "value": "",
            "opaque": {"preserve": True},
            "parameter": {
                "name": "fields",
                "type": "template_value",
                "reference": "sc_task",
                "dependent_on": "inputs.table",
                "maxsize": 65000,
                "children": [],
            },
        },
    ]
    flow["pharmacy"] = {
        "pharmacyCompound": {
            "fixture-transform": {
                "id": "8" * 32,
                "uiId": "fixture-transform",
                "sourceUiId": "",
                "order": 0,
                "prescription": "{{ui-1.status}}",
                "transformDefinitionId": "",
                "compositions": [],
                "scopeId": "global",
                "attachedTo": FLOW,
                "opaque": {"preserve": True},
            }
        }
    }
    return flow


def template_dictionary():
    def read(request):
        import httpx2

        column = request.url.params["sysparm_query"].split("element=")[-1]
        kind, reference = (
            ("reference", "sys_user_group")
            if column == "assignment_group"
            else ("html", "")
        )
        return httpx2.Response(
            200,
            json={
                "result": [
                    {
                        "name": "task",
                        "element": column,
                        "column_label": column,
                        "internal_type": kind,
                        "reference": reference,
                        "choice": "0",
                    }
                ]
            },
        )

    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").mock(side_effect=read)


def test_native_task_template_wire_preserves_json_html_and_escaped_carets():
    row = copy.deepcopy(template_flow()["actionInstances"][1]["inputs"][1])
    before = copy.deepcopy(row)
    data.set_value(row, task_template())
    assert row["value"] == task_template()
    assert row["parameter"] == before["parameter"] and row["opaque"] == before["opaque"]


@pytest.mark.parametrize(
    "value",
    [
        "description=x^description=y",
        "description",
        "^description=x",
        "description=javascript:bad()",
        "description=fd_scripted",
        "coalesce_fields=number",
        {"description": "x"},
    ],
)
def test_template_invalid_native_encoding_is_rejected(value):
    row = copy.deepcopy(template_flow()["actionInstances"][1]["inputs"][1])
    with pytest.raises(CapabilityError):
        data.set_value(row, value)


@pytest.mark.parametrize(
    "defect", ["missing", "other_flow", "transform", "cycle", "later_producer"]
)
async def test_encoded_template_transforms_fail_closed(settings, defect):
    flow = template_flow()
    compound = flow["pharmacy"]["pharmacyCompound"]["fixture-transform"]
    if defect == "missing":
        flow["pharmacy"]["pharmacyCompound"] = {}
    elif defect == "other_flow":
        compound["attachedTo"] = "e" * 32
    elif defect == "transform":
        compound["transformDefinitionId"] = "e" * 32
    elif defect == "cycle":
        compound["prescription"] = "{{fd_transform:fixture-transform}}"
    else:
        compound["prescription"] = "{{ui-3.status}}"
        flow["actionInstances"][2]["actionType"]["outputs"] = [
            {"name": "status", "type": "string"}
        ]
    fixtures(settings, flow)
    template_dictionary()
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"fields": task_template()},
                }
            ],
        )


@pytest.mark.parametrize("defect", [None, "template", "compound", "echo"])
async def test_template_save_preserves_opaque_fields_and_independent_readback(
    settings, defect
):
    import httpx2

    flow = template_flow()
    _, tools = fixtures(settings, flow)
    template_dictionary()

    def save(request):
        payload = json.loads(request.content)
        echo, actual = copy.deepcopy(payload), copy.deepcopy(payload)
        assert payload["actionInstances"][1]["inputs"][1]["opaque"] == {
            "preserve": True
        }
        if defect == "template":
            actual["actionInstances"][1]["inputs"][1]["value"] += " "
        elif defect == "compound":
            actual["pharmacy"]["pharmacyCompound"]["fixture-transform"]["opaque"] = {
                "changed": True
            }
        elif defect == "echo":
            echo["actionInstances"][1]["inputs"][1]["value"] = "description=lost"
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
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"fields": task_template()},
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


async def test_native_template_encoded_transform_preserves_identity_and_checks_prescription(
    settings,
):
    flow = template_flow()
    fixtures(settings, flow)
    template_dictionary()
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"fields": task_template()},
            }
        ],
    )
    assert (
        result["payload"]["actionInstances"][1]["inputs"][1]["value"] == task_template()
    )
    assert result["payload"]["pharmacy"] == flow["pharmacy"]
    assert "ui-1.status" in [row["name"] for row in result["payload"]["label_cache"]]
    assert "fd_transform:fixture-transform" not in [
        row["name"] for row in result["payload"]["label_cache"]
    ]
    flow["pharmacy"]["pharmacyCompound"]["fixture-transform"]["prescription"] = (
        "{{missing.status}}"
    )
    fixtures(settings, flow)
    template_dictionary()
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"fields": task_template()},
                }
            ],
        )


def binding(kind, **attributes):
    return {
        "name": "fixture",
        "value": "",
        "parameter": {
            "name": "fixture",
            "type": kind,
            "attributes": attributes,
            "children": [],
        },
        "children": [],
        "opaque": {"preserve": True},
    }


NONE_DUE = '{"action":"none","date_type":"actual","date":"{{}}","duration":1,"duration_type":"days","schedule":"","schedule_label":""}'


@pytest.mark.parametrize(
    "kind,value,attributes",
    [
        (
            "slushbucket",
            "a" * 32 + ":item_option_new," + "b" * 32 + ":item_option_new",
            {
                "source_table": "item_option_new",
                "include_multirow_variablesets": "true",
            },
        ),
        ("approval_rules", "ApprovesRejectsAnyU[{{flow_variable.manager}}]", {}),
        ("schedule_date_time", NONE_DUE, {"ui_approval": "true"}),
    ],
)
def test_native_widget_wire_strings_round_trip_without_metadata_loss(
    kind, value, attributes
):
    row = binding(kind, **attributes)
    before = copy.deepcopy(row["parameter"])
    # maxsize 40 belongs to the date widget, not its JSON envelope.
    if kind == "schedule_date_time":
        row["parameter"]["maxsize"] = 40
        before = copy.deepcopy(row["parameter"])
    data.set_value(row, value)
    assert row["value"] == value
    assert row["parameter"] == before and row["opaque"] == {"preserve": True}


def live_shaped_definitions():
    catalog = definitions()
    catch = next(row for row in catalog if row["type"] == handlers.CATCH)
    catch["variables"] = []
    catch["inputs"].insert(
        0,
        {
            "id": "b" * 32,
            "name": "__status__",
            "label": "Error Status",
            "type": "object",
            "defaultValue": "",
            "attributes": {"co_type_name": "FDACTIONSTATUS"},
            "children": [
                {"name": "code", "type": "integer", "readonly": True, "children": []},
                {"name": "message", "type": "string", "readonly": True, "children": []},
            ],
        },
    )
    return catalog


def test_native_catch_catalog_accepts_runtime_error_status_input():
    _, catch = handlers.definitions(live_shaped_definitions())
    assert [row["name"] for row in catch["inputs"]] == ["__status__", "enabled"]


@pytest.mark.parametrize("defect", ["extra", "missing", "duplicate", "wrong_status"])
def test_native_catch_contract_still_rejects_unproven_shapes(defect):
    catalog = live_shaped_definitions()
    catch = next(row for row in catalog if row["type"] == handlers.CATCH)
    if defect == "extra":
        catch["inputs"].append({"name": "custom", "type": "string"})
    elif defect == "missing":
        catch["inputs"].pop()
    elif defect == "duplicate":
        catch["inputs"].append(copy.deepcopy(catch["inputs"][-1]))
    else:
        catch["inputs"][0]["children"][0]["type"] = "string"
    with pytest.raises(CapabilityError):
        handlers.definitions(catalog)


def test_native_catch_toggle_preserves_opaque_status_value():
    catalog = live_shaped_definitions()
    main_def = next(row for row in catalog if row["type"] == handlers.TRY)
    catch_def = next(row for row in catalog if row["type"] == handlers.CATCH)
    main = tree.new_logic_step("a" * 32, main_def, {})
    catch = tree.new_logic_step("a" * 32, catch_def, {"enabled": True})
    catch.children = [
        tree.Step(
            {"uiUniqueIdentifier": "child", "inputs": [], "parent": catch.uid}, "action"
        )
    ]
    status = json.dumps(
        {
            "version": "1.0",
            "serializationFormat": "JSON",
            "complexObjectSchema": {
                "FlowDesigner:FDACTIONSTATUS": {"code": "Integer", "message": "String"}
            },
            "complexObject": {
                "code": {"$cv": {"$c": "java.lang.String", "$v": ""}},
                "message": {"$cv": {"$c": "java.lang.String", "$v": ""}},
            },
        }
    )
    catch.raw["inputs"][0]["value"] = status
    catch.raw["data"]["__status__"] = status
    roots = [main, catch]
    before = copy.deepcopy(catch.raw["inputs"][0])
    handlers.edit(
        {"id": "a" * 32},
        roots,
        {"op": "set_error_handler", "enabled": False},
        catalog,
        "1" * 32,
    )
    assert catch.raw["inputs"][0] == before
    assert catch.raw["data"]["__status__"] == status
    assert data.named(catch.raw["inputs"], "enabled")["value"] is False


@pytest.mark.parametrize(
    "kind,value,attributes",
    [
        ("slushbucket", "a" * 32 + ":wrong_table", {"source_table": "item_option_new"}),
        (
            "slushbucket",
            "not-an-id:item_option_new",
            {"source_table": "item_option_new"},
        ),
        ("slushbucket", "a" * 32 + "," + "a" * 32, {"source_table": "item_option_new"}),
        ("slushbucket", ["a" * 32], {"source_table": "item_option_new"}),
        (
            "approval_rules",
            "ApprovesRejectsAnyU[{{flow_variable.manager}}]G[{{flow_variable.manager}}]",
            {},
        ),
        ("approval_rules", "ApprovesRejectsAnyU[not-a-pill]", {}),
        ("approval_rules", "ApprovesRejectsAnyM", {}),
        ("approval_rules", "junk {{flow_variable.manager}}", {}),
        ("schedule_date_time", '{"action":"none"}', {"ui_approval": "true"}),
        (
            "schedule_date_time",
            NONE_DUE.replace('"none"', '"approve"'),
            {"ui_approval": "true"},
        ),
        ("schedule_date_time", NONE_DUE, {}),
        (
            "schedule_date_time",
            NONE_DUE.replace('"duration":1', '"duration":false'),
            {"ui_approval": "true"},
        ),
    ],
)
def test_native_widgets_reject_malformed_or_unimplemented_forms(
    kind, value, attributes
):
    with pytest.raises((CapabilityError, ValueError)):
        data.set_value(binding(kind, **attributes), value)


def catalog_flow():
    flow = base_flow()
    flow["actionInstances"][0]["actionType"].update(
        type="GETCATALOGVARIABLES", inputs=[], outputs=[]
    )
    selected = {
        "id": "b" * 32,
        "name": "subject",
        "type": "reference",
        "reference": "sys_user",
        "table": "item_option_new",
        "children": [],
        "mandatory": True,
        "maxsize": 0,
        "defaultValue": "",
        "defaultDisplayValue": "",
        "attributes": {"catalogType": "8"},
    }
    flow["actionInstances"][0].update(
        inputs=[
            {
                "name": "template_catalog_item",
                "value": "c" * 32,
                "parameter": {
                    "name": "template_catalog_item",
                    "type": "reference",
                    "reference": "sc_cat_item",
                },
            },
            {
                "name": "catalog_variables",
                "value": "b" * 32 + ":item_option_new",
                "parameter": {
                    "name": "catalog_variables",
                    "type": "slushbucket",
                    "children": [],
                    "attributes": {
                        "source_table": "item_option_new",
                        "include_multirow_variablesets": "true",
                    },
                },
            },
        ],
        outputs=[copy.deepcopy(selected)],
    )
    flow["actionInstances"][0]["outputs"][0]["opaque"] = {"preserve": True}
    return flow, selected


def catalog_routes(selected):
    second = {
        **copy.deepcopy(selected),
        "id": "d" * 32,
        "name": "operation",
        "type": "choice",
        "reference": "",
        "mandatory": False,
        "choices": [
            {"value": "ADD", "label": "Add"},
            {"value": "REMOVE", "label": "Remove"},
        ],
    }
    # Synthetic personal defaults must never enter persisted output metadata.
    selected = {
        **selected,
        "defaultValue": '{"display":"fixture-private","value":"' + "f" * 32 + '"}',
        "defaultDisplayValue": "fixture-private",
    }
    http_mock.get(
        f"{BASE}/api/now/processflow/flow/{'c' * 32}/service_catalog_variables"
    ).respond(200, json={"result": {"data": [selected, second], "errorCode": 0}})
    return second


async def test_catalog_selection_update_preserves_outputs_and_binds_native_pills(
    settings,
):
    flow, selected = catalog_flow()
    fixtures(settings, flow)
    catalog_routes(selected)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {
                    "catalog_variables": "b" * 32
                    + ":item_option_new,"
                    + "d" * 32
                    + ":item_option_new"
                },
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"log_message": "Operation {{ui-1.operation}}"},
            },
        ],
    )
    outputs = result["payload"]["actionInstances"][0]["outputs"]
    assert outputs[0] == flow["actionInstances"][0]["outputs"][0]
    assert (
        outputs[1]["name"] == "operation"
        and outputs[1]["choices"][1]["value"] == "REMOVE"
    )
    assert outputs[1]["defaultValue"] == "" and "fixture-private" not in json.dumps(
        result
    )
    assert result["payload"]["label_cache"][0]["type"] == "choice"
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"catalog_variables": ""},
                },
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"log_message": "Subject {{ui-1.subject}}"},
                },
            ],
        )


@pytest.mark.parametrize("type_name", ["reference", "string"])
async def test_approval_rules_check_user_reference_type_not_text_interpolation(
    settings, type_name
):
    flow = base_flow()
    flow["flowVariables"] = [
        {
            "name": "manager",
            "type": type_name,
            "reference": "sys_user" if type_name == "reference" else "",
            "children": [],
        }
    ]
    flow["actionInstances"][1]["inputs"] = [
        {
            "name": "approval_conditions",
            "parameter": {"name": "approval_conditions", "type": "approval_rules"},
            "value": "ApprovesRejectsAnyU[{{flow_variable.manager}}]",
        },
        {
            "name": "due_date",
            "parameter": {
                "type": "schedule_date_time",
                "maxsize": 40,
                "attributes": {"ui_approval": "true"},
            },
            "value": NONE_DUE,
        },
    ]
    fixtures(settings, flow)
    edits = [
        {
            "op": "update_inputs",
            "step": "ui-2",
            "inputs": {
                "approval_conditions": "ApprovesRejectsAnyU[{{flow_variable.manager}}]",
                "due_date": NONE_DUE,
            },
        }
    ]
    if type_name == "reference":
        result = await plan(settings, flow, edits)
        assert result["payload"]["actionInstances"][1]["inputs"][1]["value"] == NONE_DUE
    else:
        with pytest.raises(CapabilityError, match="reference type"):
            await plan(settings, flow, edits)


async def test_native_catch_error_status_leaf_resolves_from_input_schema(settings):
    from servicenow_mcp.capabilities.flows.editing import PillResolver

    catalog = live_shaped_definitions()
    main_def = next(row for row in catalog if row["type"] == handlers.TRY)
    catch_def = next(row for row in catalog if row["type"] == handlers.CATCH)
    main = tree.new_logic_step(FLOW, main_def, {})
    catch = tree.new_logic_step(FLOW, catch_def, {"enabled": True})
    child = tree.Step(
        {"uiUniqueIdentifier": "child", "inputs": [], "parent": catch.uid}, "action"
    )
    catch.children = [child]
    tree.renumber([main, catch])
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await PillResolver(client, None, {}, [main, catch]).resolve(
            catch.uid + ".__status__.message", child
        )
    assert result["type"] == "string"


@pytest.mark.parametrize("defect", [None, "selection", "outputs", "definition_drift"])
async def test_catalog_widget_save_round_trip_is_strict_and_single_use(
    settings, defect
):
    import httpx2

    flow, selected = catalog_flow()
    coordinator, tools = fixtures(settings, flow)
    catalog_routes(selected)

    def save(request):
        body = json.loads(request.content)
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        if defect == "selection":
            actual["actionInstances"][0]["inputs"][1]["value"] = ""
        elif defect == "outputs":
            actual["actionInstances"][0]["outputs"][0]["opaque"] = {"changed": True}
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
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {
                        "catalog_variables": "b" * 32
                        + ":item_option_new,"
                        + "d" * 32
                        + ":item_option_new"
                    },
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    if defect == "definition_drift":
        changed = {**selected, "type": "string", "reference": ""}
        catalog_routes(changed)
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect == "definition_drift":
        assert applied["status"] == "error" and not write.called
    elif defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME" and write.call_count == 1
    else:
        assert applied["status"] == "success" and write.call_count == 1, applied
    assert len(coordinator.store) == 0


async def test_catalog_action_insertion_uses_independent_selected_output_schema(
    settings,
):
    flow = base_flow()
    _, selected = catalog_flow()
    fixtures(settings, flow)
    catalog_routes(selected)
    definition = {
        "id": "e" * 32,
        "name": "Fixture variables",
        "type": "GETCATALOGVARIABLES",
        "state": "published",
        "is_snapshot": True,
        "entitled": True,
        "security": {"can_read": True},
        "outputs": [],
        "inputs": [
            {
                "name": "requested_item",
                "type": "reference",
                "reference": "sc_req_item",
                "children": [],
            },
            {
                "name": "template_catalog_item",
                "type": "reference",
                "reference": "sc_cat_item",
                "children": [],
            },
            {
                "name": "catalog_variables",
                "type": "slushbucket",
                "attributes": {
                    "source_table": "item_option_new",
                    "include_multirow_variablesets": "true",
                },
                "children": [],
            },
        ],
    }
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'e' * 32}").respond(
        200, json={"result": definition}
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": "e" * 32,
                "before": "ui-1",
                "ref": "variables",
                "inputs": {
                    "requested_item": "f" * 32,
                    "template_catalog_item": "c" * 32,
                    "catalog_variables": "b" * 32
                    + ":item_option_new,"
                    + "d" * 32
                    + ":item_option_new",
                },
            },
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "Operation {{variables.operation}}"},
            },
        ],
    )
    row = result["payload"]["actionInstances"][0]
    assert [p["name"] for p in row["outputs"]] == ["subject", "operation"]
    assert "fixture-private" not in json.dumps(result)
    assert row["actionType"] == definition


def test_reference_picker_maximum_does_not_limit_native_pill_expression_length():
    row = {
        "name": "requested_item",
        "parameter": {
            "name": "requested_item",
            "type": "reference",
            "reference": "sc_req_item",
            "maxsize": 32,
        },
        "children": [],
    }
    value = "{{Service Catalog_1.request_item}}"
    assert len(value) > 32
    data.set_value(row, value)
    assert row["value"] == value
    with pytest.raises(CapabilityError):
        data.set_value(row, "a" * 33)


@pytest.mark.parametrize("shape", ["no_status", "opaque_status"])
def test_native_catch_creation_and_toggling_never_replace_runtime_schema(shape):
    catalog = live_shaped_definitions()
    roots = []
    handlers.edit(
        {"id": FLOW},
        roots,
        {"op": "set_error_handler", "enabled": True},
        catalog,
        "1" * 32,
    )
    catch = roots[1]
    assert [row["name"] for row in catch.raw["inputs"]] == ["__status__", "enabled"]
    if shape == "opaque_status":
        status = '{"complexObjectSchema":{"opaque":"fixture"},"complexObject":{},"serializationFormat":"JSON"}'
        catch.raw["inputs"][0]["value"] = status
        catch.raw["data"]["__status__"] = status
    before = copy.deepcopy(catch.raw["inputs"][0])
    catch.children = [
        tree.Step({"uiUniqueIdentifier": "child", "inputs": []}, "action")
    ]
    handlers.edit(
        {"id": FLOW},
        roots,
        {"op": "set_error_handler", "enabled": False},
        catalog,
        "1" * 32,
    )
    handlers.edit(
        {"id": FLOW},
        roots,
        {"op": "set_error_handler", "enabled": True},
        catalog,
        "1" * 32,
    )
    assert catch.raw["inputs"][0] == before


@pytest.mark.parametrize("failure", ["wrong_type", "missing", "wrong_table", "async"])
async def test_catalog_metadata_cannot_be_guessed_or_accepted_incompletely(
    settings, failure
):
    flow, selected = catalog_flow()
    fixtures(settings, flow)
    if failure == "wrong_type":
        selected["type"] = "string"
    elif failure == "wrong_table":
        selected["table"] = "sc_multi_row_question_answer"
    response = {"result": {"data": [] if failure == "missing" else [selected]}}
    http_mock.get(
        f"{BASE}/api/now/processflow/flow/{'c' * 32}/service_catalog_variables"
    ).respond(202 if failure == "async" else 200, json=response)
    from servicenow_mcp.errors import ServiceNowMCPError

    with pytest.raises(ServiceNowMCPError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"log_message": "Subject {{ui-1.subject}}"},
                }
            ],
        )


async def test_native_widgets_save_echo_and_independent_get_remain_strict(settings):
    import httpx2

    flow = base_flow()
    flow["flowVariables"] = [
        {
            "name": "manager",
            "type": "reference",
            "reference": "sys_user",
            "children": [],
        }
    ]
    flow["actionInstances"][1]["inputs"] = [
        {
            "name": "approval_conditions",
            "value": "ApprovesRejectsAnyU[{{flow_variable.manager}}]",
            "parameter": {
                "name": "approval_conditions",
                "type": "approval_rules",
                "mandatory": True,
                "maxsize": 8000,
            },
        },
        {
            "name": "due_date",
            "value": NONE_DUE,
            "parameter": {
                "name": "due_date",
                "type": "schedule_date_time",
                "maxsize": 40,
                "attributes": {"ui_approval": "true"},
            },
        },
    ]
    _, tools = fixtures(settings, flow)

    def save(request):
        body = json.loads(request.content)
        assert body["actionInstances"][1]["inputs"][1]["value"] == NONE_DUE
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
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {
                        "approval_conditions": "ApprovesRejectsAllU[{{flow_variable.manager}}]",
                        "due_date": NONE_DUE,
                    },
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success" and write.call_count == 1, result


@pytest.mark.parametrize("defect", [None, "status_schema", "assigned_id"])
async def test_two_input_handler_new_and_saved_status_round_trip(settings, defect):
    import httpx2

    flow = base_flow()
    _, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": live_shaped_definitions()}
    )

    def save(request):
        body = json.loads(request.content)
        for index, row in enumerate(body["flowLogicInstances"], 20):
            row["id"] = f"{index:032x}"
        echo, actual = copy.deepcopy(body), copy.deepcopy(body)
        catch = next(
            row
            for row in actual["flowLogicInstances"]
            if row["flowLogicDefinition"]["type"] == handlers.CATCH
        )
        if defect == "status_schema":
            catch["inputs"][0]["parameter"]["children"][0]["type"] = "string"
        elif defect == "assigned_id":
            catch["id"] = "e" * 32
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
                {"op": "set_error_handler", "enabled": True},
                {"op": "move_step", "step": "ui-3", "parent": "error_handler"},
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {
                        "log_message": "Error {{error_handler.__status__.message}}"
                    },
                },
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


async def test_approval_insertion_resolves_readonly_table_from_trigger_pill(settings):
    from tests.test_flow_lifecycle import validation_flow

    flow = validation_flow()
    fixtures(settings, flow)
    definition = {
        "id": "e" * 32,
        "name": "Fixture approval",
        "state": "published",
        "is_snapshot": True,
        "entitled": True,
        "security": {"can_read": True},
        "outputs": [],
        "inputs": [
            {
                "name": "record",
                "type": "document_id",
                "dependent_on": "table",
                "children": [],
            },
            {
                "name": "table",
                "type": "table_name",
                "readonly": True,
                "defaultValue": "",
                "children": [],
            },
            {
                "name": "approval_field",
                "type": "field_name",
                "dependent_on": "table",
                "children": [],
            },
            {"name": "rules", "type": "approval_rules", "children": []},
            {
                "name": "due_date",
                "type": "schedule_date_time",
                "maxsize": 40,
                "attributes": {"ui_approval": "true"},
                "children": [],
            },
        ],
    }
    flow["flowVariables"].append(
        {
            "name": "manager",
            "type": "reference",
            "reference": "sys_user",
            "children": [],
        }
    )
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'e' * 32}").respond(
        200, json={"result": definition}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
        200,
        json={
            "result": [
                {
                    "name": "sc_req_item",
                    "element": "approval",
                    "internal_type": "string",
                    "reference": "",
                }
            ]
        },
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": "e" * 32,
                "inputs": {
                    "record": "{{Service Catalog_1.request_item}}",
                    "approval_field": "approval",
                    "rules": "ApprovesRejectsAnyU[{{flow_variable.manager}}]",
                    "due_date": NONE_DUE,
                },
            }
        ],
    )
    row = result["payload"]["actionInstances"][-1]
    assert data.named(row["inputs"], "table")["value"] == "sc_req_item"
    assert data.named(row["inputs"], "record")["data_format"] == "sc_req_item"
    assert data.named(row["inputs"], "approval_field")["table"] == "sc_req_item"
    assert row["actionType"] == definition


async def test_active_published_target_still_rejected_before_any_write(settings):
    flow = base_flow()
    flow.update(
        active=True, status="published", isPublished=True, masterSnapshotId="f" * 32
    )
    coordinator, tools = fixtures(settings, flow)
    response = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_error_handler", "enabled": False}],
        )
    )
    assert response["status"] == "error" and len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
