"""Shared test helper utilities."""

from collections.abc import Callable
from typing import Any, Protocol, cast

from mcp.server.fastmcp import FastMCP
from toon_format import decode as toon_decode


class RegisteredToolLike(Protocol):
    """Typed subset of FastMCP's registered tool model used in tests."""

    name: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]


class _ToolManagerLike(Protocol):
    """Typed subset of FastMCP's tool manager used in tests."""

    _tools: dict[str, RegisteredToolLike]


class _FastMCPLike(Protocol):
    """Typed subset of FastMCP used for test helper access."""

    _tool_manager: _ToolManagerLike


def decode_response(raw: str) -> dict[str, Any]:
    """Decode a TOON-encoded tool response, asserting it is a dict.

    All MCP tool responses in this project are TOON-encoded dicts. This helper
    narrows the return type from toon_decode's broad union to dict[str, Any],
    which eliminates mypy index errors throughout the test suite.

    Args:
        raw: TOON-encoded string from a tool call.

    Returns:
        Decoded response dict with status, data, correlation_id, etc.

    Raises:
        AssertionError: If the decoded value is not a dict.
    """
    result = toon_decode(raw)
    assert isinstance(result, dict), f"Expected dict from toon_decode, got {type(result).__name__}"
    return result


def get_registered_tools(mcp: FastMCP) -> dict[str, RegisteredToolLike]:
    """Return the registered tool mapping from a FastMCP instance."""
    typed_mcp = cast("_FastMCPLike", cast("object", mcp))
    return typed_mcp._tool_manager._tools


def get_tool_functions(mcp: FastMCP) -> dict[str, Callable[..., Any]]:
    """Return a mapping of tool name to callable for assertions and invocation."""
    return {tool.name: tool.fn for tool in get_registered_tools(mcp).values()}


def get_tool_names(mcp: FastMCP) -> list[str]:
    """Return registered tool names in insertion order."""
    return list(get_registered_tools(mcp))
