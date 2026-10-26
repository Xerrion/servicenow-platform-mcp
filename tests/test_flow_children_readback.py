"""Source-compatible GET projections can omit Studio's derived UI child cache."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, lifecycle, semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan
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


def reference_array_input_flow(*, packed):
    """Synthetic item packing follows Studio's declaration/value split.

    declarations.py cites the array registration, packing, and restoration source.
    The parent owns reference; child_reference is not a packed item property.
    """
    flow = validation_flow()
    item = {
        "name": "request_ids_child0",
        "type": "reference",
        "uiUniqueId": "reference-item-ui",
        "children": [],
    }
    parameter = {
        "name": "request_ids",
        "type": "array.reference",
        "reference": "sc_req_item",
        "uiUniqueId": "reference-array-ui",
        "vendor": {"keep": [False, 0]},
        "children": [] if packed else [item],
    }
    if packed:
        parameter["attributes"] = {
            "child_name": item["name"],
            "child_type": item["type"],
            "child_uiUniqueId": item["uiUniqueId"],
        }
    binding = {
        "name": "request_ids",
        "value": "",
        "displayValue": "",
        "children": [],
        "parameter": copy.deepcopy(parameter),
        "vendor": {"binding": True},
    }
    flow["actionInstances"][1]["inputs"].append(binding)
    flow["actionInstances"][1]["actionType"]["inputs"].append(parameter)
    return flow


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("values", [["a" * 32, "b" * 32], []])
async def test_reference_array_static_input_preview_apply_keeps_declared_schema(
    settings, packed, values
):
    flow = reference_array_input_flow(packed=packed)
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    saved = {}

    def save(request):
        body = json.loads(request.content)
        saved.update(copy.deepcopy(body))
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
                    "inputs": {"request_ids": values},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    row = saved["actionInstances"][1]["inputs"][-1]
    assert row["value"] == row["displayValue"] == ""
    assert [child["value"] for child in row["children"]] == values
    assert all(child["type"] == "reference" for child in row["children"])
    assert all(child["reference"] == "sc_req_item" for child in row["children"])
    assert all(
        child["parameter"]["reference"] == "sc_req_item" for child in row["children"]
    )
    assert row["parameter"] == before["actionInstances"][1]["inputs"][-1]["parameter"]
    declared = data.children(row)
    assert len(declared) == 1
    assert declared[0]["type"] == "reference"
    assert declared[0]["reference"] == "sc_req_item"
    assert declared[0]["uiUniqueId"] == "reference-item-ui"
    assert row["vendor"] == {"binding": True}
    assert flow == before
    assert write.call_count == 1 and len(coordinator.store) == 0
    assert any(
        call.request.method == "GET"
        and call.request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for call in http_mock.calls[http_mock.calls.index(write.calls[0]) + 1 :]
    )


@pytest.mark.parametrize(
    "value",
    [
        ["not-a-sys-id"],
        ["a" * 31],
        ["g" * 32],
        [""],
        [None],
        [True],
        [7],
        [{"sys_id": "a" * 32}],
        [["a" * 32]],
        "a" * 32,
        '["' + "a" * 32 + '"]',
    ],
)
async def test_reference_array_invalid_literal_input_never_stages(settings, value):
    flow = reference_array_input_flow(packed=True)
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"request_ids": value},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "value",
        "order",
        "count",
        "type",
        "table",
        "parameter_table",
        "schema",
        "identity",
        "metadata",
    ],
)
async def test_reference_array_input_independent_get_rejects_binding_drift(
    settings, packed, defect
):
    flow = reference_array_input_flow(packed=packed)
    flow_before = copy.deepcopy(flow)
    fixtures(settings, flow)
    expected = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"request_ids": ["a" * 32, "b" * 32]},
                }
            ],
        )
    )["payload"]
    actual = copy.deepcopy(expected)
    row = actual["actionInstances"][1]["inputs"][-1]
    if defect == "value":
        row["children"][0]["value"] = "c" * 32
    elif defect == "order":
        row["children"].reverse()
    elif defect == "count":
        row["children"].pop()
    elif defect == "type":
        row["children"][0]["type"] = "string"
    elif defect == "table":
        row["children"][0]["reference"] = "sys_user"
    elif defect == "parameter_table":
        row["children"][0]["parameter"]["reference"] = "sys_user"
    elif defect == "schema":
        row["parameter"]["reference"] = "sys_user"
    elif defect == "identity":
        row["children"][0]["uiUniqueId"] = "changed-item-ui"
    elif defect == "metadata":
        row["parameter"]["vendor"]["keep"] = [True, 0]
    expected_before = copy.deepcopy(expected)
    actual_before = copy.deepcopy(actual)
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        if defect:
            with pytest.raises(CapabilityError) as error:
                await semantics.verify_saved(
                    client, expected, copy.deepcopy(expected), generated_ids=set()
                )
            assert error.value.code == "UNKNOWN_OUTCOME"
        else:
            assert (
                await semantics.verify_saved(
                    client, expected, copy.deepcopy(expected), generated_ids=set()
                )
                == actual
            )
    assert flow == flow_before
    assert expected == expected_before
    assert actual == actual_before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "omitted_binding_schema",
        "invalid_id",
        "wrong_item_type",
        "child_table",
        "missing_table",
        "missing_schema",
        "wrong_schema_item_type",
        "missing_schema_table",
    ],
)
async def test_reference_array_lifecycle_validates_static_items_and_empty_schema(
    settings, packed, defect
):
    flow = reference_array_input_flow(packed=packed)
    fixtures(settings, flow)
    built = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"request_ids": ["a" * 32]},
                }
            ],
        )
    )["payload"]
    row = built["actionInstances"][1]["inputs"][-1]
    parameter = built["actionInstances"][1]["actionType"]["inputs"][-1]
    if defect == "invalid_id":
        row["children"][0]["value"] = "invalid"
    elif defect == "wrong_item_type":
        row["children"][0]["parameter"]["type"] = "string"
    elif defect == "child_table":
        row["children"][0]["parameter"]["reference"] = "sys_user"
    elif defect == "missing_table":
        row["parameter"].pop("reference")
    elif defect == "omitted_binding_schema":
        if packed:
            for name in ("child_name", "child_type", "child_uiUniqueId"):
                row["parameter"]["attributes"].pop(name)
        else:
            row["parameter"]["children"] = []
    elif defect == "missing_schema":
        if packed:
            parameter["attributes"].pop("child_type")
        else:
            parameter["children"] = []
    elif defect == "wrong_schema_item_type":
        if packed:
            parameter["attributes"]["child_type"] = "string"
        else:
            parameter["children"][0]["type"] = "string"
    elif defect == "missing_schema_table":
        parameter.pop("reference")
    validation_routes(built)
    before = copy.deepcopy(built)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(built, client)
    valid = defect in (None, "omitted_binding_schema")
    if valid:
        assert result["valid"] is True and result["complete"] is True, result
    else:
        assert result["valid"] is not True, result
        assert result["diagnostics"], result
    assert built == before
    if valid or defect in (
        "missing_schema",
        "wrong_schema_item_type",
        "missing_schema_table",
    ):
        row["children"] = []
        empty_before = copy.deepcopy(built)
        validation_routes(built)
        async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
            empty = await lifecycle.validate_definition(built, client)
        if valid:
            assert empty["valid"] is True and empty["complete"] is True, empty
        else:
            assert empty["valid"] is not True, empty
            assert empty["diagnostics"], empty
        assert built == empty_before
        row["children"] = before["actionInstances"][1]["inputs"][-1]["children"]
    assert built == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize(
    "reference,pill,valid",
    [
        ("sc_req_item", "ui-1.record", True),
        ("task", "ui-1.record", True),
        ("sys_user", "ui-1.record", False),
        ("sc_req_item", "ui-1.count", False),
        ("sc_req_item", "ui-1.records", False),
    ],
)
async def test_reference_array_input_element_pills_obey_scalar_type_and_parent_table(
    settings, packed, reference, pill, valid
):
    flow = reference_array_input_flow(packed=packed)
    flow["actionInstances"][1]["inputs"][-1]["parameter"]["reference"] = reference
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"request_ids": ["{{" + pill + "}}"]},
                }
            ],
        )
    )
    assert result["status"] == ("success" if valid else "error"), result
    assert len(coordinator.store) == int(valid)
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("values", [[], ["a" * 32]])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_table",
        "dynamic_table",
        "child_table",
        "child_type",
        "missing_item",
        "two_items",
    ],
)
async def test_reference_array_input_requires_complete_authoritative_item_schema(
    settings, values, defect
):
    flow = reference_array_input_flow(packed=False)
    parameter = flow["actionInstances"][1]["inputs"][-1]["parameter"]
    if defect == "missing_table":
        parameter.pop("reference")
    elif defect == "dynamic_table":
        parameter["reference"] = "{{ui-1.record}}"
    elif defect == "child_table":
        parameter["children"][0]["reference"] = "sys_user"
    elif defect == "child_type":
        parameter["children"][0]["type"] = "string"
    elif defect == "missing_item":
        parameter["children"] = []
    else:
        parameter["children"].append(copy.deepcopy(parameter["children"][0]))
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"request_ids": values},
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
