"""Decorators for reducing tool function boilerplate."""

import functools
import inspect
from collections.abc import Callable, Coroutine
from typing import Any, cast

from servicenow_mcp.sentry import set_sentry_context, set_sentry_tag
from servicenow_mcp.types import SignatureMutableCallable
from servicenow_mcp.utils import generate_correlation_id, safe_tool_call


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
    }
)

_REDACTED: str = "***REDACTED***"


def _redact_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``kwargs`` with sensitive values replaced.

    ``correlation_id`` is dropped (it is sent as a separate context field).
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
        if k == "correlation_id":
            continue
        if k.lower() in _SENSITIVE_ARG_KEYS:
            redacted[k] = _REDACTED
        else:
            redacted[k] = v
    return redacted


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

        set_sentry_context(
            "tool",
            {
                "name": fn.__name__,
                "correlation_id": correlation_id,
                "args": _redact_args(kwargs),
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
