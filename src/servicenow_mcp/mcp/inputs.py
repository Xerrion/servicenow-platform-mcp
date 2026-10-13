"""Raw protocol validation before the SDK's argument coercion."""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from jsonschema import Draft202012Validator
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.mcp.outputs import error_result


class RawInputMiddleware:
    def __init__(self, list_tools: Callable[[], Awaitable[list[Any]]], models: dict[str, type[BaseModel]]) -> None:
        self._list_tools = list_tools
        self._models = models

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params
        if not isinstance(params, dict):
            return error_result("Tool parameters must be an object.")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return error_result("Supply a tool name and an arguments object.")
        tools = {tool.name: tool for tool in await self._list_tools()}
        if name not in tools:
            return error_result("Unknown tool.", "UNKNOWN_TOOL")
        schema = {**tools[name].input_schema, "additionalProperties": False}
        if next(iter(Draft202012Validator(schema).iter_errors(arguments)), None) is not None:
            return error_result("Arguments do not match the tool schema. Check required fields and native input types.")
        try:
            self._models[name].model_validate(arguments)
        except ValidationError:
            return error_result("Arguments violate the tool's input constraints. Check the tool description.")
        except CapabilityError as error:
            return error_result(str(error), error.code)
        return await call_next(ctx)


class NativeMCPServer(MCPServer):
    """Publish strict input schemas and validate the actual protocol path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._input_models: dict[str, type[BaseModel]] = {}
        middleware = RawInputMiddleware(self.list_tools, self._input_models)
        super().__init__(*args, middleware=[middleware], **kwargs)

    def add_tool(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        signature = inspect.signature(fn, eval_str=True)
        fields: dict[str, Any] = {
            parameter.name: (
                parameter.annotation,
                ... if parameter.default is inspect.Parameter.empty else parameter.default,
            )
            for parameter in signature.parameters.values()
        }
        name = kwargs.get("name") or (args[0] if args else None) or getattr(fn, "__name__", "tool")
        if not isinstance(name, str):
            raise TypeError("A tool name must be a string.")
        self._input_models[name] = create_model(
            f"{name}NativeInput",
            __config__=ConfigDict(extra="forbid", hide_input_in_errors=True),
            **fields,
        )
        super().add_tool(fn, *args, **kwargs)

    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return tools
