"""Raw protocol validation before the SDK's argument coercion."""

import inspect
import json
from typing import TYPE_CHECKING, Any, override

from jsonschema import Draft202012Validator
from mcp.server import MCPServer
from mcp.types import CallToolResult
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.mcp.outputs import error_result
from servicenow_mcp.telemetry import trace_flow_preview, trace_flow_preview_boundary

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext


_MAX_EXPECTED = 160
_SCHEMA_MESSAGE = "Arguments do not match the tool schema. Check required fields and native input types."
_JSON_STRING_HINT = (
    "Pass arrays and objects as native JSON values, not JSON-encoded strings."
)


def _expected(schema: Any) -> str:
    """Describe a declared property type from schema structure only (no descriptions, defaults or examples)."""
    if not isinstance(schema, dict):
        return "value"
    branches = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(branches, list):
        text = " | ".join(dict.fromkeys(_expected(branch) for branch in branches))
    elif "const" in schema:
        text = (
            json.dumps(schema["const"])
            if isinstance(schema["const"], (str, int, bool))
            else "constant"
        )
    elif isinstance(schema.get("enum"), list):
        text = "one of " + ", ".join(
            json.dumps(v) for v in schema["enum"] if isinstance(v, (str, int, bool))
        )
    elif "$ref" in schema:
        text = "object"
    elif schema.get("type") == "array":
        text = (
            f"array of {_expected(schema.get('items'))}"
            if "items" in schema
            else "array"
        )
    elif isinstance(schema.get("type"), str):
        text = str(schema["type"])
    else:
        text = "value"
    return text if len(text) <= _MAX_EXPECTED else text[: _MAX_EXPECTED - 3] + "..."


def _looks_json_encoded(value: Any, schema: Any) -> bool:
    expected = _expected(schema)
    return (
        isinstance(value, str)
        and value.lstrip()[:1] in ("[", "{")
        and ("array" in expected or "object" in expected)
    )


def _diagnose(
    schema: dict[str, Any], arguments: dict[str, Any], names: list[str]
) -> CallToolResult:
    """Return schema-declared names only; user keys, values and validator text are never echoed."""
    declared = schema.get("properties")
    properties: dict[str, Any] = declared if isinstance(declared, dict) else {}
    listed = schema.get("required")
    required: list[Any] = listed if isinstance(listed, list) else []
    missing = [
        name
        for name in required
        if isinstance(name, str) and name in properties and name not in arguments
    ]
    invalid = [
        name
        for name in dict.fromkeys(names)
        if name in properties and name in arguments
    ]
    if not missing and not invalid:
        return error_result(_SCHEMA_MESSAGE)
    parts = []
    if missing:
        parts.append(f"Missing required arguments: {', '.join(missing)}.")
    if invalid:
        parts.append(
            f"Invalid argument types: {', '.join(invalid)}. See error.invalid_fields for expected types."
        )
    if any(_looks_json_encoded(arguments[name], properties[name]) for name in invalid):
        parts.append(_JSON_STRING_HINT)
    return error_result(
        " ".join(parts),
        missing_arguments=missing or None,
        invalid_fields=[
            {"field": name, "expected": _expected(properties[name])} for name in invalid
        ]
        or None,
    )


class RawInputMiddleware:
    def __init__(
        self,
        list_tools: Callable[[], Awaitable[list[Any]]],
        models: dict[str, type[BaseModel]],
    ) -> None:
        self._list_tools = list_tools
        self._models = models

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if (
            ctx.method == "tools/call"
            and isinstance(ctx.params, dict)
            and ctx.params.get("name") == "flow_preview"
        ):
            with trace_flow_preview():
                trace_flow_preview_boundary(
                    "protocol", arguments=ctx.params.get("arguments")
                )
                result = await self._validated_call(ctx, call_next)
                # MCP 2.3's runner serializes inside call_next, before middleware resumes.
                wire = (
                    result.model_dump(by_alias=True)
                    if isinstance(result, CallToolResult)
                    else result
                )
                if isinstance(wire, dict):
                    structured = wire.get("structuredContent")
                    data = (
                        structured.get("data") if isinstance(structured, dict) else None
                    )
                    trace_flow_preview_boundary(
                        "wire.structured",
                        summary=data.get("summary") if isinstance(data, dict) else None,
                    )
                    blocks = wire.get("content")
                    for block in blocks if isinstance(blocks, list) else []:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                        ):
                            try:
                                text = json.loads(block["text"])
                            except ValueError:
                                text = None
                            data = text.get("data") if isinstance(text, dict) else None
                            trace_flow_preview_boundary(
                                "wire.text",
                                summary=data.get("summary")
                                if isinstance(data, dict)
                                else None,
                            )
                            break
                return result
        return await self._validated_call(ctx, call_next)

    async def _validated_call(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
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
        errors = list(Draft202012Validator(schema).iter_errors(arguments))
        if errors:
            paths = [error.absolute_path[0] for error in errors if error.absolute_path]
            return _diagnose(
                schema, arguments, [path for path in paths if isinstance(path, str)]
            )
        try:
            self._models[name].model_validate(arguments)
        except ValidationError:
            return error_result(
                "Arguments violate the tool's input constraints. Check the tool description."
            )
        except CapabilityError as error:
            return error_result(
                str(error),
                error.code,
                missing_fields=error.missing_fields,
                missing_arguments=error.missing_arguments,
            )
        return await call_next(ctx)


class NativeMCPServer(MCPServer):
    """Publish strict input schemas and validate the actual protocol path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._input_models: dict[str, type[BaseModel]] = {}
        middleware = RawInputMiddleware(self.list_tools, self._input_models)
        super().__init__(*args, middleware=[middleware], **kwargs)

    @override
    def add_tool(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        signature = inspect.signature(fn, eval_str=True)
        fields: dict[str, Any] = {
            parameter.name: (
                parameter.annotation,
                ...
                if parameter.default is inspect.Parameter.empty
                else parameter.default,
            )
            for parameter in signature.parameters.values()
        }
        name = (
            kwargs.get("name")
            or (args[0] if args else None)
            or getattr(fn, "__name__", "tool")
        )
        if not isinstance(name, str):
            raise TypeError("A tool name must be a string.")
        self._input_models[name] = create_model(
            f"{name}NativeInput",
            __config__=ConfigDict(extra="forbid", hide_input_in_errors=True),
            **fields,
        )
        super().add_tool(fn, *args, **kwargs)

    @override
    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return tools
