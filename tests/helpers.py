"""Shared test helper utilities."""

import json
from collections.abc import Callable
from typing import Any, Protocol, cast

from mcp.server import MCPServer


class RegisteredToolLike(Protocol):
    """Typed subset of MCPServer's registered tool model used in tests."""

    name: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]


def decode_response(raw: str) -> dict[str, Any]:
    """Decode a JSON-encoded tool response, asserting it is a dict.

    All MCP tool responses in this project are JSON-encoded dicts. This helper
    narrows the return type from json.loads's broad union to dict[str, Any],
    which eliminates mypy index errors throughout the test suite.

    Args:
        raw: JSON-encoded string from a tool call.

    Returns:
        Decoded response dict with status, data, correlation_id, etc.

    Raises:
        AssertionError: If the decoded value is not a dict.
    """
    result = json.loads(raw)
    assert isinstance(result, dict), f"Expected dict from json.loads, got {type(result).__name__}"
    return result


def _get_registered_tools(mcp: MCPServer) -> dict[str, RegisteredToolLike]:
    """Return the registered tool mapping from an MCPServer instance."""
    return cast("dict[str, RegisteredToolLike]", cast("object", mcp._tool_manager._tools))


def get_tool_functions(mcp: MCPServer) -> dict[str, Callable[..., Any]]:
    """Return a mapping of tool name to callable for assertions and invocation."""
    return {tool.name: tool.fn for tool in _get_registered_tools(mcp).values()}


async def get_registered_tools(mcp: MCPServer) -> dict[str, Any]:
    """Return registered MCP tool schemas keyed by name."""
    return {tool.name: tool for tool in await mcp.list_tools()}


async def get_tool_names(mcp: MCPServer) -> list[str]:
    """Return registered tool names in insertion order."""
    return [tool.name for tool in await mcp.list_tools()]
