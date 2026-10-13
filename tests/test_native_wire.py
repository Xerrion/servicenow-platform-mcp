"""Exercise native contracts through the actual MCP protocol dispatcher."""

import json
from pathlib import Path

import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.config import Settings
from tests._mock_transport import http_mock


async def test_native_wire_validation_and_results(settings: Settings) -> None:
    settings.mcp_tool_package = "flow"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run, *server_streams, server._lowlevel_server.create_initialization_options()
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            tool = next(tool for tool in tools if tool.name == "flow")
            success = await session.call_tool("flow", {"action": "describe"})
            assert not success.is_error
            Draft202012Validator(tool.output_schema).validate(success.structured_content)
            assert "actions" in success.structured_content["data"]
            for arguments in [
                {"action": "inspect", "sys_id": "a" * 32, "sections": '["inputs"]'},
                {"action": "private-sensitive-value"},
                {"action": "describe", "unknown": "private-sensitive-value"},
                {"action": "describe", "limit": "private-sensitive-value"},
            ]:
                failure = await session.call_tool("flow", arguments)
                assert failure.is_error
                Draft202012Validator(tool.output_schema).validate(failure.structured_content)
                await session.validate_tool_result("flow", failure)
                assert failure.structured_content["status"] == "error"
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
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
    assert snapshot == json.loads(path.read_text())


async def test_native_objects_and_nested_errors_are_safe_on_wire(settings: Settings) -> None:
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run, *server_streams, server._lowlevel_server.create_initialization_options()
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            for tool, args in [
                (
                    "record_preview",
                    {"action": "create", "table": "incident", "data": '{"secret":"private-sensitive-value"}'},
                ),
                ("investigate", {"action": "run", "name": "error_analysis", "params": '{"hours":24}'}),
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
                    {"table": "incident", "aggregate": [{"operation": "avg", "field": "private-sensitive-value!"}]},
                ),
                ("query", {"table": "incident", "fields": ["private-sensitive-value!"]}),
                (
                    "query",
                    {"table": "incident", "aggregate": [{"operation": "count", "unknown": "private-sensitive-value"}]},
                ),
            ]:
                failure = await session.call_tool(tool, args)
                assert failure.is_error
                Draft202012Validator(tools[tool].output_schema).validate(failure.structured_content)
                await session.validate_tool_result(tool, failure)
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()
