"""Source-derived saved picker contracts, not captured or certified live saves.

The conditional timezone/calendar fixture declares native metadata explicitly. It
makes no claim about concrete recurrence wire fields on a ServiceNow instance.
Constructor/bindings: Studio 27.0.13 source 227216-227329; inherited read-only
rule: 336955. Every fixture copies the shared classic catalog before adapting it.
"""

import copy
import json
from typing import Any

import httpx2
import pytest

from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, persist
from tests.test_flow_fields import plan
from tests.test_flow_trigger_authoring import routes

TEMPLATE = "c" * 32
UNDERLYING = "e" * 32
SAVED = "d" * 32
CALENDAR = "b" * 32
TIMEZONE = "Europe/Copenhagen"
NATIVE_TIME = "1970-01-01 23:45:30"
TABLE = "sys_hub_trigger_template"
PICKER_PATH = f"/api/now/hub/triggerpicker/{TEMPLATE}"
TEMPLATE_PATH = f"/api/now/table/{TABLE}/{TEMPLATE}"


def saved_detail() -> dict[str, Any]:
    catalog = copy.deepcopy(DEFS["trigger_categories"])
    original = next(
        trigger
        for category in catalog
        for trigger in category["triggers"].values()
        if trigger["type"] == "service_catalog"
    )
    prototype = original["inputs"][0]["parameter"]

    def parameter(name, kind, index, default, **metadata):
        row = copy.deepcopy(prototype)
        row.update(
            id=f"{index:032x}",
            name=name,
            label=name,
            columnName=name,
            type=kind,
            type_label=kind,
            order=index,
            defaultValue=copy.deepcopy(default),
            attributes={},
            choices=[],
            defaultChoices=[],
            choiceOption="",
            children=[],
            mandatory=False,
            readonly=False,
            reference="",
            table="",
            ref_qual="",
        )
        row.update(metadata)
        return row

    locked = parameter(
        "fixture_locked",
        "string",
        101,
        "native inherited value",
        attributes={"fromTemplate": "true", "fixture_passthrough": "unchanged"},
    )
    opaque = parameter(
        "fixture_opaque",
        "fixture_native_opaque",
        102,
        {"opaque": ["preserve", {"native": True}]},
        attributes={"fromTemplate": "true"},
    )
    timezone = parameter(
        "fixture_timezone",
        "choice",
        103,
        TIMEZONE,
        mandatory=True,
        choices=[
            {"label": "Conditional Copenhagen fixture", "value": TIMEZONE},
            {"label": "Conditional UTC fixture", "value": "UTC"},
        ],
    )
    calendar = parameter(
        "fixture_calendar",
        "reference",
        104,
        "",
        reference="cmn_schedule",
        ref_qual="active=true",
    )
    clock = parameter("fixture_time", "glide_time", 105, NATIVE_TIME)
    output = copy.deepcopy(
        next(
            row["parameter"]
            for row in original["outputs"]
            if row["name"] == "run_start_time"
        )
    )
    return {
        "id": TEMPLATE,
        "type": "daily",
        "name": "Conditional saved recurrence fixture",
        "label": "Picker label differs from native name",
        "category": "Date",
        "order": 1,
        "scopeName": "global",
        "scopeLabel": "Global",
        "scopeLogo": "fixture-logo",
        "description": "Conditional metadata fixture, not a recurrence schema",
        "publishedTriggerId": "f" * 32,
        "hasDynamicOutputs": False,
        "annotation": "Preserve the native annotation",
        "triggerDefinition": UNDERLYING,
        "triggerValues": [{"fixture_native_value": "unchanged"}],
        "templateProperties": {
            "showDefinitionConditions": False,
            "fixture_opaque_property": {"preserve": [1, "two"]},
        },
        # Deliberately unsorted: the source constructor sorts native order.
        "inputs": [clock, opaque, calendar, locked, timezone],
        "outputs": [output],
    }


def template_row(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "sys_id": TEMPLATE,
        "sys_class_name": TABLE,
        "trigger_definition": {"value": UNDERLYING, "link": "fixture-link"},
        "trigger_values": json.dumps(definition["triggerValues"]),
        "template_properties": json.dumps(definition["templateProperties"]),
        "sys_updated_on": "2026-10-20 12:00:00",
        "sys_mod_count": "4",
    }


def native_instance(definition: dict[str, Any]) -> dict[str, Any]:
    """Independent fixture projection of the source constructor/reducer."""

    def bindings(collection):
        result: list[dict[str, Any]] = []
        for raw in definition[collection]:
            parameter = copy.deepcopy(raw)
            parameter["uiUniqueId"] = raw["id"]
            default = raw.get("defaultValue")
            row: dict[str, Any] = {
                "name": raw["name"],
                "label": raw["label"],
                "internalType": raw["type"],
                "type": raw["type"],
                "value": (
                    "" if default in (None, False, 0, "") else copy.deepcopy(default)
                ),
                "displayValue": "",
                "valueSysId": "",
                "order": raw["order"],
                "mandatory": raw["mandatory"],
                "parameter": parameter,
                "uiUniqueId": raw["id"],
                "children": [],
            }
            if raw["type"] == "choice":
                row["choiceList"] = copy.deepcopy(raw["choices"])
            if collection == "inputs" and raw["attributes"].get("fromTemplate"):
                row["templateValue"] = copy.deepcopy(raw["defaultValue"])
                row["fromTemplate"] = True
            result.append(row)
        return sorted(result, key=lambda row: int(row["order"]))

    name = definition.get("name") or definition["label"]
    result = {
        "name": name,
        "label": name,
        "type": definition["type"],
        "triggerType": definition["category"],
        "order": definition["order"],
        "scopeName": definition["scopeName"],
        "scopeLabel": definition["scopeLabel"],
        "scopeLogo": definition["scopeLogo"],
        "description": definition["description"],
        "triggerDefinitionId": definition["id"],
        "publishedVersion": definition["publishedTriggerId"],
        "hasDynamicOutputs": definition["hasDynamicOutputs"],
        "comment": definition["annotation"],
        "templateProperties": copy.deepcopy(definition["templateProperties"]),
        "fromTemplate": True,
        "inputs": bindings("inputs"),
        "outputs": bindings("outputs"),
        "metadata": {"query_parse": {"predicates": []}},
        "additionalMetadata": {"query_parse": {"predicates": []}},
    }
    if "recordClassName" in definition:
        result["triggerTable"] = definition["recordClassName"]
    return result


def saved_flow(definition: dict[str, Any]):
    flow = base_flow()
    flow["version"] = "2"
    instance = native_instance(definition)
    instance["id"] = SAVED
    instance["fixture_native_metadata"] = {"preserve": ["unchanged"]}
    flow["triggerInstances"] = [instance]
    return flow


def saved_routes(settings, flow, definition):
    coordinator, tools = routes(settings, flow)
    row = template_row(definition)
    http_mock.get(f"{BASE}{PICKER_PATH}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(definition)}}
        )
    )
    http_mock.get(f"{BASE}{TEMPLATE_PATH}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": copy.deepcopy(row)})
    )
    source = {
        "sys_id": definition["triggerDefinition"],
        "sys_mod_count": "6",
        "sys_updated_on": "2026-10-20 11:00:00",
    }
    http_mock.get(
        f"{BASE}/api/now/table/sys_hub_trigger_definition/{definition['triggerDefinition']}"
    ).respond(200, json={"result": source})
    return coordinator, tools, row


def input_row(flow, name):
    return next(
        row for row in flow["triggerInstances"][0]["inputs"] if row["name"] == name
    )


def assert_readonly():
    assert all(call.request.method == "GET" for call in http_mock.calls)


def assert_independent_readback(write):
    assert write.call_count == 1
    write_index = next(
        index
        for index, call in enumerate(http_mock.calls)
        if call.request.method == "PUT"
    )
    assert any(
        call.request.method == "GET"
        and call.request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for call in http_mock.calls[write_index + 1 :]
    )


@pytest.mark.parametrize(
    "trigger,expected",
    [
        ({"fromTemplate": True}, True),
        ({"triggerTable": TABLE}, True),
        ({"inputs": [{"parameter": {"attributes": {"fromTemplate": "true"}}}]}, True),
        ({"inputs": [{"parameter": {"attributes": {"fromTemplate": True}}}]}, False),
        ({"templateProperties": {"fixture": True}}, False),
    ],
)
def test_saved_template_detection_uses_native_capability_markers(trigger, expected):
    from servicenow_mcp.capabilities.flows.saved_triggers import is_saved

    assert is_saved(trigger) is expected


@pytest.mark.parametrize("record_class", [False, True])
@pytest.mark.parametrize("label_only", [False, True])
@pytest.mark.parametrize(
    "opaque_default",
    [{}, [], {"native": "preserve"}],
    ids=["empty_object", "empty_array", "object"],
)
async def test_saved_template_constructor_preserves_native_metadata_and_defaults(
    settings, record_class, label_only, opaque_default
):
    original_catalog = copy.deepcopy(DEFS)
    definition = saved_detail()
    opaque_parameter = next(
        row for row in definition["inputs"] if row["name"] == "fixture_opaque"
    )
    opaque_parameter["defaultValue"] = copy.deepcopy(opaque_default)
    if label_only:
        definition.pop("name")
    if record_class:
        definition["recordClassName"] = TABLE
    original = copy.deepcopy(definition)
    flow = base_flow()
    flow["version"] = "2"
    _, tools, _ = saved_routes(settings, flow, definition)
    edit = {"op": "set_trigger", "template_id": TEMPLATE}
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    planned = await plan(settings, flow, [edit])
    trigger = planned["payload"]["triggerInstances"][0]
    expected = native_instance(definition)
    for key, value in expected.items():
        if key not in {"inputs", "outputs"}:
            assert trigger[key] == value
    for collection in ("inputs", "outputs"):
        assert len(trigger[collection]) == len(expected[collection])
        for actual_row, expected_row in zip(
            trigger[collection], expected[collection], strict=True
        ):
            for key, value in expected_row.items():
                assert actual_row[key] == value
    assert trigger["triggerDefinitionId"] == TEMPLATE != UNDERLYING
    assert (
        trigger["name"]
        == trigger["label"]
        == (definition.get("name") or definition["label"])
    )
    assert ("triggerTable" in trigger) is record_class
    locked = input_row(planned["payload"], "fixture_locked")
    assert locked["parameter"]["readonly"] is False
    assert locked["parameter"]["attributes"]["fromTemplate"] == "true"
    assert locked["fromTemplate"] is True
    assert locked["templateValue"] == locked["value"] == "native inherited value"
    opaque = input_row(planned["payload"], "fixture_opaque")
    assert opaque["value"] == opaque["templateValue"] == opaque_default
    assert opaque["parameter"]["defaultValue"] == opaque_default
    assert type(opaque["value"]) is type(opaque_default)
    assert input_row(planned["payload"], "fixture_timezone")["value"] == TIMEZONE
    assert input_row(planned["payload"], "fixture_time")["value"] == NATIVE_TIME
    assert definition == original
    assert DEFS == original_catalog
    template_reads = [
        call.request
        for call in http_mock.calls
        if call.request.url.path == TEMPLATE_PATH
    ]
    assert template_reads
    assert set(template_reads[0].url.params["sysparm_fields"].split(",")) == {
        "sys_id",
        "sys_class_name",
        "trigger_definition",
        "trigger_values",
        "template_properties",
        "sys_updated_on",
        "sys_mod_count",
    }
    assert_readonly()


@pytest.mark.parametrize("version", [None, "1"])
@pytest.mark.parametrize(
    "operation", ["set_trigger", "update_trigger", "remove_trigger"]
)
async def test_saved_template_requires_native_version_two_capability(
    settings, version, operation
):
    definition = saved_detail()
    flow = saved_flow(definition)
    if operation == "set_trigger":
        flow["triggerInstances"] = []
    if version is None:
        flow.pop("version")
    else:
        flow["version"] = version
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = (
        {"op": operation, "template_id": TEMPLATE}
        if operation == "set_trigger"
        else {"op": operation, "inputs": {"fixture_timezone": "UTC"}}
        if operation == "update_trigger"
        else {"op": operation}
    )
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_template_explicit_type_must_match_native_picker(settings):
    definition = saved_detail()
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "template_id": TEMPLATE, "type": "weekly"}],
        )
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("operation", ["set_trigger", "update_trigger"])
@pytest.mark.parametrize("override", ["changed", "native inherited value"])
async def test_saved_template_inherited_attribute_rejects_even_equal_overrides(
    settings, operation, override
):
    definition = saved_detail()
    flow = saved_flow(definition)
    if operation == "set_trigger":
        flow["triggerInstances"] = []
    original = copy.deepcopy(flow)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = {"op": operation, "inputs": {"fixture_locked": override}}
    if operation == "set_trigger":
        edit["template_id"] = TEMPLATE
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert "read-only" in result["error"]["message"]
    assert flow == original
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("choice", ["Copenhagen", "Europe/copenhagen", "+01:00"])
async def test_saved_template_timezone_accepts_only_exact_native_choice_values(
    settings, choice
):
    definition = saved_detail()
    flow = saved_flow(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "update_trigger", "inputs": {"fixture_timezone": choice}}],
        )
    )
    assert result["error"]["code"] == "INVALID_REQUEST", result
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_template_historical_choice_default_preserves_native_declaration(
    settings,
):
    definition = saved_detail()
    parameter = next(
        row for row in definition["inputs"] if row["name"] == "fixture_timezone"
    )
    parameter.update(
        defaultValue="historical/source",
        defaultChoices=[{"label": "Historical picker facet", "value": "legacy/facet"}],
        table="fixture_native_table",
        columnName="fixture_timezone",
        dependent_on="fixture_native_dependency",
    )
    original = copy.deepcopy(parameter)
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = {"op": "set_trigger", "template_id": TEMPLATE}
    attached = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert attached["status"] == "success", attached
    planned = await plan(settings, flow, [edit])
    binding = input_row(planned["payload"], "fixture_timezone")
    assert binding["value"] == "historical/source"
    assert binding["choiceList"] == original["choices"]
    assert binding["parameter"] == {**original, "uiUniqueId": original["id"]}
    flow = saved_flow(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    rejected = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_trigger",
                    "inputs": {"fixture_timezone": "historical/source"},
                }
            ],
        )
    )
    assert rejected["error"]["code"] == "INVALID_REQUEST", rejected
    assert len(coordinator.store) == 0
    accepted = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "update_trigger", "inputs": {"fixture_timezone": "UTC"}}],
        )
    )
    assert accepted["status"] == "success", accepted
    assert parameter == original
    assert_readonly()


async def test_saved_template_attachment_uses_independent_native_readback(settings):
    definition = saved_detail()
    flow = base_flow()
    flow["version"] = "2"
    _, tools, _ = saved_routes(settings, flow, definition)
    edit = {"op": "set_trigger", "template_id": TEMPLATE, "type": "daily"}
    prepared = await plan(settings, flow, [edit])
    actual = copy.deepcopy(prepared["payload"])
    actual["triggerInstances"][0]["id"] = SAVED

    def save(request):
        body = json.loads(request.content)
        assert body["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE
        assert input_row(body, "fixture_timezone")["value"] == TIMEZONE
        assert input_row(body, "fixture_time")["value"] == NATIVE_TIME
        echo = copy.deepcopy(body)
        echo["triggerInstances"][0]["id"] = SAVED
        # This getter snapshot was prepared before PUT, never from its echo.
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[edit],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert_independent_readback(write)


@pytest.mark.parametrize(
    "defect", [None, "unknown_metadata", "parameter_metadata", "value"]
)
async def test_saved_template_update_preserves_values_and_checks_independent_readback(
    settings, defect
):
    definition = saved_detail()
    flow = saved_flow(definition)
    _, tools, _ = saved_routes(settings, flow, definition)
    actual = copy.deepcopy(flow)
    input_row(actual, "fixture_timezone")["value"] = "UTC"
    input_row(actual, "fixture_time").update(
        value="1970-01-01 00:15:30", displayValue="1970-01-01 00:15:30"
    )
    if defect == "unknown_metadata":
        actual["triggerInstances"][0]["fixture_native_metadata"]["preserve"] = [
            "changed"
        ]
    elif defect == "parameter_metadata":
        input_row(actual, "fixture_locked")["parameter"]["fixture_unknown"] = "changed"
    elif defect == "value":
        input_row(actual, "fixture_timezone")["value"] = TIMEZONE

    def save(request):
        body = json.loads(request.content)
        trigger = body["triggerInstances"][0]
        assert trigger["id"] == SAVED
        assert trigger["fixture_native_metadata"] == {"preserve": ["unchanged"]}
        assert input_row(body, "fixture_timezone")["value"] == "UTC"
        assert input_row(body, "fixture_time")["value"] == "1970-01-01 00:15:30"
        assert input_row(body, "fixture_opaque") == input_row(flow, "fixture_opaque")
        persist(copy.deepcopy(actual))
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
                    "op": "update_trigger",
                    "inputs": {
                        "fixture_timezone": "UTC",
                        "fixture_time": "1970-01-01 00:15:30",
                    },
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    else:
        assert result["status"] == "success", result
    assert_independent_readback(write)


@pytest.mark.parametrize("identity", [CALENDAR, "a" * 32, None])
async def test_saved_template_calendar_uses_declared_table_qualifier_and_current_identity(
    settings, identity
):
    definition = saved_detail()
    flow = saved_flow(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)

    def calendar(request):
        assert request.url.params["sysparm_query"] == f"sys_id={CALENDAR}^active=true"
        assert set(request.url.params["sysparm_fields"].split(",")) == {
            "sys_id",
            "sys_mod_count",
        }
        assert request.url.params["sysparm_limit"] == "2"
        rows = [] if identity is None else [{"sys_id": identity, "sys_mod_count": "3"}]
        return httpx2.Response(200, json={"result": rows})

    reference = http_mock.get(f"{BASE}/api/now/table/cmn_schedule").mock(
        side_effect=calendar
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "update_trigger", "inputs": {"fixture_calendar": CALENDAR}}],
        )
    )
    assert reference.called
    if identity == CALENDAR:
        assert result["status"] == "success", result
        assert len(coordinator.store) == 1
    else:
        assert result["error"]["code"] == "INVALID_REQUEST", result
        assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_template_preview_rejects_missing_reference_revision(settings):
    definition = saved_detail()
    flow = saved_flow(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    reference = http_mock.get(f"{BASE}/api/now/table/cmn_schedule").respond(
        200, json={"result": [{"sys_id": CALENDAR}]}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_trigger",
                    "inputs": {"fixture_calendar": CALENDAR},
                }
            ],
        )
    )
    assert reference.called
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_template_calendar_revision_is_rechecked_at_apply(settings):
    definition = saved_detail()
    flow = saved_flow(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    current = {"sys_id": CALENDAR, "sys_mod_count": "3"}
    reference = http_mock.get(f"{BASE}/api/now/table/cmn_schedule").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": [copy.deepcopy(current)]}
        )
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_trigger",
                    "inputs": {"fixture_calendar": CALENDAR},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    before_apply = reference.call_count
    current["sys_mod_count"] = "4"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert reference.call_count > before_apply
    assert len(coordinator.store) == 0
    assert_readonly()


async def test_saved_template_writable_unsupported_type_is_not_reshaped(settings):
    definition = saved_detail()
    parameter = next(
        row for row in definition["inputs"] if row["name"] == "fixture_locked"
    )
    parameter.update(type="fixture_native_opaque", attributes={})
    flow = base_flow()
    flow["version"] = "2"
    original = copy.deepcopy(definition)
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "template_id": TEMPLATE,
                    "inputs": {"fixture_locked": "replacement scalar"},
                }
            ],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert definition == original
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize(
    "field", ["trigger_definition", "trigger_values", "template_properties"]
)
async def test_saved_template_preview_rejects_independent_storage_disagreement(
    settings, field
):
    definition = saved_detail()
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, row = saved_routes(settings, flow, definition)
    row[field] = (
        "a" * 32
        if field == "trigger_definition"
        else json.dumps([{"different": True}])
        if field == "trigger_values"
        else json.dumps({"different": True})
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "template_id": TEMPLATE}],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("field", ["sys_mod_count", "sys_updated_on"])
async def test_saved_template_preview_rejects_missing_template_revision(
    settings, field
):
    definition = saved_detail()
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, row = saved_routes(settings, flow, definition)
    row.pop(field)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "template_id": TEMPLATE}],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("field", ["sys_mod_count", "sys_updated_on"])
async def test_saved_template_preview_rejects_missing_source_definition_revision(
    settings, field
):
    definition = saved_detail()
    flow = base_flow()
    flow["version"] = "2"
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    source = {
        "sys_id": definition["triggerDefinition"],
        "sys_mod_count": "6",
        "sys_updated_on": "2026-10-20 11:00:00",
    }
    source.pop(field)
    http_mock.get(
        f"{BASE}/api/now/table/sys_hub_trigger_definition/{definition['triggerDefinition']}"
    ).respond(200, json={"result": source})
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "set_trigger", "template_id": TEMPLATE}],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize(
    "operation", ["set_trigger", "update_trigger", "remove_trigger"]
)
async def test_saved_template_apply_rechecks_underlying_source_definition_revision(
    settings, operation
):
    definition = saved_detail()
    flow = saved_flow(definition)
    if operation == "set_trigger":
        flow["triggerInstances"] = []
    coordinator, tools, _ = saved_routes(settings, flow, definition)
    edit = (
        {"op": operation, "template_id": TEMPLATE}
        if operation == "set_trigger"
        else {"op": operation, "inputs": {"fixture_timezone": "UTC"}}
        if operation == "update_trigger"
        else {"op": operation}
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    source_path = (
        f"/api/now/table/sys_hub_trigger_definition/{definition['triggerDefinition']}"
    )
    before_apply = len(http_mock.calls)
    source_read = http_mock.get(f"{BASE}{source_path}").respond(
        200,
        json={
            "result": {
                "sys_id": definition["triggerDefinition"],
                "sys_mod_count": "7",
                "sys_updated_on": "2026-10-20 11:00:01",
            }
        },
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert any(
        call.request.url.path == source_path for call in http_mock.calls[before_apply:]
    )
    assert set(
        source_read.calls.last.request.url.params["sysparm_fields"].split(",")
    ) == {
        "sys_id",
        "sys_mod_count",
        "sys_updated_on",
    }
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize(
    "operation", ["set_trigger", "update_trigger", "remove_trigger"]
)
@pytest.mark.parametrize(
    "drift", ["template_revision", "definition_schema", "template_properties"]
)
async def test_saved_template_apply_rechecks_template_and_native_schema_before_any_write(
    settings, operation, drift
):
    definition = saved_detail()
    flow = saved_flow(definition)
    if operation == "set_trigger":
        flow["triggerInstances"] = []
    coordinator, tools, row = saved_routes(settings, flow, definition)
    edit = (
        {"op": operation, "template_id": TEMPLATE}
        if operation == "set_trigger"
        else {"op": operation, "inputs": {"fixture_timezone": "UTC"}}
        if operation == "update_trigger"
        else {"op": operation}
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    before_apply = len(http_mock.calls)
    if drift == "template_revision":
        row.update(sys_updated_on="2026-10-20 12:00:01", sys_mod_count="5")
    elif drift == "definition_schema":
        definition["inputs"][0]["hint"] = "The native schema has changed"
    else:
        definition["templateProperties"]["fixture_opaque_property"] = {"changed": True}
        row["template_properties"] = json.dumps(definition["templateProperties"])
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if operation == "set_trigger" or drift == "template_revision":
        assert result["error"]["code"] == "STALE_PREVIEW", result
    else:
        # An incompatible current schema may fail validation before replan can
        # compare its revision. Neither failure permits a mutation.
        assert result["error"]["code"] in {"STALE_PREVIEW", "UNAVAILABLE"}, result
    apply_paths = {call.request.url.path for call in http_mock.calls[before_apply:]}
    assert PICKER_PATH in apply_paths
    assert TEMPLATE_PATH in apply_paths
    assert len(coordinator.store) == 0
    assert_readonly()


@pytest.mark.parametrize("surface", ["action", "default", "pharmacy", "script"])
async def test_saved_template_removal_rejects_surviving_native_dependencies(
    settings, surface
):
    definition = saved_detail()
    flow = saved_flow(definition)
    pill = "{{" + definition["name"] + "_1.run_start_time}}"
    if surface == "action":
        flow["actionInstances"][0]["inputs"][0]["value"] = pill
    elif surface == "default":
        flow["outputs"] = [
            {"name": "fixture_result", "type": "string", "defaultValue": pill}
        ]
    elif surface == "pharmacy":
        flow["pharmacy"] = {"prescriptions": [{"prescription": pill}]}
    else:
        flow["actionInstances"][0]["inputs"][0].update(
            scriptActive=True, script="return fd_data.trigger.run_start_time;"
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
    assert len(coordinator.store) == 0
    assert flow == original
    assert_readonly()


@pytest.mark.parametrize("retained", [False, True])
async def test_saved_template_removal_uses_saved_instance_deletion_manifest_and_readback(
    settings, retained
):
    definition = saved_detail()
    flow = saved_flow(definition)
    _, tools, _ = saved_routes(settings, flow, definition)
    actual = copy.deepcopy(flow)
    actual["triggerInstances"] = []
    if retained:
        actual["triggerInstances"] = copy.deepcopy(flow["triggerInstances"])

    def save(request):
        body = json.loads(request.content)
        assert body["triggerInstances"] == []
        assert body["deletedTriggers"] == [{"id": SAVED}]
        assert body["deletedTriggers"] != [{"id": TEMPLATE}]
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[{"op": "remove_trigger"}],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if retained:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    else:
        assert result["status"] == "success", result
    assert_independent_readback(write)
