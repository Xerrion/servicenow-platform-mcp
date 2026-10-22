"""Source-compatible GET projections can omit Studio's derived UI child cache."""

import copy

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures
from tests.test_flow_lifecycle import status_header, validation_flow, validation_routes


def saved_branches():
    """Synthetic wire-compatible projection with the caller's persisted topology.

    This is not a captured target GET. docs/designer-bundle.min.js supports absent
    children: GET_FLOW_OK calls onAnyFlowUpdateOK (203770-203771), which calls
    generateComponentInstances (204662). Its mapChildren (92801-92842) initializes
    an absent cache and appends direct-child uiUniqueIdentifiers sorted by order.
    splitComponentInstancesForSavingToServer (130432-130460) retains that cache.
    """
    flow = base_flow()
    for kind, identity, uid, order in (
        ("IF", "6", "if-ui", "2"),
        ("ELSE", "7", "else-ui", "4"),
    ):
        definition = next(d for d in DEFS["flow_logic"] if d["type"] == kind)
        flow["flowLogicInstances"].append(
            {
                "id": identity * 32,
                "uiUniqueIdentifier": uid,
                "order": order,
                "parent": "",
                "type": "flowlogic",
                "definitionId": definition["id"],
                "flowLogicDefinition": copy.deepcopy(definition),
                "inputs": [],
            },
        )
    flow["actionInstances"][0]["order"] = "1"
    flow["actionInstances"][2].update(order="3", parent="if-ui")
    flow["actionInstances"][1].update(order="5", parent="else-ui")
    flow["subFlowInstances"][0]["order"] = "6"
    return flow


def with_child_cache(flow):
    result = copy.deepcopy(flow)
    steps = sorted(
        [step for collection in semantics.COLLECTIONS for step in flow[collection]],
        key=lambda step: int(step["order"]),
    )
    for logic in result["flowLogicInstances"]:
        logic["children"] = [
            step["uiUniqueIdentifier"]
            for step in steps
            if step["parent"] == logic["uiUniqueIdentifier"]
        ]
    return result


def test_absent_children_matches_verified_ui_cache_without_changing_payload():
    flow = saved_branches()
    before = copy.deepcopy(flow)
    parsed = semantics.studio_semantics(flow)
    assert [(s.order, s.parent) for s in parsed.steps] == [
        (1, ""),
        (2, ""),
        (3, "if-ui"),
        (4, ""),
        (5, "else-ui"),
        (6, ""),
    ]
    cached = with_child_cache(flow)
    assert [logic["children"] for logic in cached["flowLogicInstances"]] == [
        ["ui-3"],
        ["ui-2"],
    ]
    assert parsed == semantics.studio_semantics(cached)
    semantics.compare_saved(cached, flow, generated_ids=set())
    semantics.compare_saved(flow, cached, generated_ids=set())
    assert flow == before


def test_child_cache_lists_direct_actions_subflows_and_logic_in_global_order():
    flow = saved_branches()
    flow["flowLogicInstances"][0].update(parent="else-ui", order="4")
    flow["flowLogicInstances"][1]["order"] = "2"
    flow["actionInstances"][1]["order"] = "3"
    flow["actionInstances"][2]["order"] = "5"
    flow["subFlowInstances"][0]["parent"] = "else-ui"
    before = copy.deepcopy(flow)
    cached = with_child_cache(flow)
    assert [logic["children"] for logic in cached["flowLogicInstances"]] == [
        ["ui-3"],
        ["ui-2", "if-ui", "ui-4"],
    ]
    assert semantics.studio_semantics(flow) == semantics.studio_semantics(cached)
    semantics.compare_saved(cached, flow, generated_ids=set())
    assert flow == before


def test_empty_cache_is_valid_only_for_a_childless_block():
    flow = saved_branches()
    for action in flow["actionInstances"]:
        action["parent"] = ""
    cached = with_child_cache(flow)
    assert all(logic["children"] == [] for logic in cached["flowLogicInstances"])
    assert semantics.studio_semantics(flow) == semantics.studio_semantics(cached)


@pytest.mark.parametrize(
    "children",
    [
        None,
        [],
        "ui-3",
        ["3" * 32],
        [{"uiUniqueIdentifier": "ui-3"}],
        ["ui-3", "ui-3"],
        ["missing"],
        ["ui-2"],
    ],
)
def test_present_children_must_match_direct_parent_links(children):
    flow = saved_branches()
    flow["flowLogicInstances"][0]["children"] = children
    with pytest.raises(semantics.IncompleteSemantics) as error:
        semantics.studio_semantics(flow)
    assert error.value.path == "flow.flowLogicInstances[0].children"
    assert str(error.value) == (
        "Read the ordered child UI identities. They must match the persisted parent links exactly."
    )


@pytest.mark.parametrize("children", [["ui-2", "if-ui", "ui-3"], ["if-ui", "ui-2"]])
def test_child_cache_cannot_include_all_descendants_or_reorder_direct_children(
    children,
):
    flow = saved_branches()
    flow["flowLogicInstances"][0].update(parent="else-ui", order="4")
    flow["flowLogicInstances"][1]["order"] = "2"
    flow["actionInstances"][2]["order"] = "5"
    flow["actionInstances"][1]["order"] = "3"
    flow["flowLogicInstances"][1]["children"] = children
    with pytest.raises(semantics.IncompleteSemantics) as error:
        semantics.studio_semantics(flow)
    assert error.value.path == "flow.flowLogicInstances[1].children"
    flow["flowLogicInstances"][1]["children"] = ["ui-2", "if-ui"]
    semantics.studio_semantics(flow)


@pytest.mark.parametrize(
    "defect",
    [
        "cycle",
        "orphan",
        "missing_parent",
        "duplicate_ui",
        "duplicate_id",
        "duplicate_order",
    ],
)
def test_absent_cache_does_not_relax_topology_invariants(defect):
    flow = saved_branches()
    if defect == "cycle":
        flow["flowLogicInstances"][0]["parent"] = "else-ui"
        flow["flowLogicInstances"][1]["parent"] = "if-ui"
    elif defect == "orphan":
        flow["actionInstances"][0]["parent"] = "missing"
    elif defect == "missing_parent":
        flow["actionInstances"][0].pop("parent")
    else:
        field = {
            "duplicate_ui": "uiUniqueIdentifier",
            "duplicate_id": "id",
            "duplicate_order": "order",
        }[defect]
        flow["actionInstances"][1][field] = flow["actionInstances"][0][field]
    with pytest.raises(semantics.IncompleteSemantics):
        semantics.studio_semantics(flow)


def test_absent_cache_does_not_permit_disconnected_else():
    flow = saved_branches()
    flow["flowLogicInstances"][1]["parent"] = "if-ui"
    semantics.studio_semantics(flow)
    with pytest.raises(CapabilityError, match="directly follow"):
        tree.validate_structure(tree.build_tree(flow))


@pytest.mark.parametrize("echo_cached", [False, True])
@pytest.mark.parametrize(
    "defect", [None, "parent", "value", "identity", "opaque", "contradictory_cache"]
)
async def test_independent_readback_remains_strict_with_absent_cache(
    settings, defect, echo_cached
):
    flow = saved_branches()
    expected = with_child_cache(flow)
    echo = copy.deepcopy(expected if echo_cached else flow)
    if defect == "parent":
        flow["actionInstances"][2]["parent"] = "else-ui"
    elif defect == "value":
        flow["actionInstances"][2]["inputs"][0]["value"] = "changed"
    elif defect == "identity":
        flow["actionInstances"][2]["id"] = "9" * 32
    elif defect == "opaque":
        flow["flowLogicInstances"][0]["vendorSetting"] = True
    elif defect == "contradictory_cache":
        flow["flowLogicInstances"][0]["children"] = []
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": flow}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        if defect:
            with pytest.raises(CapabilityError) as error:
                await semantics.verify_saved(
                    client, expected, echo, generated_ids=set()
                )
            assert error.value.code == "UNKNOWN_OUTCOME"
        else:
            assert (
                await semantics.verify_saved(
                    client, expected, echo, generated_ids=set()
                )
                == flow
            )
    assert route.call_count == 1
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_contradictory_echo_cache_blocks_independent_read(settings):
    flow = saved_branches()
    echo = with_child_cache(flow)
    echo["flowLogicInstances"][0]["children"] = []
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": flow}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, flow, echo, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert not route.called


@pytest.mark.parametrize("cache", ["absent", "matching", "contradictory"])
async def test_registered_validation_checks_child_cache_and_dangling_pills(
    settings, cache
):
    flow = validation_flow()
    flow["flowLogicInstances"] = saved_branches()["flowLogicInstances"][:1]
    logic = flow["flowLogicInstances"][0]
    logic["inputs"] = [
        {
            "name": p["name"],
            "parameter": copy.deepcopy(p),
            "value": p.get("defaultValue", ""),
        }
        for p in logic["flowLogicDefinition"]["inputs"]
    ]
    for item in logic["inputs"]:
        item["value"] = (
            "{{Service Catalog_1.request_item.state}}=1"
            if item["name"] == "condition"
            else "Open"
        )
    flow["actionInstances"][1].update(order=3, parent="if-ui")
    flow["actionInstances"][2]["order"] = 4
    if cache != "absent":
        logic["children"] = ["ui-2"] if cache == "matching" else []
    validation_routes(flow)
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    if cache == "contradictory":
        assert result["data"]["valid"] is None
        assert result["data"]["complete"] is False
        diagnostic = result["data"]["diagnostics"][0]
        assert diagnostic["code"] == "INCOMPLETE_SEMANTICS"
        assert diagnostic["path"] == "flow.flowLogicInstances[0].children"
        assert flow == before
        assert all(call.request.method == "GET" for call in http_mock.calls)
        return
    assert result["data"]["valid"] is True, result
    assert result["data"]["complete"] is True
    assert flow == before
    flow["actionInstances"][1]["inputs"][1]["value"] = "{{missing.count}}"
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is not True
    assert result["data"]["diagnostics"]
    assert all(call.request.method == "GET" for call in http_mock.calls)
