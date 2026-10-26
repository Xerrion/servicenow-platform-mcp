"""Source-derived saved trigger interfaces, not captured or certified live saves.

Studio 27.0.13 createFlowInputs (184408-184414) copies REST/external output
bindings into flow inputs. Conditional profiles below expose native flat picker
metadata; they do not assert instance endpoint names or execute providers.
Service Catalog instead uses its independently selected saved output schema to
bind the backend-owned call-input declarations.
"""

import copy
import json

import httpx2
import pytest

from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_application_triggers import definition as application_definition
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, persist
from tests.test_flow_fields import plan
from tests.test_flow_rest_trigger_transport import (
    QUERY as REST_QUERY,
)
from tests.test_flow_rest_trigger_transport import (
    envelope as rest_envelope,
)
from tests.test_flow_saved_trigger_validation import (
    _assert_complete,
    _saved_validation,
    _validate,
)
from tests.test_flow_saved_triggers import (
    PICKER_PATH,
    SAVED,
    TABLE,
    TEMPLATE,
    TEMPLATE_PATH,
    UNDERLYING,
    assert_independent_readback,
    assert_readonly,
    input_row,
    saved_detail,
    saved_flow,
    saved_routes,
    template_row,
)

PROFILES = [
    pytest.param("REST", "fixture_native_rest", id="rest_category_arbitrary_type"),
    pytest.param("Application", "rest_async", id="application_rest_async"),
    pytest.param("Application", "rest_sync", id="application_rest_sync"),
    pytest.param(
        "Conditional external category", "external_trigger", id="external_trigger"
    ),
]


@pytest.fixture(autouse=True)
def shared_definitions_are_not_mutated():
    before = copy.deepcopy(DEFS)
    yield
    assert DEFS == before


def _classic_catalog_definition():
    catalog = copy.deepcopy(DEFS)
    return next(
        trigger
        for category in catalog["trigger_categories"]
        for trigger in category["triggers"].values()
        if trigger["type"] == "service_catalog"
    )


def _profile_definition(category, kind):
    definition = saved_detail()
    definition.update(
        category=category,
        type=kind,
        name="Conditional saved interface fixture",
        description="Metadata-shaped interface fixture, not a certified live profile",
    )
    definition["inputs"] = [
        row
        for row in definition["inputs"]
        if row["name"] in {"fixture_locked", "fixture_opaque", "fixture_timezone"}
    ]
    opaque = next(
        row for row in definition["inputs"] if row["name"] == "fixture_opaque"
    )
    opaque["readonly"] = True
    opaque["fixture_native_extension"] = {"preserve": [None, False, {"zero": 0}]}

    native = application_definition("rest_async")
    body = copy.deepcopy(
        next(row["parameter"] for row in native["outputs"] if row["name"] == "body")
    )
    scalar = copy.deepcopy(
        next(
            row["parameter"]
            for row in native["inputs"]
            if row["name"] == "display_path"
        )
    )
    scalar.update(
        id=f"{203:032x}",
        name="fixture_scalar",
        label="Conditional scalar output",
        columnName="fixture_scalar",
        order=140,
        defaultValue="",
    )
    scalar["attributes"]["fixture_opaque_attribute"] = {"preserve": [False, 0, None]}
    details = copy.deepcopy(body)
    details.update(
        id=f"{201:032x}", name="body.details", label="Conditional details", order=1
    )
    message = copy.deepcopy(scalar)
    message.update(
        id=f"{202:032x}",
        name="body.details.message",
        label="Conditional message",
        columnName="message",
        order=1,
        mandatory=True,
        maxsize=137,
    )
    message["fixture_native_extension"] = {"preserve": ["æøå", {"native": True}]}
    details["children"] = [message]
    body["children"] = [details]
    body["fixture_native_extension"] = {"preserve": [None, {"opaque": []}]}
    definition["outputs"] = [scalar, body]
    if (
        category == "REST"
        or category == "Application"
        and kind in {"rest_async", "rest_sync"}
    ):
        # mapInputsToOutputs requires typed metadata twins for native REST
        # outputs. External trigger outputs do not have that requirement.
        for index, raw in enumerate(definition["outputs"], start=301):
            parameter = copy.deepcopy(raw)
            parameter["id"] = f"{index:032x}"
            parameter["attributes"]["fromTemplate"] = "true"
            definition["inputs"].append(parameter)
    return definition


def _catalog_definition():
    definition = _profile_definition("Application", "service_catalog")
    definition["outputs"] = [
        copy.deepcopy(row["parameter"])
        for row in _classic_catalog_definition()["outputs"]
    ]
    for row in definition["outputs"]:
        if row["type"] in {"reference", "table_name"}:
            row["label"] = "Conditional saved " + row["label"]
            row["hint"] = "Selected saved schema, not the classic catalog schema"
            row["attributes"]["fixture_saved_schema"] = {"preserve": [False, None]}
    return definition


def _schema_definition(category="REST", kind="fixture_native_rest"):
    definition = _profile_definition(category, kind)
    definition["inputs"] = [
        row
        for row in definition["inputs"]
        if row["name"] not in {"body", "fixture_scalar"}
    ]
    native = application_definition("rest_async")
    roots = {"body", "query_parameters"}
    definition["inputs"].extend(
        copy.deepcopy(row["parameter"])
        for row in native["inputs"]
        if row["name"] in roots
    )
    definition["outputs"] = [
        copy.deepcopy(row["parameter"])
        for row in native["outputs"]
        if row["name"] in roots
    ]
    for row in definition["inputs"]:
        if row["name"] in roots:
            row["fixture_native_extension"] = {"preserve": [False, {"opaque": []}]}
    return definition


def _path_definition(default="/api/fixture/template"):
    definition = _schema_definition()
    native = application_definition("rest_async")
    roles = {"base_path", "display_path", "path", "path_parameters"}
    definition["inputs"].extend(
        copy.deepcopy(row["parameter"])
        for row in native["inputs"]
        if row["name"] in roles
    )
    definition["outputs"].extend(
        copy.deepcopy(row["parameter"])
        for row in native["outputs"]
        if row["name"] == "path_parameters"
    )
    for row in definition["inputs"]:
        if row["name"] in {"base_path", "path"}:
            row.update(defaultValue=default, readonly=False)
            row["attributes"]["fromTemplate"] = "true"
            row["fixture_native_extension"] = {"preserve": [False, {"native": 0}]}
    return definition


def _path_edit(definition, *, display=None):
    if display is None:
        display = (
            "/requests/{{" + definition["name"] + "_1.path_parameters.request_id}}"
        )
    return {
        "op": "set_trigger",
        "template_id": definition["id"],
        "inputs": {"display_path": display},
        "interface_schemas": {
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
            }
        },
    }


def _rest_metadata_route(current, *, default="/api/fixture/template"):
    def metadata(request):
        body = json.loads(request.content)
        assert body["operationName"] == "restTiggerInfoQuery"
        assert body["query"] == REST_QUERY
        assert set(body) == {"operationName", "query", "variables"}
        info = body["variables"]["flowInfo"]
        assert set(body["variables"]) == {"flowInfo"}
        assert set(info) == {"scopeId", "flowName", "basePath"}
        assert info["scopeId"] == "global"
        assert info["flowName"] == "Fixture"
        assert info["basePath"]["value"] in {default, "/api/fixture/generated"}
        return httpx2.Response(
            200,
            json=rest_envelope(
                {
                    "__typename": "now_restTrigger_BasePathResponse",
                    "path": current["path"],
                }
            ),
        )

    return http_mock.post(f"{BASE}/api/now/graphql").mock(side_effect=metadata)


def _assert_metadata_reads_only():
    assert all(
        call.request.method == "GET"
        or call.request.method == "POST"
        and call.request.url.path == "/api/now/graphql"
        and json.loads(call.request.content)["operationName"] == "restTiggerInfoQuery"
        for call in http_mock.calls
    )


def _authored_schemas(*, updated=False):
    children = [
        {
            "name": "summary",
            "label": "Updated summary" if updated else "Summary",
            "type": "string",
            "mandatory": True,
            "maxsize": 193 if updated else 137,
        }
    ]
    if updated:
        children.append({"name": "count", "label": "Count", "type": "integer"})
    return {
        "body": {
            "type": "object",
            "children": [
                {
                    "name": "ticket",
                    "label": "Ticket",
                    "type": "object",
                    "children": children,
                }
            ],
        },
        "query_parameters": {
            "type": "object",
            "children": [
                {
                    "name": "limit",
                    "label": "Limit",
                    "type": "integer",
                }
            ],
        },
    }


def _declaration(flow, name):
    return next(row for row in flow["inputs"] if row["name"] == name)


def _assert_authored_schema(flow, *, updated=False):
    declaration = _declaration(flow, "body")
    summary = declaration["children"][0]["children"][0]
    assert summary["name"] == "body.ticket.summary"
    assert summary["type"] == "string"
    assert summary["mandatory"] is True
    assert summary["maxsize"] == (193 if updated else 137)
    assert summary["label"] == ("Updated summary" if updated else "Summary")
    assert "value" not in summary
    assert declaration["value"] == ""
    assert declaration["parameter"]["fixture_native_extension"] == {
        "preserve": [False, {"opaque": []}]
    }
    assert {row["name"] for row in declaration["children"][0]["children"]} == (
        {"body.ticket.summary", "body.ticket.count"}
        if updated
        else {"body.ticket.summary"}
    )


def _source_parameter(raw, parent=""):
    """Independent projection of the saved constructor's parameter identities."""
    parameter = copy.deepcopy(raw)
    parameter["uiUniqueId"] = raw["id"]
    if parent:
        parameter["parent"] = parent
    if raw.get("children"):
        parameter["children"] = [
            _source_parameter(child, raw["id"]) for child in raw["children"]
        ]
    return parameter


def _owned_flow(definition):
    flow = saved_flow(definition)
    for collection in ("inputs", "outputs"):
        for binding in flow["triggerInstances"][0][collection]:
            raw = next(
                row for row in definition[collection] if row["name"] == binding["name"]
            )
            binding["parameter"] = _source_parameter(raw)
            binding["children"] = copy.deepcopy(binding["parameter"]["children"])
    if (
        definition["category"] == "REST"
        or definition["category"] == "Application"
        and definition["type"] in {"rest_async", "rest_sync"}
    ):
        # Native mapInputsToOutputs overlays matching input parameter metadata
        # and the expanded top-level schema onto the original output owner.
        for output in flow["triggerInstances"][0]["outputs"]:
            source = input_row(flow, output["name"])
            source["type_label"] = source["parameter"]["type_label"]
            source["attributes"] = copy.deepcopy(source["parameter"]["attributes"])
            source["canDrag"] = True
            for key in (
                "type",
                "type_label",
                "children",
                "attributes",
                "uiUniqueId",
                "canDrag",
            ):
                output[key] = copy.deepcopy(source[key])
            output["parameter"] = {
                **output["parameter"],
                **copy.deepcopy(source["parameter"]),
                **{
                    key: copy.deepcopy(source[key])
                    for key in (
                        "type",
                        "type_label",
                        "children",
                        "attributes",
                        "canDrag",
                    )
                },
            }
    flow["inputs"] = copy.deepcopy(flow["triggerInstances"][0]["outputs"])
    for index, row in enumerate(flow["inputs"], start=501):
        row["id"] = f"{index:032x}"
        row["fixture_declaration_metadata"] = {"preserve": [False, {"native": 0}]}
    return flow


def _catalog_declarations(definition):
    """Native Service Catalog projection, independent of the production helper."""
    result = []
    for kind in ("table_name", "reference"):
        row = copy.deepcopy(
            next(row for row in definition["outputs"] if row["type"] == kind)
        )
        row.pop("id")
        if row.get("internal_link") == "":
            row.pop("internal_link")
        row["readonly"] = False
        row["attributes"].update(
            uiType=row["type"],
            uiTypeLabel=row["type_label"],
            element_mapping_provider="com.glide.flow_design.action.data.FlowDesignVariableMapper",
        )
        result.append(row)
    return result


def _body_message(flow):
    root = next(row for row in flow["inputs"] if row["name"] == "body")
    return root["children"][0]["children"][0]


def _assert_interface(flow, definition):
    trigger = flow["triggerInstances"][0]
    source_outputs = _owned_flow(definition)["triggerInstances"][0]["outputs"]
    assert trigger["triggerDefinitionId"] == definition["id"] != UNDERLYING
    assert trigger["triggerType"] == definition["category"]
    assert trigger["type"] == definition["type"]
    assert flow["inputs"] == trigger["outputs"]
    assert {row["name"] for row in flow["inputs"]} == {"body", "fixture_scalar"}
    for output in trigger["outputs"]:
        expected = next(row for row in source_outputs if row["name"] == output["name"])
        assert output["parameter"] == expected["parameter"]
        assert output["children"] == output["parameter"]["children"]
        assert output["value"] == ""
        assert "templateValue" not in output
    message = _body_message(flow)
    assert message["type"] == "string"
    assert message["mandatory"] is True
    assert message["maxsize"] == 137
    assert message["fixture_native_extension"] == {
        "preserve": ["æøå", {"native": True}]
    }
    assert "value" not in message
    assert "displayValue" not in message
    assert message["attributes"]["fixture_opaque_attribute"] == {
        "preserve": [False, 0, None]
    }


def _owner_records(rows, *, overrides=None):
    for row in rows:
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow_input/{row['id']}").respond(
            200,
            json={
                "result": {
                    "sys_id": row["id"],
                    "model": FLOW,
                    "element": row["name"],
                    "name": "var__m_sys_hub_flow_input_" + FLOW,
                    "sys_class_name": "sys_hub_flow_input",
                    "sys_mod_count": "4",
                    **(overrides or {}),
                }
            },
        )


def _additional_saved_routes(definition):
    identity = definition["id"]
    row = template_row(definition)
    row["sys_id"] = identity
    row["trigger_definition"] = {
        "value": definition["triggerDefinition"],
        "link": "fixture-link",
    }
    http_mock.get(f"{BASE}/api/now/hub/triggerpicker/{identity}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(definition)}}
        )
    )
    http_mock.get(f"{BASE}/api/now/table/{TABLE}/{identity}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": copy.deepcopy(row)})
    )
    http_mock.get(
        f"{BASE}/api/now/table/sys_hub_trigger_definition/{definition['triggerDefinition']}"
    ).respond(
        200,
        json={
            "result": {
                "sys_id": definition["triggerDefinition"],
                "sys_mod_count": "4",
                "sys_updated_on": "2026-10-20 12:00:00",
            }
        },
    )


def _catalog_flow(definition):
    flow = saved_flow(definition)
    flow["inputs"] = _catalog_declarations(definition)
    for index, row in enumerate(flow["inputs"], start=801):
        row["id"] = f"{index:032x}"
    return flow


async def test_saved_catalog_attach_binds_selected_flat_definition_and_backend_inputs(
    settings,
):
    definition = _catalog_definition()
    original = copy.deepcopy(definition)
    classic = _classic_catalog_definition()
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edits = [{"op": "set_trigger", "template_id": TEMPLATE}]
    prepared = await plan(settings, flow, edits)
    declarations = _catalog_declarations(definition)
    manifest = prepared["generated_trigger_inputs"]
    assert manifest == {"definition_id": TEMPLATE, "declarations": declarations}
    assert manifest["definition_id"] != classic["id"]
    assert prepared["payload"]["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
    assert prepared["payload"]["inputs"] == []
    assert [row["name"] for row in declarations] == ["table_name", "request_item"]
    assert all(
        row["hint"] == "Selected saved schema, not the classic catalog schema"
        for row in declarations
    )
    assert all(row["readonly"] is False and "id" not in row for row in declarations)
    assert all(
        row["attributes"]["fixture_saved_schema"] == {"preserve": [False, None]}
        for row in declarations
    )
    assert not any(row["type"] == "glide_date_time" for row in declarations)

    # The independent GET snapshot and its ownership records exist before PUT.
    actual = copy.deepcopy(prepared["payload"])
    actual["triggerInstances"][0]["id"] = SAVED
    actual["inputs"] = copy.deepcopy(declarations)
    for index, row in enumerate(actual["inputs"], start=601):
        row["id"] = f"{index:032x}"
    _owner_records(actual["inputs"])

    def save(request):
        body = json.loads(request.content)
        assert body["inputs"] == []
        assert body["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
        assert input_row(body, "fixture_locked")["fromTemplate"] is True
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        echo["inputs"] = copy.deepcopy(actual["inputs"])
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["triggers"][0]["definition"] == TEMPLATE
    assert {row["name"] for row in preview["data"]["summary"]["fields"]["inputs"]} == {
        "table_name",
        "request_item",
    }
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert len(coordinator.store) == 0
    assert definition == original
    assert_independent_readback(write)


@pytest.mark.parametrize("category,kind", PROFILES)
async def test_saved_native_interface_attach_projects_outputs_and_nested_consumer(
    settings, category, kind
):
    definition = _profile_definition(category, kind)
    original = copy.deepcopy(definition)
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    pill = definition["name"] + "_1.body.details.message"
    edits = [
        {"op": "set_trigger", "template_id": TEMPLATE},
        {
            "op": "update_inputs",
            "step": "ui-1",
            "inputs": {"log_message": "Saved {{" + pill + "}}"},
        },
    ]
    prepared = await plan(settings, flow, edits)
    _assert_interface(prepared["payload"], definition)
    assert (
        prepared["payload"]["inputs"]
        is not prepared["payload"]["triggerInstances"][0]["outputs"]
    )
    assert all(
        declaration is not output
        for declaration, output in zip(
            prepared["payload"]["inputs"],
            prepared["payload"]["triggerInstances"][0]["outputs"],
            strict=True,
        )
    )
    assert all(
        declaration["parameter"] is not output["parameter"]
        and declaration["children"] is not output["children"]
        for declaration, output in zip(
            prepared["payload"]["inputs"],
            prepared["payload"]["triggerInstances"][0]["outputs"],
            strict=True,
        )
    )
    assert prepared["generated_trigger_inputs"] is None
    assert {
        row["name"]
        for row in prepared["created_fields"]
        if row["collection"] == "inputs"
    } == {"body", "fixture_scalar"}
    assert pill in {row["name"] for row in prepared["payload"]["label_cache"]}
    assert {"base_path", "display_path", "path"}.isdisjoint(
        row["name"] for row in definition["inputs"]
    )
    assert not any(
        call.request.url.path == "/api/now/graphql" for call in http_mock.calls
    )
    actual = copy.deepcopy(prepared["payload"])
    actual["triggerInstances"][0]["id"] = SAVED

    def save(request):
        body = json.loads(request.content)
        _assert_interface(body, definition)
        assert (
            body["actionInstances"][0]["inputs"][0]["value"] == "Saved {{" + pill + "}}"
        )
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    assert_readonly()
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert len(coordinator.store) == 0
    assert definition == original
    assert_independent_readback(write)


@pytest.mark.parametrize("category,kind", PROFILES)
@pytest.mark.parametrize(
    "defect", [None, "nested_type", "nested_metadata", "nested_value"]
)
async def test_saved_scalar_update_preserves_interface_flags_and_strict_nested_readback(
    settings, category, kind, defect
):
    definition = _profile_definition(category, kind)
    flow = _owned_flow(definition)
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edits = [{"op": "update_trigger", "inputs": {"fixture_timezone": "UTC"}}]
    prepared = await plan(settings, flow, edits)
    planned = prepared["payload"]
    assert planned["inputs"] == original["inputs"]
    assert (
        planned["triggerInstances"][0]["outputs"]
        == original["triggerInstances"][0]["outputs"]
    )
    for name in ("fixture_locked", "fixture_opaque"):
        assert input_row(planned, name) == input_row(original, name)
        assert (
            input_row(planned, name)["parameter"]["attributes"]["fromTemplate"]
            == "true"
        )
        assert input_row(planned, name)["fromTemplate"] is True
    assert input_row(planned, "fixture_locked")["parameter"]["readonly"] is False
    assert input_row(planned, "fixture_opaque")["parameter"]["readonly"] is True
    assert prepared["created_fields"] == prepared["removed_fields"] == []
    actual = copy.deepcopy(planned)
    if defect == "nested_type":
        _body_message(actual)["type"] = "integer"
    elif defect == "nested_metadata":
        _body_message(actual)["fixture_native_extension"]["preserve"] = ["changed"]
    elif defect == "nested_value":
        _body_message(actual)["value"] = "An invented runtime scalar"

    def save(request):
        body = json.loads(request.content)
        assert input_row(body, "fixture_timezone")["value"] == "UTC"
        assert body["inputs"] == original["inputs"]
        assert input_row(body, "fixture_locked") == input_row(
            original, "fixture_locked"
        )
        assert input_row(body, "fixture_opaque") == input_row(
            original, "fixture_opaque"
        )
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert flow == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize("category,kind", PROFILES)
async def test_saved_owned_interface_removal_requires_explicit_consumer_repair(
    settings, category, kind
):
    definition = _profile_definition(category, kind)
    flow = _owned_flow(definition)
    pill = definition["name"] + "_1.body.details.message"
    flow["actionInstances"][0]["inputs"][0]["value"] = "{{" + pill + "}}"
    flow["outputs"] = [
        {
            "name": "fixture_result",
            "type": "string",
            "defaultValue": "Retain this unrelated declaration",
        }
    ]
    flow["flowVariables"] = [
        {
            "name": "fixture_variable",
            "type": "string",
            "defaultValue": "Retain this unrelated variable",
        }
    ]
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    blocked = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "remove_trigger"}],
        )
    )
    assert blocked["status"] == "error", blocked
    assert len(coordinator.store) == 0
    assert flow == original
    assert_readonly()

    edits = [
        {
            "op": "update_inputs",
            "step": "ui-1",
            "inputs": {"log_message": "Consumer explicitly repaired"},
        },
        {"op": "remove_trigger"},
    ]
    prepared = await plan(settings, flow, edits)
    actual = copy.deepcopy(prepared["payload"])
    assert actual["triggerInstances"] == actual["inputs"] == []
    assert actual["outputs"] == original["outputs"]
    assert actual["flowVariables"] == original["flowVariables"]
    assert actual["deletedTriggers"] == [{"id": SAVED}]
    removed = {
        row["id"] for row in prepared["removed_fields"] if row["collection"] == "inputs"
    }
    assert {row["id"] for row in flow["inputs"]} <= removed

    def save(request):
        body = json.loads(request.content)
        assert body["triggerInstances"] == body["inputs"] == []
        assert (
            body["actionInstances"][0]["inputs"][0]["value"]
            == "Consumer explicitly repaired"
        )
        assert body["outputs"] == original["outputs"]
        assert body["flowVariables"] == original["flowVariables"]
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize("category,kind", PROFILES)
async def test_saved_removal_never_clears_a_mismatched_unowned_interface(
    settings, category, kind
):
    definition = _profile_definition(category, kind)
    flow = _owned_flow(definition)
    flow["inputs"].append(
        {
            "id": f"{700:032x}",
            "name": "fixture_unrelated_interface",
            "type": "string",
            "children": [],
            "defaultValue": "",
        }
    )
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "remove_trigger"}],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert flow == original
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("surface", ["interface_pill", "default", "pharmacy", "script"])
async def test_saved_external_interface_removal_blocks_hidden_surviving_dependencies(
    settings, surface
):
    definition = _profile_definition(
        "Conditional external category", "external_trigger"
    )
    flow = _owned_flow(definition)
    pill = "{{" + definition["name"] + "_1.body.details.message}}"
    if surface == "interface_pill":
        flow["actionInstances"][0]["inputs"][0]["value"] = (
            "{{subflow.body.details.message}}"
        )
    elif surface == "default":
        flow["outputs"] = [
            {"name": "fixture_result", "type": "string", "defaultValue": pill}
        ]
    elif surface == "pharmacy":
        flow["pharmacy"] = {"prescriptions": [{"prescription": pill}]}
    else:
        flow["actionInstances"][0]["inputs"][0].update(
            scriptActive=True,
            script="return fd_data.subflow.body.details.message;",
        )
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "remove_trigger"}],
        )
    )
    assert result["status"] == "error", result
    assert flow == original
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize(
    "category,kind",
    [
        ("Date", "rest_async"),
        ("Application", "fixture_native_rest"),
        ("Conditional external category", "external"),
    ],
)
async def test_saved_profile_acceptance_does_not_invent_an_interface_outside_native_predicate(
    settings, category, kind
):
    definition = _profile_definition(category, kind)
    flow = base_flow()
    flow["version"] = "2"
    _, tools, _ = saved_routes(settings, flow, definition)
    edits = [{"op": "set_trigger", "template_id": TEMPLATE}]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    prepared = await plan(settings, flow, edits)
    assert prepared["payload"]["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
    assert len(prepared["payload"]["triggerInstances"][0]["outputs"]) == 2
    assert prepared["payload"]["inputs"] == []
    assert prepared["created_fields"] == []
    assert prepared["generated_trigger_inputs"] is None
    assert_readonly()


@pytest.mark.parametrize(
    "category,kind",
    [
        *PROFILES,
        pytest.param("Application", "service_catalog", id="service_catalog"),
        pytest.param("Conditional profile", "rest", id="literal_rest_non_interface"),
        pytest.param(
            "Conditional profile", "external", id="literal_external_non_interface"
        ),
    ],
)
async def test_local_validation_binds_saved_profile_snapshot_without_classic_catalog_misbind(
    settings, category, kind
):
    definition = (
        _catalog_definition()
        if kind == "service_catalog"
        else _profile_definition(category, kind)
    )
    flow, template = _saved_validation(settings, definition)
    projected = _owned_flow(definition)
    flow["triggerInstances"] = projected["triggerInstances"]
    if kind == "service_catalog":
        flow["inputs"] = _catalog_declarations(definition)
    elif kind in {"rest", "external"}:
        flow["inputs"] = []
    else:
        flow["inputs"] = projected["inputs"]
    before = copy.deepcopy(flow)
    result = await _validate(settings, flow, definition, template)
    _assert_complete(result)
    assert flow == before
    assert flow["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
    paths = {call.request.url.path for call in http_mock.calls}
    assert PICKER_PATH in paths
    assert TEMPLATE_PATH in paths
    assert "/api/now/trigger_api/trigger_categories" not in paths


@pytest.mark.parametrize("category,kind", PROFILES[:3])
async def test_saved_writable_rest_schema_attach_projects_public_declarations(
    settings, category, kind
):
    definition = _schema_definition(category, kind)
    original = copy.deepcopy(definition)
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    pill = definition["name"] + "_1.body.ticket.summary"
    edits = [
        {
            "op": "set_trigger",
            "template_id": TEMPLATE,
            "interface_schemas": _authored_schemas(),
        },
        {
            "op": "update_inputs",
            "step": "ui-1",
            "inputs": {"log_message": "{{" + pill + "}}"},
        },
    ]
    prepared = await plan(settings, flow, edits)
    planned = prepared["payload"]
    _assert_authored_schema(planned)
    assert planned["inputs"] == planned["triggerInstances"][0]["outputs"]
    assert planned["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
    assert input_row(planned, "body")["value"] == ""
    assert (
        input_row(planned, "body")["children"]
        == _declaration(planned, "body")["children"]
    )
    assert pill in {row["name"] for row in planned["label_cache"]}
    assert {row["name"] for row in prepared["created_fields"]} >= {
        "body",
        "body.ticket",
        "body.ticket.summary",
        "query_parameters",
        "query_parameters.limit",
    }
    actual = copy.deepcopy(planned)
    actual["triggerInstances"][0]["id"] = SAVED

    def save(request):
        body = json.loads(request.content)
        _assert_authored_schema(body)
        assert body["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert definition == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize("defect", [None, "nested_metadata", "nested_value"])
async def test_saved_rest_schema_only_update_preserves_other_owners_and_checks_nested_readback(
    settings, defect
):
    definition = _schema_definition("Application", "rest_async")
    empty = base_flow()
    empty["version"] = "2"
    saved_routes(settings, empty, definition)
    initial = await plan(
        settings,
        empty,
        [
            {
                "op": "set_trigger",
                "template_id": TEMPLATE,
                "interface_schemas": _authored_schemas(),
            }
        ],
    )
    flow = copy.deepcopy(initial["payload"])
    flow["triggerInstances"][0]["id"] = SAVED
    for index, row in enumerate(flow["inputs"], start=701):
        row["id"] = f"{index:032x}"
        row["fixture_declaration_metadata"] = {"preserve": [False, {"native": 0}]}
    _body_message(flow)["fixture_declaration_extension"] = {
        "preserve": ["native", None]
    }
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edits = [
        {
            "op": "update_trigger",
            "interface_schemas": {"body": _authored_schemas(updated=True)["body"]},
        }
    ]
    prepared = await plan(settings, flow, edits)
    planned = prepared["payload"]
    _assert_authored_schema(planned, updated=True)
    assert _declaration(planned, "query_parameters") == _declaration(
        original, "query_parameters"
    )
    assert _declaration(planned, "body")["id"] == _declaration(original, "body")["id"]
    assert (
        _declaration(planned, "body")["fixture_declaration_metadata"]
        == _declaration(original, "body")["fixture_declaration_metadata"]
    )
    assert _body_message(planned)["uiUniqueId"] == _body_message(original)["uiUniqueId"]
    assert _body_message(planned)["fixture_declaration_extension"] == {
        "preserve": ["native", None]
    }
    assert input_row(planned, "fixture_locked") == input_row(original, "fixture_locked")
    assert input_row(planned, "fixture_opaque") == input_row(original, "fixture_opaque")
    assert any(row["name"] == "body.ticket.count" for row in prepared["created_fields"])
    actual = copy.deepcopy(planned)
    if defect == "nested_metadata":
        _body_message(actual)["fixture_declaration_extension"]["preserve"] = ["changed"]
    elif defect == "nested_value":
        _body_message(actual)["value"] = "An invented request body value"

    def save(request):
        body = json.loads(request.content)
        _assert_authored_schema(body, updated=True)
        assert body["triggerInstances"][0]["id"] == SAVED
        assert _declaration(body, "query_parameters") == _declaration(
            original, "query_parameters"
        )
        assert _body_message(body)["fixture_declaration_extension"] == {
            "preserve": ["native", None]
        }
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert flow == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize("operation", ["set_trigger", "update_trigger"])
@pytest.mark.parametrize("lock", ["inherited", "readonly"])
async def test_saved_rest_schema_rejects_locked_native_roots_even_for_equal_schema(
    settings, operation, lock
):
    definition = _schema_definition()
    root = next(row for row in definition["inputs"] if row["name"] == "body")
    if lock == "inherited":
        root["attributes"]["fromTemplate"] = "true"
    else:
        root["readonly"] = True
    flow = _owned_flow(definition) if operation == "update_trigger" else base_flow()
    flow["version"] = "2"
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = {
        "op": operation,
        "interface_schemas": {"body": {"type": "object", "children": []}},
    }
    if operation == "set_trigger":
        edit["template_id"] = TEMPLATE
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert (
        "read-only" in result["error"]["message"]
        or "Inherited" in result["error"]["message"]
    )
    assert flow == original
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("direction", ["catalog_to_rest", "rest_to_catalog"])
async def test_saved_cross_family_replacement_cleans_only_proven_old_interface(
    settings, direction
):
    catalog = _catalog_definition()
    rest = _profile_definition("REST", "fixture_native_rest")
    before_definition, after_definition = (
        (catalog, rest) if direction == "catalog_to_rest" else (rest, catalog)
    )
    after_definition["id"] = "9" * 32
    flow = (
        _catalog_flow(before_definition)
        if direction == "catalog_to_rest"
        else _owned_flow(before_definition)
    )
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, before_definition)
    _additional_saved_routes(after_definition)
    if direction == "catalog_to_rest":
        _owner_records(flow["inputs"])
    edits = [
        {"op": "set_trigger", "template_id": after_definition["id"], "replace": True}
    ]
    prepared = await plan(settings, flow, edits)
    planned = prepared["payload"]
    assert (
        planned["triggerInstances"][0]["triggerDefinitionId"]
        == after_definition["id"]
        != TEMPLATE
    )
    assert planned["deletedTriggers"] == [{"id": SAVED}]
    removed = {
        row["id"] for row in prepared["removed_fields"] if row["collection"] == "inputs"
    }
    assert {row["id"] for row in original["inputs"]} <= removed
    actual = copy.deepcopy(planned)
    actual["triggerInstances"][0]["id"] = "6" * 32
    if direction == "catalog_to_rest":
        _assert_interface(planned, after_definition)
        assert prepared["generated_trigger_inputs"] is None
        owner_reads = [
            call.request
            for call in http_mock.calls
            if "/table/sys_hub_flow_input/" in call.request.url.path
        ]
        assert owner_reads
        assert all(
            "sys_mod_count" in request.url.params["sysparm_fields"].split(",")
            for request in owner_reads
        )
    else:
        assert planned["inputs"] == []
        assert prepared["generated_trigger_inputs"] == {
            "definition_id": after_definition["id"],
            "declarations": _catalog_declarations(after_definition),
        }
        actual["inputs"] = _catalog_declarations(after_definition)
        for index, row in enumerate(actual["inputs"], start=901):
            row["id"] = f"{index:032x}"
        _owner_records(actual["inputs"])

    def save(request):
        body = json.loads(request.content)
        assert (
            body["triggerInstances"][0]["triggerDefinitionId"] == after_definition["id"]
        )
        assert body["deletedTriggers"] == [{"id": SAVED}]
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = "6" * 32
        if direction == "rest_to_catalog":
            assert body["inputs"] == []
            echo["inputs"] = copy.deepcopy(actual["inputs"])
        else:
            _assert_interface(body, after_definition)
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert flow == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize(
    "defect", ["owner", "revision", "extra_declaration", "consumer"]
)
async def test_saved_catalog_cutover_requires_independent_ownership_and_removed_consumers(
    settings, defect
):
    before_definition = _catalog_definition()
    after_definition = _profile_definition("REST", "fixture_native_rest")
    after_definition["id"] = "9" * 32
    flow = _catalog_flow(before_definition)
    overrides = (
        {"model": "f" * 32}
        if defect == "owner"
        else {"sys_mod_count": "unreadable"}
        if defect == "revision"
        else {}
    )
    _owner_records(flow["inputs"], overrides=overrides)
    if defect == "extra_declaration":
        flow["inputs"].append(
            {
                "id": "7" * 32,
                "name": "fixture_unrelated",
                "type": "string",
                "children": [],
            }
        )
    elif defect == "consumer":
        flow["actionInstances"][0]["inputs"][0]["value"] = "{{subflow.request_item}}"
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, before_definition)
    _additional_saved_routes(after_definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "template_id": after_definition["id"],
                    "replace": True,
                }
            ],
        )
    )
    assert result["status"] == "error", result
    if defect != "consumer":
        assert result["error"]["code"] == "UNAVAILABLE", result
    assert flow == original
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_inputless_sla_profile_keeps_native_class_marker_without_inventing_interface(
    settings,
):
    definition = _profile_definition("Application", "sla_task")
    definition["inputs"] = []
    definition["recordClassName"] = TABLE
    definition["outputs"] = [
        copy.deepcopy(row["parameter"])
        for row in application_definition("sla_task")["outputs"]
    ]
    original = copy.deepcopy(definition)
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edits = [{"op": "set_trigger", "template_id": TEMPLATE}]
    prepared = await plan(settings, flow, edits)
    planned = prepared["payload"]
    trigger = planned["triggerInstances"][0]
    assert trigger["triggerDefinitionId"] == TEMPLATE
    assert trigger["fromTemplate"] is False
    assert trigger["triggerTable"] == TABLE
    assert trigger["inputs"] == []
    assert {row["name"] for row in trigger["outputs"]} == {
        row["name"] for row in definition["outputs"]
    }
    assert planned["inputs"] == []
    assert prepared["generated_trigger_inputs"] is None
    assert prepared["created_fields"] == []
    actual = copy.deepcopy(planned)
    actual["triggerInstances"][0]["id"] = SAVED

    def save(request):
        body = json.loads(request.content)
        assert body["triggerInstances"][0]["fromTemplate"] is False
        assert body["triggerInstances"][0]["triggerTable"] == TABLE
        assert body["inputs"] == []
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert definition == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize(
    "inherited_default",
    ["", "/api/fixture/template"],
    ids=["empty_inherited_default", "nonempty_inherited_default"],
)
async def test_saved_rest_declared_path_roles_preserve_inherited_metadata_and_use_native_resolver(
    settings, inherited_default
):
    definition = _path_definition(inherited_default)
    original = copy.deepcopy(definition)
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    current = {"path": "/api/fixture/generated"}
    metadata = _rest_metadata_route(current, default=inherited_default)
    edit = _path_edit(definition)
    prepared = await plan(settings, flow, [edit])
    planned = prepared["payload"]
    for name, expected in (
        ("base_path", current["path"]),
        ("path", "/requests/{request_id}"),
    ):
        row = input_row(planned, name)
        raw = next(raw for raw in definition["inputs"] if raw["name"] == name)
        assert row["parameter"] == _source_parameter(raw)
        assert row["value"] == row["displayValue"] == expected
        assert row["templateValue"] == inherited_default
        assert row["fromTemplate"] is (inherited_default == "")
        assert row["parameter"]["readonly"] is False
        assert row["parameter"]["attributes"]["fromTemplate"] == "true"
        assert row["parameter"]["fixture_native_extension"] == {
            "preserve": [False, {"native": 0}]
        }
    assert input_row(planned, "display_path")["value"] == edit["inputs"]["display_path"]
    assert planned["inputs"] == planned["triggerInstances"][0]["outputs"]
    path_root = _declaration(planned, "path_parameters")
    assert path_root["children"][0]["name"] == "path_parameters.request_id"
    assert path_root["children"][0]["type"] == "integer"
    assert "value" not in path_root["children"][0]
    assert input_row(planned, "path_parameters")["parameter"]["children"] == []
    assert input_row(planned, "path_parameters")["children"] == path_root["children"]
    assert metadata.called
    _assert_metadata_reads_only()
    actual = copy.deepcopy(planned)
    actual["triggerInstances"][0]["id"] = SAVED

    def save(request):
        body = json.loads(request.content)
        assert input_row(body, "base_path")["value"] == current["path"]
        assert input_row(body, "path")["value"] == "/requests/{request_id}"
        assert (
            input_row(body, "display_path")["value"] == edit["inputs"]["display_path"]
        )
        assert input_row(body, "base_path")["templateValue"] == inherited_default
        assert input_row(body, "path")["templateValue"] == inherited_default
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    assert not write.called
    reviewed = preview["data"]["summary"]["triggers"][0]["inputs"]
    assert reviewed["base_path"] == current["path"]
    assert reviewed["path"] == "/requests/{request_id}"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert definition == original
    assert len(coordinator.store) == 0
    assert_independent_readback(write)


@pytest.mark.parametrize("operation", ["set_trigger", "update_trigger"])
@pytest.mark.parametrize("name", ["base_path", "path"])
async def test_saved_rest_derived_path_roles_reject_user_override_even_when_not_readonly(
    settings, operation, name
):
    definition = _path_definition()
    raw = next(row for row in definition["inputs"] if row["name"] == name)
    raw["attributes"].pop("fromTemplate")
    assert raw["readonly"] is False
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    _rest_metadata_route({"path": "/api/fixture/generated"})
    if operation == "update_trigger":
        flow = copy.deepcopy(
            (await plan(settings, flow, [_path_edit(definition)]))["payload"]
        )
        flow["triggerInstances"][0]["id"] = SAVED
        coordinator, tools, _ = saved_routes(settings, flow, definition)
    original = copy.deepcopy(flow)
    edit = {"op": operation, "inputs": {name: "/caller_override"}}
    if operation == "set_trigger":
        edit["template_id"] = TEMPLATE
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert flow == original
    assert len(coordinator.store) == 0
    _assert_metadata_reads_only()


@pytest.mark.parametrize("defect", ["foreign", "undeclared", "wrong_root"])
async def test_saved_rest_display_path_accepts_only_own_declared_parameter_pills(
    settings, defect
):
    definition = _path_definition()
    prefix = definition["name"] + "_1."
    path = {
        "foreign": "Other trigger_1.path_parameters.request_id",
        "undeclared": prefix + "path_parameters.missing",
        "wrong_root": prefix + "body.message",
    }[defect]
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    _rest_metadata_route({"path": "/api/fixture/generated"})
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[_path_edit(definition, display="/requests/{{" + path + "}}")],
        )
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert len(coordinator.store) == 0
    _assert_metadata_reads_only()


async def test_saved_rest_resolver_change_invalidates_apply_before_any_put(settings):
    definition = _path_definition()
    flow = base_flow()
    flow["version"] = "2"
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    current = {"path": "/api/fixture/generated"}
    metadata = _rest_metadata_route(current)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[_path_edit(definition)],
        )
    )
    assert preview["status"] == "success", preview
    before_apply = metadata.call_count
    current["path"] = "/api/fixture/changed"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert metadata.call_count > before_apply
    assert flow == original
    assert len(coordinator.store) == 0
    _assert_metadata_reads_only()


@pytest.mark.parametrize("operation", ["set_trigger", "update_trigger"])
@pytest.mark.parametrize(
    "schemas",
    [None, {}, [], "not a schema mapping"],
    ids=["null", "empty_mapping", "array", "string"],
)
async def test_saved_rest_explicit_interface_schemas_requires_nonempty_mapping(
    settings, operation, schemas
):
    definition = _schema_definition()
    flow = _owned_flow(definition) if operation == "update_trigger" else base_flow()
    flow["version"] = "2"
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = {"op": operation, "interface_schemas": copy.deepcopy(schemas)}
    if operation == "set_trigger":
        edit["template_id"] = TEMPLATE
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert "preview_token" not in (result.get("data") or {})
    assert len(coordinator.store) == 0
    assert flow == original
    assert_readonly()


async def test_saved_rest_explicit_empty_root_schema_clears_declared_children(settings):
    definition = _schema_definition()
    empty = base_flow()
    empty["version"] = "2"
    saved_routes(settings, empty, definition)
    initial = await plan(
        settings,
        empty,
        [
            {
                "op": "set_trigger",
                "template_id": TEMPLATE,
                "interface_schemas": _authored_schemas(),
            }
        ],
    )
    flow = copy.deepcopy(initial["payload"])
    flow["triggerInstances"][0]["id"] = SAVED
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edits = [
        {
            "op": "update_trigger",
            "interface_schemas": {"body": {"type": "object", "children": []}},
        }
    ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["preview_token"]
    assert len(coordinator.store) == 1
    planned = (await plan(settings, flow, edits))["payload"]
    assert _declaration(original, "body")["children"]
    assert input_row(planned, "body")["children"] == []
    assert _declaration(planned, "body")["children"] == []
    assert _declaration(planned, "body")["parameter"]["children"] == []
    assert _declaration(planned, "query_parameters") == _declaration(
        original, "query_parameters"
    )
    assert flow == original
    assert_readonly()
