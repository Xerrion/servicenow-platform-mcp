"""Safe prerequisite diagnostics through the registered local validation tool."""

import copy

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import semantics
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures
from tests.test_flow_lifecycle import status_header, validation_flow, validation_routes


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active", None),
        ("status", None),
        ("isPublished", None),
        ("snapshot", None),
        ("masterSnapshot", None),
        ("masterSnapshotId", None),
        ("flowEngineVersion", None),
        ("actionInstances", None),
        ("label_cache", None),
        ("stages", None),
        ("contentACLFilteredOrders", [1]),
        ("nonCriticalErrors", ["private-sensitive-marker"]),
        ("deletedActions", [{"id": "private-sensitive-marker"}]),
    ],
)
async def test_validate_identifies_exact_header_prerequisite(settings, field, value):
    flow = validation_flow()
    flow[field] = value
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["status"] == "success", result
    data = result["data"]
    assert data["valid"] is None
    assert data["complete"] is False
    assert data["diagnostics"][0]["code"] == "INCOMPLETE_SEMANTICS"
    assert data["diagnostics"][0]["path"] == f"flow.{field}"
    assert data["diagnostics"][0]["message"] != (
        "Read a complete inactive V2 draft with unique identities, orders, and valid parent links."
    )
    assert "private-sensitive-marker" not in str(result)
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    ("field", "value", "suffix"),
    [
        ("uiUniqueIdentifier", None, "uiUniqueIdentifier"),
        ("id", "private-sensitive-marker", "id"),
        ("order", True, "order"),
        ("order", "invalid", "order"),
        ("order", 0, "order"),
        ("parent", None, "parent"),
        ("parent", "private-sensitive-marker", "parent"),
        ("actionTypeSysId", None, "actionTypeSysId"),
        ("type", "other", "type"),
        ("actionType", {"id": "private-sensitive-marker"}, "actionType.id"),
        ("flowLogicDefinition", None, "flowLogicDefinition"),
        ("inputs", None, "inputs"),
        ("inputs", [{"name": "private-sensitive-marker"}], "inputs[0].value"),
        (
            "inputs",
            [{"name": "x", "value": 1}, {"name": "x", "value": 2}],
            "inputs[1].name",
        ),
        ("data", {"private-sensitive-marker": "sensitive"}, "data"),
    ],
)
async def test_validate_identifies_exact_step_prerequisite(
    settings, field, value, suffix
):
    flow = validation_flow()
    flow["actionInstances"][0][field] = value
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is None
    assert result["data"]["complete"] is False
    diagnostic = result["data"]["diagnostics"][0]
    assert diagnostic["code"] == "INCOMPLETE_SEMANTICS"
    assert diagnostic["path"] == f"flow.actionInstances[0].{suffix}"
    assert "private-sensitive-marker" not in str(result)
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    ("field", "value", "suffix"),
    [
        ("type", None, "type"),
        ("triggerDefinitionId", "private-sensitive-marker", "triggerDefinitionId"),
        ("label", "private-sensitive-marker", "label"),
        ("inputs", [{"name": "private-sensitive-marker"}], "inputs[0].value"),
    ],
)
async def test_validate_identifies_trigger_invariant_without_exposing_values(
    settings, field, value, suffix
):
    flow = validation_flow()
    flow["triggerInstances"][0][field] = value
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is None
    diagnostic = result["data"]["diagnostics"][0]
    assert diagnostic["path"] == f"flow.triggerInstances[0].{suffix}"
    assert "private-sensitive-marker" not in str(result)


@pytest.mark.parametrize("value", [None, True, {"value": "true"}, {}])
async def test_validate_identifies_unreadable_subflow_wait(settings, value):
    flow = validation_flow()
    flow["subFlowInstances"] = base_flow()["subFlowInstances"]
    flow["subFlowInstances"][0]["waitForCompletion"] = value
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    result = decode_response(
        await _register_and_get_tools(settings, OAuthPKCEProvider(settings))["flow"](
            action="validate", sys_id=FLOW
        ),
    )
    assert result["data"]["valid"] is None
    assert (
        result["data"]["diagnostics"][0]["path"]
        == "flow.subFlowInstances[0].waitForCompletion.value"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "children",
        "cycle",
        "missing_parent",
        "duplicate_ui",
        "duplicate_id",
        "duplicate_order",
    ],
)
async def test_validate_topology_invariants_retain_source_positions(settings, defect):
    flow = validation_flow()
    flow["flowLogicInstances"] = [
        {
            "id": "7" * 32,
            "uiUniqueIdentifier": "branch",
            "order": 4,
            "parent": "",
            "type": "flowlogic",
            "definitionId": DEFS["flow_logic"][0]["id"],
            "flowLogicDefinition": DEFS["flow_logic"][0],
            "inputs": [],
            "children": [],
        },
    ]
    expected = "flow.actionInstances[0].parent"
    if defect == "children":
        flow["flowLogicInstances"][0]["children"] = ["private-sensitive-marker"]
        expected = "flow.flowLogicInstances[0].children"
    elif defect == "cycle":
        flow["flowLogicInstances"][0].update(parent="branch", children=["branch"])
        expected = "flow.flowLogicInstances[0].parent"
    elif defect == "missing_parent":
        flow["actionInstances"][0]["parent"] = "private-sensitive-marker"
    else:
        field = {
            "duplicate_ui": "uiUniqueIdentifier",
            "duplicate_id": "id",
            "duplicate_order": "order",
        }[defect]
        flow["actionInstances"][1][field] = flow["actionInstances"][0][field]
        expected = "flow.actionInstances[1]." + field
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    result = decode_response(
        await _register_and_get_tools(settings, OAuthPKCEProvider(settings))["flow"](
            action="validate", sys_id=FLOW
        ),
    )
    assert result["data"]["valid"] is None
    assert result["data"]["complete"] is False
    assert result["data"]["diagnostics"][0]["path"] == expected
    assert "private-sensitive-marker" not in str(result)


async def test_trigger_active_absence_is_not_a_validation_prerequisite(settings):
    flow = validation_flow()
    assert "active" not in flow["triggerInstances"][0]
    validation_routes(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    result = decode_response(
        await _register_and_get_tools(settings, OAuthPKCEProvider(settings))["flow"](
            action="validate", sys_id=FLOW
        ),
    )
    assert result["data"]["valid"] is True, result
    assert result["data"]["complete"] is True
    assert result["data"]["diagnostics"] == []
    assert all(call.request.method == "GET" for call in http_mock.calls)


def test_safe_diagnostics_do_not_relax_independent_comparison():
    flow = base_flow()
    before = copy.deepcopy(flow)
    changed = copy.deepcopy(flow)
    changed["actionInstances"][0]["id"] = "9" * 32
    with pytest.raises(CapabilityError):
        semantics.compare_saved(flow, changed, generated_ids=set())
    changed = copy.deepcopy(flow)
    changed["actionInstances"][0]["inputs"][0]["value"] = "private-sensitive-marker"
    with pytest.raises(CapabilityError) as error:
        semantics.compare_saved(flow, changed, generated_ids=set())
    assert "private-sensitive-marker" not in str(error.value)
    assert flow == before
