"""Decorators for reducing tool function boilerplate."""

import functools
import inspect
from collections.abc import Callable, Coroutine
from typing import Any, cast

from servicenow_mcp.policy import is_dangerous_bypass_enabled
from servicenow_mcp.sentry import set_sentry_context, set_sentry_tag
from servicenow_mcp.types import SignatureMutableCallable
from servicenow_mcp.utils import generate_correlation_id, safe_tool_call


def tool_handler(
    fn: Callable[..., Coroutine[Any, Any, str]],
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Wrap a tool function with automatic correlation ID and error handling.

    The decorated function receives ``correlation_id`` as a keyword argument
    injected at call time. The ``__signature__`` is overridden to hide
    ``correlation_id`` from FastMCP's schema introspection.

    Usage::

        @mcp.tool()
        @tool_handler
        async def my_tool(table: str, *, correlation_id: str) -> str:
            ...
            return format_response(data=result, correlation_id=correlation_id)
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        correlation_id = generate_correlation_id()

        set_sentry_tag("tool.name", fn.__name__)
        set_sentry_tag("tool.correlation_id", correlation_id)
        if is_dangerous_bypass_enabled():
            set_sentry_tag("servicenow.dangerous_bypass", "true")

        set_sentry_context(
            "tool",
            {
                "name": fn.__name__,
                "correlation_id": correlation_id,
                "arg_keys": sorted(k for k in kwargs if k != "correlation_id"),
            },
        )

        async def _run() -> str:
            return await fn(*args, correlation_id=correlation_id, **kwargs)

        return await safe_tool_call(_run, correlation_id)

    # Hide correlation_id from FastMCP tool schema introspection.
    # inspect.signature() follows __wrapped__ set by functools.wraps,
    # so we must remove it and provide an explicit __signature__ instead.
    original_sig = inspect.signature(fn)
    typed_wrapper = cast("SignatureMutableCallable", cast("object", wrapper))
    typed_wrapper.__signature__ = original_sig.replace(
        parameters=[p for p in original_sig.parameters.values() if p.name != "correlation_id"]
    )
    del typed_wrapper.__wrapped__

    return wrapper
