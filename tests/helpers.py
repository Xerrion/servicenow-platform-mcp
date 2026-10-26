"""Shared test helper utilities."""

import inspect
from typing import TYPE_CHECKING, Any, Protocol, cast

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult

from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator
from servicenow_mcp.mcp.outputs import error_result
from servicenow_mcp.mcp.tools.mutations import register_tools
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server import MCPServer
    from pydantic import BaseModel

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


class RegisteredToolLike(Protocol):
    """Typed subset of MCPServer's registered tool model used in tests."""

    name: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]


def decode_response(raw: BaseModel) -> dict[str, Any]:
    """Inspect a native capability or MCP result."""
    result = (
        raw.structured_content
        if isinstance(raw, CallToolResult)
        else raw.model_dump(mode="json")
    )
    assert isinstance(result, dict)
    assert "correlation_id" not in result
    assert "selection" not in result
    return result


def _get_registered_tools(mcp: MCPServer) -> dict[str, RegisteredToolLike]:
    """Return the registered tool mapping from an MCPServer instance."""
    return cast(
        "dict[str, RegisteredToolLike]", cast("object", mcp._tool_manager._tools)
    )


def get_tool_functions(mcp: MCPServer) -> dict[str, Callable[..., Any]]:
    """Invoke the public SDK with native arguments in existing behavioral fixtures."""

    def invoke(tool: RegisteredToolLike) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            try:
                bound = inspect.signature(tool.fn).bind(*args, **kwargs)
            except TypeError:
                return error_result("Arguments do not match the tool schema.")
            schema = {**tool.parameters, "additionalProperties": False}
            if (
                next(
                    iter(
                        Draft202012Validator(schema).iter_errors(dict(bound.arguments))
                    ),
                    None,
                )
                is not None
            ):
                return error_result("Arguments do not match the tool schema.")
            return await mcp.call_tool(tool.name, dict(bound.arguments))

        return call

    return {tool.name: invoke(tool) for tool in _get_registered_tools(mcp).values()}


async def get_registered_tools(mcp: MCPServer) -> dict[str, Any]:
    """Return registered MCP tool schemas keyed by name."""
    return {tool.name: tool for tool in await mcp.list_tools()}


async def get_tool_names(mcp: MCPServer) -> list[str]:
    """Return registered tool names in insertion order."""
    return [tool.name for tool in await mcp.list_tools()]


def register_mutations(
    mcp: MCPServer,
    settings: Settings,
    auth: OAuthPKCEProvider,
    families: frozenset[str],
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> MutationCoordinator:
    clients = client_factory or (lambda: ServiceNowClient(settings, auth))
    dictionary = dictionary or DictionaryRegistry(settings, auth, clients)
    coordinator = MutationCoordinator(settings, auth, clients, dictionary, families)
    register_tools(mcp, coordinator)
    return coordinator


async def preview_and_apply(
    tools: dict[str, Callable[..., Any]], tool: str, **arguments: Any
) -> Any:
    preview = await tools[tool](**arguments)
    value = decode_response(preview)
    if value["status"] == "error":
        return preview
    return await tools["mutation_apply"](preview_token=value["data"]["preview_token"])
