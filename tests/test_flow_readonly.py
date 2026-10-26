"""Flow reads remain usable after deleting the authoring implementation."""

import copy
import json

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import TypeAdapter, ValidationError

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.capabilities.changes.models import ChangeCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import SYS_ID_FLOW, _encode_values, _register_and_get_tools

BASE = "https://test.service-now.com/api/now/table"
DEFINITION = "d" * 32


def saved_structure(defect=None):
    header = {
        "sys_id": SYS_ID_FLOW,
        "name": "Read-only flow",
        "type": "flow",
        "active": "false",
        "master_snapshot": "",
        "latest_snapshot": "",
        "sys_mod_count": "3",
        "sys_updated_on": "2026-10-26 10:00:00",
    }
    action = {
        "sys_id": "a" * 32,
        "ui_id": "action-one",
        "parent_ui_id": "",
        "order": "1",
        "action_type": DEFINITION,
        "values": _encode_values([]),
    }
    if defect == "orphan":
        action["parent_ui_id"] = "missing-parent"
    elif defect == "order":
        action["order"] = "invalid"
    elif defect == "binding":
        action["values"] = _encode_values([{"name": "secret-field-without-value"}])
    elif defect == "missing_revision":
        header.pop("sys_mod_count")
    elif defect == "empty_revision":
        header["sys_mod_count"] = ""
    rows = [action]
    if defect in {"duplicate", "truncated"}:
        rows.append({**action, "sys_id": "b" * 32, "ui_id": "action-two"})
    reads = 0

    def flow_header(_request):
        nonlocal reads
        reads += 1
        result = copy.deepcopy(header)
        if defect == "race" and reads >= 3:
            result["sys_mod_count"] = "4"
        return httpx2.Response(
            403 if defect == "denied" else 200, json={"result": result}
        )

    http_mock.get(f"{BASE}/sys_hub_flow/{SYS_ID_FLOW}").mock(side_effect=flow_header)
    datasets = {
        "sys_hub_action_instance_v2": rows,
        "sys_hub_action_instance": [{"sys_id": "c" * 32}] if defect == "v1" else [],
        "sys_hub_sub_flow_instance_v2": [],
        "sys_hub_flow_logic_instance_v2": [],
        "sys_hub_flow_logic": [],
        "sys_hub_trigger_instance_v2": [],
        "sys_hub_trigger_instance": [],
        "sys_hub_action_type_base": [{"sys_id": DEFINITION, "name": "Log"}],
    }
    for table, values in datasets.items():
        http_mock.get(f"{BASE}/{table}").respond(200, json={"result": values})


@pytest.mark.parametrize(
    "defect,valid,complete,code",
    [
        (None, True, True, None),
        ("orphan", False, True, "INVALID_STRUCTURE"),
        ("duplicate", False, True, "INVALID_STRUCTURE"),
        ("order", False, True, "INVALID_STRUCTURE"),
        ("binding", False, True, "INVALID_STRUCTURE"),
        ("denied", None, False, "UNREADABLE_DEFINITION"),
        ("truncated", None, False, "INCOMPLETE_READ"),
        ("v1", None, False, "UNSUPPORTED_V1_VALIDATION"),
        ("race", None, False, "READ_RACE"),
        ("missing_revision", None, False, "READ_RACE"),
        ("empty_revision", None, False, "READ_RACE"),
    ],
)
async def test_validation_checks_only_saved_structure(
    settings, defect, valid, complete, code
):
    saved_structure(defect)
    if defect == "truncated":
        settings.max_row_limit = 1
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=SYS_ID_FLOW))
    assert result["status"] == "success", result
    data = result["data"]
    assert data["valid"] is valid, data
    assert data["complete"] is complete
    assert data["completeness_scope"] == "saved_v2_structure_only"
    assert data["semantic_validation"] == data["runtime_validation"] == "not_performed"
    if code:
        assert code in {item["code"] for item in data["diagnostics"]}, data
    else:
        assert data["diagnostics"] == []
    assert "secret-field-without-value" not in json.dumps(result)
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert all("/processflow/" not in call.request.url.path for call in http_mock.calls)


async def test_native_validation_advertises_its_scope_without_authoring(settings):
    saved_structure()
    settings.mcp_tool_package = "flow"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            description = await session.call_tool("flow", {"action": "describe"})
            assert set(description.structured_content["data"]) == {"actions"}
            result = await session.call_tool(
                "flow", {"action": "validate", "sys_id": SYS_ID_FLOW}
            )
            assert not result.is_error, result
            assert result.structured_content["data"]["valid"] is True
            assert (
                result.structured_content["data"]["completeness_scope"]
                == "saved_v2_structure_only"
            )
        tasks.cancel_scope.cancel()
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "resource", ["flow", f"flow/{SYS_ID_FLOW}/deactivate", f"flow/{SYS_ID_FLOW}/test"]
)
async def test_flow_transport_rejects_effect_resources_before_http(settings, resource):
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ValueError):
            await client.studio_request(resource)
    assert not http_mock.calls


def test_flow_commands_are_rejected_by_the_change_contract():
    with pytest.raises(ValidationError):
        TypeAdapter(ChangeCommand).validate_python(
            {
                "family": "flows",
                "action": "create",
                "name": "No authoring",
                "scope": "global",
            }
        )
