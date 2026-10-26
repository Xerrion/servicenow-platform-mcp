"""Exercise native contracts through the actual MCP protocol dispatcher."""

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from tests._mock_transport import http_mock
from tests.generate_tool_schemas import generate

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


async def test_native_wire_validation_and_results(settings: Settings) -> None:
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
            tools = (await session.list_tools()).tools
            tool = next(tool for tool in tools if tool.name == "flow")
            assert isinstance(tool.output_schema, dict)
            success = await session.call_tool("flow", {"action": "describe"})
            assert not success.is_error
            assert isinstance(success.structured_content, dict)
            Draft202012Validator(tool.output_schema).validate(
                success.structured_content
            )
            assert "actions" in success.structured_content["data"]
            for arguments in [
                {"action": "inspect", "sys_id": "a" * 32, "sections": '["inputs"]'},
                {"action": "private-sensitive-value"},
                {"action": "describe", "unknown": "private-sensitive-value"},
                {"action": "describe", "limit": "private-sensitive-value"},
            ]:
                failure = await session.call_tool("flow", arguments)
                assert failure.is_error
                assert isinstance(failure.structured_content, dict)
                Draft202012Validator(tool.output_schema).validate(
                    failure.structured_content
                )
                await session.validate_tool_result("flow", failure)
                assert failure.structured_content["status"] == "error"
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_removed_flow_preview_is_not_exposed_or_dispatched(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    settings.mcp_tool_package = "full"
    server = create_mcp_server(settings)
    private = "private-sensitive-marker"
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
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert "flow" in names and "flow_preview" not in names
            failure = await session.call_tool(
                "flow_preview",
                {"action": "create", "name": private, "scope": "global"},
            )
            assert failure.is_error
            assert private not in str(failure)
            assert not http_mock.calls
            boundaries = [
                json.loads(record.getMessage().removeprefix("Flow preview boundary "))
                for record in caplog.records
                if record.getMessage().startswith("Flow preview boundary ")
            ]
            assert boundaries == []
            assert private not in str(boundaries)
        tasks.cancel_scope.cancel()


async def test_native_schema_snapshot(settings: Settings) -> None:
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        tools = await server.list_tools()
    snapshot = {
        tool.name: {
            "input": tool.input_schema,
            "output": tool.output_schema,
            "annotations": tool.annotations.model_dump(),
        }
        for tool in tools
    }
    path = Path(__file__).parent / "fixtures/native_tool_schemas.json"
    if os.environ.get("UPDATE_TOOL_SCHEMAS") == "1":
        await generate()
    expected = json.loads(path.read_text())
    assert snapshot == expected


async def test_native_objects_and_nested_errors_are_safe_on_wire(
    settings: Settings,
) -> None:
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
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            for tool, args in [
                (
                    "record_preview",
                    {
                        "action": "create",
                        "table": "incident",
                        "data": '{"secret":"private-sensitive-value"}',
                    },
                ),
                (
                    "investigate",
                    {
                        "action": "run",
                        "name": "error_analysis",
                        "params": '{"hours":24}',
                    },
                ),
                (
                    "service_catalog_preview",
                    {
                        "action": "order_now",
                        "item_sys_id": "a" * 32,
                        "variables": '{"secret":"private-sensitive-value"}',
                    },
                ),
                (
                    "query",
                    {
                        "table": "incident",
                        "aggregate": [
                            {"operation": "avg", "field": "private-sensitive-value!"}
                        ],
                    },
                ),
                (
                    "query",
                    {"table": "incident", "fields": ["private-sensitive-value!"]},
                ),
                (
                    "query",
                    {
                        "table": "incident",
                        "aggregate": [
                            {"operation": "count", "unknown": "private-sensitive-value"}
                        ],
                    },
                ),
            ]:
                failure = await session.call_tool(tool, args)
                assert failure.is_error
                assert isinstance(failure.structured_content, dict)
                schema = tools[tool].output_schema
                assert isinstance(schema, dict)
                Draft202012Validator(schema).validate(failure.structured_content)
                await session.validate_tool_result(tool, failure)
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_native_argument_diagnostics_are_actionable_and_safe(
    settings: Settings,
) -> None:
    server = create_mcp_server(settings)
    secret = "private-sensitive-value"
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
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            cases = [
                ("query", {"fields": ["number"]}, ["table"], None),
                (
                    "query",
                    {"table": "incident", "fields": f'["number","{secret}"]'},
                    None,
                    [{"field": "fields", "expected": 'array of string | "*" | null'}],
                ),
                ("query", {"table": "incident", "fields": secret}, None, None),
                ("query", {"table": "incident"}, ["fields"], None),
                ("query", {"table": "incident", "fields": None}, ["fields"], None),
                ("query", {"table": "incident", "fields": []}, ["fields"], None),
                (
                    "query",
                    {"fields": f'["{secret}"]'},
                    ["table"],
                    [{"field": "fields", "expected": 'array of string | "*" | null'}],
                ),
                (
                    "query",
                    {"table": 7, "limit": secret, "display_values": "yes"},
                    None,
                    [
                        {"field": "table", "expected": "string"},
                        {"field": "limit", "expected": "integer | null"},
                        {"field": "display_values", "expected": "boolean | null"},
                    ],
                ),
                (
                    "query",
                    {"table": "incident", "aggregate": [{"operation": secret}]},
                    None,
                    None,
                ),
                (
                    "query",
                    {"table": "incident", f"{secret}_api_key": secret},
                    None,
                    None,
                ),
                ("query", {f"{secret}<script>": 1}, ["table"], None),
                ("flow", {"action": secret}, None, None),
            ]
            for tool, args, missing, invalid in cases:
                failure = await session.call_tool(tool, args)
                assert failure.is_error
                content = failure.structured_content
                assert isinstance(content, dict)
                schema = tools[tool].output_schema
                assert isinstance(schema, dict)
                Draft202012Validator(schema).validate(content)
                await session.validate_tool_result(tool, failure)
                assert content["status"] == "error"
                assert content["data"] is None
                error = content["error"]
                assert error["code"] == "INVALID_REQUEST"
                assert error.get("missing_arguments") == missing
                assert "missing_fields" not in error
                if invalid is not None:
                    assert error["invalid_fields"] == invalid
                assert secret not in str(failure)
                assert "script" not in str(failure)
                assert "api_key" not in str(failure)
                assert len(error["message"]) < 400
            json_string = await session.call_tool(
                "query", {"table": "incident", "fields": '["number"]'}
            )
            assert isinstance(json_string.structured_content, dict)
            assert (
                "not JSON-encoded strings"
                in json_string.structured_content["error"]["message"]
            )
            literal = await session.call_tool("flow", {"action": secret})
            assert isinstance(literal.structured_content, dict)
            assert (
                literal.structured_content["error"]["invalid_fields"][0]["field"]
                == "action"
            )
            assert literal.structured_content["error"]["invalid_fields"][0][
                "expected"
            ].startswith("one of ")
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_native_record_fields_and_projection_arguments_remain_distinct(
    settings: Settings,
) -> None:
    server = create_mcp_server(settings)
    base_url = settings.servicenow_instance_url
    http_mock.get(f"{base_url}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"super_class": ""}]}
    )
    http_mock.get(f"{base_url}/api/now/table/sys_dictionary").respond(
        200,
        json={"result": [{"element": "name", "mandatory": "true"}]},
    )
    records = [{"sys_id": "a" * 32, "number": "INC0001"}]
    projected = http_mock.get(f"{base_url}/api/now/table/incident").respond(
        200, json={"result": records}
    )
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
            failure = await session.call_tool(
                "record_preview", {"action": "create", "table": "incident", "data": {}}
            )
            assert failure.is_error
            await session.validate_tool_result("record_preview", failure)
            assert failure.structured_content == {
                "status": "error",
                "data": None,
                "error": {
                    "code": "MISSING_MANDATORY_FIELDS",
                    "message": "Missing mandatory fields for table 'incident': name",
                    "missing_fields": ["name"],
                },
            }
            success = await session.call_tool(
                "query", {"table": "incident", "fields": ["number"]}
            )
            assert not success.is_error
            await session.validate_tool_result("query", success)
            assert isinstance(success.structured_content, dict)
            assert success.structured_content["status"] == "success"
            assert success.structured_content["data"] == records
            assert (
                projected.calls.last.request.url.params["sysparm_fields"]
                == "sys_id,number"
            )
            assert all(call.request.method == "GET" for call in http_mock.calls)
        tasks.cancel_scope.cancel()
