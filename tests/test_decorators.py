"""Tests for the tool_handler decorator."""

import inspect
import uuid
from typing import Any

from toon_format import decode as toon_decode

from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import ForbiddenError
from servicenow_mcp.utils import format_response


class TestToolHandler:
    """Tests for the tool_handler decorator."""

    async def test_injects_correlation_id(self) -> None:
        """Decorator injects a correlation_id kwarg at call time."""
        captured: dict[str, str] = {}

        @tool_handler
        async def my_tool(table: str, *, correlation_id: str) -> str:
            captured["correlation_id"] = correlation_id
            return format_response(data={"table": table}, correlation_id=correlation_id)

        result = await my_tool("incident")
        assert captured["correlation_id"]  # non-empty UUID
        parsed = toon_decode(result)
        assert parsed["status"] == "success"
        assert parsed["correlation_id"] == captured["correlation_id"]

    async def test_correlation_id_is_uuid(self) -> None:
        """Injected correlation_id is a valid UUID string."""
        captured: dict[str, str] = {}

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            captured["cid"] = correlation_id
            return format_response(data=None, correlation_id=correlation_id)

        await my_tool()
        uuid.UUID(captured["cid"])  # Raises if not valid UUID

    async def test_hides_correlation_id_from_signature(self) -> None:
        """The correlation_id parameter is hidden from inspect.signature()."""

        @tool_handler
        async def my_tool(table: str, limit: int = 10, *, correlation_id: str) -> str:
            return format_response(data=None, correlation_id=correlation_id)

        sig = inspect.signature(my_tool)
        param_names = list(sig.parameters.keys())
        assert "correlation_id" not in param_names
        assert "table" in param_names
        assert "limit" in param_names

    async def test_preserves_function_name(self) -> None:
        """functools.wraps preserves __name__ and __doc__."""

        @tool_handler
        async def my_tool(table: str, *, correlation_id: str) -> str:
            """My tool docstring."""
            return format_response(data=None, correlation_id=correlation_id)

        assert my_tool.__name__ == "my_tool"
        assert my_tool.__doc__ == "My tool docstring."

    async def test_no_wrapped_attribute(self) -> None:
        """__wrapped__ is deleted to prevent inspect.signature from following it."""

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            return format_response(data=None, correlation_id=correlation_id)

        assert not hasattr(my_tool, "__wrapped__")

    async def test_catches_generic_exception(self) -> None:
        """Exceptions in the tool body are caught and returned as error envelopes."""

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            raise ValueError("something broke")

        result = await my_tool()
        parsed = toon_decode(result)
        assert parsed["status"] == "error"
        assert "something broke" in parsed["error"]["message"]

    async def test_catches_forbidden_error(self) -> None:
        """ForbiddenError is caught and returned as an ACL denial error envelope."""

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            raise ForbiddenError("ACL blocked")

        result = await my_tool()
        parsed = toon_decode(result)
        assert parsed["status"] == "error"
        assert "Access denied" in parsed["error"]["message"] or "ACL" in parsed["error"]["message"]

    async def test_passes_args_and_kwargs(self) -> None:
        """Positional and keyword arguments are forwarded correctly."""
        captured: dict[str, Any] = {}

        @tool_handler
        async def my_tool(table: str, fields: str = "", *, correlation_id: str) -> str:
            captured["table"] = table
            captured["fields"] = fields
            return format_response(data={"ok": True}, correlation_id=correlation_id)

        await my_tool("incident", fields="name,state")
        assert captured["table"] == "incident"
        assert captured["fields"] == "name,state"

    async def test_unique_correlation_ids_per_call(self) -> None:
        """Each invocation gets a unique correlation_id."""
        ids: list[str] = []

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            ids.append(correlation_id)
            return format_response(data=None, correlation_id=correlation_id)

        await my_tool()
        await my_tool()
        assert len(ids) == 2
        assert ids[0] != ids[1]

    async def test_works_with_fastmcp_tool_registration(self) -> None:
        """Verify the decorator works with @mcp.tool() registration."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")

        @mcp.tool()
        @tool_handler
        async def test_tool(table: str, *, correlation_id: str) -> str:
            """A test tool.

            Args:
                table: The table name.
            """
            return format_response(data={"table": table}, correlation_id=correlation_id)

        # Check the tool was registered
        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        assert "test_tool" in tools

        # Check the schema does NOT contain correlation_id
        tool = tools["test_tool"]
        schema = tool.parameters
        assert "correlation_id" not in schema.get("properties", {})
        assert "table" in schema.get("properties", {})

        # Check calling the tool works
        result = await tool.fn("my_table")
        parsed = toon_decode(result)
        assert parsed["status"] == "success"
        assert parsed["data"]["table"] == "my_table"
