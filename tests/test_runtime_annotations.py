"""Registered tool annotations are runtime contracts, not type-checker-only imports."""

import inspect
from typing import TYPE_CHECKING, get_type_hints

import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records._query_parsing import (
    AggregateOperation,
    LabelPair,
)
from tests._mock_transport import http_mock
from tests.helpers import _get_registered_tools

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


async def test_all_tool_annotations_resolve_at_registration_and_schema_generation(
    settings: Settings,
) -> None:
    settings.mcp_tool_package = "full"
    server = create_mcp_server(settings)
    functions = {name: tool.fn for name, tool in _get_registered_tools(server).items()}
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert len(functions) == len(tools) == 18
    for name, function in functions.items():
        original = inspect.unwrap(function)
        signature = inspect.signature(original)
        annotations = get_type_hints(original)
        assert annotations["return"] is CapabilityResult
        assert all(
            parameter.annotation is not inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )
        Draft202012Validator.check_schema(tools[name].input_schema)
        Draft202012Validator.check_schema(tools[name].output_schema)
    query = get_type_hints(inspect.unwrap(functions["query"]))
    assert query["aggregate"] == list[AggregateOperation] | None
    assert query["resolve_labels"] == list[LabelPair] | None
    assert not http_mock.calls


async def test_full_mcp_initialize_list_tools_and_shutdown_without_network(
    settings: Settings,
) -> None:
    settings.mcp_tool_package = "full"
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
            initialized = await session.initialize()
            assert initialized.server_info.name == "servicenow-platform-mcp"
            tools = (await session.list_tools()).tools
            assert len(tools) == 18
            assert {
                "query",
                "flow",
                "flow_preview",
                "service_catalog_preview",
                "mutation_apply",
            } <= {tool.name for tool in tools}
        tasks.cancel_scope.cancel()
    assert not http_mock.calls
