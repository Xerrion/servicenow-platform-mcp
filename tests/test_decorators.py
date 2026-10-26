"""Tests for the tool_handler decorator."""

import inspect
from unittest.mock import patch

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.mcp.handlers import _REDACTED, _redact_args, tool_handler
from servicenow_mcp.mcp.outputs import ToolResult


class TestToolHandler:
    """Tests for the tool_handler decorator."""

    def test_publishes_output_contract_and_preserves_wrapped_function(self) -> None:
        """The SDK sees both envelopes while the capability signature stays intact."""

        @tool_handler
        async def my_tool() -> CapabilityResult:
            return operation_result(data=None)

        assert inspect.unwrap(my_tool) is not my_tool
        assert inspect.signature(my_tool).return_annotation is ToolResult
        assert (
            inspect.signature(inspect.unwrap(my_tool)).return_annotation
            is CapabilityResult
        )

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "null",
            "false",
            "123",
            '"quoted"',
            '{"field":null}',
            '[false,0,""]',
        ],
    )
    async def test_nullable_string_survives_mcp_validation(
        self, value: str | None
    ) -> None:
        """Nullable text must not be JSON-decoded by the MCP SDK."""
        mcp = MCPServer("test")

        @mcp.tool()
        @tool_handler
        async def echo(required: str, value: str | None = None) -> CapabilityResult:
            return operation_result(data={"required": required, "value": value})

        schema = (await mcp.list_tools())[0].input_schema
        assert schema["required"] == ["required"]
        assert schema["properties"]["value"]["default"] is None
        assert {
            choice["type"] for choice in schema["properties"]["value"]["anyOf"]
        } == {"string", "null"}
        result = await mcp.call_tool("echo", {"required": "null", "value": value})
        assert isinstance(result, CallToolResult)
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["data"] == {"required": "null", "value": value}

    async def test_nullable_string_rejects_non_string_values(self) -> None:
        """Adding null support does not make arbitrary JSON values valid text."""
        for value in [{"field": None}, [False, 0], False, 123]:
            mcp = MCPServer("test")

            @mcp.tool()
            @tool_handler
            async def echo(value: str | None = None) -> CapabilityResult:
                return operation_result(data=value)

            with pytest.raises(ToolError, match="Input should be a valid string"):
                await mcp.call_tool("echo", {"value": value})


class TestRedactArgs:
    """Sensitive arg names are redacted before being attached to Sentry context."""

    def test_redacts_known_sensitive_keys(self) -> None:
        """All canonical sensitive keys are replaced with the redaction marker."""
        sensitive = {
            "data": {"name": "alice"},
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
                "variables": {"email": "alice@example.com"},
                "conditions": '[{"field": "x", "op": "=", "value": "secret"}]',
                "text": "search for something private",
            },
        )
        assert out["variables"] == _REDACTED
        assert out["conditions"] == _REDACTED
        assert out["text"] == _REDACTED

    async def test_wrapper_pipes_redacted_args_into_sentry_context(self) -> None:
        """``tool_handler`` must attach the *redacted* kwargs dict to Sentry.

        ``_redact_args`` is exercised in isolation by the tests above; this
        test pins the wiring at decorators.py so that a future refactor
        cannot accidentally hand raw kwargs (including ``password``,
        ``data``, etc.) to ``set_sentry_context``.
        """
        with patch("servicenow_mcp.mcp.handlers.set_sentry_context") as mock_ctx:

            @tool_handler
            async def my_tool(
                table: str,
                data: str = "",
                password: str = "",
                **kwargs: object,
            ) -> CapabilityResult:
                return operation_result(data=None)

            await my_tool(
                table="incident",
                data={"password": "hunter2"},
                password="hunter2",  # NOSONAR(S2068) - test fixture, not a credential
                Token="private-token",
            )

            tool_calls = [
                c for c in mock_ctx.call_args_list if c.args and c.args[0] == "tool"
            ]
            assert tool_calls, "set_sentry_context('tool', ...) was never called"
            ctx_payload = tool_calls[-1].args[1]
            recorded_args = ctx_payload["args"]

            assert recorded_args["data"] == _REDACTED
            assert recorded_args["password"] == _REDACTED
            assert recorded_args["table"] == "incident"
            assert recorded_args["Token"] == _REDACTED
