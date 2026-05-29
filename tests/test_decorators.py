"""Tests for the tool_handler decorator."""

import inspect
import json
import uuid
from typing import Any

from servicenow_mcp.decorators import _REDACTED, _redact_args, tool_handler
from servicenow_mcp.errors import ForbiddenError
from servicenow_mcp.utils import format_response
from tests.helpers import get_registered_tools


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
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
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

    def test_hides_correlation_id_from_signature(self) -> None:
        """The correlation_id parameter is hidden from inspect.signature()."""

        @tool_handler
        async def my_tool(_table: str, _limit: int = 10, *, correlation_id: str) -> str:
            return format_response(data=None, correlation_id=correlation_id)

        sig = inspect.signature(my_tool)
        param_names = list(sig.parameters.keys())
        assert "correlation_id" not in param_names
        assert "_table" in param_names
        assert "_limit" in param_names

    def test_preserves_function_name(self) -> None:
        """functools.wraps preserves __name__ and __doc__."""

        @tool_handler
        async def my_tool(_table: str, *, correlation_id: str) -> str:
            """My tool docstring."""
            return format_response(data=None, correlation_id=correlation_id)

        assert my_tool.__name__ == "my_tool"
        assert my_tool.__doc__ == "My tool docstring."

    def test_no_wrapped_attribute(self) -> None:
        """__wrapped__ is deleted to prevent inspect.signature from following it."""

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            return format_response(data=None, correlation_id=correlation_id)

        assert not hasattr(my_tool, "__wrapped__")

    async def test_catches_generic_exception(self) -> None:
        """Exceptions in the tool body are caught and returned as opaque envelopes.

        The wire-level message MUST NOT contain ``str(exc)`` — that would leak
        internal hostnames, paths, and platform stack fragments. The full
        exception is logged locally (see SECURITY in utils.safe_tool_call).
        """

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            _ = correlation_id
            raise RuntimeError("something broke")

        result = await my_tool()
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert parsed["status"] == "error"
        message = parsed["error"]["message"]
        assert "something broke" not in message
        assert message.startswith("Internal error")
        assert parsed["correlation_id"] in message

    async def test_catches_forbidden_error(self) -> None:
        """ForbiddenError is caught and returned as an ACL denial error envelope."""

        @tool_handler
        async def my_tool(*, correlation_id: str) -> str:
            _ = correlation_id
            raise ForbiddenError("ACL blocked")

        result = await my_tool()
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
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
        tools = get_registered_tools(mcp)
        assert "test_tool" in tools

        # Check the schema does NOT contain correlation_id
        tool = tools["test_tool"]
        schema = tool.parameters
        assert "correlation_id" not in schema.get("properties", {})
        assert "table" in schema.get("properties", {})

        # Check calling the tool works
        result = await tool.fn("my_table")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert parsed["status"] == "success"
        assert parsed["data"]["table"] == "my_table"


class TestRedactArgs:
    """Sensitive arg names are redacted before being attached to Sentry context."""

    def test_drops_correlation_id(self) -> None:
        """``correlation_id`` is dropped (sent as a separate context field)."""
        out = _redact_args({"correlation_id": "abc", "table": "incident"})
        assert "correlation_id" not in out
        assert out == {"table": "incident"}

    def test_redacts_known_sensitive_keys(self) -> None:
        """All canonical sensitive keys are replaced with the redaction marker."""
        sensitive = {
            "data": '{"name": "alice"}',
            "params": "anything",
            "password": "hunter2",
            "token": "deadbeef",
            "secret": "shh",
            "api_key": "k",
            "authorization": "Bearer x",
            "script_path": "/etc/passwd",
            "encoded_query": "active=true",
            "content_base64": "AAAA",
            "value": "raw",
        }
        out = _redact_args(sensitive)
        for key in sensitive:
            assert out[key] == _REDACTED, f"{key} was not redacted"

    def test_redacts_user_supplied_content_keys(self) -> None:
        """``variables``, ``conditions``, ``text`` carry user content and must redact."""
        out = _redact_args(
            {
                "variables": '{"email": "alice@example.com"}',
                "conditions": '[{"field": "x", "op": "=", "value": "secret"}]',
                "text": "search for something private",
            }
        )
        assert out["variables"] == _REDACTED
        assert out["conditions"] == _REDACTED
        assert out["text"] == _REDACTED

    def test_passes_through_non_sensitive(self) -> None:
        """Non-sensitive keys retain their original values."""
        out = _redact_args({"table": "incident", "limit": 10, "sys_id": "abc"})
        assert out == {"table": "incident", "limit": 10, "sys_id": "abc"}

    def test_key_match_is_case_insensitive(self) -> None:
        """Sensitive key matching ignores case."""
        out = _redact_args({"DATA": "x", "Token": "y"})
        assert out["DATA"] == _REDACTED
        assert out["Token"] == _REDACTED
