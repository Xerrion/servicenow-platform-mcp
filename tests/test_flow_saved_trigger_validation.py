"""Read-only saved-trigger validation binds native picker and stored dependencies."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import lifecycle
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.test_flow_editing import BASE, DEFS
from tests.test_flow_lifecycle import validation_flow, validation_routes
from tests.test_flow_saved_triggers import (
    CALENDAR,
    PICKER_PATH,
    SAVED,
    TEMPLATE,
    TEMPLATE_PATH,
    input_row,
    native_instance,
    saved_detail,
    saved_routes,
)


def _serialized(*values):
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture(autouse=True)
def shared_definitions_are_not_mutated():
    before = _serialized(DEFS)
    yield
    assert _serialized(DEFS) == before


def _saved_validation(settings, definition):
    flow = validation_flow()
    flow["version"] = "2"
    trigger = native_instance(definition)
    trigger["id"] = SAVED
    trigger["fixture_native_metadata"] = {"preserve": ["unchanged"]}
    flow["triggerInstances"] = [trigger]
    validation_routes(flow)
    _, _, template = saved_routes(settings, flow, definition)
    return flow, template


async def _validate(settings, flow, definition, template, *references):
    before = _serialized(flow, definition, template, *references)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(flow, client)
    assert _serialized(flow, definition, template, *references) == before
    assert not http_mock.unexpected
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result


def _assert_complete(result):
    assert result["valid"] is True, result
    assert result["complete"] is True, result
    assert result["diagnostics"] == [], result
    assert result["dependency_revision"]


def _assert_incomplete(result):
    assert result["valid"] is None, result
    assert result["complete"] is False, result
    assert any(issue["incomplete"] for issue in result["diagnostics"]), result


def _calendar_route(current):
    def calendar(request):
        assert request.url.params["sysparm_query"] == f"sys_id={CALENDAR}^active=true"
        assert set(request.url.params["sysparm_fields"].split(",")) == {
            "sys_id",
            "sys_mod_count",
        }
        assert request.url.params["sysparm_limit"] == "2"
        return httpx2.Response(200, json={"result": copy.deepcopy(current)})

    return http_mock.get(f"{BASE}/api/now/table/cmn_schedule").mock(
        side_effect=calendar
    )


@pytest.mark.parametrize("kind", ["daily", "fixture_saved_family"])
async def test_saved_native_metadata_bypasses_classic_family_catalog(settings, kind):
    definition = saved_detail()
    definition["type"] = kind
    flow, template = _saved_validation(settings, definition)
    assert all("parameter" not in row for row in definition["inputs"])
    assert flow["triggerInstances"][0]["triggerDefinitionId"] == TEMPLATE

    result = await _validate(settings, flow, definition, template)

    _assert_complete(result)
    paths = {call.request.url.path for call in http_mock.calls}
    assert PICKER_PATH in paths
    assert TEMPLATE_PATH in paths
    assert "/api/now/trigger_api/trigger_categories" not in paths


@pytest.mark.parametrize("version", [None, "1"])
async def test_saved_validation_requires_native_version_two(settings, version):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    if version is None:
        flow.pop("version")
    else:
        flow["version"] = version

    result = await _validate(settings, flow, definition, template)

    _assert_incomplete(result)


@pytest.mark.parametrize(
    "name,value",
    [
        ("fixture_timezone", ""),
        ("fixture_timezone", "Europe/copenhagen"),
        ("fixture_time", "2035-01-01 23:45:30"),
        ("fixture_locked", "replacement inherited value"),
        ("fixture_opaque", {"opaque": ["changed"]}),
    ],
)
async def test_flat_saved_schema_constraints_are_not_skipped(settings, name, value):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    input_row(flow, name)["value"] = value

    result = await _validate(settings, flow, definition, template)

    _assert_incomplete(result)


async def test_saved_validation_preserves_inherited_opaque_values_and_metadata(
    settings,
):
    definition = saved_detail()
    opaque = next(
        row for row in definition["inputs"] if row["name"] == "fixture_opaque"
    )
    opaque["defaultValue"] = {
        "opaque": ["æøå", {"native": True, "empty": [], "zero": 0}],
    }
    opaque["fixture_native_extension"] = {"preserve": [None, False, "\\native"]}
    definition["templateProperties"]["fixture_opaque_property"] = {
        "preserve": [0, False, None, {"text": " æøå\n "}],
    }
    definition["triggerValues"].append({"fixture_native_blob": [False, {"zero": 0}]})
    flow, template = _saved_validation(settings, definition)
    flow["triggerInstances"][0]["fixture_native_metadata"] = {
        "preserve": [None, {"unknown": " æøå\n "}],
    }
    inherited = input_row(flow, "fixture_opaque")
    inherited["fixture_instance_metadata"] = {"opaque": [False, 0, None]}
    before = _serialized(inherited, flow["triggerInstances"][0])

    result = await _validate(settings, flow, definition, template)

    _assert_complete(result)
    assert _serialized(inherited, flow["triggerInstances"][0]) == before


@pytest.mark.parametrize(
    "defect",
    [
        "trigger_definition",
        "trigger_values",
        "template_properties",
        "template_unreadable",
        "picker_unreadable",
        "picker_identity",
        "wrapped_picker_inputs",
        "picker_schema_drift",
    ],
)
async def test_saved_validation_cannot_certify_mismatched_or_unreadable_schema(
    settings, defect
):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    if defect == "trigger_definition":
        template[defect] = "a" * 32
    elif defect == "trigger_values":
        template[defect] = json.dumps([{"different": True}])
    elif defect == "template_properties":
        template[defect] = json.dumps({"different": True})
    elif defect == "template_unreadable":
        http_mock.get(f"{BASE}{TEMPLATE_PATH}").respond(403, json={"error": {}})
    elif defect == "picker_unreadable":
        http_mock.get(f"{BASE}{PICKER_PATH}").respond(403, json={"error": {}})
    elif defect == "picker_identity":
        definition["id"] = "a" * 32
    elif defect == "wrapped_picker_inputs":
        definition["inputs"] = [
            {"name": row["name"], "parameter": row} for row in definition["inputs"]
        ]
    else:
        definition["inputs"][0]["fixture_new_constraint"] = "changed"

    result = await _validate(settings, flow, definition, template)

    _assert_incomplete(result)


@pytest.mark.parametrize("revision", [None, ""])
async def test_saved_validation_requires_independent_template_revision(
    settings, revision
):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    if revision is None:
        template.pop("sys_mod_count")
    else:
        template["sys_mod_count"] = revision

    result = await _validate(settings, flow, definition, template)

    _assert_incomplete(result)


@pytest.mark.parametrize("revision_field", ["sys_mod_count", "sys_updated_on"])
async def test_dependency_revision_binds_independently_read_template_revision(
    settings, revision_field
):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    original_inputs = _serialized(flow, definition)
    first = await _validate(settings, flow, definition, template)
    unchanged = await _validate(settings, flow, definition, template)
    _assert_complete(first)
    _assert_complete(unchanged)
    assert first["dependency_revision"] == unchanged["dependency_revision"]

    template[revision_field] = (
        "5" if revision_field == "sys_mod_count" else "2026-10-20 12:00:01"
    )
    changed = await _validate(settings, flow, definition, template)

    _assert_complete(changed)
    assert changed["dependency_revision"] != first["dependency_revision"]
    assert _serialized(flow, definition) == original_inputs
    assert sum(call.request.url.path == TEMPLATE_PATH for call in http_mock.calls) >= 3


async def test_dependency_revision_binds_complete_native_picker_metadata(settings):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    independent_inputs = _serialized(flow, template)
    first = await _validate(settings, flow, definition, template)
    _assert_complete(first)

    definition["fixture_dependency_metadata"] = {"native": ["changed"]}
    changed = await _validate(settings, flow, definition, template)

    _assert_complete(changed)
    assert changed["dependency_revision"] != first["dependency_revision"]
    assert _serialized(flow, template) == independent_inputs


async def test_dependency_revision_binds_current_qualified_saved_reference(settings):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    input_row(flow, "fixture_calendar")["value"] = CALENDAR
    current = [{"sys_id": CALENDAR, "sys_mod_count": "3"}]
    reference = _calendar_route(current)
    independent_inputs = _serialized(flow, definition, template)
    first = await _validate(settings, flow, definition, template, current)
    unchanged = await _validate(settings, flow, definition, template, current)
    _assert_complete(first)
    _assert_complete(unchanged)
    assert unchanged["dependency_revision"] == first["dependency_revision"]
    before_drift = reference.call_count

    current[0]["sys_mod_count"] = "4"
    changed = await _validate(settings, flow, definition, template, current)

    _assert_complete(changed)
    assert changed["dependency_revision"] != first["dependency_revision"]
    assert reference.call_count > before_drift
    assert _serialized(flow, definition, template) == independent_inputs


@pytest.mark.parametrize(
    "defect",
    ["absent", "wrong_identity", "unreadable", "missing_revision", "blank_revision"],
)
async def test_saved_validation_requires_current_reference_identity_and_revision(
    settings, defect
):
    definition = saved_detail()
    flow, template = _saved_validation(settings, definition)
    input_row(flow, "fixture_calendar")["value"] = CALENDAR
    current = [{"sys_id": CALENDAR, "sys_mod_count": "3"}]
    reference = _calendar_route(current)
    if defect == "absent":
        current.clear()
    elif defect == "wrong_identity":
        current[0]["sys_id"] = "a" * 32
    elif defect == "unreadable":
        reference.respond(403, json={"error": {}})
    elif defect == "missing_revision":
        current[0].pop("sys_mod_count")
    else:
        current[0]["sys_mod_count"] = ""

    result = await _validate(settings, flow, definition, template, current)

    assert reference.called
    _assert_incomplete(result)
