"""Regression coverage for compact envelopes and optional query inputs."""

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2
import pytest
from jsonschema import Draft202012Validator
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.tools.query import register_tools
from servicenow_mcp.mcp.tools.service_catalog import (
    register_tools as register_catalog_tools,
)
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response

if TYPE_CHECKING:
    from pydantic import JsonValue

    from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            httpx2.ReadTimeout(
                "private timeout",
                request=httpx2.Request("GET", f"{BASE_URL}/api/now/table/incident"),
            ),
            id="upstream",
        )
    ],
)
async def test_tool_results_conform_to_output_schema(failure: Exception | None) -> None:
    mcp = MCPServer("test")

    @mcp.tool()
    @tool_handler
    async def tool() -> CapabilityResult:
        if failure is not None:
            raise failure
        return operation_result(
            data={"ok": True},
            pagination={"offset": 0, "limit": 1, "total": 1},
            warnings=["Incomplete response"],
            truncation={"limit": 1},
        )

    schema = (await mcp.list_tools())[0].output_schema
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    response = await mcp.call_tool("tool", {})
    assert isinstance(response, CallToolResult)
    assert response.is_error is (failure is not None)
    assert isinstance(response.structured_content, dict)
    validator.validate(response.structured_content)
    assert response.structured_content["status"] == (
        "success" if failure is None else "error"
    )
    assert isinstance(response.content[0], TextContent)
    assert json.loads(response.content[0].text) == response.structured_content
    if failure is None:
        assert response.structured_content == {
            "status": "success",
            "data": {"ok": True},
            "pagination": {"offset": 0, "limit": 1, "total": 1},
            "warnings": ["Incomplete response"],
            "truncation": {"limit": 1},
        }
    for invalid in [
        {"status": "error", "data": None},
        {
            "status": "success",
            "data": {},
            "error": {"code": "INVALID_REQUEST", "message": "Invalid request"},
        },
        {"status": "unexpected", "data": None},
    ]:
        assert not validator.is_valid(invalid)


@pytest.mark.parametrize("should_raise", [pytest.param(True, id="True")])
async def test_envelopes_have_no_internal_correlation_id(should_raise: bool) -> None:
    @tool_handler
    async def tool() -> CapabilityResult:
        if should_raise:
            raise RuntimeError("private failure")
        return operation_result(data={"correlation_id": "record-value"})

    with patch("servicenow_mcp.mcp.errors.sentry_capture") as capture:
        response = decode_response(await tool())
    assert "correlation_id" not in response
    if should_raise:
        assert response == {
            "status": "error",
            "data": None,
            "error": {"code": "INTERNAL_ERROR", "message": "Internal error"},
        }
        capture.assert_called_once()
        assert isinstance(capture.call_args.args[0], RuntimeError)
    else:
        assert response == {
            "status": "success",
            "data": {"correlation_id": "record-value"},
        }
        capture.assert_not_called()


@pytest.mark.parametrize(
    "empty,mode", [pytest.param("", {"fields": ["correlation_id"]}, id="mode1-")]
)
async def test_query_mcp_calls_omit_empty_parameters(
    settings: Settings,
    empty: str | None,
    mode: dict[str, JsonValue],
) -> None:
    is_single = "sys_id" in mode
    is_aggregate = "aggregate" in mode
    path = "stats/incident" if is_aggregate else "table/incident"
    if is_single:
        sys_id = mode["sys_id"]
        assert isinstance(sys_id, str)
        path += "/" + sys_id
    route = http_mock.get(f"{BASE_URL}/api/now/{path}").respond(
        200, json={"result": {} if is_single else []}
    )
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    optional: dict[str, JsonValue] = (
        {}
        if empty == "omitted"
        else dict.fromkeys(
            (
                "sys_id",
                "encoded_query",
                "fields",
                "order_by",
                "aggregate",
                "group_by",
                "resolve_labels",
            ),
            empty,
        )
    )
    for name in ("fields", "aggregate", "group_by", "resolve_labels"):
        if name in optional and optional[name] == "":
            optional[name] = []
    result = await mcp.call_tool("query", {"table": "incident", **optional, **mode})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = result.structured_content
    assert response["status"] == "success"
    assert "correlation_id" not in response
    assert "selection" not in response
    params = route.calls.last.request.url.params
    assert "sysparm_query" not in params
    assert "sysparm_orderby" not in params
    assert all(value != "" for value in params.values())
    if not is_single and not is_aggregate:
        assert params["sysparm_limit"] == "20"
        assert "sysparm_offset" not in params
        assert params["sysparm_display_value"] == "false"
        assert params["sysparm_suppress_pagination_header"] == "true"


@pytest.mark.parametrize("empty", [pytest.param("", id="")])
async def test_client_omits_empty_query_parameters(
    settings: Settings, empty: str | None
) -> None:
    table = http_mock.get(f"{BASE_URL}/api/now/table/incident").respond(
        200, json={"result": []}
    )
    stats = http_mock.get(f"{BASE_URL}/api/now/stats/incident").respond(
        200, json={"result": {}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.query_records(
            "incident",
            empty,
            fields=[],
            order_by=empty,
            limit=5,
            offset=0,
            display_values=False,
        )
        await client.aggregate(
            "incident", empty, group_by=empty, having=empty, order_by=empty
        )
    assert dict(table.calls.last.request.url.params) == {
        "sysparm_limit": "5",
        "sysparm_display_value": "false",
        "sysparm_suppress_pagination_header": "true",
    }
    assert dict(stats.calls.last.request.url.params) == {
        "sysparm_count": "true",
        "sysparm_display_value": "false",
    }


@pytest.mark.parametrize(
    "fields,field_name,is_single",
    [
        pytest.param(
            "explicit", "correlation_id", False, id="False-correlation_id-explicit"
        )
    ],
)
async def test_query_preserves_record_metadata_fields(
    settings: Settings,
    fields: str,
    field_name: str,
    is_single: bool,
) -> None:
    record = {"sys_id": "a" * 32, field_name: "record-value"}
    path = "table/incident" + ("/" + record["sys_id"] if is_single else "")
    http_mock.get(f"{BASE_URL}/api/now/{path}").respond(
        200, json={"result": record if is_single else [record]}
    )
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    arguments = {
        "table": "incident",
        "fields": [field_name] if fields == "explicit" else fields,
    }
    if is_single:
        arguments["sys_id"] = record["sys_id"]
    result = await mcp.call_tool("query", arguments)
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = result.structured_content
    assert response["status"] == "success"
    assert "correlation_id" not in response
    assert "selection" not in response
    assert response["data"] == (record if is_single else [record])


@pytest.mark.parametrize("empty", [pytest.param("", id="")])
async def test_catalog_client_omits_empty_filters(
    settings: Settings, empty: str | None
) -> None:
    catalogs = http_mock.get(f"{BASE_URL}/api/sn_sc/servicecatalog/catalogs").respond(
        200, json={"result": []}
    )
    items = http_mock.get(f"{BASE_URL}/api/sn_sc/servicecatalog/items").respond(
        200, json={"result": []}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.sc_get_catalogs(text=empty)
        await client.sc_get_items(
            text=empty, catalog=empty, category=empty, limit=5, offset=0
        )
    assert dict(catalogs.calls.last.request.url.params) == {}
    assert dict(items.calls.last.request.url.params) == {
        "sysparm_limit": "5",
        "sysparm_offset": "0",
    }


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


@pytest.mark.parametrize(
    "empty,action", [pytest.param("", "items_list", id="items_list-")]
)
async def test_catalog_mcp_calls_omit_empty_filters(
    settings: Settings, empty: str | None, action: str
) -> None:
    path = "catalogs" if action == "catalogs_list" else "items"
    route = http_mock.get(f"{BASE_URL}/api/sn_sc/servicecatalog/{path}").respond(
        200, json={"result": []}
    )
    mcp = MCPServer("test")
    register_catalog_tools(mcp, settings, OAuthPKCEProvider(settings))
    optional = (
        {}
        if empty == "omitted"
        else dict.fromkeys(("text", "catalog", "category"), empty)
    )
    result = await mcp.call_tool("service_catalog", {"action": action, **optional})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    assert result.structured_content == {"status": "success", "data": []}
    expected = {"sysparm_limit": "20"}
    if action == "items_list":
        expected["sysparm_offset"] = "0"
    assert dict(route.calls.last.request.url.params) == expected


async def test_unfiltered_related_reads_omit_empty_queries(settings: Settings) -> None:
    attachments = http_mock.get(f"{BASE_URL}/api/now/attachment").respond(
        200, json={"result": []}
    )
    triggers = [
        http_mock.get(f"{BASE_URL}/api/now/table/{table}").respond(
            200, json={"result": []}
        )
        for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2")
    ]
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await client.list_attachments(limit=5, offset=0)
        await client.list_triggers_filtered(limit=5)
    assert dict(attachments.calls.last.request.url.params) == {
        "sysparm_limit": "5",
        "sysparm_offset": "0",
    }
    for route in triggers:
        assert dict(route.calls.last.request.url.params) == {
            "sysparm_display_value": "all",
            "sysparm_limit": "5",
        }


@pytest.mark.parametrize("fields", [pytest.param(None, id="None")])
async def test_null_or_empty_list_projection_still_fails_before_io(
    settings: Settings,
    fields: list[str] | None,
) -> None:
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    result = await mcp.call_tool("query", {"table": "incident", "fields": fields})
    assert result.result_type == "complete"
    assert isinstance(result.structured_content, dict)
    response = result.structured_content
    assert response["status"] == "error"
    assert "fields is required" in response["error"]["message"]
    assert response["error"]["missing_arguments"] == ["fields"]
    assert "missing_fields" not in response["error"]
    assert not http_mock.calls
