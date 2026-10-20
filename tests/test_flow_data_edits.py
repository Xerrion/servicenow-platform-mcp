"""Authoring data contracts use persisted producer identities, not display orders."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import editing, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow, fixtures, persist


def typed_flow():
    flow = base_flow()
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "count", "type": "integer", "children": []},
        {
            "name": "record",
            "type": "reference",
            "reference": "sc_req_item",
            "children": [],
        },
        {
            "name": "records",
            "type": "records",
            "reference": "sc_req_item",
            "children": [],
        },
        {
            "name": "details",
            "type": "object",
            "children": [{"name": "message", "type": "string", "children": []}],
        },
    ]
    for step in flow["actionInstances"]:
        step["inputs"][0]["parameter"] = {
            "name": "log_message",
            "type": "string",
            "maxsize": 255,
        }
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "count",
            "value": 0,
            "parameter": {"name": "count", "type": "integer"},
        },
    )
    flow["flowVariables"] = [
        {
            "id": "6" * 32,
            "name": "counter",
            "label": "Counter",
            "type": "integer",
            "children": [],
            "value": 0,
        },
    ]
    return flow


async def preview(tools, edits):
    return decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )


@pytest.mark.parametrize("pill", ["ui-1.count", "flow_variable.counter"])
async def test_typed_step_and_variable_pills(pill, settings):
    _, tools = fixtures(settings, typed_flow())
    result = await preview(
        tools,
        [{"op": "update_inputs", "step": "2", "inputs": {"count": "{{" + pill + "}}"}}],
    )
    assert result["status"] == "success", result
    assert result["data"]["summary"]["data_pills"] == [pill]


@pytest.mark.parametrize(
    "pill",
    ["ui-2.count", "ui-3.count", "missing.count", "ui-1.missing", "ui-1.records"],
)
async def test_invalid_producer_or_cardinality_never_stages(pill, settings):
    coordinator, tools = fixtures(settings, typed_flow())
    result = await preview(
        tools,
        [{"op": "update_inputs", "step": "2", "inputs": {"count": "{{" + pill + "}}"}}],
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_move_revalidates_existing_reference(settings):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"][1]["value"] = "{{ui-1.count}}"
    _, tools = fixtures(settings, flow)
    result = await preview(tools, [{"op": "move_step", "step": "2", "before": "1"}])
    assert result["status"] == "error"


async def test_saved_action_deletion_tombstone_and_independent_read(settings):
    flow = typed_flow()
    _, tools = fixtures(settings, flow)
    saved = {}

    def save(request):
        data = json.loads(request.content)
        assert data["deletedActions"] == [{"id": "1" * 32}]
        assert len(data["actionInstances"]) == 2
        saved.update(copy.deepcopy(data))
        data.pop("deletedActions")
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    result = await preview(
        tools, [{"op": "delete_step", "step": "1", "children": "reject"}]
    )
    assert result["status"] == "success", result
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert saved["subFlowInstances"]
    assert len(flow["actionInstances"]) == 3


async def test_delete_rejects_dangling_reference(settings):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"][1]["value"] = "{{ui-1.count}}"
    _, tools = fixtures(settings, flow)
    result = await preview(tools, [{"op": "delete_step", "step": "1"}])
    assert result["status"] == "error"


async def test_object_input_uses_children_wire_shape(settings):
    flow = typed_flow()
    child = {"name": "message", "type": "string", "children": []}
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "payload",
            "value": "",
            "children": [
                {**child, "value": "old", "parameter": child, "vendor": {"keep": True}}
            ],
            "parameter": {"name": "payload", "type": "object", "children": [child]},
        },
    )
    _, tools = fixtures(settings, flow)

    def save(request):
        data = json.loads(request.content)
        item = data["actionInstances"][1]["inputs"][-1]
        assert item["value"] == ""
        assert item["children"][0]["value"] == "{{ui-1.details.message}}"
        assert item["children"][0]["vendor"] == {"keep": True}
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"payload": {"message": "{{ui-1.details.message}}"}},
            },
        ],
    )
    assert result["status"] == "success", result
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied


async def test_branch_output_does_not_escape(settings):
    flow = typed_flow()
    definition = next(d for d in DEFS["flow_logic"] if d["type"] == "IF")
    flow["flowLogicInstances"] = [
        {
            "id": "7" * 32,
            "uiUniqueIdentifier": "branch",
            "parent": "",
            "order": 1,
            "type": "flowlogic",
            "definitionId": definition["id"],
            "flowLogicDefinition": definition,
            "inputs": [
                {"name": "condition", "value": "true", "parameter": {"type": "string"}}
            ],
            "children": ["ui-1"],
        },
    ]
    for step in flow["actionInstances"] + flow["subFlowInstances"]:
        step["order"] += 1
    flow["actionInstances"][0]["parent"] = "branch"
    _, tools = fixtures(settings, flow)
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "2" * 32,
                "inputs": {"count": "{{ui-1.count}}"},
            },
        ],
    )
    assert result["status"] == "error"


def family_definitions():
    def definition(kind, identity, inputs=None, variables=None, **attributes):
        return {
            "type": kind,
            "name": kind,
            "id": identity * 32,
            "inputs": inputs or [],
            "variables": variables or [],
            "userCanRead": True,
            "connectedTo": "",
            "attributes": attributes,
        }

    result = copy.deepcopy(DEFS["flow_logic"])
    result.extend(
        [
            definition("TRY", "8"),
            {
                **definition(
                    "CATCH",
                    "9",
                    [{"name": "enabled", "type": "boolean", "defaultValue": "1"}],
                ),
                "connectedTo": "8" * 32,
            },
            definition("PARALLEL", "a"),
            {
                **definition(
                    "PARALLELBLOCK", "b", allow_children="true", allow_siblings="false"
                ),
                "connectedTo": "a" * 32,
            },
            definition("BREAK", "c", allow_children="false"),
            definition("CONTINUE", "d", allow_children="false", allow_siblings="false"),
            definition("SETFLOWVARIABLES", "e", allow_children="false"),
        ],
    )
    return result


async def plan(settings, flow, edits):
    fixtures(settings, flow)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        return await editing.plan(
            FlowCommand(action="edit", sys_id=FLOW, edits=edits),
            {"flow": flow, "logic": family_definitions(), "triggers": []},
            client,
        )


async def test_try_insertion_pairs_catch_and_scope(settings):
    result = await plan(
        settings,
        typed_flow(),
        [
            {"op": "insert_logic", "logic": "TRY", "after": "1", "ref": "try"},
            {"op": "move_step", "step": "2" * 32, "parent": "try"},
        ],
    )
    logic = result["payload"]["flowLogicInstances"]
    assert [s["flowLogicDefinition"]["type"] for s in logic] == ["TRY", "CATCH"]
    assert logic[1]["connectedTo"] == logic[0]["uiUniqueIdentifier"]
    assert logic[0]["children"] == ["ui-2"]


@pytest.mark.parametrize(
    ("source_region", "target_region", "allowed"),
    [
        ("if", "else", False),
        ("if", "if", True),
        ("loop", "root", False),
        ("left", "right", False),
        ("left", "root", True),
        ("try", "catch", False),
    ],
)
def test_scope_regions(source_region, target_region, allowed):
    from servicenow_mcp.capabilities.flows.data import Scope

    def block(uid, kind):
        return tree.Step(
            {
                "uiUniqueIdentifier": uid,
                "parent": "",
                "flowLogicDefinition": {"type": kind},
            },
            "flowlogic",
        )

    containers = {
        "if": block("if", "IF"),
        "else": block("else", "ELSE"),
        "loop": block("loop", "FOREACH"),
        "left": block("left", "PARALLELBLOCK"),
        "right": block("right", "PARALLELBLOCK"),
        "try": block("try", "TRY"),
        "catch": block("catch", "CATCH"),
    }
    parallel = block("parallel", "PARALLEL")
    parallel.children = [containers["left"], containers["right"]]
    for branch in parallel.children:
        branch.raw["parent"] = parallel.uid
    producer = tree.Step({"uiUniqueIdentifier": "source"}, "action")
    consumer = tree.Step({"uiUniqueIdentifier": "target"}, "action")
    containers[source_region].children.append(producer)
    roots = [
        containers["if"],
        containers["else"],
        containers["loop"],
        parallel,
        containers["try"],
        containers["catch"],
    ]
    if target_region == "root":
        roots.append(consumer)
    else:
        containers[target_region].children.append(consumer)
    scope = Scope(roots)
    if allowed:
        scope.visible(producer, consumer, ["value"])
    else:
        with pytest.raises(CapabilityError):
            scope.visible(producer, consumer, ["value"])


async def test_parallel_requires_explicit_nonempty_branches(settings):
    with pytest.raises(CapabilityError, match="Parallel requires"):
        await plan(
            settings,
            typed_flow(),
            [{"op": "insert_logic", "logic": "PARALLEL", "after": "1"}],
        )
    result = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "PARALLEL",
                "after": "1",
                "ref": "parallel",
            },
            {
                "op": "insert_logic",
                "logic": "PARALLELBLOCK",
                "parent": "parallel",
                "ref": "left",
            },
            {"op": "move_step", "step": "2" * 32, "parent": "left"},
        ],
    )
    assert len(result["payload"]["flowLogicInstances"]) == 2


async def test_for_each_item_type_and_local_scope(settings):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "record",
            "value": "",
            "parameter": {"type": "reference", "reference": "sc_req_item"},
        },
    )
    # The insertion alias is used for placement. Pills use the stable generated UI ID from the plan.
    first = await plan(
        settings,
        flow,
        [
            {
                "op": "insert_logic",
                "logic": "For Each",
                "after": "1",
                "inputs": {"items": "{{ui-1.records}}"},
            },
        ],
    )
    stored = first["payload"]
    loop = stored["flowLogicInstances"][0]
    loop["id"] = "f" * 32
    result = await plan(
        settings,
        stored,
        [
            {"op": "move_step", "step": "2" * 32, "parent": loop["uiUniqueIdentifier"]},
            {
                "op": "update_inputs",
                "step": "2" * 32,
                "inputs": {"record": "{{" + loop["uiUniqueIdentifier"] + ".item}}"},
            },
        ],
    )
    assert result["payload"]["label_cache"][0]["reference"] == "sc_req_item"


async def test_variable_assignments_use_observed_mirror(settings):
    result = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "set_variables",
                "inputs": {"counter": "{{ui-1.count}}"},
                "after": "1",
            },
        ],
    )
    step = result["payload"]["flowLogicInstances"][0]
    assert step["inputs"] == step["flowVariables"]
    assert step["inputs"][0]["parameter"]["id"] == "6" * 32


async def test_saved_logic_delete_requires_explicit_children_choice(settings):
    built = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "If",
                "after": "1",
                "ref": "if",
                "inputs": {"condition": "true"},
            },
            {"op": "move_step", "step": "2" * 32, "parent": "if"},
        ],
    )
    flow = built["payload"]
    logic = flow["flowLogicInstances"][0]
    logic["id"] = "7" * 32
    with pytest.raises(CapabilityError, match="has children"):
        await plan(settings, flow, [{"op": "delete_step", "step": logic["id"]}])
    result = await plan(
        settings,
        flow,
        [{"op": "delete_step", "step": logic["id"], "children": "promote"}],
    )
    assert result["payload"]["deletedFlowLogicInstances"] == [{"id": logic["id"]}]
    assert len(result["payload"]["actionInstances"]) == 3


async def test_stage_reordering_keeps_attachment_identity(settings):
    flow = typed_flow()
    flow["stages"] = {
        "stage-id": {
            "stageId": "stage-id",
            "label": "One",
            "value": "one",
            "componentIndexes": [1],
            "vendor": True,
        },
    }
    result = await plan(
        settings,
        flow,
        [
            {"op": "move_step", "step": "2", "before": "1"},
            {"op": "update_stage", "stage": "stage-id", "label": "Renamed"},
        ],
    )
    stage = result["payload"]["stages"]["stage-id"]
    assert stage["componentIndexes"] == [0]
    assert stage["label"] == "Renamed"
    assert stage["vendor"] is True


async def test_interface_metadata_edit_preserves_identity_type_and_opaque_fields(
    settings,
):
    flow = typed_flow()
    flow["inputs"] = [
        {
            "id": "8" * 32,
            "name": "count",
            "label": "Count",
            "type": "integer",
            "vendor": True,
        },
    ]
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "count",
                "label": "Renamed",
            },
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"count": "{{subflow.count}}"},
            },
        ],
    )
    assert result["payload"]["inputs"][0] == {**flow["inputs"][0], "label": "Renamed"}


async def test_capability_ledger_distinguishes_authorable_and_unverified(settings):
    from tests.test_flow import _register_and_get_tools

    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="describe"))
    ledger = result["data"]["authoring"]
    assert ledger["features"]["step_output_pills"]["authorable"] is True
    assert ledger["features"]["handler_creation"]["authorable"] is False
    assert ledger["features"]["scheduled_triggers"]["live_verified"] is False


@pytest.mark.parametrize("value", [True, "1", {}, "{{broken", "{{ui-1.records}}"])
async def test_integer_and_pill_boundary_rejects_invalid_values(settings, value):
    _, tools = fixtures(settings, typed_flow())
    assert (
        await preview(
            tools, [{"op": "update_inputs", "step": "2", "inputs": {"count": value}}]
        )
    )["status"] == "error"


@pytest.mark.parametrize(
    ("target", "allowed"), [("sc_req_item", True), ("task", True), ("incident", False)]
)
async def test_reference_table_compatibility(settings, target, allowed):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "record",
            "value": "",
            "parameter": {"type": "reference", "reference": target},
        },
    )
    _, tools = fixtures(settings, flow)
    result = await preview(
        tools,
        [{"op": "update_inputs", "step": "2", "inputs": {"record": "{{ui-1.record}}"}}],
    )
    assert (result["status"] == "success") is allowed


async def test_array_and_nested_object_schema_validation(settings):
    flow = typed_flow()
    child = {"name": "entry", "type": "integer", "children": []}
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "numbers",
            "value": "",
            "children": [],
            "parameter": {"type": "array.integer", "children": [child]},
        },
    )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"numbers": [1, 2, "{{ui-1.count}}"]},
            },
        ],
    )
    numbers = result["payload"]["actionInstances"][1]["inputs"][-1]
    assert numbers["value"] == ""
    assert [item["value"] for item in numbers["children"]] == [1, 2, "{{ui-1.count}}"]
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [{"op": "update_inputs", "step": "2", "inputs": {"numbers": [True]}}],
        )


async def test_delete_connected_branches_is_explicit(settings):
    first = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "If",
                "after": "1",
                "ref": "if",
                "inputs": {"condition": "true"},
            },
            {"op": "insert_logic", "logic": "Else", "after": "if"},
        ],
    )
    flow = first["payload"]
    for i, logic in enumerate(flow["flowLogicInstances"], start=7):
        logic["id"] = str(i) * 32
    with pytest.raises(CapabilityError, match="connected branches"):
        await plan(settings, flow, [{"op": "delete_step", "step": "7" * 32}])
    deleted = await plan(
        settings,
        flow,
        [
            {
                "op": "delete_step",
                "step": "7" * 32,
                "cascade": "connected",
                "children": "delete",
            },
        ],
    )
    assert deleted["payload"]["flowLogicInstances"] == []
    assert deleted["payload"]["deletedFlowLogicInstances"] == [
        {"id": "7" * 32},
        {"id": "8" * 32},
    ]


def test_catch_can_inspect_paired_try_status_only():
    from servicenow_mcp.capabilities.flows.data import Scope

    source = tree.Step({"uiUniqueIdentifier": "source"}, "action")
    target = tree.Step({"uiUniqueIdentifier": "target"}, "action")
    attempt = tree.Step(
        {"uiUniqueIdentifier": "try", "flowLogicDefinition": {"type": "TRY"}},
        "flowlogic",
        [source],
    )
    catch = tree.Step(
        {
            "uiUniqueIdentifier": "catch",
            "connectedTo": "try",
            "flowLogicDefinition": {"type": "CATCH"},
        },
        "flowlogic",
        [target],
    )
    scope = Scope([attempt, catch])
    scope.visible(source, target, ["__action_status__", "code"])
    with pytest.raises(CapabilityError):
        scope.visible(source, target, ["record"])
    source.raw["errorHandlingInstance"] = True
    with pytest.raises(CapabilityError, match="top-level"):
        scope.visible(source, target, ["__action_status__", "code"])


async def test_loop_control_and_terminal_invariants(settings):
    with pytest.raises(CapabilityError, match="conditional branch inside a loop"):
        await plan(
            settings,
            typed_flow(),
            [{"op": "insert_logic", "logic": "BREAK", "after": "1"}],
        )
    with pytest.raises(CapabilityError, match="following siblings"):
        await plan(
            settings,
            typed_flow(),
            [{"op": "insert_logic", "logic": "END", "after": "1"}],
        )


async def test_unverified_family_and_arbitrary_fields_fail_closed(settings):
    _, tools = fixtures(settings, typed_flow())
    for edits in [
        [{"op": "update_inputs", "step": "1", "inputs": {}, "raw": {"active": True}}],
        [
            {
                "op": "update_field",
                "collection": "flowVariables",
                "name": "counter",
                "type": "string",
            },
        ],
        [{"op": "set_handler_enabled", "enabled": True}],
        [
            {
                "op": "set_trigger",
                "type": "record_create",
                "inputs": {"table": "incident"},
            },
        ],
        [{"op": ["update_inputs"], "step": "1", "inputs": {}}],
        [{"op": "update_inputs", "step": "1", "inputs": []}],
    ]:
        assert (await preview(tools, edits))["status"] == "error"


async def test_delete_readback_still_present_is_unknown_outcome(settings):
    flow = typed_flow()
    _, tools = fixtures(settings, flow)

    def save(request):
        data = json.loads(request.content)
        # Independent GET intentionally returns the original flow, including the deleted action.
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    result = await preview(tools, [{"op": "delete_step", "step": "1"}])
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "UNKNOWN_OUTCOME"


async def test_subflow_output_requires_wait_for_completion(settings):
    flow = typed_flow()
    call = flow["subFlowInstances"][0]
    call["order"] = 2
    call["subFlow"] = {
        "outputs": [{"name": "count", "type": "integer", "children": []}]
    }
    flow["actionInstances"][1]["order"] = 3
    flow["actionInstances"][2]["order"] = 4
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "2" * 32,
                "inputs": {"count": "{{ui-4.count}}"},
            },
        ],
    )
    assert result["payload"]["label_cache"][0]["type"] == "integer"
    call["waitForCompletion"]["value"] = False
    with pytest.raises(CapabilityError, match="does not wait"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "2" * 32,
                    "inputs": {"count": "{{ui-4.count}}"},
                },
            ],
        )


async def test_step_record_path_resolves_dictionary_type(settings):
    result = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"log_message": "State={{ui-1.record.state}}"},
            },
        ],
    )
    assert result["payload"]["label_cache"][0]["parent_table_name"] == "sc_req_item"


async def test_reference_validation_is_not_skipped_on_cached_pill(settings):
    flow = typed_flow()
    flow["actionInstances"][2]["inputs"].append(
        {"name": "count", "value": 0, "parameter": {"type": "integer"}}
    )
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "after": "1",
                    "ref": "if",
                    "inputs": {"condition": "true"},
                },
                {"op": "move_step", "step": "1" * 32, "parent": "if"},
                {"op": "move_step", "step": "2" * 32, "parent": "if"},
                {
                    "op": "update_inputs",
                    "step": "2" * 32,
                    "inputs": {"count": "{{ui-1.count}}"},
                },
                {
                    "op": "update_inputs",
                    "step": "3" * 32,
                    "inputs": {"count": "{{ui-1.count}}"},
                },
            ],
        )


async def test_loop_controls_valid_only_inside_conditional_loop(settings):
    result = await plan(
        settings,
        typed_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "For Each",
                "after": "1",
                "ref": "loop",
                "inputs": {"items": "{{ui-1.records}}"},
            },
            {
                "op": "insert_logic",
                "logic": "If",
                "parent": "loop",
                "ref": "if",
                "inputs": {"condition": "true"},
            },
            {"op": "insert_logic", "logic": "BREAK", "parent": "if"},
        ],
    )
    assert (
        result["payload"]["flowLogicInstances"][-1]["flowLogicDefinition"]["type"]
        == "BREAK"
    )


async def test_reference_metadata_revision_blocks_apply_before_write(settings):
    flow = typed_flow()
    _, tools = fixtures(settings, flow)
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"log_message": "State={{ui-1.record.state}}"},
            },
        ],
    )
    assert result["status"] == "success", result
    http_mock.get(f"{BASE}/api/now/table/sys_choice").respond(
        200,
        json={"result": [{"label": "Changed", "value": "9"}]},
    )
    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow")
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW"
    assert not route.called


async def test_trigger_replacement_records_only_existing_trigger_tombstone(settings):
    from servicenow_mcp.capabilities.flows import semantics

    flow = typed_flow()
    flow["triggerInstances"] = [
        {
            "id": "f" * 32,
            "name": "Service Catalog",
            "type": "service_catalog",
            "triggerDefinitionId": "c43a1011c36813002841b63b12d3ae15",
            "inputs": [],
            "outputs": [],
        },
    ]
    fixtures(settings, flow)
    command = FlowCommand(
        action="edit",
        sys_id=FLOW,
        edits=[{"op": "set_trigger", "type": "service_catalog", "replace": True}],
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            command,
            {"flow": flow, "logic": [], "triggers": DEFS["trigger_categories"]},
            client,
        )
    assert result["payload"]["deletedTriggers"] == [{"id": "f" * 32}]
    saved = copy.deepcopy(result["payload"])
    saved.pop("deletedTriggers")
    saved["triggerInstances"][0]["id"] = "e" * 32
    semantics.compare_saved(
        result["payload"],
        saved,
        generated_ids=set(),
        pending_deletions=result["pending_deletions"],
    )


async def test_stage_attached_step_cannot_silently_disappear(settings):
    flow = typed_flow()
    flow["stages"] = {
        "stage-id": {
            "stageId": "stage-id",
            "label": "One",
            "value": "one",
            "componentIndexes": [0],
        },
    }
    with pytest.raises(CapabilityError, match="stage-attached"):
        await plan(settings, flow, [{"op": "delete_step", "step": "1"}])


@pytest.mark.parametrize(
    "kind", ["object", "array.object", "array.string", "array.integer"]
)
async def test_unreadable_whole_complex_producer_never_stages(settings, kind):
    flow = typed_flow()
    flow["actionInstances"][0]["actionType"]["outputs"].append(
        {"name": "complex", "type": kind, "children": []}
    )
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "complex",
            "value": "",
            "parameter": {"name": "complex", "type": kind, "children": []},
        },
    )
    coordinator, tools = fixtures(settings, flow)
    http_mock.get(
        f"{BASE}/api/now/processflow/action/action_types/{flow['actionInstances'][0]['actionTypeSysId']}"
    ).respond(403, json={"error": {"message": "unreadable"}})
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "2",
                "inputs": {"complex": "{{ui-1.complex}}"},
            },
        ],
    )
    assert result["status"] == "error", result
    assert result["error"]["code"] == (
        "FORBIDDEN" if kind == "object" else "UNAVAILABLE"
    ), result
    assert len(coordinator.store) == 0


@pytest.mark.parametrize("malformed", [None, "unknown", False, 1])
async def test_malformed_declared_children_cannot_stage_an_input_edit(
    settings, malformed
):
    flow = typed_flow()
    flow["actionInstances"][1]["inputs"][0]["parameter"]["children"] = malformed
    coordinator, tools = fixtures(settings, flow)
    result = await preview(
        tools,
        [{"op": "update_inputs", "step": "2", "inputs": {"log_message": "changed"}}],
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0


async def test_capture_blocker_is_explicit_and_does_not_enable_remaining_families(
    settings,
):
    from tests.test_flow import _register_and_get_tools

    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="describe"))
    ledger = result["data"]["authoring"]
    evidence = ledger["contract_capture"]
    assert evidence["state"] == "blocked_bootstrap"
    assert evidence["save_body_captures"] == 0
    assert evidence["effect_requests_forwarded"] == 0
    assert ledger["features"]["delete_saved_subflow"]["authorable"] is True
    assert ledger["features"]["delete_saved_subflow"]["live_verified"] is False
    assert ledger["features"]["create_delete_stage"]["authorable"] is True
    assert ledger["features"]["create_delete_stage"]["live_verified"] is False
    assert ledger["features"]["create_delete_interface_variable"]["authorable"] is True
    assert (
        ledger["features"]["create_delete_interface_variable"]["live_verified"] is False
    )
    assert ledger["features"]["whole_complex_pills"]["authorable"] is True
    assert ledger["features"]["whole_complex_pills"]["live_verified"] is False
    for family in (
        "handler_creation",
        "record_triggers",
        "scheduled_triggers",
        "try_catch_pair_moves",
    ):
        assert ledger["features"][family]["authorable"] is False
        assert ledger["features"][family]["live_verified"] is False
