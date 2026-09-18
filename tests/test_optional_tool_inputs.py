"""Optional inputs work through the advertised MCP schema and invocation path."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import respx
from mcp.server import MCPServer
from mcp.types import CallToolResult

from servicenow_mcp.config import Settings
from servicenow_mcp.server import create_mcp_server
from tests.helpers import decode_response, get_registered_tools


BASE_URL = "https://test.service-now.com"
TABLE_URL = f"{BASE_URL}/api/now/table"
SYS_ID = "a" * 32
REQUIRED = {
    "list_tool_packages": [],
    "query": ["table"],
    "describe": [],
    "record_write": ["action"],
    "record_apply": ["preview_token"],
    "record_read": ["table"],
    "attachment": ["action"],
    "attachment_write": ["action"],
    "investigate": ["action"],
    "resolve_choice": ["table", "field"],
    "service_catalog": ["action"],
    "analysis": ["action"],
    "audit": ["action"],
    "flow": ["action"],
    "code_search": [],
    "cmdb": ["action"],
}


@pytest.fixture()
async def server(settings: Settings) -> AsyncIterator[MCPServer]:
    """Use production registration while keeping configuration and HTTP isolated."""
    with patch("servicenow_mcp.server.Settings", return_value=settings):
        mcp = create_mcp_server()
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server):
        yield mcp


async def _call(server: MCPServer, tool: str, arguments: dict[str, Any], *, fill_null: bool = False) -> dict[str, Any]:
    """Optionally fill every unused optional input with null, as a client might."""
    optional: dict[str, Any] = {}
    if fill_null:
        schema = (await get_registered_tools(server))[tool].input_schema
        optional = {name: None for name in schema["properties"] if name not in schema.get("required", [])}
    result = await server.call_tool(tool, {**optional, **arguments})
    assert isinstance(result, CallToolResult)
    assert result.result_type == "complete"
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return decode_response(result.structured_content["result"])


async def test_all_optional_schema_inputs_are_nullable_and_omittable(server: MCPServer) -> None:
    schemas = await get_registered_tools(server)
    assert schemas.keys() == REQUIRED.keys()
    for name, tool in schemas.items():
        schema = tool.input_schema
        assert schema.get("required", []) == REQUIRED[name], name
        for parameter, spec in schema["properties"].items():
            nullable = any(branch.get("type") == "null" for branch in spec.get("anyOf", []))
            assert nullable is (parameter not in REQUIRED[name]), (name, parameter)
            if nullable:
                assert "default" in spec
                assert spec["default"] != "", (name, parameter)
    for tool, parameters in {
        "audit": ["window_days", "limit"],
        "analysis": ["window_days", "limit"],
        "flow": ["limit", "section_limit"],
    }.items():
        for parameter in parameters:
            assert schemas[tool].input_schema["properties"][parameter]["default"] is None
    for tool, parameter, default in [
        ("record_write", "preview", True),
        ("query", "limit", 20),
        ("query", "offset", 0),
        ("query", "display_values", False),
        ("describe", "field_limit", 25),
        ("code_search", "action", "search"),
        ("investigate", "params", "{}"),
        ("attachment_write", "content_type", "application/octet-stream"),
    ]:
        assert schemas[tool].input_schema["properties"][parameter]["default"] == default


@pytest.mark.parametrize("fill_null", [False, True])
@pytest.mark.parametrize("commit", [False, True])
@respx.mock(assert_all_called=False)
async def test_create_needs_no_sys_id_and_null_keeps_preview_default(
    server: MCPServer, fill_null: bool, commit: bool, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{TABLE_URL}/sys_dictionary").respond(200, json={"result": []})
    respx_mock.get(f"{TABLE_URL}/sys_db_object").respond(200, json={"result": []})
    payload = {"short_description": "Optional input regression", "assigned_to": None, "active": False}
    created = respx_mock.post(f"{TABLE_URL}/sc_req_item").respond(200, json={"result": {"sys_id": SYS_ID, **payload}})
    args: dict[str, Any] = {"action": "create", "table": "sc_req_item", "data": json.dumps(payload)}
    if commit:
        args["preview"] = False
    response = await _call(server, "record_write", args, fill_null=fill_null)
    assert response["status"] == "success"
    if commit:
        assert created.call_count == 1
        assert json.loads(created.calls.last.request.content) == payload
    else:
        assert response["data"]["preview_token"]
        assert response["data"]["preview"]["data"] == payload
        assert not created.called
        assert all(call.request.method == "GET" for call in respx_mock.calls)


@pytest.mark.parametrize("fill_null", [False, True])
@pytest.mark.parametrize("since", [None, "2026-09-15"])
@respx.mock
async def test_history_needs_no_irrelevant_fields_and_keeps_window_and_limit_defaults(
    server: MCPServer, settings: Settings, fill_null: bool, since: str | None
) -> None:
    route = respx.get(f"{TABLE_URL}/sys_audit").respond(200, json={"result": []})
    args: dict[str, Any] = {"action": "history", "table": "sys_script", "sys_id": SYS_ID}
    if since is not None:
        args["since"] = since
    response = await _call(server, "audit", args, fill_null=fill_null)
    assert response["status"] == "success"
    cutoff = since or (datetime.now(UTC).date() - timedelta(days=90)).isoformat()
    assert response["data"]["window"] == {
        "since": cutoff,
        "window_days": 0 if since else 90,
        "explicit_since": since is not None,
    }
    params = route.calls.last.request.url.params
    assert params["sysparm_limit"] == str(settings.max_row_limit)
    assert f"sys_created_on>={cutoff}" in params["sysparm_query"]
    assert f"documentkey={SYS_ID}" in params["sysparm_query"]


@pytest.mark.parametrize("fill_null", [False, True])
@pytest.mark.parametrize(
    ("tool", "arguments", "error"),
    [
        ("record_write", {"action": "create", "table": "incident"}, "data is required"),
        ("record_write", {"action": "update", "table": "incident", "data": "{}"}, "sys_id is required"),
        ("audit", {"action": "history", "table": "incident"}, "sys_id is required"),
        ("audit", {"action": "check_fields", "table": "incident"}, "fields_csv must list"),
        ("record_read", {"table": "incident"}, "exactly one of sys_id or name"),
        ("attachment_write", {"action": "upload"}, "table is required"),
        ("service_catalog", {"action": "item_get"}, "sys_id is required"),
        ("code_search", {}, "'term' is required"),
        ("query", {"table": "incident"}, "fields is required"),
        ("describe", {"table": "incident", "field_limit": 0}, "field_limit must be between"),
        ("code_search", {"action": ""}, "Unknown action"),
    ],
)
@respx.mock
async def test_null_does_not_bypass_action_requirements_or_explicit_values(
    server: MCPServer, fill_null: bool, tool: str, arguments: dict[str, Any], error: str
) -> None:
    response = await _call(server, tool, arguments, fill_null=fill_null)
    assert response["status"] == "error"
    assert error in response["error"]["message"]
    assert not respx.calls


@pytest.mark.parametrize("fill_null", [False, True])
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query", {"table": "incident", "fields": "*"}),
        ("describe", {"table": "incident"}),
        ("record_read", {"table": "incident", "sys_id": SYS_ID}),
        ("attachment", {"action": "list", "table": "incident", "table_sys_id": SYS_ID}),
        ("investigate", {"action": "describe"}),
        ("resolve_choice", {"table": "incident", "field": "state"}),
        ("service_catalog", {"action": "items_list"}),
        ("analysis", {"action": "journal_history", "table": "incident", "sys_id": SYS_ID}),
        ("audit", {"action": "check_fields", "table": "incident", "fields_csv": "state"}),
        ("flow", {"action": "list_triggers"}),
        ("code_search", {"term": "current.update"}),
    ],
)
@respx.mock(assert_all_called=False)
async def test_optional_inputs_reach_all_read_tool_handlers(
    server: MCPServer, fill_null: bool, tool: str, arguments: dict[str, Any], respx_mock: respx.MockRouter
) -> None:
    dictionary = [
        {"element": name, "internal_type.name": "journal_input", "mandatory": "false"}
        for name in ("comments", "work_notes")
    ]
    respx_mock.get(f"{TABLE_URL}/sys_dictionary").respond(200, json={"result": dictionary})
    respx_mock.get(f"{TABLE_URL}/sys_db_object").respond(200, json={"result": []})
    respx_mock.get(f"{TABLE_URL}/sys_choice").respond(200, json={"result": []})
    respx_mock.get(f"{TABLE_URL}/sys_documentation").respond(200, json={"result": []})
    respx_mock.get(f"{TABLE_URL}/incident/{SYS_ID}").respond(200, json={"result": {"sys_id": SYS_ID}})
    respx_mock.get(f"{TABLE_URL}/incident").respond(200, json={"result": [{"sys_id": SYS_ID}]})
    respx_mock.get(f"{TABLE_URL}/sys_journal_field").respond(200, json={"result": []})
    respx_mock.get(f"{BASE_URL}/api/now/attachment").respond(200, json={"result": []})
    respx_mock.get(f"{BASE_URL}/api/now/stats/sys_audit").respond(200, json={"result": {"stats": {"count": "0"}}})
    respx_mock.get(f"{BASE_URL}/api/sn_sc/servicecatalog/items").respond(200, json={"result": []})
    respx_mock.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").respond(200, json={"result": {}})
    for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2"):
        respx_mock.get(f"{TABLE_URL}/{table}").respond(200, json={"result": []})
    response = await _call(server, tool, arguments, fill_null=fill_null)
    assert response["status"] == "success", response
    assert all(call.request.method == "GET" for call in respx_mock.calls)
    if tool == "query":
        params = respx_mock.calls.last.request.url.params
        assert params["sysparm_limit"] == "20"
        assert params["sysparm_offset"] == "0"
        assert params["sysparm_display_value"] == "false"


@pytest.mark.parametrize("fill_null", [False, True])
@respx.mock
async def test_attachment_upload_null_content_type_uses_default(server: MCPServer, fill_null: bool) -> None:
    route = respx.post(f"{BASE_URL}/api/now/attachment/file").respond(200, json={"result": {"sys_id": SYS_ID}})
    response = await _call(
        server,
        "attachment_write",
        {
            "action": "upload",
            "table": "incident",
            "table_sys_id": SYS_ID,
            "file_name": "test.txt",
            "content_base64": "dGVzdA==",
        },
        fill_null=fill_null,
    )
    assert response["status"] == "success"
    assert route.calls.last.request.headers["content-type"] == "application/octet-stream"
    assert route.calls.last.request.content == b"test"
