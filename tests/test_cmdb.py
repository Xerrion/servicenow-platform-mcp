"""Exercise the CMDB MCP contract through the real client with mocked HTTP."""

from typing import Any

import httpx
import pytest
import respx
from mcp.server import MCPServer
from mcp.types import CallToolResult

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.cmdb import register_tools
from tests.helpers import decode_response


BASE = "https://test.service-now.com/api/now/cmdb"
SYS_ID = "a" * 32


async def _call(settings: Settings, **arguments: Any) -> dict[str, Any]:
    server = MCPServer("cmdb-test")
    register_tools(server, settings, OAuthPKCEProvider(settings))
    result = await server.call_tool("cmdb", arguments)
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return decode_response(result.structured_content["result"])


@pytest.mark.parametrize("null_defaults", [False, True])
@respx.mock
async def test_query_caps_and_paginates_without_inventing_a_total(settings: Settings, null_defaults: bool) -> None:
    route = respx.get(f"{BASE}/instance/cmdb_ci").respond(200, json={"result": [{"sys_id": SYS_ID, "name": "Example"}]})
    response = await _call(
        settings,
        action="query",
        class_name="cmdb_ci",
        encoded_query=None if null_defaults else "name=Example",
        limit=None if null_defaults else settings.max_row_limit + 1,
        offset=None if null_defaults else 40,
    )
    assert response["status"] == "success"
    assert response["data"] == {"records": [{"sys_id": SYS_ID, "name": "Example"}], "count": 1}
    params = route.calls.last.request.url.params
    assert int(params["sysparm_limit"]) == (20 if null_defaults else settings.max_row_limit)
    assert int(params["sysparm_offset"]) == (0 if null_defaults else 40)
    assert ("sysparm_query" in params) is (not null_defaults)
    assert "total" not in response["pagination"]


@pytest.mark.parametrize("action", ["get", "meta"])
@respx.mock
async def test_ci_relationships_and_class_metadata_are_preserved_and_masked(settings: Settings, action: str) -> None:
    path = f"instance/cmdb_ci/{SYS_ID}" if action == "get" else "meta/cmdb_ci"
    payload = {
        "attributes": {"name": "Example", "password": "fixture-secret"},
        "inbound_relations": [{"target": {"value": "b" * 32, "api_key": "fixture-secret"}}],
    }
    route = respx.get(f"{BASE}/{path}").respond(200, json={"result": payload})
    response = await _call(settings, action=action, class_name="cmdb_ci", sys_id=SYS_ID if action == "get" else None)
    assert response["status"] == "success"
    assert response["data"]["attributes"] == {"name": "Example", "password": "***MASKED***"}
    assert response["data"]["inbound_relations"][0]["target"] == {"value": "b" * 32, "api_key": "***MASKED***"}
    assert route.calls.call_count == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "unknown"},
        {"action": "query"},
        {"action": "query", "class_name": "../incident"},
        {"action": "meta", "class_name": "oauth_credential"},
        {"action": "get", "class_name": "cmdb_ci"},
        {"action": "get", "class_name": "cmdb_ci", "sys_id": "../bad"},
        {"action": "query", "class_name": "cmdb_ci", "limit": 0},
        {"action": "query", "class_name": "cmdb_ci", "offset": -1},
    ],
)
@respx.mock
async def test_invalid_or_denied_inputs_fail_before_http(settings: Settings, arguments: dict[str, Any]) -> None:
    response = await _call(settings, **arguments)
    assert response["status"] == "error"
    assert not respx.calls


@respx.mock
async def test_large_cmdb_classes_require_date_bounds(settings: Settings) -> None:
    settings = settings.model_copy(update={"large_table_names_csv": "cmdb_ci"})
    response = await _call(settings, action="query", class_name="cmdb_ci")
    assert response["status"] == "error"
    assert "date-bounded" in response["error"]["message"]
    assert not respx.calls


@respx.mock
async def test_describe_needs_no_class_or_http(settings: Settings) -> None:
    response = await _call(settings, action="describe")
    assert set(response["data"]["actions"]) == {"query", "get", "meta", "describe"}
    assert not respx.calls


@respx.mock
async def test_upstream_timeout_is_a_structured_error(settings: Settings) -> None:
    route = respx.get(f"{BASE}/meta/cmdb_ci").mock(side_effect=httpx.ReadTimeout("private-detail"))
    response = await _call(settings, action="meta", class_name="cmdb_ci")
    assert response["status"] == "error"
    assert response["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert "private-detail" not in str(response)
    assert route.calls.call_count == 1


@pytest.mark.parametrize(
    ("action", "result"),
    [("query", {}), ("meta", []), ("get", {"error": {"message": "private-detail"}})],
)
@respx.mock
async def test_invalid_or_failed_cmdb_results_do_not_report_success(
    settings: Settings, action: str, result: Any
) -> None:
    path = "meta/cmdb_ci" if action == "meta" else "instance/cmdb_ci"
    if action == "get":
        path += f"/{SYS_ID}"
    respx.get(f"{BASE}/{path}").respond(200, json={"result": result})
    response = await _call(settings, action=action, class_name="cmdb_ci", sys_id=SYS_ID)
    assert response["status"] == "error"
    assert "private-detail" not in str(response)
