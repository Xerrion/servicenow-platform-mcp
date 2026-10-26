"""Experimental Studio authoring preserves the staged mutation safety contract."""

import copy
import json
from typing import TYPE_CHECKING

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import editing
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions, register_mutations

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
FLOW = "a" * 32
ACTION = "b" * 32
STUDIO = f"{BASE}/api/now/processflow"
SUBFLOW = "3" * 32
SNAPSHOT = "4" * 32


def persist(flow):
    for index, row in enumerate(
        flow["actionInstances"] + flow["subFlowInstances"], start=128
    ):
        row.setdefault("id", f"{index:032x}")
    http_mock.get(f"{STUDIO}/flow/{FLOW}").respond(200, json={"result": {"data": flow}})


def fixtures(settings):
    server = MCPServer("flow-authoring")
    coordinator = register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    flow = {
        "id": FLOW,
        "name": "Fixture",
        "type": "flow",
        "scope": "global",
        "active": False,
        "status": "draft",
        "isPublished": False,
        "masterSnapshotId": "",
        "security": {"can_write": True},
        "actionInstances": [],
        "triggerInstances": [],
        "label_cache": [],
        "flowLogicInstances": [],
        "subFlowInstances": [],
        "inputs": [],
        "outputs": [],
        "flowVariables": [],
        "stages": [],
        "snapshot": False,
        "masterSnapshot": False,
        "flowEngineVersion": 2,
    }
    action = {
        "id": ACTION,
        "name": "Log",
        "internal_name": "log",
        "state": "published",
        "is_snapshot": True,
        "entitled": True,
        "security": {"can_read": True},
        "parent_action": "c" * 32,
        "inputs": [
            {"id": "d" * 32, "name": "message", "type": "string", "maxsize": 255}
        ],
    }
    http_mock.get(f"{STUDIO}/flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": {"data": flow}}),
    )
    http_mock.get(f"{STUDIO}/action/action_types/{ACTION}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": action}),
    )
    http_mock.get(f"{BASE}/api/now/table/sys_scope/global").respond(
        200,
        json={"result": {"sys_id": "global", "name": "Global"}},
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow").respond(
        200, json={"result": []}
    )
    return coordinator, get_tool_functions(server), flow, action


async def test_create_preview_then_one_post(settings: Settings):
    _, tools, flow, _ = fixtures(settings)
    flow.update(
        {
            "description": "",
            "runAs": "user",
            "access": "public",
            "flowPriority": "MEDIUM",
        },
    )
    route = http_mock.post(f"{STUDIO}/flow").respond(
        200,
        json={
            "result": {
                "data": {
                    "id": FLOW,
                    "name": "Fixture",
                    "scope": "global",
                    "active": False,
                    "status": "draft",
                },
            },
        },
    )
    preview = decode_response(
        await tools["flow_preview"](action="create", name="Fixture", scope="global")
    )
    assert preview["status"] == "success"
    assert not route.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success"
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.url.params["sysparm_transaction_scope"] == "global"
    assert json.loads(request.content)["active"] is False


async def test_append_preserves_existing_definition_and_static_inputs(
    settings: Settings,
):
    _, tools, flow, _ = fixtures(settings)
    original = copy.deepcopy(flow)

    def save(request):
        data = json.loads(request.content)
        assert data["scope"] == "global"
        assert data["active"] is False
        assert data["actionInstances"][0]["inputs"][0]["value"] == "Fixture message"
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data, "errorCode": 0}})

    route = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action",
            sys_id=FLOW,
            action_type_sys_id=ACTION,
            inputs={"message": "Fixture message"},
        ),
    )
    assert not route.called
    token = preview["data"]["preview_token"]
    assert (
        decode_response(await tools["mutation_apply"](preview_token=token))["status"]
        == "success"
    )
    assert route.call_count == 1
    assert flow == original
    assert (await tools["mutation_apply"](preview_token=token)).is_error


@pytest.mark.parametrize("change", ["flow", "action", "production", "family"])
async def test_drift_or_policy_change_blocks_dispatch(settings: Settings, change):
    coordinator, tools, flow, action = fixtures(settings)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=ACTION
        )
    )
    if change == "flow":
        flow["updated"] = "changed"
    elif change == "action":
        action["name"] = "changed"
    elif change == "production":
        settings.servicenow_env = "prod"
    else:
        coordinator.families = frozenset()
    result = await tools["mutation_apply"](
        preview_token=preview["data"]["preview_token"]
    )
    assert result.is_error
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active", True),
        ("isPublished", True),
        ("status", "published"),
        ("triggerInstances", [{"id": "x"}]),
        ("label_cache", None),
        ("flowLogicInstances", [{"id": "x"}]),
    ],
)
async def test_unsupported_flow_never_stages(settings: Settings, field, value):
    coordinator, tools, flow, _ = fixtures(settings)
    flow[field] = value
    assert (
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=ACTION
        )
    ).is_error
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "inputs",
    [
        {"unknown": "x"},
        {"message": {}},
        {"message": "{{pill}}"},
        {"message": "x" * 256},
    ],
)
async def test_invalid_inputs_never_stage(settings: Settings, inputs):
    coordinator, tools, _, _ = fixtures(settings)
    assert (
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=ACTION, inputs=inputs
        )
    ).is_error
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "failure",
    ["timeout", "invalid_json", "missing_result", "error_code", "missing_action"],
)
async def test_put_failure_consumes_token_without_replay(settings: Settings, failure):
    coordinator, tools, flow, _ = fixtures(settings)

    def save(request):
        if failure == "timeout":
            raise httpx2.ReadTimeout("private-marker", request=request)
        if failure == "invalid_json":
            return httpx2.Response(200, content=b"private-marker")
        body = (
            {}
            if failure == "missing_result"
            else {
                "result": {
                    "data": flow,
                    "errorCode": 9 if failure == "error_code" else 0,
                },
            }
        )
        return httpx2.Response(200, json=body)

    route = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=ACTION
        )
    )
    token = preview["data"]["preview_token"]
    result = await tools["mutation_apply"](preview_token=token)
    assert result.is_error
    assert "unknown" in str(result).lower() or "check" in str(result).lower()
    assert "private-marker" not in str(result)
    assert len(coordinator.store) == 0
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert route.call_count == 1


@pytest.mark.parametrize(
    "change", ["duplicate", "missing_scope", "invalid_scope", "separator"]
)
async def test_creation_rejects_invalid_or_duplicate_target(settings: Settings, change):
    coordinator, tools, _, _ = fixtures(settings)
    args = {"action": "create", "name": "Fixture", "scope": "global"}
    if change == "duplicate":
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow").respond(
            200, json={"result": [{"sys_id": FLOW}]}
        )
    elif change == "missing_scope":
        args.pop("scope")
    elif change == "invalid_scope":
        args["scope"] = "not-an-id"
    else:
        args["name"] = "Fixture^ORactive=true"
    assert (await tools["flow_preview"](**args)).is_error
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    ("kind", "value", "valid"),
    [
        ("boolean", True, True),
        ("boolean", "true", False),
        ("integer", 2, True),
        ("integer", True, False),
        ("choice", "info", True),
        ("choice", "other", False),
    ],
)
async def test_input_types_and_choices(settings: Settings, kind, value, valid):
    _, tools, _, action = fixtures(settings)
    action["inputs"][0].update(
        {"type": kind, "choices": [{"value": "info", "label": "Info"}]}
    )
    result = await tools["flow_preview"](
        action="add_action",
        sys_id=FLOW,
        action_type_sys_id=ACTION,
        inputs={"message": value},
    )
    assert result.is_error is not valid


async def test_preserve_prior_action_and_reject_changed_save_inputs(settings: Settings):
    _, tools, flow, _ = fixtures(settings)
    flow["actionInstances"] = [
        {
            "id": "e" * 32,
            "uiUniqueIdentifier": "existing-action",
            "type": "action",
            "parent": "",
            "order": "1",
            "actionTypeSysId": ACTION,
            "actionType": {"userCanRead": True},
            "inputs": [
                {
                    "name": "message",
                    "value": "Existing",
                    "parameter": {"type": "string"},
                }
            ],
        },
    ]

    def save(request):
        data = json.loads(request.content)
        existing = flow["actionInstances"][0]
        assert {key: data["actionInstances"][0][key] for key in existing} == {
            **existing,
            "order": 1,
        }
        assert data["actionInstances"][1]["order"] == 2
        data["actionInstances"][1]["inputs"][0]["value"] = "Changed by server"
        data["actionInstances"][1]["id"] = "f" * 32
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action",
            sys_id=FLOW,
            action_type_sys_id=ACTION,
            inputs={"message": "New"},
        ),
    )
    result = await tools["mutation_apply"](
        preview_token=preview["data"]["preview_token"]
    )
    assert result.is_error
    assert result.structured_content["error"]["code"] == "UNKNOWN_OUTCOME"


@pytest.mark.parametrize(
    "mismatch", [None, "order", "type", "missing_existing", "extra_action"]
)
async def test_server_assigns_new_action_id_without_replay(
    settings: Settings, mismatch
):
    _, tools, flow, _ = fixtures(settings)
    existing = {
        "id": "e" * 32,
        "uiUniqueIdentifier": "existing-action",
        "type": "action",
        "parent": "",
        "order": "1",
        "actionTypeSysId": ACTION,
        "actionType": {"userCanRead": True},
        "inputs": [
            {"name": "message", "value": "Existing", "parameter": {"type": "string"}}
        ],
    }
    flow["actionInstances"] = [existing]
    assigned_id = "f" * 32

    def save(request):
        data = json.loads(request.content)
        assert {key: data["actionInstances"][0][key] for key in existing} == {
            **existing,
            "order": 1,
        }
        data["actionInstances"][1]["id"] = assigned_id
        if mismatch == "order":
            data["actionInstances"][1]["order"] = "3"
        elif mismatch == "type":
            data["actionInstances"][1]["actionTypeSysId"] = "1" * 32
        elif mismatch == "missing_existing":
            data["actionInstances"].pop(0)
        elif mismatch == "extra_action":
            data["actionInstances"].append({**existing, "id": "2" * 32})
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action",
            sys_id=FLOW,
            action_type_sys_id=ACTION,
            inputs={"message": "New"},
        ),
    )
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    assert result["status"] == ("error" if mismatch else "success")
    if mismatch:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    else:
        assert result["data"]["action_sys_id"] == assigned_id
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert route.call_count == 1


def subflow_fixture(settings):
    coordinator, tools, flow, action = fixtures(settings)
    flow["actionInstances"] = [
        {
            "id": "e" * 32,
            "uiUniqueIdentifier": "existing-action",
            "type": "action",
            "parent": "",
            "order": "3",
            "actionTypeSysId": ACTION,
            "actionType": {"userCanRead": True},
            "inputs": [
                {
                    "name": "message",
                    "value": "Existing",
                    "parameter": {"type": "string"},
                }
            ],
        },
    ]
    parent = {
        "sys_id": SUBFLOW,
        "type": "subflow",
        "active": "true",
        "master_snapshot": SNAPSHOT,
    }
    definition = {
        "id": SNAPSHOT,
        "parentFlow": SUBFLOW,
        "type": "subflow",
        "name": "Fixture subflow",
        "snapshot": True,
        "masterSnapshot": True,
        "userCanRead": True,
        "isFlowEntitled": True,
        "security": {"can_read": True},
        "scope": "global",
        "access": "public",
        "inputs": [
            {
                "id": "5" * 32,
                "name": "action",
                "type": "choice",
                "mandatory": True,
                "choices": [{"value": "REMOVE", "label": "REMOVE"}],
            },
            {"id": "6" * 32, "name": "user", "type": "string", "mandatory": True},
            {"id": "7" * 32, "name": "group_name", "type": "string", "mandatory": True},
        ],
    }
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{SUBFLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": parent}),
    )
    http_mock.get(f"{STUDIO}/flow/{SNAPSHOT}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": definition}}
        ),
    )
    return coordinator, tools, flow, action, parent, definition


def subflow_args():
    return {
        "action": "add_subflow",
        "sys_id": FLOW,
        "subflow_sys_id": SUBFLOW,
        "inputs": {"action": "REMOVE", "user": "test_user", "group_name": "TEST_GROUP"},
    }


@pytest.mark.parametrize("subflow", [False, True])
@pytest.mark.parametrize("provided", [False, True])
async def test_append_matches_insert_plan_for_native_scalar_defaults_and_context(
    settings: Settings, subflow, provided
):
    if subflow:
        _, tools, flow, _, _, definition = subflow_fixture(settings)
    else:
        _, tools, flow, definition = fixtures(settings)
    definition["inputs"] = [
        {
            "id": "d" * 32,
            "name": "message",
            "type": "string",
            "defaultValue": "Default message",
            "maxsize": 255,
        },
        {
            "id": "1" * 32,
            "name": "enabled",
            "type": "boolean",
            "defaultValue": "true",
        },
        {
            "id": "2" * 32,
            "name": "count",
            "type": "integer",
            "defaultValue": "7",
        },
        {
            "id": "3" * 32,
            "name": "level",
            "type": "choice",
            "defaultValue": "info",
            "choices": [
                {"value": "info", "label": "Information"},
                {"value": "debug", "label": "Debug"},
            ],
        },
    ]
    inputs = (
        {"message": "Provided message", "enabled": False, "count": 0, "level": "debug"}
        if provided
        else {}
    )
    values = inputs or {
        "message": "Default message",
        "enabled": True,
        "count": "7",
        "level": "info",
    }
    target_key = "subflow_sys_id" if subflow else "action_type_sys_id"
    identity = SUBFLOW if subflow else ACTION
    command = FlowCommand(
        action="edit",
        sys_id=FLOW,
        expected_scope="global",
        expected_revision="7",
        edits=[
            {
                "op": "insert_subflow" if subflow else "insert_action",
                target_key: identity,
                "inputs": inputs,
                "ref": "appended_step",
            }
        ],
    )
    original = copy.deepcopy(flow)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        state = await editing.load_state(command, client)
        planned = await editing.plan(command, state, client)
    collection = "subFlowInstances" if subflow else "actionInstances"
    planned_step = planned["payload"][collection][-1]
    assert {item["name"]: item["value"] for item in planned_step["inputs"]} == values
    collections = ("actionInstances", "subFlowInstances")
    assert {
        row["id"]: row["uiUniqueIdentifier"]
        for name in collections
        for row in planned["payload"][name]
        if row.get("id")
    } == {
        row["id"]: row["uiUniqueIdentifier"]
        for name in collections
        for row in original[name]
    }
    assert planned_step["order"] == max(
        row["order"] for name in collections for row in planned["payload"][name]
    )
    header = {
        "sys_id": FLOW,
        "sys_scope": "global",
        "sys_mod_count": "7",
        "sys_updated_on": "2026-01-01 00:00:00",
    }
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": header})
    )
    persisted = "9" * 32

    def save(request):
        data = json.loads(request.content)
        assert data == planned["payload"]
        assert data[collection][-1]["order"] == summary["proposed_order"]
        data[collection][-1]["id"] = persisted
        persist(data)
        header.update(sys_mod_count="8", sys_updated_on="2026-01-01 00:00:01")
        return httpx2.Response(200, json={"result": {"data": data}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_subflow" if subflow else "add_action",
            sys_id=FLOW,
            expected_scope="global",
            expected_revision="7",
            inputs=inputs,
            **{target_key: identity},
        )
    )
    assert preview["status"] == "success", preview
    summary = preview["data"]["summary"]
    assert summary["inputs"] == values
    assert summary["proposed_order"] == planned_step["order"]
    assert summary["existing_action_count"] == len(original["actionInstances"])
    assert summary["existing_subflow_count"] == len(original["subFlowInstances"])
    assert summary["context"] == {
        "state": "bound",
        "expected_scope": "global",
        "expected_revision": "7",
        "scope_semantics": "target_application",
    }
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert result["data"] == {
        "action": "add_subflow" if subflow else "add_action",
        "table": "sys_hub_flow",
        "sys_id": FLOW,
        "name": flow["name"],
        "active": False,
        "status": "draft",
        "subflow_instance_sys_id" if subflow else "action_sys_id": persisted,
        "revision": "8",
    }
    assert write.call_count == 1
    assert flow == original


@pytest.mark.parametrize("subflow", [False, True])
@pytest.mark.parametrize("change", ["scope", "revision", "studio_scope"])
async def test_append_context_drift_rejects_before_save(
    settings: Settings, subflow, change
):
    if subflow:
        coordinator, tools, flow, _, _, _ = subflow_fixture(settings)
        args = subflow_args()
    else:
        coordinator, tools, flow, _ = fixtures(settings)
        args = {"action": "add_action", "sys_id": FLOW, "action_type_sys_id": ACTION}
    header = {
        "sys_id": FLOW,
        "sys_scope": "global",
        "sys_mod_count": "7",
        "sys_updated_on": "2026-01-01 00:00:00",
    }
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": header})
    )
    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").respond(200, json={})
    preview = decode_response(
        await tools["flow_preview"](
            **args, expected_scope="global", expected_revision="7"
        )
    )
    assert preview["status"] == "success", preview
    if change == "scope":
        header["sys_scope"] = "8" * 32
    elif change == "revision":
        header["sys_mod_count"] = "8"
    else:
        flow["scope"] = "8" * 32
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == (
        "STALE_PREVIEW" if change == "revision" else "CONTEXT_MISMATCH"
    )
    assert not write.called
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_append_published_subflow_preserves_actions_and_waits(settings: Settings):
    _, tools, flow, _, _, _ = subflow_fixture(settings)
    before = copy.deepcopy(flow)
    persisted = "8" * 32

    def save(request):
        data = json.loads(request.content)
        existing = before["actionInstances"][0]
        assert len(data["actionInstances"]) == 1
        assert {
            key: data["actionInstances"][0][key] for key in existing if key != "order"
        } == {key: value for key, value in existing.items() if key != "order"}
        instance = data["subFlowInstances"][0]
        assert instance["order"] == preview["data"]["summary"]["proposed_order"]
        assert instance["order"] > data["actionInstances"][0]["order"]
        assert instance["subflowSysId"] == SNAPSHOT
        assert instance["waitForCompletion"]["value"] is True
        assert {i["name"]: i["value"] for i in instance["inputs"]} == subflow_args()[
            "inputs"
        ]
        instance["id"] = persisted
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(await tools["flow_preview"](**subflow_args()))
    assert preview["status"] == "success"
    assert not route.called
    snapshot_request = next(
        c.request for c in http_mock.calls if c.request.url.path.endswith(SNAPSHOT)
    )
    assert snapshot_request.url.params["use_min_flow"] == "true"
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    assert result["status"] == "success"
    assert result["data"]["subflow_instance_sys_id"] == persisted
    assert route.call_count == 1
    assert flow == before
    assert (await tools["mutation_apply"](preview_token=token)).is_error


@pytest.mark.parametrize(
    "change",
    [
        "unpublished",
        "wrong_parent",
        "not_snapshot",
        "unreadable",
        "not_entitled",
        "private_scope",
        "invalid_choice",
        "missing_user",
        "pill",
        "both_targets",
    ],
)
async def test_subflow_preview_rejects_unsafe_or_invalid_contract(
    settings: Settings, change
):
    coordinator, tools, _, _, parent, definition = subflow_fixture(settings)
    args = subflow_args()
    if change == "unpublished":
        parent["master_snapshot"] = ""
    elif change == "wrong_parent":
        definition["parentFlow"] = "9" * 32
    elif change == "not_snapshot":
        definition["snapshot"] = False
    elif change == "unreadable":
        definition["security"]["can_read"] = False
    elif change == "not_entitled":
        definition["isFlowEntitled"] = False
    elif change == "private_scope":
        definition.update({"scope": "9" * 32, "access": "package_private"})
    elif change == "invalid_choice":
        args["inputs"]["action"] = "INVALID"
    elif change == "missing_user":
        args["inputs"].pop("user")
    elif change == "pill":
        args["inputs"]["user"] = "{{pill}}"
    else:
        args["action_type_sys_id"] = ACTION
    assert (await tools["flow_preview"](**args)).is_error
    assert len(coordinator.store) == 0
    assert all(c.request.method == "GET" for c in http_mock.calls)


async def test_subflow_contract_drift_consumes_token_without_write(settings: Settings):
    coordinator, tools, _, _, _, definition = subflow_fixture(settings)
    preview = decode_response(await tools["flow_preview"](**subflow_args()))
    definition["name"] = "Changed"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert len(coordinator.store) == 0
    assert all(c.request.method == "GET" for c in http_mock.calls)


async def test_append_action_after_subflow_uses_global_order(settings: Settings):
    _, tools, flow, _, _, definition = subflow_fixture(settings)
    flow["subFlowInstances"] = [
        {
            "id": "8" * 32,
            "uiUniqueIdentifier": "existing-subflow",
            "type": "subflow",
            "parent": "",
            "order": "4",
            "subflowSysId": SNAPSHOT,
            "subFlow": definition,
            "inputs": [],
            "waitForCompletion": {"value": True},
        },
    ]

    def save(request):
        data = json.loads(request.content)
        appended = data["actionInstances"][-1]
        assert appended["order"] == preview["data"]["summary"]["proposed_order"]
        assert appended["order"] > max(
            row["order"]
            for row in data["actionInstances"][:-1] + data["subFlowInstances"]
        )
        assert data["actionInstances"][0]["id"] == flow["actionInstances"][0]["id"]
        existing = flow["subFlowInstances"][0]
        assert len(data["subFlowInstances"]) == 1
        assert {
            key: data["subFlowInstances"][0][key] for key in existing if key != "order"
        } == {key: value for key, value in existing.items() if key != "order"}
        persist(data)
        return httpx2.Response(200, json={"result": {"data": data}})

    http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(
        await tools["flow_preview"](
            action="add_action", sys_id=FLOW, action_type_sys_id=ACTION
        )
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success"


@pytest.mark.parametrize("kind", ["add_action", "add_subflow"])
async def test_append_checks_independent_read_not_only_save_echo(
    settings: Settings, kind
):
    if kind == "add_subflow":
        _, tools, _, _, _, _ = subflow_fixture(settings)
        args = subflow_args()
    else:
        _, tools, _, _ = fixtures(settings)
        args = {
            "action": kind,
            "sys_id": FLOW,
            "action_type_sys_id": ACTION,
            "inputs": {"message": "expected"},
        }

    def save(request):
        data = json.loads(request.content)
        collection = "subFlowInstances" if kind == "add_subflow" else "actionInstances"
        data[collection][-1]["id"] = "9" * 32
        stored = copy.deepcopy(data)
        stored[collection][-1]["inputs"][0]["value"] = "different"
        persist(stored)
        return httpx2.Response(200, json={"result": {"data": data}})

    route = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    preview = decode_response(await tools["flow_preview"](**args))
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
    assert (await tools["mutation_apply"](preview_token=token)).is_error


async def test_create_requires_independent_complete_readback(settings: Settings):
    _, tools, _, _ = fixtures(settings)
    route = http_mock.post(f"{STUDIO}/flow").respond(
        200,
        json={
            "result": {
                "data": {
                    "id": FLOW,
                    "name": "Fixture",
                    "scope": "global",
                    "active": False,
                    "status": "draft",
                },
            },
        },
    )
    preview = decode_response(
        await tools["flow_preview"](action="create", name="Fixture", scope="global")
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
