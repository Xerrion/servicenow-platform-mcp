"""MCP tool exception translation."""

import logging
from collections.abc import Awaitable, Callable

import httpx

from servicenow_mcp.errors import ACLError, ForbiddenError, ServiceNowMCPError
from servicenow_mcp.response import format_response
from servicenow_mcp.sentry import capture_exception as sentry_capture


logger = logging.getLogger(__name__)

_MUTATION_METHODS = frozenset({"POST", "PATCH", "DELETE"})


async def safe_tool_call(fn: Callable[[], Awaitable[str]]) -> str:
    """Run an MCP tool body and translate exceptions to error envelopes."""
    try:
        return await fn()
    except httpx.TimeoutException as e:
        sentry_capture(e)
        return format_response(data=None, status="error", error=_timeout_message(e))
    except ACLError as e:
        sentry_capture(e)
        return format_response(data=None, status="error", error=f"Access denied by ServiceNow ACL: {e}")
    except ForbiddenError as e:
        sentry_capture(e)
        return format_response(data=None, status="error", error=f"Access forbidden by ServiceNow: {e}")
    except ServiceNowMCPError as e:
        # Domain errors carry curated, caller-actionable messages.
        sentry_capture(e)
        return format_response(data=None, status="error", error=str(e))
    except ValueError as e:
        # Validators use ValueError for curated user-input errors.
        sentry_capture(e)
        return format_response(data=None, status="error", error=str(e))
    except Exception as e:
        # Keep unclassified internal details out of the wire response.
        logger.exception("Unhandled exception in tool")
        sentry_capture(e)
        return format_response(data=None, status="error", error="Internal error")


def _timeout_message(error: httpx.TimeoutException) -> str:
    method = _timeout_method(error)
    phase = _timeout_phase(error)
    if method in _MUTATION_METHODS:
        return (
            f"ServiceNow {method} request timed out while {phase}; remote outcome is unknown. "
            "Verify the remote outcome before retrying."
        )
    if method == "GET":
        return (
            f"ServiceNow GET request timed out while {phase}. Narrow the query with indexed predicates or a smaller "
            "date window. For sys_audit, prefer documentkey. Lowering limit only reduces returned rows and might not "
            "reduce scan or count cost."
        )
    return f"ServiceNow HTTP request timed out while {phase}."


def _timeout_method(error: httpx.TimeoutException) -> str | None:
    try:
        return error.request.method.upper()
    except RuntimeError:
        return None


def _timeout_phase(error: httpx.TimeoutException) -> str:
    if isinstance(error, httpx.ConnectTimeout):
        return "connecting"
    if isinstance(error, httpx.WriteTimeout):
        return "sending the request"
    if isinstance(error, httpx.ReadTimeout):
        return "receiving the response"
    if isinstance(error, httpx.PoolTimeout):
        return "waiting for a connection"
    return "processing the request"
