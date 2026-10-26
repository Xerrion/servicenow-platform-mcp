"""Studio reconstructs UI topology metadata, not authoritative persisted parent links.

Source facts from local docs/designer-bundle.min.js:
generateComponentInstances 92844-92895 calls getAncestorData 15835-15881.
updateActionInstanceIndices 156582-156604 derives first-child and Then flags.
shouldFirstChildShowThen 15401 includes IF, ELSEIF and CATCH.
calcDepth/calculateDepthForChildren 156741-156795 derive depth from children.
These fixtures omit the whole family, not just the observed root ancestorType.
They are synthetic and are not a captured target Studio response.
"""

import copy
from typing import Any

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_children_readback import saved_branches
from tests.test_flow_editing import BASE, FLOW, fixtures
from tests.test_flow_lifecycle import status_header, validation_flow, validation_routes


def without_ui_cache(flow):
    result = copy.deepcopy(flow)
    for collection in semantics.COLLECTIONS:
        for step in result[collection]:
            for key in tree.UI_FIELDS | {"children"}:
                step.pop(key, None)
    return result


def with_ui_cache(flow):
    result = copy.deepcopy(flow)
    roots = tree.build_tree(flow)
    tree.renumber(roots)
    for collection in semantics.COLLECTIONS:
        result[collection] = []
    for step in tree.walk(roots):
        result[tree.COLLECTIONS[step.kind]].append(step.raw)
    return result


@pytest.mark.parametrize("echo_cached", [False, True])
async def test_whole_generated_family_can_be_absent_without_losing_semantics(
    settings, echo_cached
):
    saved = without_ui_cache(saved_branches())
    request = with_ui_cache(saved)
    before = copy.deepcopy((saved, request))
    echo = request if echo_cached else saved
    assert semantics.studio_semantics(saved) == semantics.studio_semantics(request)
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": saved}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await semantics.verify_saved(client, request, echo, generated_ids=set())
            == saved
        )
    assert route.call_count == 1
    assert (saved, request) == before


@pytest.mark.parametrize(
    ("collection", "index", "field", "value"),
    [
        ("actionInstances", 0, "ancestorType", "IF"),
        ("actionInstances", 0, "ancestorUiId", "if-ui"),
        ("actionInstances", 0, "depth", 2),
        ("actionInstances", 0, "isFirstFlowLogicChild", True),
        ("actionInstances", 0, "isPrecededByThen", True),
        ("actionInstances", 0, "children", ["ui-2"]),
        ("actionInstances", 2, "ancestorType", None),
        ("actionInstances", 2, "ancestorUiId", "else-ui"),
        ("actionInstances", 2, "depth", 1),
        ("actionInstances", 2, "isFirstFlowLogicChild", False),
        ("actionInstances", 2, "isPrecededByThen", False),
        ("subFlowInstances", 0, "depth", True),
        ("flowLogicInstances", 0, "children", []),
    ],
)
def test_explicit_contradictions_are_never_hidden(collection, index, field, value):
    flow = without_ui_cache(saved_branches())
    flow[collection][index][field] = value
    with pytest.raises(semantics.IncompleteSemantics) as error:
        semantics.studio_semantics(flow)
    assert error.value.path == f"flow.{collection}[{index}].{field}"


def test_root_empty_and_null_ancestors_match_but_nested_null_is_not_inferred():
    flow = with_ui_cache(saved_branches())
    root = flow["actionInstances"][0]
    original = semantics.studio_semantics(flow)
    root.update(ancestorType="", ancestorUiId="")
    assert semantics.studio_semantics(flow) == original
    flow["actionInstances"][2]["ancestorUiId"] = None
    with pytest.raises(semantics.IncompleteSemantics):
        semantics.studio_semantics(flow)


@pytest.mark.parametrize(
    "kind", ["IF", "ELSEIF", "CATCH", "TRY", "FOREACH", "PARALLELBLOCK"]
)
def test_nested_ancestors_are_outermost_but_first_child_and_then_are_local(kind):
    flow = without_ui_cache(saved_branches())
    outer, inner = flow["flowLogicInstances"]
    inner["flowLogicDefinition"]["type"] = kind
    inner["parent"] = outer["uiUniqueIdentifier"]
    action = flow["actionInstances"][1]
    action["parent"] = inner["uiUniqueIdentifier"]
    if kind == "PARALLELBLOCK":
        # Studio labels a Parallel block's first child with its native arrow,
        # not the ordinary numeric order this fixture inherited from Else.
        action["order"] = "4->5"
    cached = with_ui_cache(flow)
    nested = cached["actionInstances"][-1]
    assert nested["ancestorType"] == "IF"
    assert nested["ancestorUiId"] == outer["uiUniqueIdentifier"]
    assert nested["depth"] == 3
    assert nested["isFirstFlowLogicChild"] is True
    assert nested["isPrecededByThen"] is (kind in {"IF", "ELSEIF", "CATCH"})
    assert semantics.studio_semantics(cached) == semantics.studio_semantics(flow)
    nested["ancestorUiId"] = inner["uiUniqueIdentifier"]
    with pytest.raises(semantics.IncompleteSemantics):
        semantics.studio_semantics(cached)


def test_hidden_catch_boundary_does_not_become_outer_ancestor_of_nested_steps():
    flow = without_ui_cache(saved_branches())
    outer, inner = flow["flowLogicInstances"]
    outer["flowLogicDefinition"]["type"] = "TOP_LEVEL_CATCH"
    inner["parent"] = outer["uiUniqueIdentifier"]
    flow["actionInstances"][1]["parent"] = inner["uiUniqueIdentifier"]
    cached = with_ui_cache(flow)
    nested = cached["actionInstances"][-1]
    assert (nested["ancestorType"], nested["ancestorUiId"]) == (
        "ELSE",
        inner["uiUniqueIdentifier"],
    )
    # A standalone hidden Catch still has derivable UI ancestry, but does not
    # establish a complete native handler serialization contract.
    with pytest.raises(CapabilityError, match="root Try/Catch pair"):
        semantics.studio_semantics(cached)


@pytest.mark.parametrize(
    "defect",
    ["parent", "order", "definition", "value", "opaque", "connectedTo", "handler"],
)
def test_normalizing_ui_caches_does_not_erase_authoritative_differences(defect):
    expected = without_ui_cache(saved_branches())
    actual = copy.deepcopy(expected)
    step = actual["actionInstances"][2]
    if defect == "parent":
        step["parent"] = "else-ui"
    elif defect == "order":
        step["order"], actual["actionInstances"][1]["order"] = (
            actual["actionInstances"][1]["order"],
            step["order"],
        )
    elif defect == "definition":
        step["actionTypeSysId"] = "9" * 32
    elif defect == "value":
        step["inputs"][0]["value"] = "different"
    elif defect == "handler":
        step["errorHandlingInstance"] = True
    elif defect == "connectedTo":
        actual["flowLogicInstances"][1]["connectedTo"] = "different"
    else:
        step["vendorSetting"] = True
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


@pytest.mark.parametrize("contradictory", [False, True])
async def test_registered_validation_accepts_missing_ui_family_but_not_contradictions(
    settings, contradictory
):
    flow = validation_flow()
    if contradictory:
        flow["actionInstances"][0]["isFirstFlowLogicChild"] = True
    validation_routes(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))[
        "data"
    ]
    if contradictory:
        assert result["valid"] is None
        assert result["complete"] is False
        assert (
            result["diagnostics"][0]["path"]
            == "flow.actionInstances[0].isFirstFlowLogicChild"
        )
    else:
        assert result["valid"] is True
        assert result["complete"] is True
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0), (12, 12), ("12", 12), ("12->13", 13)]
)
def test_native_order_sort_uses_parallel_arrow_tail(value, expected):
    assert tree.order_key(value) == expected


@pytest.mark.parametrize(
    "value", [True, -1, 1.5, "1.2", "1->", "->2", "1->2->3", "9" * 5000]
)
def test_unknown_native_order_never_gets_repaired(value):
    with pytest.raises(CapabilityError, match="native integer or Parallel"):
        tree.order_key(value)


def test_unresolved_nested_sandwich_closing_label_requires_native_evidence():
    def loop(uid, order):
        return tree.Step(
            {
                "uiUniqueIdentifier": uid,
                "order": order,
                "flowLogicDefinition": {
                    "type": "FOREACH",
                    "attributes": {"display_as_sandwich": "true"},
                },
            },
            "flowlogic",
        )

    outer, inner = loop("outer", 1), loop("inner", 2)
    inner.children = [tree.Step({"uiUniqueIdentifier": "work", "order": 3}, "action")]
    outer.children = [inner]
    tail = tree.Step({"uiUniqueIdentifier": "tail", "order": 5}, "action")
    with pytest.raises(CapabilityError, match="no source-defined closing order"):
        tree.validate_projection([outer, tail])
    # The literal Studio branch carries an existing inner boundary unchanged.
    inner.raw["orderBottom"] = 4
    assert tree.project_orders([outer, tail])[inner.uid].order_bottom == 4


@pytest.mark.parametrize(
    "logic_type", [None, "TRY", "TOP_LEVEL_CATCH", "TOP_LEVEL_TRY"]
)
def test_table_order_zero_requires_verified_native_top_try_type(logic_type):
    kwargs: dict[str, Any] = {
        "kind": "flowlogic",
        "identity": "1" * 32,
        "ui_id": "native-main",
        "order": "0",
        "parent": "",
        "definition": "2" * 32,
        "inputs": [],
        "settings": {"logic_type": logic_type},
    }
    if logic_type == "TOP_LEVEL_TRY":
        assert semantics.table_step(**kwargs).order == 0
    else:
        with pytest.raises(CapabilityError):
            semantics.table_step(**kwargs)
