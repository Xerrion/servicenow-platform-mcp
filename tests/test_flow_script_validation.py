"""Lifecycle validation consumes native scripts as static authoring text only."""

import copy
import json
from typing import Any

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import data, lifecycle, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.test_flow_editing import BASE, DEFS, LOG
from tests.test_flow_input_modes import INPUT, definition, mode_flow, routes, scripted
from tests.test_flow_lifecycle import validation_flow
from tests.test_flow_loopback_blocks import NATIVE, go_back, logic, native_flow


def _serialized(*values: Any) -> bytes:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture(autouse=True)
def shared_native_definitions_are_unchanged():
    before = _serialized(DEFS, NATIVE)
    yield
    assert _serialized(DEFS, NATIVE) == before


def _flow(declared: dict[str, Any]) -> dict[str, Any]:
    flow = mode_flow(copy.deepcopy(declared))
    flow["type"] = "subflow"
    return flow


async def _validate(settings, flow, *dependencies):
    before = _serialized(flow, *dependencies)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(flow, client)
    assert _serialized(flow, *dependencies) == before
    assert not http_mock.unexpected
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result


def _assert_valid(result):
    assert result["valid"] is True, result
    assert result["complete"] is True, result
    assert result["diagnostics"] == [], result
    assert result["dependency_revision"], result


def _assert_binding_rejected(result, *, uid="ui-2", collection="actionInstances"):
    assert result["valid"] is (False if result["complete"] else None), result
    issues = [
        issue
        for issue in result["diagnostics"]
        if issue["path"] == f"{collection}[{uid}].inputs"
        and issue["code"]
        in {"INVALID_BINDING", "UNAVAILABLE_BINDING", "MALFORMED_BINDING"}
    ]
    assert issues, result
    assert result["dependency_revision"], result
    return issues


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "return 'authored literal';",
        "throw new Error('must not execute');\nreturn fd_data._1__log.status;",
        "return fd_data /* native producer */ . _1__log . details.message;",
        "return fd_data._1__log.record.state;",
        (
            "// fd_data._99__missing.status\n"
            "const text = 'fd_data._99__missing.status';\n"
            "const pattern = /fd_data\\._99__missing\\.status/;\n"
            "return `raw fd_data._99__missing.status ${fd_data._1__log.status}`;"
        ),
    ],
)
async def test_supported_native_script_maps_validate_without_executing_sources(
    settings, source, mirror
):
    declared = definition()
    flow = _flow(declared)
    scripted(flow["actionInstances"][1]["inputs"][0], source, mirror=mirror)
    _, _, action_read = routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_valid(result)
    assert action_read.called
    if "record.state" in source:
        assert any(
            call.request.url.path == "/api/now/table/sys_dictionary"
            and "element=state" in call.request.url.params["sysparm_query"]
            and "sc_req_item" in call.request.url.params["sysparm_query"]
            for call in http_mock.calls
        )


@pytest.mark.parametrize("producer", ["trigger", "flow_var", "subflow_inputs"])
async def test_declared_native_root_references_use_lifecycle_resolver(
    settings, producer
):
    declared = definition()
    flow = _flow(declared)
    if producer == "trigger":
        flow["type"] = "flow"
        flow["triggerInstances"] = copy.deepcopy(validation_flow()["triggerInstances"])
        path = "request_item.state"
    else:
        collection = "flowVariables" if producer == "flow_var" else "inputs"
        flow[collection] = [
            {
                "id": "6" * 32,
                "name": "amount",
                "label": "Amount",
                "type": "integer",
                "children": [],
                "value": 0,
            }
        ]
        path = "amount"
    scripted(
        flow["actionInstances"][1]["inputs"][0], f"return fd_data.{producer}.{path};"
    )
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_valid(result)


@pytest.mark.parametrize("fallback", ["", None, "saved basic value"])
@pytest.mark.parametrize("active", [False, True])
async def test_required_basic_fallback_is_exempt_only_while_script_is_active(
    settings, fallback, active
):
    declared = definition(mandatory=True)
    flow = _flow(declared)
    row = flow["actionInstances"][1]["inputs"][0]
    row["value"] = fallback
    scripted(row, "throw new Error('authoring only');", active=active)
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    if active or fallback:
        _assert_valid(result)
    else:
        assert result["valid"] is False, result
        assert result["complete"] is True, result
        assert any(
            issue["path"] == "actionInstances[ui-2].inputs"
            and issue["code"] in {"INVALID_VALUE", "INVALID_BINDING"}
            for issue in result["diagnostics"]
        ), result


@pytest.mark.parametrize("fallback", [True, ["not a string"], "x" * 256])
async def test_active_script_does_not_exempt_nonblank_fallback_type_or_size(
    settings, fallback
):
    declared = definition(mandatory=True)
    flow = _flow(declared)
    row = flow["actionInstances"][1]["inputs"][0]
    row["value"] = fallback
    scripted(row, "return 'authored text';")
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_binding_rejected(result)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_map",
        "map_not_object",
        "entry_not_object",
        "source_not_text",
        "entry_flag_not_boolean",
        "show_flag_not_boolean",
        "parent_inactive",
        "entry_inactive",
        "malformed_mirror",
        "different_mirror",
        "nonobject_mirror",
    ],
)
async def test_malformed_saved_active_modes_and_mirrors_are_not_repaired(
    settings, defect
):
    declared = definition()
    flow = _flow(declared)
    row = flow["actionInstances"][1]["inputs"][0]
    scripted(row, "return 'saved source';")
    if defect == "missing_map":
        row.pop("script")
        row.pop("scriptAsJsonString")
    elif defect == "map_not_object":
        row["script"] = []
    elif defect == "entry_not_object":
        row["script"][INPUT] = "return 'wrong shape';"
    elif defect == "source_not_text":
        row["script"][INPUT]["script"] = 7
    elif defect == "entry_flag_not_boolean":
        row["script"][INPUT]["scriptActive"] = "true"
    elif defect == "show_flag_not_boolean":
        row["script"][INPUT]["showScript"] = 1
    elif defect == "parent_inactive":
        row["scriptActive"] = False
    elif defect == "entry_inactive":
        row["script"][INPUT]["scriptActive"] = False
    elif defect == "malformed_mirror":
        row["scriptAsJsonString"] = "{"
    elif defect == "different_mirror":
        row["scriptAsJsonString"] = "{}"
    elif defect == "nonobject_mirror":
        row["scriptAsJsonString"] = "[]"
    if defect not in {
        "missing_map",
        "malformed_mirror",
        "different_mirror",
        "nonobject_mirror",
    }:
        row["scriptAsJsonString"] = json.dumps(row["script"])
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_binding_rejected(result)
    assert result["complete"] is False, result


@pytest.mark.parametrize(
    "field,value",
    [
        ("readonly", True),
        ("readonly", "true"),
        ("is_scriptable", False),
        ("is_scriptable", "false"),
    ],
)
async def test_saved_script_obeys_native_readonly_and_scriptability_metadata(
    settings, field, value
):
    declared = definition()
    if field == "is_scriptable":
        declared["inputs"][0]["attributes"][field] = value
    else:
        declared["inputs"][0][field] = value
    flow = _flow(declared)
    scripted(flow["actionInstances"][1]["inputs"][0], "return 'not permitted';")
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_binding_rejected(result)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "reference,allowed",
    [
        ("fd_data._1__log.status", True),
        ("fd_data._99__log.status", False),
        ("fd_data._3__log.status", False),
        ("fd_data._2__log.status", False),
        ("fd_data._1__log.missing", False),
        ("fd_data._1__log.details.missing", False),
        ("fd_data[producer].status", False),
        ("fd_data._1__log[field]", False),
    ],
)
async def test_active_and_stored_inactive_sources_resolve_actual_lexical_dependencies(
    settings, active, reference, allowed
):
    declared = definition()
    flow = _flow(declared)
    scripted(
        flow["actionInstances"][1]["inputs"][0], f"return {reference};", active=active
    )
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    if allowed:
        _assert_valid(result)
    else:
        _assert_binding_rejected(result)


@pytest.mark.parametrize("active", [False, True])
async def test_branch_local_script_producer_cannot_escape_its_scope(settings, active):
    declared = definition()
    flow = _flow(declared)
    producer, consumer, later = [
        tree.Step(row, "action") for row in flow["actionInstances"]
    ]
    branch = logic("IF", "branch")
    flow = native_flow(
        [(branch, "", 1), (producer, branch.uid, 2), (consumer, "", 3), (later, "", 4)]
    )
    flow["type"] = "subflow"
    scripted(
        flow["actionInstances"][1]["inputs"][0],
        "return fd_data._2__log.status;",
        active=active,
    )
    routes(settings, flow, declared)

    result = await _validate(settings, flow, declared)

    _assert_binding_rejected(result)
    assert not any(
        issue["code"] == "INVALID_TOPOLOGY" for issue in result["diagnostics"]
    ), result


@pytest.mark.parametrize("scripted_selector", [False, True])
async def test_native_loop_target_remains_static_even_with_a_valid_basic_target(
    settings, scripted_selector
):
    declared = definition()
    target = tree.Step(_flow(declared)["actionInstances"][0], "action")
    branch = logic("IF", "branch")
    source = go_back("source", target.uid)
    flow = native_flow([(target, "", 1), (branch, "", 2), (source, branch.uid, 3)])
    flow["type"] = "subflow"
    if scripted_selector:
        row = data.named(flow["flowLogicInstances"][1]["inputs"], "go_back_to_step")
        row["script"] = {
            "go_back_to_step": {
                "script": f"return '{target.uid}';",
                "scriptActive": True,
                "showScript": True,
            }
        }
        row["scriptActive"] = True
        row["scriptAsJsonString"] = json.dumps(row["script"])
    routes(settings, flow, declared)
    catalog = copy.deepcopy(DEFS["flow_logic"])
    catalog.append(copy.deepcopy(NATIVE["GOBACKTO"]))
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog}
    )

    result = await _validate(settings, flow, declared, catalog)

    if scripted_selector:
        assert result["valid"] is (False if result["complete"] else None), result
        assert result["diagnostics"], result
    else:
        _assert_valid(result)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("drift", [False, True])
async def test_script_producer_schema_is_bound_to_independently_read_definition(
    settings, active, drift
):
    declared = definition()
    flow = _flow(declared)
    scripted(
        flow["actionInstances"][1]["inputs"][0],
        "return fd_data._1__log.status;",
        active=active,
    )
    current = copy.deepcopy(declared)
    if drift:
        current["outputs"][0]["type"] = "integer"
    _, _, action_read = routes(settings, flow, current)

    result = await _validate(settings, flow, declared, current)

    assert action_read.called
    assert any(
        call.request.url.path == f"/api/now/processflow/action/action_types/{LOG}"
        for call in http_mock.calls
    )
    if drift:
        issues = _assert_binding_rejected(result)
        assert any(issue["code"] == "UNAVAILABLE_BINDING" for issue in issues), result
        assert result["complete"] is False, result
    else:
        _assert_valid(result)
