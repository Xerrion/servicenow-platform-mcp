"""Decorators for reducing tool function boilerplate."""

import functools
import inspect
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol

from servicenow_mcp.mcp.errors import safe_tool_call
from servicenow_mcp.mcp.optional_text import nullable_string_signature
from servicenow_mcp.mcp.outputs import ToolResult
from servicenow_mcp.sentry import sentry_tool_scope, set_sentry_context, set_sentry_tag
from servicenow_mcp.telemetry import (
    current_tool_trace,
    trace_tool_call,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from mcp.types import CallToolResult

    from servicenow_mcp.capabilities.contracts import CapabilityResult


# Arg names whose values may carry credentials, PII, or large untrusted payloads.
# Values for these keys are replaced with a constant placeholder before being
# attached to the Sentry "tool" context. Matched case-insensitively on the arg
# name. See SECURITY: do not narrow this set without an explicit review.
_SENSITIVE_ARG_KEYS: frozenset[str] = frozenset(
    {
        "data",
        "content_base64",
        "value",
        "script_path",
        "encoded_query",
        "params",
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
        "preview_token",
        "name",
        "description",
        "file_name",
        "label",
        "element_id",
        # User-supplied content surfaces that may carry PII, credentials, or
        # other sensitive material. ``variables`` (service_catalog catalog-item
        # variables) often holds names, addresses, license keys. ``conditions``
        # can hold untrusted filter values. ``text`` is free-form search input.
        "variables",
        "conditions",
        "text",
        "term",
        "resolve_labels",
        "item_type",
        "items",
        "also_request_for",
        "requested_for",
        "user_sys_id",
    },
)

_REDACTED: str = "***REDACTED***"


class _ToolFunction(Protocol):
    """An async tool function with a name for telemetry."""

    __name__: str

    def __call__(
        self, *args: Any, **kwargs: Any
    ) -> Coroutine[Any, Any, CapabilityResult]: ...


def _redact_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Redact payload arguments and nested values before diagnostic output."""
    redacted: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k.lower() in _SENSITIVE_ARG_KEYS or not isinstance(
            v, str | int | float | bool | type(None)
        ):
            redacted[k] = _REDACTED
        else:
            redacted[k] = v
    return redacted


def _bound_tool_args(
    signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Include positional and default inputs while redacting unstructured arguments."""
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    values: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        kind = signature.parameters[name].kind
        if kind == inspect.Parameter.VAR_KEYWORD:
            values.update(value)
        else:
            values[name] = (
                _REDACTED if kind == inspect.Parameter.VAR_POSITIONAL else value
            )
    return _redact_args(values)


def tool_handler(
    fn: _ToolFunction,
) -> Callable[..., Coroutine[Any, Any, CapabilityResult | CallToolResult]]:
    """Preserve native inputs while adding safe diagnostics and error results."""
    input_signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> CapabilityResult | CallToolResult:
        with sentry_tool_scope(fn.__name__), trace_tool_call(fn.__name__):
            started = perf_counter()
            set_sentry_tag("tool.name", fn.__name__)

            async def _run() -> CapabilityResult:
                try:
                    set_sentry_context(
                        "tool",
                        {
                            "name": fn.__name__,
                            "args": _bound_tool_args(input_signature, args, kwargs),
                        },
                    )
                    result = await fn(*args, **kwargs)
                    return result
                finally:
                    trace = current_tool_trace()
                    if trace is not None:
                        set_sentry_context(
                            "tool_trace",
                            {
                                "trace_id": trace.trace_id,
                                "tool": trace.tool,
                                "http_requests": trace.request_count,
                                "duration_ms": round(
                                    (perf_counter() - started) * 1000, 3
                                ),
                            },
                        )

            return await safe_tool_call(_run)

    signature = nullable_string_signature(fn) or input_signature
    # RootModel publishes both envelopes without the SDK wrapping a bare union in {"result": ...}.
    wrapper.__signature__ = signature.replace(return_annotation=ToolResult)  # ty: ignore[unresolved-attribute]
    return wrapper
