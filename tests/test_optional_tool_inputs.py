"""Optional inputs work through the advertised MCP schema and invocation path."""

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from mcp.types import CallToolResult

from servicenow_mcp.server import create_mcp_server
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_registered_tools, get_tool_functions

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server import MCPServer

    from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"
TABLE_URL = f"{BASE_URL}/api/now/table"
SYS_ID = "a" * 32
REQUIRED = {
    "list_tool_packages": [],
    "query": ["table"],
    "describe": [],
    "record_preview": ["action", "table"],
    "apply_change": ["preview_token"],
    "record_read": ["table"],
    "attachment": ["action"],
    "attachment_preview": ["action"],
    "investigate": ["action"],
    "resolve_choice": ["table", "field"],
    "service_catalog": ["action"],
    "service_catalog_preview": ["action"],
    "analysis": ["action"],
    "audit": ["action"],
    "flow": ["action"],
    "code_search": [],
    "cmdb": ["action"],
}


@pytest.fixture()
async def server(settings: Settings) -> AsyncIterator[MCPServer]:
    """Use production registration while keeping configuration and HTTP isolated."""
    with patch("servicenow_mcp.bootstrap.stdio.Settings", return_value=settings):
        mcp = create_mcp_server()
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server):
        yield mcp


async def _call(
    server: MCPServer, tool: str, arguments: dict[str, Any], *, fill_null: bool = False
) -> dict[str, Any]:
    """Optionally fill every unused optional input with null, as a client might."""
    optional: dict[str, Any] = {}
    if fill_null:
        schema = (await get_registered_tools(server))[tool].input_schema
        optional = {
            name: None
            for name in schema["properties"]
            if name not in schema.get("required", [])
        }
    result = await get_tool_functions(server)[tool](**{**optional, **arguments})
    assert isinstance(result, CallToolResult)
    assert result.result_type == "complete"
    assert result.is_error is (result.structured_content["status"] == "error")
    assert isinstance(result.structured_content, dict)
    return decode_response(result)


async def test_all_optional_schema_inputs_are_nullable_and_omittable(
    server: MCPServer,
) -> None:
    schemas = await get_registered_tools(server)
    assert schemas.keys() == REQUIRED.keys()
    for name, tool in schemas.items():
        schema = tool.input_schema
        assert schema.get("required", []) == REQUIRED[name], name
        for parameter, spec in schema["properties"].items():
            nullable = any(
                branch.get("type") == "null" for branch in spec.get("anyOf", [])
            )
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
            assert (
                schemas[tool].input_schema["properties"][parameter]["default"] is None
            )
    for tool, parameter, default in [
        ("query", "limit", 20),
        ("query", "offset", 0),
        ("query", "display_values", False),
        ("describe", "field_limit", 25),
        ("code_search", "action", "search"),
        ("investigate", "params", None),
        ("attachment_preview", "content_type", "application/octet-stream"),
    ]:
        assert schemas[tool].input_schema["properties"][parameter]["default"] == default


@pytest.mark.parametrize("fill_null,commit", [pytest.param(True, True, id="True-True")])
async def test_create_needs_no_sys_id_and_null_keeps_preview_default(
    server: MCPServer,
    fill_null: bool,
    commit: bool,
) -> None:
    http_mock.get(f"{TABLE_URL}/sys_dictionary").respond(200, json={"result": []})
    http_mock.get(f"{TABLE_URL}/sys_db_object").respond(200, json={"result": []})
    payload = {
        "short_description": "Optional input regression",
        "assigned_to": None,
        "active": False,
    }
    created = http_mock.post(f"{TABLE_URL}/sc_req_item").respond(
        200, json={"result": {"sys_id": SYS_ID, **payload}}
    )
    args: dict[str, Any] = {"action": "create", "table": "sc_req_item", "data": payload}
    response = await _call(server, "record_preview", args, fill_null=fill_null)
    assert response["status"] == "success"
    if commit:
        response = await _call(
            server,
            "apply_change",
            {"preview_token": response["data"]["preview_token"]},
        )
        assert response["status"] == "success"
        assert created.call_count == 1
        assert json.loads(created.calls.last.request.content) == payload
        assert created.calls.last.request.headers["content-type"] == "application/json"
    else:
        assert response["data"]["preview_token"]
        assert response["data"]["summary"]["data"] == payload
        assert not created.called
        assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("fill_null,since", [pytest.param(True, None, id="None-True")])
async def test_history_needs_no_irrelevant_fields_and_keeps_window_and_limit_defaults(
    server: MCPServer,
    settings: Settings,
    fill_null: bool,
    since: str | None,
) -> None:
    route = http_mock.get(f"{TABLE_URL}/sys_audit").respond(200, json={"result": []})
    args: dict[str, Any] = {
        "action": "history",
        "table": "sys_script",
        "sys_id": SYS_ID,
    }
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


@pytest.mark.parametrize(
    "fill_null,tool,arguments,error",
    [
        pytest.param(
            True,
            "record_read",
            {"table": "incident"},
            "exactly one of sys_id or name",
            id="record_read-arguments4-exactly one of sys_id or name-True",
        ),
        pytest.param(
            False,
            "attachment_preview",
            {"action": "upload"},
            "table",
            id="attachment_preview-arguments5-table-False",
        ),
        pytest.param(
            False,
            "service_catalog",
            {"action": "item_get"},
            "sys_id",
            id="service_catalog-arguments6-sys_id-False",
        ),
    ],
)
async def test_null_does_not_bypass_action_requirements_or_explicit_values(
    server: MCPServer,
    fill_null: bool,
    tool: str,
    arguments: dict[str, Any],
    error: str,
) -> None:
    response = await _call(server, tool, arguments, fill_null=fill_null)
    assert response["status"] == "error"
    assert error in response["error"]["message"]
    assert not http_mock.calls


@pytest.mark.parametrize(
    "fill_null,tool,arguments",
    [
        pytest.param(
            True,
            "record_read",
            {"table": "incident", "sys_id": SYS_ID},
            id="record_read-arguments2-True",
        ),
        pytest.param(
            False,
            "resolve_choice",
            {"table": "incident", "field": "state"},
            id="resolve_choice-arguments5-False",
        ),
        pytest.param(
            False,
            "analysis",
            {"action": "journal_history", "table": "incident", "sys_id": SYS_ID},
            id="analysis-arguments7-False",
        ),
        pytest.param(
            False,
            "audit",
            {"action": "check_fields", "table": "incident", "fields": ["state"]},
            id="audit-arguments8-False",
        ),
    ],
)
async def test_optional_inputs_reach_all_read_tool_handlers(
    server: MCPServer,
    fill_null: bool,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    dictionary = [
        {"element": name, "internal_type.name": "journal_input", "mandatory": "false"}
        for name in ("comments", "work_notes")
    ]
    http_mock.get(f"{TABLE_URL}/sys_dictionary").respond(
        200, json={"result": dictionary}
    )
    http_mock.get(f"{TABLE_URL}/sys_db_object").respond(200, json={"result": []})
    http_mock.get(f"{TABLE_URL}/sys_choice").respond(200, json={"result": []})
    http_mock.get(f"{TABLE_URL}/sys_documentation").respond(200, json={"result": []})
    http_mock.get(f"{TABLE_URL}/incident/{SYS_ID}").respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    http_mock.get(f"{TABLE_URL}/incident").respond(
        200, json={"result": [{"sys_id": SYS_ID}]}
    )
    http_mock.get(f"{TABLE_URL}/sys_journal_field").respond(200, json={"result": []})
    http_mock.get(f"{BASE_URL}/api/now/attachment").respond(200, json={"result": []})
    http_mock.get(f"{BASE_URL}/api/now/stats/sys_audit").respond(
        200, json={"result": {"stats": {"count": "0"}}}
    )
    http_mock.get(f"{BASE_URL}/api/sn_sc/servicecatalog/items").respond(
        200, json={"result": []}
    )
    http_mock.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").respond(
        200, json={"result": {}}
    )
    for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2"):
        http_mock.get(f"{TABLE_URL}/{table}").respond(200, json={"result": []})
    response = await _call(server, tool, arguments, fill_null=fill_null)
    assert response["status"] == "success", response
    assert all(call.request.method == "GET" for call in http_mock.calls)
    if tool == "query":
        params = http_mock.calls.last.request.url.params
        assert params["sysparm_limit"] == "20"
        assert "sysparm_offset" not in params
        assert params["sysparm_display_value"] == "false"
        assert params["sysparm_suppress_pagination_header"] == "true"


@pytest.mark.parametrize("fill_null", [pytest.param(True, id="True")])
async def test_attachment_upload_null_content_type_uses_default(
    server: MCPServer, fill_null: bool
) -> None:
    http_mock.get(f"{TABLE_URL}/incident/{SYS_ID}").respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    route = http_mock.post(f"{BASE_URL}/api/now/attachment/file").respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    response = await _call(
        server,
        "attachment_preview",
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
    assert not route.called
    response = await _call(
        server, "apply_change", {"preview_token": response["data"]["preview_token"]}
    )
    assert response["status"] == "success"
    assert (
        route.calls.last.request.headers["content-type"] == "application/octet-stream"
    )
    assert route.calls.last.request.content == b"test"
