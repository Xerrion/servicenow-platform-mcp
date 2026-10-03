"""Decorators for reducing tool function boilerplate."""

import functools
import inspect
from collections.abc import Callable, Coroutine
from time import perf_counter
from typing import Any, Protocol

from servicenow_mcp.sentry import sentry_tool_scope, set_sentry_context, set_sentry_tag
from servicenow_mcp.telemetry import current_tool_trace, trace_tool_call
from servicenow_mcp.tool_errors import safe_tool_call
from servicenow_mcp.tool_inputs import nullable_string_signature


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
        # User-supplied content surfaces that may carry PII, credentials, or
        # other sensitive material. ``variables`` (service_catalog catalog-item
        # variables) often holds names, addresses, license keys. ``conditions``
        # can hold untrusted filter values. ``text`` is free-form search input.
        "variables",
        "conditions",
        "text",
        "term",
        "resolve_labels",
    }
)

_REDACTED: str = "***REDACTED***"


class _ToolFunction(Protocol):
    """An async tool function with a name for telemetry."""

    __name__: str

    def __call__(self, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, str]: ...


def _redact_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``kwargs`` with sensitive values replaced.

    Keys are matched case-insensitively against ``_SENSITIVE_ARG_KEYS``.

    Shallow redaction is sufficient because every current tool arg is either
    a primitive or a JSON-encoded string; JSON-shaped args (``data``,
    ``params``, ``variables``, ``conditions``) arrive here as unparsed
    strings and are redacted whole. Revisit if a tool ever accepts a
    ``dict``/``list`` parameter directly - nested sensitive values inside
    such a structure would not be reached by this pass.
    """
    redacted: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k.lower() in _SENSITIVE_ARG_KEYS:
            redacted[k] = _REDACTED
        else:
            redacted[k] = v
    return redacted


def _bound_tool_args(signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Include positional and default inputs while redacting unstructured arguments."""
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    values: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        kind = signature.parameters[name].kind
        if kind == inspect.Parameter.VAR_KEYWORD:
            values.update(value)
        else:
            values[name] = _REDACTED if kind == inspect.Parameter.VAR_POSITIONAL else value
    return _redact_args(values)


def tool_handler(
    fn: _ToolFunction,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Preserve tool inputs while adding Sentry context and error envelopes.

    Usage::

        @mcp.tool()
        @tool_handler
        async def my_tool(table: str) -> str:
            ...
            return format_response(data=result)
    """
    input_signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        with sentry_tool_scope(fn.__name__), trace_tool_call(fn.__name__):
            started = perf_counter()
            set_sentry_tag("tool.name", fn.__name__)

            async def _run() -> str:
                try:
                    set_sentry_context(
                        "tool",
                        {"name": fn.__name__, "args": _bound_tool_args(input_signature, args, kwargs)},
                    )
                    return await fn(*args, **kwargs)
                finally:
                    trace = current_tool_trace()
                    if trace is not None:
                        set_sentry_context(
                            "tool_trace",
                            {
                                "trace_id": trace.trace_id,
                                "tool": trace.tool,
                                "http_requests": trace.request_count,
                                "duration_ms": round((perf_counter() - started) * 1000, 3),
                            },
                        )

            return await safe_tool_call(_run)

    signature = nullable_string_signature(fn)
    if signature is not None:
        # functools.wraps preserves the runtime function object, whose dynamic
        # signature is consumed by inspect.signature and MCP schema generation.
        wrapper.__signature__ = signature  # ty: ignore[unresolved-attribute]
    return wrapper
