"""Structural flow edits preserve the staged mutation contract and Studio's tree semantics."""

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import tree
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions, register_mutations

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
FLOW = "a" * 32
LOG = "5bc1bcc6531003003bf1d9109ec587d4"
DEFS = json.loads(
    (Path(__file__).parent / "fixtures/flow_studio_definitions.json").read_text()
)
DEFS_SC_OUTPUTS = next(
    trigger["outputs"]
    for category in DEFS["trigger_categories"]
    for trigger in category["triggers"].values()
    if trigger["type"] == "service_catalog"
)


def action(sys_id: str, ui: str, order: int, message: str) -> dict[str, Any]:
    return {
        "id": sys_id,
        "uiUniqueIdentifier": ui,
        "order": order,
        "parent": "",
        "type": "action",
        "name": "Log",
        "actionTypeSysId": LOG,
        "actionType": {"userCanRead": True},
        "inputs": [
            {
                "name": "log_message",
                "value": message,
                "maxsize": 255,
                "parameter": {"type": "string"},
            },
        ],
    }


def base_flow() -> dict[str, Any]:
    return {
        "id": FLOW,
        "name": "Fixture",
        "type": "flow",
        "scope": "global",
        "scopeName": "global",
        "active": False,
        "status": "draft",
        "isPublished": False,
        "masterSnapshotId": "",
        "snapshot": False,
        "masterSnapshot": False,
        "flowEngineVersion": 2,
        "security": {"can_write": True},
        "triggerInstances": [],
        "actionInstances": [
            action("1" * 32, "ui-1", 1, "one"),
            action("2" * 32, "ui-2", 2, "two"),
            action("3" * 32, "ui-3", 3, "three"),
        ],
        "flowLogicInstances": [],
        "subFlowInstances": [
            {
                "id": "4" * 32,
                "uiUniqueIdentifier": "ui-4",
                "order": 4,
                "parent": "",
                "type": "subflow",
                "name": "Provision",
                "subflowSysId": "5" * 32,
                "inputs": [
                    {
                        "name": "action",
                        "value": "REMOVE",
                        "parameter": {"type": "choice"},
                    },
                ],
                "waitForCompletion": {"value": True},
            },
        ],
        "inputs": [],
        "outputs": [],
        "flowVariables": [],
        "stages": [],
        "label_cache": [],
    }


IF_EDITS = [
    {"op": "set_trigger", "type": "service_catalog"},
    {
        "op": "insert_logic",
        "logic": "If",
        "after": "1" * 32,
        "ref": "branch",
        "inputs": {
            "condition_name": "RITM is open",
            "condition": "{{Service Catalog_1.request_item.state}}=1",
        },
    },
    {"op": "move_step", "step": "3" * 32, "parent": "branch"},
]


def fixtures(settings: Settings, flow: dict[str, Any]):
    server = MCPServer("flow-edit")
    coordinator = register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": {"data": flow}}),
    )
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": DEFS["flow_logic"]}
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200,
        json={"result": DEFS["trigger_categories"]},
    )

    def dictionary(request):
        query = request.url.params["sysparm_query"]
        if "element=state" in query and "sc_req_item" in query:
            return httpx2.Response(
                200,
                json={
                    "result": [
                        {
                            "name": "task",
                            "element": "state",
                            "column_label": "State",
                            "internal_type": "integer",
                            "reference": "",
                            "choice": "3",
                        },
                    ],
                },
            )
        return httpx2.Response(200, json={"result": []})

    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").mock(side_effect=dictionary)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"super_class.name": "task"}]}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_choice").respond(
        200,
        json={
            "result": [
                {"label": "New", "value": "1"},
                {"label": "Work in Progress", "value": "2"},
            ],
        },
    )
    return coordinator, get_tool_functions(server)


def native_trigger_labels(flow: dict[str, Any]) -> None:
    """Model the native parameter getter from the writable REST label slot."""
    for trigger in flow["triggerInstances"]:
        if (
            trigger.get("type")
            in {"service_catalog", "daily", "weekly", "monthly", "run_once", "repeat"}
            and "remoteSysId" in trigger
        ):
            for row in trigger["inputs"]:
                parameter = row["parameter"]
                if "type_label" in parameter:
                    parameter["uiDisplayTypeLabel"] = parameter.pop("type_label")


def persist(flow: dict[str, Any], *, generated_catalog_inputs: bool = False) -> None:
    """Model a saved definition separately from the preview's original object."""
    native_trigger_labels(flow)
    if generated_catalog_inputs:
        flow["inputs"] = copy.deepcopy(
            json.loads(
                (
                    Path(__file__).parent / "fixtures/flow_service_catalog_inputs.json"
                ).read_text()
            )["inputs"]
        )
        for index, row in enumerate(flow["inputs"], start=128):
            row["id"] = f"{index:032x}"
            http_mock.get(
                f"{BASE}/api/now/table/sys_hub_flow_input/{row['id']}"
            ).respond(
                200,
                json={
                    "result": {
                        "sys_id": row["id"],
                        "model": flow["id"],
                        "name": "var__m_sys_hub_flow_input_" + flow["id"],
                        "element": row["name"],
                        "sys_class_name": "sys_hub_flow_input",
                    }
                },
            )
    for index, logic in enumerate(flow["flowLogicInstances"], start=10):
        logic.setdefault("id", f"{index:032x}")
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": flow}}
    )


async def test_if_with_trigger_and_nested_step(settings: Settings):
    flow = base_flow()
    _, tools = fixtures(settings, flow)
    saved: dict[str, Any] = {}

    def save(request):
        data = json.loads(request.content)
        saved.update(data)
        persist(data, generated_catalog_inputs=True)
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=IF_EDITS)
    )
    assert preview["status"] == "success", preview
    assert not route.called
    summary = preview["data"]["summary"]
    assert summary["after"] == [
        "Trigger: Service Catalog",
        "Log (one)",
        "If {{Service Catalog_1.request_item.state}}=1",
        "    Log (three)",
        "Log (two)",
        "Provision (REMOVE)",
    ]
    assert summary["data_pills"] == ["Service Catalog_1.request_item.state"]

    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert route.call_count == 1

    logic = saved["flowLogicInstances"][0]
    nested = next(a for a in saved["actionInstances"] if a["id"] == "3" * 32)
    assert (logic["order"], logic["parent"]) == (2, "")
    assert logic["children"] == [nested["uiUniqueIdentifier"]]
    assert (nested["parent"], nested["order"]) == (logic["uiUniqueIdentifier"], 3)
    assert (nested["ancestorType"], nested["isPrecededByThen"]) == ("IF", True)
    assert [a["order"] for a in saved["actionInstances"]] == [1, 3, 4]
    assert saved["subFlowInstances"][0]["order"] == 5
    trigger = saved["triggerInstances"][0]
    assert (trigger["type"], trigger["triggerDefinitionId"]) == (
        "service_catalog",
        "c43a1011c36813002841b63b12d3ae15",
    )
    cache = saved["label_cache"][0]
    assert cache["name"] == "Service Catalog_1.request_item.state"
    assert (cache["parent_table_name"], cache["column_name"]) == (
        "sc_req_item",
        "state",
    )
    assert {c["value"] for c in cache["choices"]} == {"1", "2"}
    assert cache["usedInstances"] == {logic["uiUniqueIdentifier"]: ["condition"]}
    assert flow["triggerInstances"] == []


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        (
            [{"op": "insert_logic", "logic": "Else", "after": "1"}],
            "must directly follow",
        ),
        (
            [{"op": "insert_logic", "logic": "If", "after": "1", "inputs": {}}],
            "requires input 'condition'",
        ),
        ([{"op": "set_trigger", "type": "nope"}], "Unknown trigger type"),
        (
            [
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "after": "1",
                    "inputs": {"condition": "{{Service Catalog_1.x}}=1"},
                },
            ],
            "needs a trigger",
        ),
        (
            [{"op": "move_step", "step": "3" * 32, "parent": "1" * 32}],
            "Only flow-logic blocks",
        ),
        ([{"op": "rename"}], "Unsupported edit operation"),
        (
            [
                {"op": "insert_logic", "logic": "End", "after": "1", "ref": "e"},
                {"op": "move_step", "step": "2" * 32, "parent": "e"},
            ],
            "cannot contain steps",
        ),
        (
            [{"op": "move_step", "step": "1" * 32, "after": "1" * 32}],
            "relative to itself",
        ),
    ],
)
async def test_invalid_edits_never_stage_or_write(settings: Settings, edits, message):
    coordinator, tools = fixtures(settings, base_flow())
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert result["status"] == "error"
    assert message in result["error"]["message"]
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_if_else_if_else_chain_with_branches(settings: Settings):
    _, tools = fixtures(settings, base_flow())
    edits = [
        {"op": "set_trigger", "type": "service_catalog"},
        {
            "op": "insert_logic",
            "logic": "If",
            "after": "1" * 32,
            "ref": "if",
            "inputs": {"condition": "{{Service Catalog_1.request_item.state}}=1"},
        },
        {
            "op": "insert_logic",
            "logic": "Else If",
            "after": "if",
            "ref": "elif",
            "inputs": {"condition": "{{Service Catalog_1.request_item.state}}=2"},
        },
        {"op": "insert_logic", "logic": "Else", "after": "elif", "ref": "else"},
        {"op": "move_step", "step": "2" * 32, "parent": "if"},
        {"op": "move_step", "step": "3" * 32, "parent": "else"},
    ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["after"] == [
        "Trigger: Service Catalog",
        "Log (one)",
        "If {{Service Catalog_1.request_item.state}}=1",
        "    Log (two)",
        "Else If {{Service Catalog_1.request_item.state}}=2",
        "Else",
        "    Log (three)",
        "Provision (REMOVE)",
    ]


async def test_else_without_if_is_rejected_after_move(settings: Settings):
    _, tools = fixtures(settings, base_flow())
    edits = [
        {"op": "set_trigger", "type": "service_catalog"},
        {
            "op": "insert_logic",
            "logic": "If",
            "after": "1" * 32,
            "ref": "if",
            "inputs": {"condition": "{{Service Catalog_1.request_item.state}}=1"},
        },
        {"op": "insert_logic", "logic": "Else", "after": "if", "ref": "else"},
        {"op": "move_step", "step": "if", "after": "3" * 32},
    ]
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert "must directly follow" in result["error"]["message"]


async def test_saved_trigger_without_label_is_read_by_name(settings: Settings):
    # Live Studio responses carry only "name" on saved triggers. Apply must not fail after the write.
    _, tools = fixtures(settings, base_flow())

    def save(request):
        data = json.loads(request.content)
        for trigger in data["triggerInstances"]:
            trigger.pop("label", None)
        persist(data, generated_catalog_inputs=True)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=IF_EDITS)
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert result["data"]["outline"][0] == "Trigger: Service Catalog"


async def test_edit_flow_with_saved_unlabelled_trigger(settings: Settings):
    flow = base_flow()
    flow["triggerInstances"] = [
        {
            "name": "Service Catalog",
            "type": "service_catalog",
            "triggerDefinitionId": "c43a1011c36813002841b63b12d3ae15",
            "inputs": [],
            "outputs": DEFS_SC_OUTPUTS,
        },
    ]
    _, tools = fixtures(settings, flow)
    edits = [
        {
            "op": "insert_logic",
            "logic": "If",
            "after": "1" * 32,
            "inputs": {"condition": "{{Service Catalog_1.request_item.state}}=1"},
        },
    ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["before"][0] == "Trigger: Service Catalog"


async def test_malformed_save_response_is_unknown_outcome(settings: Settings):
    _, tools = fixtures(settings, base_flow())

    def save(request):
        data = json.loads(request.content)
        data["triggerInstances"] = [{"type": "service_catalog"}]
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=IF_EDITS)
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"


async def test_existing_trigger_requires_replace(settings: Settings):
    flow = base_flow()
    flow["triggerInstances"] = [
        {
            "label": "Service Catalog",
            "type": "service_catalog",
            "triggerDefinitionId": "c43a1011c36813002841b63b12d3ae15",
            "inputs": [],
            "outputs": [],
        },
    ]
    _, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "set_trigger",
                    "type": "record_create",
                    "inputs": {"table": "incident"},
                },
            ],
        ),
    )
    assert "replace=true" in result["error"]["message"]


async def test_drift_between_preview_and_apply_blocks_write(settings: Settings):
    flow = base_flow()
    _, tools = fixtures(settings, flow)
    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow")
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=IF_EDITS)
    )
    flow["actionInstances"][1]["inputs"][0]["value"] = "changed elsewhere"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert not route.called


async def test_server_structure_mismatch_reports_unknown_outcome(settings: Settings):
    _, tools = fixtures(settings, base_flow())

    def save(request):
        data = json.loads(request.content)
        for item in data["actionInstances"]:
            item["parent"] = ""
        data["flowLogicInstances"][0]["children"] = []
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=IF_EDITS)
    )
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
    assert (
        decode_response(await tools["mutation_apply"](preview_token=token))["status"]
        == "error"
    )


def test_tree_round_trip_preserves_branch_membership():
    flow = base_flow()
    logic = {
        "uiUniqueIdentifier": "if-1",
        "order": 2,
        "parent": "",
        "type": "flowlogic",
        "definitionId": "af4e1945c3e232002841b63b12d3ae3e",
        "flowLogicDefinition": next(d for d in DEFS["flow_logic"] if d["type"] == "IF"),
        "inputs": [],
    }
    flow["flowLogicInstances"] = [logic]
    flow["actionInstances"][1]["order"] = 3
    flow["actionInstances"][1]["parent"] = "if-1"
    flow["actionInstances"][2]["order"] = 4
    flow["subFlowInstances"][0]["order"] = 5
    roots = tree.build_tree(flow)
    assert [s.uid for s in roots] == ["ui-1", "if-1", "ui-3", "ui-4"]
    assert [s.uid for s in roots[1].children] == ["ui-2"]
    payload = tree.flatten(flow, roots)
    again = tree.build_tree(payload)
    assert tree.outline(again) == tree.outline(roots)


def test_tree_rejects_orphan_parent():
    flow = copy.deepcopy(base_flow())
    flow["actionInstances"][0]["parent"] = "missing"
    with pytest.raises(CapabilityError):
        tree.build_tree(flow)


@pytest.mark.parametrize(
    "defect",
    [
        "second_input",
        "subflow_input",
        "subflow_move",
        "subflow_parent",
        "subflow_omitted",
        "interface",
        "acl",
        "variables",
        "stages",
        "handler",
        "opaque",
        "definition",
        "wait",
        "duplicate_inputs",
        "missing_collection",
        "pill",
        "read_timeout",
        "malformed",
    ],
)
async def test_independent_readback_catches_semantic_defects(
    settings: Settings, defect
):
    flow = base_flow()
    flow["actionInstances"][0]["inputs"].append({"name": "level", "value": "info"})
    _, tools = fixtures(settings, flow)

    def save(request):
        data = json.loads(request.content)
        persisted = copy.deepcopy(data)
        if defect == "second_input":
            persisted["actionInstances"][0]["inputs"][1]["value"] = "error"
        elif defect == "subflow_input":
            persisted["subFlowInstances"][0]["inputs"][0]["value"] = "ADD"
        elif defect == "subflow_move":
            persisted["subFlowInstances"][0]["order"] = 9
        elif defect == "subflow_parent":
            persisted["subFlowInstances"][0]["parent"] = "missing"
        elif defect == "subflow_omitted":
            persisted["subFlowInstances"] = []
        elif defect == "interface":
            persisted["inputs"] = [{"name": "unexpected"}]
        elif defect == "acl":
            persisted["contentACLFilteredOrders"] = [4]
        elif defect == "variables":
            persisted["flowVariables"] = [{"name": "lost"}]
        elif defect == "stages":
            persisted["stages"] = [{"name": "lost"}]
        elif defect == "handler":
            persisted["startingIndexOfErrorHandlingInstances"] = 2
        elif defect == "opaque":
            persisted["vendorSetting"] = {"enabled": False}
        elif defect == "definition":
            persisted["subFlowInstances"][0]["subflowSysId"] = "6" * 32
        elif defect == "wait":
            persisted["subFlowInstances"][0]["waitForCompletion"]["value"] = False
        elif defect == "duplicate_inputs":
            persisted["actionInstances"][0]["inputs"].append(
                persisted["actionInstances"][0]["inputs"][0]
            )
        elif defect == "missing_collection":
            persisted.pop("subFlowInstances")
        elif defect == "pill":
            persisted["actionInstances"][0]["inputs"][1]["value"] = (
                "{{Service Catalog_1.request_item.state}}"
            )
        elif defect == "malformed":
            persisted["subFlowInstances"][0]["waitForCompletion"] = None
        http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
            200, json={"result": {"data": persisted}}
        )
        if defect == "read_timeout":
            http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").mock(
                side_effect=httpx2.ReadTimeout("private-marker"),
            )
        # The save echo is correct. Only the independent GET exposes the defect.
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
        ),
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert "private-marker" not in str(result)
    assert route.call_count == 1


def test_semantic_comparison_catches_same_outline_different_values():
    from servicenow_mcp.capabilities.flows.semantics import compare_saved

    flow = base_flow()
    flow["actionInstances"][0]["inputs"].append({"name": "level", "value": "info"})
    changed = copy.deepcopy(flow)
    changed["actionInstances"][0]["inputs"][1]["value"] = "error"
    assert tree.outline(tree.build_tree(flow)) == tree.outline(tree.build_tree(changed))
    with pytest.raises(CapabilityError, match="values differ"):
        compare_saved(flow, changed, generated_ids=set())


def test_semantics_preserves_opaque_fields_and_checks_all_pills():
    from servicenow_mcp.capabilities.flows.semantics import compare_saved

    flow = base_flow()
    flow["vendorSetting"] = {"opaque": [1, {"keep": True}]}
    flow["actionInstances"][0]["inputs"][0]["value"] = {
        "nested": ["{{Service Catalog_1.request_item.state}}", False]
    }
    before = copy.deepcopy(flow)
    compare_saved(flow, copy.deepcopy(flow), generated_ids=set())
    assert flow == before
    changed = copy.deepcopy(flow)
    changed["actionInstances"][0]["inputs"][0]["value"]["nested"][0] = (
        "{{Service Catalog_1.request_item.number}}"
    )
    with pytest.raises(CapabilityError):
        compare_saved(flow, changed, generated_ids=set())


@pytest.mark.parametrize(
    "field", ["type", "parent", "uiUniqueIdentifier", "inputs", "actionTypeSysId"]
)
def test_semantics_rejects_partial_step_fields(field):
    from servicenow_mcp.capabilities.flows.semantics import studio_semantics

    flow = base_flow()
    flow["actionInstances"][0].pop(field)
    with pytest.raises(CapabilityError):
        studio_semantics(flow)


@pytest.mark.parametrize("field", ["inputs", "outputs", "flowVariables", "stages"])
def test_semantics_rejects_partial_interfaces(field):
    from servicenow_mcp.capabilities.flows.semantics import studio_semantics

    flow = base_flow()
    flow.pop(field)
    with pytest.raises(CapabilityError):
        studio_semantics(flow)


def test_cyclic_logic_cannot_disappear_from_tree():
    flow = base_flow()
    flow["flowLogicInstances"] = [
        {"uiUniqueIdentifier": "a", "parent": "b", "order": 5},
        {"uiUniqueIdentifier": "b", "parent": "a", "order": 6},
    ]
    with pytest.raises(CapabilityError, match="cyclic"):
        tree.build_tree(flow)


async def test_edit_preserves_interfaces_handlers_and_opaque_state(settings: Settings):
    flow = base_flow()
    for key in ("inputs", "outputs", "flowVariables", "stages"):
        flow[key] = [{"name": key, "vendor": {"keep": [False, 0, "{{opaque.pill}}"]}}]
    flow["startingIndexOfErrorHandlingInstances"] = -1
    flow["errorHandlerSettings"] = {"opaque": True}
    flow["vendorSetting"] = ["unchanged"]
    before = copy.deepcopy(flow)
    _, tools = fixtures(settings, flow)

    def save(request):
        data = json.loads(request.content)
        for key in (
            "inputs",
            "outputs",
            "flowVariables",
            "stages",
            "errorHandlerSettings",
            "vendorSetting",
        ):
            assert data[key] == before[key]
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
        ),
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert flow == before
