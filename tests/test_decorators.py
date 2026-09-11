"""Tests for the tool_handler decorator."""

import inspect
import json
from typing import Any
from unittest.mock import patch

from servicenow_mcp.decorators import _REDACTED, _redact_args, tool_handler
from servicenow_mcp.errors import ForbiddenError
from servicenow_mcp.response import format_response
from tests.helpers import get_registered_tools


class TestToolHandler:
    """Tests for the tool_handler decorator."""

    def test_preserves_signature(self) -> None:
        """Tool inputs and defaults remain available to schema introspection."""

        @tool_handler
        async def my_tool(_table: str, _limit: int = 10) -> str:
            return format_response(data=None)

        sig = inspect.signature(my_tool)
        param_names = list(sig.parameters.keys())
        assert "correlation_id" not in param_names
        assert "_table" in param_names
        assert "_limit" in param_names
        assert sig.parameters["_limit"].default == 10

    def test_preserves_function_name(self) -> None:
        """functools.wraps preserves __name__ and __doc__."""

        @tool_handler
        async def my_tool(_table: str) -> str:
            """My tool docstring."""
            return format_response(data=None)

        assert my_tool.__name__ == "my_tool"
        assert my_tool.__doc__ == "My tool docstring."

    def test_preserves_wrapped_function(self) -> None:
        """Standard functools metadata needs no custom signature override."""

        @tool_handler
        async def my_tool() -> str:
            return format_response(data=None)

        assert inspect.unwrap(my_tool) is not my_tool
        assert not hasattr(my_tool, "__signature__")

    async def test_catches_generic_exception(self) -> None:
        """Exceptions in the tool body are caught and returned as opaque envelopes.

        The wire-level message MUST NOT contain ``str(exc)`` — that would leak
        internal hostnames, paths, and platform stack fragments. The full
        exception is logged locally by ``safe_tool_call``.
        """

        @tool_handler
        async def my_tool() -> str:
            raise RuntimeError("something broke")

        result = await my_tool()
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert parsed["status"] == "error"
        message = parsed["error"]["message"]
        assert "something broke" not in message
        assert message.startswith("Internal error")
        assert "correlation_id" not in parsed

    async def test_catches_forbidden_error(self) -> None:
        """ForbiddenError is caught and returned as an ACL denial error envelope."""

        @tool_handler
        async def my_tool() -> str:
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
        async def my_tool(table: str, fields: str = "") -> str:
            captured["table"] = table
            captured["fields"] = fields
            return format_response(data={"ok": True})

        await my_tool("incident", fields="name,state")
        assert captured["table"] == "incident"
        assert captured["fields"] == "name,state"

    async def test_no_internal_arguments_injected(self) -> None:
        """Repeated calls receive only the caller's inputs."""
        calls: list[dict[str, Any]] = []

        @tool_handler
        async def my_tool(**kwargs: Any) -> str:
            calls.append(kwargs)
            return format_response(data=None)

        await my_tool()
        await my_tool()
        assert calls == [{}, {}]

    async def test_works_with_mcp_server_tool_registration(self) -> None:
        """Verify the decorator works with @mcp.tool() registration."""
        from mcp.server import MCPServer

        mcp = MCPServer("test")

        @mcp.tool()
        @tool_handler
        async def test_tool(table: str) -> str:
            """A test tool.

            Args:
                table: The table name.
            """
            return format_response(data={"table": table})

        # Check the tool was registered
        tools = await get_registered_tools(mcp)
        assert "test_tool" in tools

        # Check the schema does NOT contain correlation_id
        tool = tools["test_tool"]
        schema = tool.input_schema
        assert "correlation_id" not in schema.get("properties", {})
        assert "table" in schema.get("properties", {})

        # Check calling the tool works
        call_result = await mcp.call_tool("test_tool", {"table": "my_table"})
        assert call_result.result_type == "complete"
        result = call_result.structured_content
        assert isinstance(result, dict)
        raw = result["result"]
        assert isinstance(raw, str)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert parsed["status"] == "success"
        assert parsed["data"]["table"] == "my_table"


class TestRedactArgs:
    """Sensitive arg names are redacted before being attached to Sentry context."""

    def test_redacts_known_sensitive_keys(self) -> None:
        """All canonical sensitive keys are replaced with the redaction marker."""
        sensitive = {
            "data": '{"name": "alice"}',
            "params": "anything",
            "password": "hunter2",  # NOSONAR(S2068) - test fixture asserting redaction; not a credential.
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

    async def test_wrapper_pipes_redacted_args_into_sentry_context(self) -> None:
        """``tool_handler`` must attach the *redacted* kwargs dict to Sentry.

        ``_redact_args`` is exercised in isolation by the tests above; this
        test pins the wiring at decorators.py so that a future refactor
        cannot accidentally hand raw kwargs (including ``password``,
        ``data``, etc.) to ``set_sentry_context``.
        """
        with patch("servicenow_mcp.decorators.set_sentry_context") as mock_ctx:

            @tool_handler
            async def my_tool(
                table: str,
                data: str = "",
                password: str = "",
            ) -> str:
                return format_response(data=None)

            await my_tool(
                table="incident",
                data='{"password":"hunter2"}',
                password="hunter2",  # NOSONAR(S2068) - test fixture, not a credential
            )

            tool_calls = [c for c in mock_ctx.call_args_list if c.args and c.args[0] == "tool"]
            assert tool_calls, "set_sentry_context('tool', ...) was never called"
            ctx_payload = tool_calls[-1].args[1]
            recorded_args = ctx_payload["args"]

            assert recorded_args["data"] == _REDACTED
            assert recorded_args["password"] == _REDACTED
            assert recorded_args["table"] == "incident"
