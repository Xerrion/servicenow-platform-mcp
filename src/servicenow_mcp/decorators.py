"""Decorators for reducing tool function boilerplate."""

import functools
from collections.abc import Callable, Coroutine
from typing import Any

from servicenow_mcp.sentry import set_sentry_context, set_sentry_tag
from servicenow_mcp.utils import safe_tool_call


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


def tool_handler(
    fn: Callable[..., Coroutine[Any, Any, str]],
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Preserve tool inputs while adding Sentry context and error envelopes.

    Usage::

        @mcp.tool()
        @tool_handler
        async def my_tool(table: str) -> str:
            ...
            return format_response(data=result)
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        set_sentry_tag("tool.name", fn.__name__)

        set_sentry_context(
            "tool",
            {
                "name": fn.__name__,
                "args": _redact_args(kwargs),
            },
        )

        async def _run() -> str:
            return await fn(*args, **kwargs)

        return await safe_tool_call(_run)

    return wrapper
