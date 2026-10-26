"""Installed Application definitions, with deterministic read-only metadata mocks.

The catalog fixture is copied from the installed authoring catalog. It is not a
captured save and does not claim that an inactive draft has been activated.
"""

import copy
import json
from pathlib import Path

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    application_triggers,
    data,
    editing,
    semantics,
)
from servicenow_mcp.errors import PolicyError
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_trigger_readback import blob, read_flow

CATALOG = json.loads(
    (Path(__file__).parent / "fixtures/flow_application_triggers.json").read_text()
)
KINDS = (
    "email",
    "remote_table_query",
    "rest_async",
    "analytics",
    "metric",
    "sla_task",
)
METRIC = "c" * 32
ANALYTICS = "d" * 32
MODERATOR = "e" * 32
REMOTE = "f" * 32
REMOTE_ALTERNATE = "6" * 32
ROLE = "9" * 32
SAVED = "8" * 32


def categories():
    result = copy.deepcopy(DEFS["trigger_categories"])
    result.extend(copy.deepcopy(CATALOG["trigger_categories"]))
    return result


def definition(kind):
    result = next(
        row
        for category in copy.deepcopy(CATALOG["trigger_categories"])
        for row in category["triggers"].values()
        if row["type"] == kind
    )
    result["triggerType"] = "Application"
    return result


def inputs(kind):
    return copy.deepcopy(
        {
            "email": {
                "email_conditions": "type=received",
                "target_table": "incident",
                "order": 10,
                "stop_condition_evaluation": False,
            },
            "remote_table_query": {"u_table": "u_remote_assets"},
            "rest_async": {"http_method": "post", "display_path": "/requests"},
            "analytics": {
                "proactive_analytics_trigger": ANALYTICS,
                "condition": "active=true",
            },
            "metric": {
                "metric_trigger": METRIC,
                "moderator": MODERATOR,
                "condition": "active=true",
            },
            "sla_task": {},
        }[kind]
    )


def set_trigger(kind, **extra):
    return {"op": "set_trigger", "type": kind, "inputs": inputs(kind), **extra}


def input_row(flow, name):
    return data.named(flow["triggerInstances"][0]["inputs"], name)


def output_row(flow, name):
    return data.named(flow["triggerInstances"][0]["outputs"], name)


async def prepare_evidence(settings, flow):
    owned = copy.deepcopy(flow)
    trigger = owned["triggerInstances"][0]
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        resolver = editing.PillResolver(client, trigger, owned)
        return await application_triggers.prepare(
            trigger, definition(trigger["type"]), client, resolver=resolver, flow=owned
        )


def routes(settings, flow, *, catalog=None):
    coordinator, tools = fixtures(settings, flow)
    current_catalog = copy.deepcopy(catalog if catalog is not None else categories())
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(current_catalog)}
        )
    )
    http_mock.post(f"{BASE}/api/now/graphql").respond(
        200,
        json={
            "data": {
                "now": {
                    "restTrigger": {
                        "generateBasePath": {
                            "__typename": "now_restTrigger_BasePathResponse",
                            "path": "/api/fixture/application",
                        }
                    }
                }
            }
        },
    )
    tables = {
        name: {"name": name, "label": label, "sys_id": f"{index:032x}"}
        for index, (name, label) in enumerate(
            [
                ("incident", "Incident"),
                ("task", "Task"),
                ("sys_email", "Email"),
                ("u_remote_assets", "Remote Assets"),
                ("u_remote_devices", "Remote Devices"),
                ("task_sla", "Task SLA"),
            ],
            start=100,
        )
    }
    tables["incident"]["super_class.name"] = "task"

    def table_rows(request):
        query = request.url.params.get("sysparm_query", "")
        result = [row for name, row in tables.items() if query == f"name={name}"]
        return httpx2.Response(200, json={"result": copy.deepcopy(result)})

    http_mock.get(f"{BASE}/api/now/table/sys_db_object").mock(side_effect=table_rows)
    for name in tables:
        http_mock.get(f"{BASE}/api/now/processflow/table/{name}/descriptor").respond(
            200,
            json={
                "result": {
                    "data": {
                        "displayField": "subject" if name == "sys_email" else "number"
                    }
                }
            },
        )

    dictionary_fields = {
        ("sys_email", "type"): ("string", "Type"),
        ("sys_email", "subject"): ("string", "Subject"),
        ("task", "number"): ("string", "Number"),
        ("task", "active"): ("boolean", "Active"),
    }

    def dictionary(request):
        query = request.url.params.get("sysparm_query", "")
        terms = query.split("^")
        result = []
        if (
            len(terms) != 2
            or not terms[0].startswith("nameIN")
            or not terms[1].startswith("element=")
        ):
            return httpx2.Response(200, json={"result": result})
        column = terms[1].removeprefix("element=")
        for table in terms[0].removeprefix("nameIN").split(","):
            if (table, column) not in dictionary_fields:
                continue
            kind, label = dictionary_fields[(table, column)]
            result.append(
                {
                    "name": table,
                    "element": column,
                    "column_label": label,
                    "internal_type": kind,
                    "reference": "",
                    "choice": "0",
                }
            )
        return httpx2.Response(200, json={"result": result})

    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").mock(side_effect=dictionary)
    records = {
        ("sys_metric_trigger_definition", METRIC): {
            "sys_id": METRIC,
            "name": "Fixture metric",
            "sys_mod_count": "4",
            "metric_table": "1" * 32,
            "metric_table.name": "incident",
        },
        ("sys_analytics_trigger_definition", ANALYTICS): {
            "sys_id": ANALYTICS,
            "name": "Fixture analytics",
            "sys_mod_count": "7",
            "event_table": "1" * 32,
            "event_table.name": "incident",
            "encoded_query": "active=false",
        },
        ("sys_metric_trigger_moderator", MODERATOR): {
            "sys_id": MODERATOR,
            "name": "Fixture moderator",
            "sys_mod_count": "2",
        },
        ("sys_script_vtable", REMOTE): {
            "sys_id": REMOTE,
            "name": "u_remote_assets",
            "sys_mod_count": "3",
        },
        ("sys_script_vtable", REMOTE_ALTERNATE): {
            "sys_id": REMOTE_ALTERNATE,
            "name": "u_remote_devices",
            "sys_mod_count": "2",
        },
        ("sys_user_role", ROLE): {
            "sys_id": ROLE,
            "name": "fixture_rest_role",
            "sys_mod_count": "1",
        },
    }
    for table, identity in records:
        http_mock.get(f"{BASE}/api/now/table/{table}/{identity}").mock(
            side_effect=lambda _, key=(table, identity): httpx2.Response(
                200, json={"result": copy.deepcopy(records[key])}
            )
        )

    def reference_rows(request):
        table = request.url.path.rsplit("/", 1)[-1]
        query = request.url.params.get("sysparm_query", "")
        identity = next(
            (
                term.removeprefix("sys_id=")
                for term in query.split("^")
                if term.startswith("sys_id=")
            ),
            None,
        )
        record = records.get((table, identity))
        return httpx2.Response(
            200, json={"result": [copy.deepcopy(record)] if record else []}
        )

    for table in {table for table, _ in records} - {"sys_script_vtable"}:
        http_mock.get(f"{BASE}/api/now/table/{table}").mock(side_effect=reference_rows)

    def remote_table_rows(request):
        query = request.url.params.get("sysparm_query", "")
        matches = [
            copy.deepcopy(record)
            for (table, _), record in records.items()
            if table == "sys_script_vtable" and query == "name=" + record["name"]
        ]
        return httpx2.Response(200, json={"result": matches})

    http_mock.get(f"{BASE}/api/now/table/sys_script_vtable").mock(
        side_effect=remote_table_rows
    )
    return coordinator, tools, current_catalog, records, tables


def assert_readonly():
    assert all(
        call.request.method == "GET"
        or call.request.method == "POST"
        and call.request.url.path == "/api/now/graphql"
        for call in http_mock.calls
    )


@pytest.mark.parametrize("kind", KINDS)
async def test_native_application_create_is_catalog_owned_inactive_draft(
    settings, kind
):
    flow = base_flow()
    routes(settings, flow)
    planned = await plan(settings, flow, [set_trigger(kind)])
    built = planned["payload"]
    trigger = built["triggerInstances"][0]
    expected = definition(kind)
    assert trigger["type"] == kind
    assert trigger["triggerDefinitionId"] == expected["id"]
    assert {row["name"] for row in trigger["inputs"]} == {
        row["name"] for row in expected["inputs"]
    }
    assert [row["name"] for row in trigger["outputs"]] == [
        row["name"] for row in expected["outputs"]
    ]
    for name, value in inputs(kind).items():
        assert input_row(built, name)["value"] == value
    assert built["active"] is False
    assert built["isPublished"] is False
    assert built["status"] == "draft"
    if kind == "rest_async":
        for name in ("path_parameters", "query_parameters", "headers", "body"):
            assert data.value_type(data.named(built["inputs"], name)).kind == "object"
    else:
        assert built["inputs"] == []
    assert_readonly()


async def test_email_resolves_reply_table_and_conditions_against_email_dictionary(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    planned = await plan(settings, flow, [set_trigger("email")])
    built = planned["payload"]
    target = input_row(built, "target_table")
    condition = input_row(built, "email_conditions")
    assert target["reference"] == "incident"
    assert target["reference_display"] == target["displayValue"] == "Incident"
    assert target["displayField"] == "number"
    assert target["valueSysId"]
    assert (
        condition["reference"]
        == condition["table"]
        == condition["data_format"]
        == "sys_email"
    )
    evidence = await prepare_evidence(settings, built)
    assert evidence["tables"]["incident"]["record"]["name"] == "incident"
    assert evidence["dictionary"]["sys_email.type"]["element"] == "type"
    assert evidence["dictionary"]["sys_email.type"]["name"] == "sys_email"
    assert (
        output_row(built, "target_record")["parameter"]["internal_link"]
        == "target_table"
    )
    assert_readonly()


@pytest.mark.parametrize("defect", ["table", "descriptor", "condition_column"])
async def test_email_unreadable_metadata_does_not_stage(settings, defect):
    coordinator, tools, _, _, _ = routes(settings, base_flow())
    if defect == "table":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": []}
        )
    elif defect == "descriptor":
        http_mock.get(f"{BASE}/api/now/processflow/table/incident/descriptor").respond(
            200, json={"result": {"data": {}}}
        )
    else:
        http_mock.get(f"{BASE}/api/now/table/sys_dictionary").respond(
            200, json={"result": []}
        )
    result = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[set_trigger("email")]
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize(
    "kind,input_name,column,table",
    [
        ("email", "email_conditions", "active", "sys_email"),
        ("metric", "condition", "subject", "incident"),
        ("analytics", "condition", "type", "incident"),
    ],
)
async def test_application_conditions_do_not_borrow_other_table_fields(
    settings, kind, input_name, column, table
):
    coordinator, tools, _, _, _ = routes(settings, base_flow())
    edit = set_trigger(kind)
    edit["inputs"][input_name] = f"{column}=value"
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert f"Field '{column}' was not found on {table}" in result["error"]["message"]
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_remote_table_uses_declared_remote_table_reference(settings):
    flow = base_flow()
    routes(settings, flow)
    planned = await plan(settings, flow, [set_trigger("remote_table_query")])
    remote = input_row(planned["payload"], "u_table")
    assert remote["parameter"]["reference"] == "sys_script_vtable"
    assert remote["value"] == "u_remote_assets"
    assert remote["reference"] == "u_remote_assets"
    assert remote["displayField"] == "number"
    assert (
        output_row(planned["payload"], "table_name")["parameter"]["internal_link"]
        == "u_table"
    )
    assert_readonly()


async def test_remote_table_reference_policy_is_not_bypassed(settings, monkeypatch):
    from servicenow_mcp import policy

    flow = base_flow()
    routes(settings, flow)
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_script_vtable"}
    )
    with pytest.raises(PolicyError, match="sys_script_vtable"):
        await plan(settings, flow, [set_trigger("remote_table_query")])
    assert_readonly()


@pytest.mark.parametrize(
    "kind,provider_name,provider_table,identity,table_field",
    [
        (
            "metric",
            "metric_trigger",
            "sys_metric_trigger_definition",
            METRIC,
            "metric_table.name",
        ),
        (
            "analytics",
            "proactive_analytics_trigger",
            "sys_analytics_trigger_definition",
            ANALYTICS,
            "event_table.name",
        ),
    ],
)
async def test_provider_table_is_derived_and_provider_revision_is_bound(
    settings, kind, provider_name, provider_table, identity, table_field
):
    flow = base_flow()
    _, _, _, records, _ = routes(settings, flow)
    planned = await plan(settings, flow, [set_trigger(kind)])
    built = planned["payload"]
    assert input_row(built, "table_name")["value"] == "incident"
    assert input_row(built, "table_name")["reference"] == "incident"
    assert input_row(built, "condition")["table"] == "incident"
    assert input_row(built, "condition")["value"] == "active=true"
    assert output_row(built, "current")["parameter"]["internal_link"] == "table_name"
    evidence = await prepare_evidence(settings, built)
    assert evidence["providers"][provider_name]["sys_id"] == identity
    assert (
        evidence["providers"][provider_name]["sys_mod_count"]
        == records[(provider_table, identity)]["sys_mod_count"]
    )
    assert evidence["providers"][provider_name][table_field] == "incident"
    assert evidence["tables"]["incident"]["descriptor"]["displayField"] == "number"
    assert evidence["dictionary"]["incident.active"]["name"] == "task"
    assert_readonly()


async def test_metric_requires_provider_and_rejects_authored_derived_table(settings):
    flow = base_flow()
    routes(settings, flow)
    for values in ({}, {"metric_trigger": METRIC, "table_name": "task"}):
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                flow,
                [{"op": "set_trigger", "type": "metric", "inputs": values}],
            )
    assert_readonly()


@pytest.mark.parametrize("kind", ["metric", "analytics"])
async def test_derived_table_cannot_be_authored_during_update(settings, kind):
    flow = base_flow()
    routes(settings, flow)
    built = (await plan(settings, flow, [set_trigger(kind)]))["payload"]
    routes(settings, built)
    if kind == "analytics":
        assert input_row(built, "table_name")["parameter"]["readonly"] is False
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            built,
            [{"op": "update_trigger", "inputs": {"table_name": "task"}}],
        )
    assert_readonly()


async def test_analytics_uses_provider_condition_only_without_authored_condition(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    inherited = await plan(
        settings,
        flow,
        [
            {
                "op": "set_trigger",
                "type": "analytics",
                "inputs": {"proactive_analytics_trigger": ANALYTICS},
            }
        ],
    )
    assert input_row(inherited["payload"], "condition")["value"] == "active=false"
    authored = await plan(settings, flow, [set_trigger("analytics")])
    assert input_row(authored["payload"], "condition")["value"] == "active=true"
    routes(settings, authored["payload"])
    edited = await plan(
        settings,
        authored["payload"],
        [{"op": "update_trigger", "inputs": {"condition": "numberSTARTSWITHINC"}}],
    )
    assert input_row(edited["payload"], "condition")["value"] == "numberSTARTSWITHINC"
    assert_readonly()


@pytest.mark.parametrize(
    "kind,provider_table,identity,field",
    [
        ("metric", "sys_metric_trigger_definition", METRIC, "metric_table.name"),
        (
            "analytics",
            "sys_analytics_trigger_definition",
            ANALYTICS,
            "event_table.name",
        ),
    ],
)
@pytest.mark.parametrize(
    "defect", ["missing_table", "unsafe_table", "provider_missing"]
)
async def test_incomplete_provider_metadata_never_stages(
    settings, kind, provider_table, identity, field, defect
):
    coordinator, tools, _, records, _ = routes(settings, base_flow())
    record = records[(provider_table, identity)]
    if defect == "missing_table":
        record.pop(field)
    elif defect == "unsafe_table":
        record[field] = "incident^ORactive=true"
    else:
        http_mock.get(f"{BASE}/api/now/table/{provider_table}/{identity}").respond(
            200, json={"result": {}}
        )
    result = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[set_trigger(kind)]
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_sla_retains_native_object_outputs_without_inventing_interfaces(settings):
    flow = base_flow()
    routes(settings, flow)
    planned = await plan(settings, flow, [set_trigger("sla_task")])
    built = planned["payload"]
    assert built["triggerInstances"][0]["inputs"] == []
    assert built["inputs"] == built["outputs"] == []
    assert output_row(built, "task_sla_record")["parameter"]["reference"] == "task_sla"
    assert (
        output_row(built, "sla_flow_inputs")["parameter"]["children"]
        == data.named(definition("sla_task")["outputs"], "sla_flow_inputs")[
            "parameter"
        ]["children"]
    )
    routes(settings, built)
    with pytest.raises(CapabilityError):
        await plan(
            settings, built, [{"op": "update_trigger", "inputs": {"table": "task_sla"}}]
        )
    removed = await plan(settings, built, [{"op": "remove_trigger"}])
    assert removed["payload"]["triggerInstances"] == []
    assert_readonly()


@pytest.mark.parametrize("kind", KINDS)
async def test_catalog_change_invalidates_preview_without_write(settings, kind):
    _, tools, catalog, _, _ = routes(settings, base_flow())
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[set_trigger(kind)]
        )
    )
    assert preview["status"] == "success", preview
    current = next(
        row
        for category in catalog
        for row in category["triggers"].values()
        if row["type"] == kind
    )
    current["description"] = "Changed native catalog revision"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert_readonly()


@pytest.mark.parametrize(
    "kind,table,identity",
    [
        ("metric", "sys_metric_trigger_definition", METRIC),
        ("analytics", "sys_analytics_trigger_definition", ANALYTICS),
    ],
)
async def test_provider_revision_change_invalidates_preview_without_write(
    settings, kind, table, identity
):
    _, tools, _, records, _ = routes(settings, base_flow())
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[set_trigger(kind)]
        )
    )
    assert preview["status"] == "success", preview
    records[(table, identity)]["sys_mod_count"] = "99"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert_readonly()


@pytest.mark.parametrize(
    "kind,changes",
    [
        ("email", {"order": 20, "stop_condition_evaluation": True}),
        ("remote_table_query", {"u_table": "u_remote_devices"}),
        ("rest_async", {"http_method": "patch"}),
        ("analytics", {"condition": "active=false"}),
        ("metric", {"condition": "active=false"}),
    ],
)
async def test_update_preserves_identity_and_unrelated_opaque_metadata(
    settings, kind, changes
):
    flow = base_flow()
    routes(settings, flow)
    built = (await plan(settings, flow, [set_trigger(kind)]))["payload"]
    trigger = built["triggerInstances"][0]
    trigger.update(id=SAVED, vendor={"keep": [False, 0, {"native": "opaque"}]})
    trigger["inputs"][0]["vendor"] = {"binding": "unchanged"}
    built["vendor"] = {"root": ["unchanged"]}
    before = copy.deepcopy(built)
    routes(settings, built)
    planned = await plan(settings, built, [{"op": "update_trigger", "inputs": changes}])
    updated = planned["payload"]["triggerInstances"][0]
    assert updated["id"] == SAVED
    assert updated["vendor"] == before["triggerInstances"][0]["vendor"]
    assert updated["inputs"][0]["vendor"] == {"binding": "unchanged"}
    assert planned["payload"]["vendor"] == before["vendor"]
    assert updated["outputs"] == before["triggerInstances"][0]["outputs"]
    for name, value in changes.items():
        assert input_row(planned["payload"], name)["value"] == value
    assert_readonly()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("defect", ["output_schema", "opaque_metadata", "identity"])
async def test_semantic_readback_rejects_native_application_drift(
    settings, kind, defect
):
    flow = base_flow()
    routes(settings, flow)
    expected = (await plan(settings, flow, [set_trigger(kind)]))["payload"]
    expected["triggerInstances"][0].update(id=SAVED, vendor={"native": [1, False]})
    actual = copy.deepcopy(expected)
    trigger = actual["triggerInstances"][0]
    if defect == "output_schema":
        trigger["outputs"][0]["parameter"]["type"] = "integer"
    elif defect == "opaque_metadata":
        trigger["vendor"]["native"] = [2, False]
    else:
        trigger["id"] = "7" * 32
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(expected, actual, generated_ids=set())
    assert_readonly()


@pytest.mark.parametrize("kind", KINDS)
async def test_inspection_exposes_authored_native_inputs_and_object_output_schema(
    settings, kind
):
    flow = base_flow()
    routes(settings, flow)
    built = (await plan(settings, flow, [set_trigger(kind)]))["payload"]
    trigger = built["triggerInstances"][0]
    row = {
        "sys_id": SAVED,
        "flow": FLOW,
        "trigger_type": {"value": kind, "display_value": trigger["name"]},
        "trigger_definition": trigger["triggerDefinitionId"],
        "name": trigger["name"],
        "remote_sys_id": "",
        "trigger_inputs": blob(copy.deepcopy(trigger["inputs"])),
        "trigger_outputs": blob(copy.deepcopy(trigger["outputs"])),
    }
    inspected = await read_flow(
        settings,
        "inspect",
        [row],
        header={"sys_id": FLOW, "type": "flow", "active": "false"},
    )
    observed = inspected["data"]["triggers"][0]
    assert observed["type"] == kind
    assert observed["values_decoded"] == trigger["inputs"]
    assert observed["outputs_decoded"] == trigger["outputs"]
    assert observed["activation"]["flow_active"] is False
    assert_readonly()


CONSUMERS = {
    "email": "inbound_email.subject",
    "remote_table_query": "query_id",
    "rest_async": "body.message",
    "analytics": "current.number",
    "metric": "current.number",
    "sla_task": "sla_flow_inputs.name",
}


def consumer_pill(kind):
    return definition(kind)["label"] + "_1." + CONSUMERS[kind]


@pytest.mark.parametrize("kind", KINDS)
async def test_native_application_outputs_are_typed_consumers_in_same_plan(
    settings, kind
):
    flow = base_flow()
    routes(settings, flow)
    edit = set_trigger(kind)
    if kind == "rest_async":
        edit["interface_schemas"] = {
            "body": {
                "type": "object",
                "children": [{"name": "message", "label": "Message", "type": "string"}],
            }
        }
    pill = consumer_pill(kind)
    planned = await plan(
        settings,
        flow,
        [
            edit,
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "Native {{" + pill + "}}"},
            },
        ],
    )
    assert (
        planned["payload"]["actionInstances"][0]["inputs"][0]["value"]
        == "Native {{" + pill + "}}"
    )
    assert_readonly()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("operation", ["remove_trigger", "replace"])
async def test_replacement_and_removal_reject_surviving_consumers(
    settings, kind, operation
):
    flow = base_flow()
    routes(settings, flow)
    edit = set_trigger(kind)
    if kind == "rest_async":
        edit["interface_schemas"] = {
            "body": {
                "type": "object",
                "children": [{"name": "message", "label": "Message", "type": "string"}],
            }
        }
    built = (
        await plan(
            settings,
            flow,
            [
                edit,
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "{{" + consumer_pill(kind) + "}}"},
                },
            ],
        )
    )["payload"]
    routes(settings, built)
    requested = (
        {"op": "remove_trigger"}
        if operation == "remove_trigger"
        else set_trigger(kind, replace=True)
    )
    with pytest.raises(CapabilityError):
        await plan(settings, built, [requested])
    repaired = await plan(
        settings,
        built,
        [
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "No trigger source"},
            },
            requested,
        ],
    )
    assert bool(repaired["payload"]["triggerInstances"]) is (operation == "replace")
    assert_readonly()


def rest_schemas():
    return {
        "path_parameters": {
            "type": "object",
            "children": [
                {
                    "name": "request_id",
                    "label": "Request ID",
                    "type": "integer",
                    "mandatory": True,
                }
            ],
        },
        "query_parameters": {
            "type": "object",
            "children": [
                {"name": "limit", "label": "Limit", "type": "integer"},
                {"name": "ratio", "label": "Ratio", "type": "decimal"},
            ],
        },
        "headers": {
            "type": "object",
            "children": [
                {
                    "name": "request_token",
                    "label": "Request token",
                    "type": "string",
                    "maxsize": 120,
                }
            ],
        },
        "body": {
            "type": "object",
            "children": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "mandatory": True,
                },
                {
                    "name": "details",
                    "label": "Details",
                    "type": "object",
                    "children": [
                        {"name": "amount", "label": "Amount", "type": "float"},
                        {"name": "tags", "label": "Tags", "type": "array.string"},
                    ],
                },
            ],
        },
    }


def schema_child(root, name):
    return data.child(root, name)


async def test_rest_interfaces_project_to_native_outputs_and_owned_flow_inputs(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    values = inputs("rest_async")
    values.update(
        display_path="/requests/{{REST API - Asynchronous_1.path_parameters.request_id}}",
        requires_authentication=True,
        allow_external_access=False,
        roles=ROLE,
    )
    pill = "REST API - Asynchronous_1.body.message"
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "set_trigger",
                "type": "rest_async",
                "inputs": values,
                "interface_schemas": rest_schemas(),
            },
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "{{" + pill + "}}"},
            },
        ],
    )
    built = planned["payload"]
    trigger = built["triggerInstances"][0]
    assert input_row(built, "base_path")["value"] == "/api/fixture/application"
    assert input_row(built, "path")["value"] == "/requests/{request_id}"
    assert input_row(built, "display_path")["value"] == values["display_path"]
    assert input_row(built, "media_type")["value"] == "application/json"
    assert input_row(built, "requires_authentication")["value"] is True
    assert input_row(built, "allow_external_access")["value"] is False
    assert input_row(built, "roles")["value"] == ROLE
    for name in rest_schemas():
        assert data.spec(input_row(built, name))["children"] == []
    for roots in (trigger["outputs"], built["inputs"]):
        path = data.named(roots, "path_parameters")
        request_id = schema_child(path, "request_id")
        assert data.value_type(request_id).kind == "integer"
        assert data.spec(request_id)["mandatory"] is True
        query = data.named(roots, "query_parameters")
        assert data.value_type(schema_child(query, "limit")).kind == "integer"
        assert data.value_type(schema_child(query, "ratio")).kind == "decimal"
        token = schema_child(data.named(roots, "headers"), "request_token")
        assert data.value_type(token).kind == "string"
        assert data.spec(token)["maxsize"] == 120
        body = data.named(roots, "body")
        message = schema_child(body, "message")
        assert data.value_type(message).kind == "string"
        assert data.spec(message)["mandatory"] is True
        details = schema_child(body, "details")
        assert data.value_type(schema_child(details, "amount")).kind == "float"
        assert data.value_type(schema_child(details, "tags")).kind == "array.string"
    assert data.named(built["label_cache"], pill)["type"] == "string"
    assert built["actionInstances"][0]["inputs"][0]["value"] == "{{" + pill + "}}"
    evidence = await prepare_evidence(settings, built)
    assert evidence["references"]["sys_user_role." + ROLE]["sys_id"] == ROLE
    assert_readonly()


@pytest.mark.parametrize(
    "kind", ["object", "array.object", "array.string", "array.integer", "array.boolean"]
)
async def test_rest_body_uses_only_native_catalog_root_types(settings, kind):
    flow = base_flow()
    routes(settings, flow)
    children = (
        [{"name": "message", "label": "Message", "type": "string"}]
        if kind in {"object", "array.object"}
        else []
    )
    planned = await plan(
        settings,
        flow,
        [
            set_trigger(
                "rest_async",
                interface_schemas={"body": {"type": kind, "children": children}},
            )
        ],
    )
    root = output_row(planned["payload"], "body")
    assert data.value_type(root).kind == kind
    if kind.startswith("array."):
        item = data.children(root)[0]
        assert data.value_type(item).kind == kind.removeprefix("array.")
        if kind == "array.object":
            assert data.value_type(schema_child(item, "message")).kind == "string"
    else:
        assert data.value_type(schema_child(root, "message")).kind == "string"
    assert_readonly()


async def test_rest_partial_schema_update_replaces_only_named_root_and_preserves_identity(
    settings,
):
    flow = base_flow()
    routes(settings, flow)
    built = (
        await plan(
            settings,
            flow,
            [set_trigger("rest_async", interface_schemas=rest_schemas())],
        )
    )["payload"]
    built["triggerInstances"][0]["id"] = SAVED
    before = copy.deepcopy(built)
    routes(settings, built)
    schema = {
        "type": "object",
        "children": [
            {"name": "replacement", "label": "Replacement", "type": "integer"}
        ],
    }
    planned = await plan(
        settings,
        built,
        [{"op": "update_trigger", "interface_schemas": {"body": schema}}],
    )
    updated = planned["payload"]
    assert updated["triggerInstances"][0]["id"] == SAVED
    assert [
        row["name"].split(".")[-1] for row in data.children(output_row(updated, "body"))
    ] == ["replacement"]
    for name in ("path_parameters", "query_parameters", "headers"):
        assert input_row(updated, name) == input_row(before, name)
        assert output_row(updated, name) == output_row(before, name)
    assert updated["inputs"] == updated["triggerInstances"][0]["outputs"]
    assert_readonly()


@pytest.mark.parametrize(
    "changes",
    [
        {"base_path": "/api/unsafe"},
        {"media_type": "text/plain"},
        {"path": "/direct"},
        {"http_method": "POST"},
        {"body": {"message": "runtime value"}},
        {"requires_authentication": "true"},
    ],
)
async def test_rest_rejects_readonly_derived_and_runtime_schema_values(
    settings, changes
):
    flow = base_flow()
    routes(settings, flow)
    values = inputs("rest_async")
    values.update(copy.deepcopy(changes))
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [{"op": "set_trigger", "type": "rest_async", "inputs": values}],
        )
    assert_readonly()


@pytest.mark.parametrize(
    "display",
    [
        "/requests/has space",
        "/requests/{{Other_1.path_parameters.request_id}}",
        "/requests/{{REST API - Asynchronous_1.path_parameters.missing}}",
        "/requests/{{REST API - Asynchronous_1.body.message}}",
        "/requests/prefix{{REST API - Asynchronous_1.path_parameters.request_id}}",
    ],
)
async def test_rest_paths_require_own_declared_complete_parameter_segments(
    settings, display
):
    flow = base_flow()
    routes(settings, flow)
    values = inputs("rest_async")
    values["display_path"] = display
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "set_trigger",
                    "type": "rest_async",
                    "inputs": values,
                    "interface_schemas": rest_schemas(),
                }
            ],
        )
    assert_readonly()


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"invented_root": {"type": "object", "children": []}},
        {
            "headers": {
                "type": "object",
                "children": [{"name": "flag", "label": "Flag", "type": "boolean"}],
            }
        },
        {
            "query_parameters": {
                "type": "object",
                "children": [
                    {
                        "name": "nested",
                        "label": "Nested",
                        "type": "object",
                        "children": [],
                    }
                ],
            }
        },
        {"body": {"type": "string", "children": []}},
        {
            "body": {
                "type": "object",
                "children": [{"name": "opaque", "label": "Opaque", "type": "json"}],
            }
        },
        {
            "body": {
                "type": "object",
                "children": [
                    {
                        "name": "one",
                        "label": "One",
                        "type": "string",
                        "parameter": {"type": "integer"},
                    }
                ],
            }
        },
    ],
)
def test_rest_helper_rejects_opaque_or_catalog_disallowed_schema(schema):
    with pytest.raises(CapabilityError):
        application_triggers.new_instance(
            base_flow(),
            definition("rest_async"),
            inputs("rest_async"),
            schemas=copy.deepcopy(schema),
        )


@pytest.mark.parametrize("kind", KINDS)
def test_definition_metadata_mismatch_cannot_be_silently_reconstructed(kind):
    expected = definition(kind)
    expected["outputs"][0]["parameter"]["name"] = "not_the_declared_output"
    with pytest.raises(CapabilityError):
        application_triggers.new_instance(base_flow(), expected, inputs(kind))


async def test_rest_schema_replacement_rejects_existing_nested_consumer(settings):
    flow = base_flow()
    routes(settings, flow)
    built = (
        await plan(
            settings,
            flow,
            [
                set_trigger("rest_async", interface_schemas=rest_schemas()),
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {
                        "log_message": "{{REST API - Asynchronous_1.body.message}}"
                    },
                },
            ],
        )
    )["payload"]
    routes(settings, built)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            built,
            [
                {
                    "op": "update_trigger",
                    "interface_schemas": {"body": {"type": "object", "children": []}},
                }
            ],
        )
    assert_readonly()


@pytest.mark.parametrize(
    "kind,root", [("sla_task", "sla_flow_inputs"), ("rest_async", "body")]
)
async def test_native_application_whole_object_output_is_a_typed_consumer(
    settings, kind, root
):
    flow = base_flow()
    schemas = rest_schemas() if kind == "rest_async" else None
    if schemas is not None:
        schemas["body"]["children"].append(
            {"name": "ratio", "label": "Ratio", "type": "decimal"}
        )
    sample = application_triggers.new_instance(
        flow, definition(kind), inputs(kind), schemas=schemas
    )
    target = copy.deepcopy(data.spec(data.named(sample["outputs"], root)))
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "payload",
            "value": "",
            "children": [],
            "parameter": target,
        }
    )
    routes(settings, flow)
    edit = set_trigger(kind)
    if schemas is not None:
        edit["interface_schemas"] = copy.deepcopy(schemas)
    pill = definition(kind)["label"] + "_1." + root
    planned = await plan(
        settings,
        flow,
        [
            edit,
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"payload": "{{" + pill + "}}"},
            },
        ],
    )
    payload = data.named(planned["payload"]["actionInstances"][1]["inputs"], "payload")
    assert payload["value"] == "{{" + pill + "}}"
    assert pill in {row["name"] for row in planned["payload"]["label_cache"]}
    assert data.value_type(output_row(planned["payload"], root)).kind == "object"
    if kind == "rest_async":
        body = output_row(planned["payload"], root)
        details = schema_child(body, "details")
        assert data.value_type(schema_child(details, "amount")).kind == "float"
        assert data.value_type(schema_child(body, "ratio")).kind == "decimal"
    assert_readonly()


async def test_rest_nested_object_output_resolves_authored_leaf(settings):
    flow = base_flow()
    routes(settings, flow)
    schema = {
        "body": {
            "type": "object",
            "children": [
                {
                    "name": "ticket",
                    "label": "Ticket",
                    "type": "object",
                    "children": [
                        {"name": "summary", "label": "Summary", "type": "string"}
                    ],
                }
            ],
        }
    }
    pill = "REST API - Asynchronous_1.body.ticket.summary"
    planned = await plan(
        settings,
        flow,
        [
            set_trigger("rest_async", interface_schemas=copy.deepcopy(schema)),
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "{{" + pill + "}}"},
            },
        ],
    )
    assert pill in {row["name"] for row in planned["payload"]["label_cache"]}
    ticket = schema_child(output_row(planned["payload"], "body"), "ticket")
    assert data.value_type(schema_child(ticket, "summary")).kind == "string"
    assert_readonly()


@pytest.mark.parametrize("kind", ["sla_task", "rest_async"])
async def test_complex_trigger_consumer_rechecks_current_catalog_schema(settings, kind):
    flow = base_flow()
    routes(settings, flow)
    edit = set_trigger(kind)
    if kind == "rest_async":
        edit["interface_schemas"] = rest_schemas()
    built = (await plan(settings, flow, [edit]))["payload"]
    current = categories()
    current_definition = next(
        row
        for category in current
        for row in category["triggers"].values()
        if row["type"] == kind
    )
    if kind == "sla_task":
        parameter = data.spec(
            data.named(current_definition["outputs"], "sla_flow_inputs")
        )
        parameter["children"][0]["type"] = "integer"
    else:
        parameter = data.spec(data.named(current_definition["inputs"], "body"))
        parameter["attributes"]["customSupportedChildFieldTypes"] = (
            "object;string;integer"
        )
    routes(settings, built, catalog=current)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            built,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "{{" + consumer_pill(kind) + "}}"},
                }
            ],
        )
    assert_readonly()


@pytest.mark.parametrize(
    "kind,table,identity,table_field",
    [
        ("metric", "sys_metric_trigger_definition", METRIC, "metric_table.name"),
        (
            "analytics",
            "sys_analytics_trigger_definition",
            ANALYTICS,
            "event_table.name",
        ),
    ],
)
async def test_provider_table_and_revision_accept_native_display_wrappers(
    settings, kind, table, identity, table_field
):
    flow = base_flow()
    _, _, _, records, _ = routes(settings, flow)
    record = records[(table, identity)]
    record[table_field] = {"value": "incident", "display_value": "Incident"}
    record["sys_mod_count"] = {"value": "4", "display_value": "4"}
    if kind == "analytics":
        record["encoded_query"] = {
            "value": "active=false",
            "display_value": "Active is false",
        }
    planned = await plan(settings, flow, [set_trigger(kind)])
    assert input_row(planned["payload"], "table_name")["value"] == "incident"
    assert input_row(planned["payload"], "condition")["value"] == "active=true"
    assert_readonly()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("surface", ["default", "pharmacy", "script"])
async def test_hidden_application_trigger_consumers_prevent_removal(
    settings, kind, surface
):
    flow = base_flow()
    routes(settings, flow)
    edit = set_trigger(kind)
    if kind == "rest_async":
        edit["interface_schemas"] = rest_schemas()
    built = (await plan(settings, flow, [edit]))["payload"]
    pill = "{{" + consumer_pill(kind) + "}}"
    if surface == "default":
        built["outputs"] = [
            {"name": "retained", "type": "string", "defaultValue": pill}
        ]
    elif surface == "pharmacy":
        built["pharmacy"] = {"prescriptions": [{"prescription": pill}]}
    else:
        built["actionInstances"][0]["inputs"][0].update(
            scriptActive=True,
            script="return fd_data.trigger;",
        )
    routes(settings, built)
    with pytest.raises(CapabilityError):
        await plan(settings, built, [{"op": "remove_trigger"}])
    assert_readonly()


@pytest.mark.parametrize(
    "kind,lose_input_label",
    [
        *[(kind, False) for kind in KINDS],
        *[(kind, True) for kind in KINDS if kind != "sla_task"],
    ],
)
async def test_application_save_echo_and_independent_readback_preserve_consumer_types(
    settings, kind, lose_input_label
):
    flow = base_flow()
    coordinator, tools, _, _, _ = routes(settings, flow)
    saved = {}

    def save(request):
        body = json.loads(request.content)
        assert body["active"] is False
        assert body["isPublished"] is False
        assert body["status"] == "draft"
        trigger = body["triggerInstances"][0]
        trigger["id"] = SAVED
        # Model the native getter from its writable label slot. An editor-only
        # alias in the request cannot populate this saved native type label.
        for row in trigger["inputs"]:
            parameter = row["parameter"]
            parameter["uiDisplayTypeLabel"] = parameter.pop("type_label", "")
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        if lose_input_label:
            actual["triggerInstances"][0]["inputs"][0]["parameter"][
                "uiDisplayTypeLabel"
            ] = ""
        saved.update(copy.deepcopy(actual))
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    edit = set_trigger(kind)
    if kind == "rest_async":
        edit["interface_schemas"] = rest_schemas()
    pill = consumer_pill(kind)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                edit,
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "{{" + pill + "}}"},
                },
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert write.called
    assert len(coordinator.store) == 0
    if lose_input_label:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
        return
    assert applied["status"] == "success", applied
    assert saved["triggerInstances"][0]["id"] == SAVED
    for row in saved["triggerInstances"][0]["inputs"]:
        assert row["parameter"]["uiDisplayTypeLabel"]
    routes(settings, saved)
    continued = await plan(
        settings,
        saved,
        [
            {
                "op": "update_inputs",
                "step": "ui-1",
                "inputs": {"log_message": "Saved {{" + pill + "}}"},
            }
        ],
    )
    assert pill in {row["name"] for row in continued["payload"]["label_cache"]}
    assert (
        continued["payload"]["actionInstances"][0]["inputs"][0]["value"]
        == "Saved {{" + pill + "}}"
    )
    assert continued["payload"]["active"] is False
    assert continued["payload"]["status"] == "draft"
