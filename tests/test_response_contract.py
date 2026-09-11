"""Regression coverage for compact envelopes and optional query inputs."""

import json
from unittest.mock import patch

import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.response import format_response
from servicenow_mcp.tools.query import register_tools
from servicenow_mcp.tools.service_catalog import register_tools as register_catalog_tools


BASE_URL = "https://test.service-now.com"


@pytest.mark.parametrize("should_raise", [False, True])
async def test_envelopes_have_no_internal_correlation_id(should_raise: bool) -> None:
    @tool_handler
    async def tool() -> str:
        if should_raise:
            raise RuntimeError("private failure")
        return format_response(data={"correlation_id": "record-value"})

    with patch("servicenow_mcp.tool_errors.sentry_capture") as capture:
        response = json.loads(await tool())
    assert "correlation_id" not in response
    if should_raise:
        assert response == {"status": "error", "data": None, "error": {"message": "Internal error"}}
        capture.assert_called_once()
        assert isinstance(capture.call_args.args[0], RuntimeError)
    else:
        assert response == {"status": "success", "data": {"correlation_id": "record-value"}}
        capture.assert_not_called()


async def test_query_schema_has_optional_inputs_without_empty_defaults(settings: Settings) -> None:
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    schema = (await mcp.list_tools())[0].input_schema
    assert schema["required"] == ["table"]
    assert "correlation_id" not in schema["properties"]
    for name in ("sys_id", "encoded_query", "fields", "order_by", "aggregate", "group_by", "resolve_labels"):
        parameter = schema["properties"][name]
        assert parameter["default"] is None
        assert {choice["type"] for choice in parameter["anyOf"]} == {"string", "null"}
    assert schema["properties"]["limit"]["default"] == 20
    assert schema["properties"]["offset"]["default"] == 0
    assert schema["properties"]["display_values"]["default"] is False


@pytest.mark.parametrize("empty", ["omitted", "", None])
@pytest.mark.parametrize(
    "mode", [{"fields": "*"}, {"fields": "correlation_id"}, {"aggregate": "count"}, {"sys_id": "a" * 32}]
)
@respx.mock
async def test_query_mcp_calls_omit_empty_parameters(
    settings: Settings, empty: str | None, mode: dict[str, str]
) -> None:
    is_single = "sys_id" in mode
    is_aggregate = "aggregate" in mode
    path = "stats/incident" if is_aggregate else "table/incident"
    if is_single:
        path += "/" + mode["sys_id"]
    route = respx.get(f"{BASE_URL}/api/now/{path}").respond(200, json={"result": {} if is_single else []})
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    optional = (
        {}
        if empty == "omitted"
        else dict.fromkeys(
            ("sys_id", "encoded_query", "fields", "order_by", "aggregate", "group_by", "resolve_labels"), empty
        )
    )
    result = await mcp.call_tool("query", {"table": "incident", **optional, **mode})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = json.loads(result.structured_content["result"])
    assert response["status"] == "success"
    assert "correlation_id" not in response
    assert "omitted" not in response.get("selection", {})
    params = route.calls.last.request.url.params
    assert "sysparm_query" not in params
    assert "sysparm_orderby" not in params
    assert all(value != "" for value in params.values())
    if not is_single and not is_aggregate:
        assert params["sysparm_limit"] == "20"
        assert params["sysparm_offset"] == "0"
        assert params["sysparm_display_value"] == "false"


@pytest.mark.parametrize("empty", ["", None])
@respx.mock
async def test_client_omits_empty_query_parameters(settings: Settings, empty: str | None) -> None:
    table = respx.get(f"{BASE_URL}/api/now/table/incident").respond(200, json={"result": []})
    stats = respx.get(f"{BASE_URL}/api/now/stats/incident").respond(200, json={"result": {}})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.query_records(
            "incident", empty, fields=[], order_by=empty, limit=5, offset=0, display_values=False
        )
        await client.aggregate("incident", empty, group_by=empty, having=empty, order_by=empty)
    assert dict(table.calls.last.request.url.params) == {
        "sysparm_limit": "5",
        "sysparm_offset": "0",
        "sysparm_display_value": "false",
    }
    assert dict(stats.calls.last.request.url.params) == {"sysparm_count": "true", "sysparm_display_value": "false"}


@pytest.mark.parametrize("fields", ["correlation_id", "*"])
@pytest.mark.parametrize("is_single", [False, True])
@respx.mock
async def test_query_preserves_record_correlation_id(settings: Settings, fields: str, is_single: bool) -> None:
    record = {"sys_id": "a" * 32, "correlation_id": "external-record-id"}
    path = "table/incident" + ("/" + record["sys_id"] if is_single else "")
    respx.get(f"{BASE_URL}/api/now/{path}").respond(200, json={"result": record if is_single else [record]})
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    arguments = {"table": "incident", "fields": fields}
    if is_single:
        arguments["sys_id"] = record["sys_id"]
    result = await mcp.call_tool("query", arguments)
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = json.loads(result.structured_content["result"])
    assert response["status"] == "success"
    assert "correlation_id" not in response
    assert response["data"] == (record if is_single else [record])


@pytest.mark.parametrize("empty", ["", None])
@respx.mock
async def test_catalog_client_omits_empty_filters(settings: Settings, empty: str | None) -> None:
    catalogs = respx.get(f"{BASE_URL}/api/sn_sc/servicecatalog/catalogs").respond(200, json={"result": []})
    items = respx.get(f"{BASE_URL}/api/sn_sc/servicecatalog/items").respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.sc_get_catalogs(text=empty)
        await client.sc_get_items(text=empty, catalog=empty, category=empty, limit=5, offset=0)
    assert dict(catalogs.calls.last.request.url.params) == {}
    assert dict(items.calls.last.request.url.params) == {"sysparm_limit": "5", "sysparm_offset": "0"}


async def test_catalog_schema_has_nullable_filters(settings: Settings) -> None:
    mcp = MCPServer("test")
    register_catalog_tools(mcp, settings, OAuthPKCEProvider(settings))
    schema = (await mcp.list_tools())[0].input_schema
    assert schema["required"] == ["action"]
    assert "correlation_id" not in schema["properties"]
    for name in ("text", "catalog", "category"):
        parameter = schema["properties"][name]
        assert parameter["default"] is None
        assert {choice["type"] for choice in parameter["anyOf"]} == {"string", "null"}


@pytest.mark.parametrize("empty", ["omitted", "", None])
@pytest.mark.parametrize("action", ["catalogs_list", "items_list"])
@respx.mock
async def test_catalog_mcp_calls_omit_empty_filters(settings: Settings, empty: str | None, action: str) -> None:
    path = "catalogs" if action == "catalogs_list" else "items"
    route = respx.get(f"{BASE_URL}/api/sn_sc/servicecatalog/{path}").respond(200, json={"result": []})
    mcp = MCPServer("test")
    register_catalog_tools(mcp, settings, OAuthPKCEProvider(settings))
    optional = {} if empty == "omitted" else dict.fromkeys(("text", "catalog", "category"), empty)
    result = await mcp.call_tool("service_catalog", {"action": action, **optional})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    assert json.loads(result.structured_content["result"]) == {"status": "success", "data": []}
    expected = {"sysparm_limit": "20"}
    if action == "items_list":
        expected["sysparm_offset"] = "0"
    assert dict(route.calls.last.request.url.params) == expected


@respx.mock
async def test_unfiltered_related_reads_omit_empty_queries(settings: Settings) -> None:
    attachments = respx.get(f"{BASE_URL}/api/now/attachment").respond(200, json={"result": []})
    triggers = [
        respx.get(f"{BASE_URL}/api/now/table/{table}").respond(200, json={"result": []})
        for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2")
    ]
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.list_attachments(limit=5, offset=0)
        await client.list_triggers_filtered(limit=5)
    assert dict(attachments.calls.last.request.url.params) == {"sysparm_limit": "5", "sysparm_offset": "0"}
    for route in triggers:
        assert dict(route.calls.last.request.url.params) == {"sysparm_display_value": "all", "sysparm_limit": "5"}


@pytest.mark.parametrize("fields", [None, ""])
@respx.mock
async def test_null_or_empty_list_projection_still_fails_before_io(settings: Settings, fields: str | None) -> None:
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    result = await mcp.call_tool("query", {"table": "incident", "fields": fields})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = json.loads(result.structured_content["result"])
    assert response["status"] == "error"
    assert "fields is required" in response["error"]["message"]
    assert not respx.calls
