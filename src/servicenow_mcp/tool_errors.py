"""MCP tool exception translation."""

import logging
from collections.abc import Awaitable, Callable

import httpx

from servicenow_mcp.errors import ACLError, ForbiddenError, ServiceNowMCPError
from servicenow_mcp.response import format_response
from servicenow_mcp.sentry import capture_exception as sentry_capture
from servicenow_mcp.telemetry import current_tool_trace, request_operation, timeout_phase


logger = logging.getLogger(__name__)


def _timeout_error(error: httpx.TimeoutException) -> dict[str, str]:
    phase = timeout_phase(error)
    try:
        operation = request_operation(error.request)
        is_read = error.request.method in {"GET", "HEAD"}
    except RuntimeError:
        operation = "unknown"
        is_read = False

    guidance = {
        "connect": "Check connectivity to ServiceNow; this is not evidence of a slow query or an ACL denial.",
        "pool": "The HTTP connection pool wait expired. Check in-flight requests before issuing more calls.",
        "write": "Sending the request timed out. Check connectivity and the remote outcome before retrying.",
    }.get(
        phase,
        "Inspect the matching local trace to identify the operation that exceeded its HTTP timeout.",
    )
    if phase == "read" and operation in {"records", "aggregate", "unknown"} and (is_read or operation == "unknown"):
        guidance += (
            " If this was a query, narrow it with indexed predicates or a smaller date window."
            " For sys_audit, prefer documentkey. Lowering limit only reduces returned rows and might not reduce "
            "scan or count cost."
        )
    if operation in {"table_metadata", "dictionary", "choices", "documentation"}:
        guidance += " The timed-out request was a metadata lookup, not a read of the target records."
    if not is_read:
        guidance += " A write may have completed remotely; verify its outcome before retrying."
    result = {
        "code": "UPSTREAM_TIMEOUT",
        "message": f"ServiceNow HTTP request timed out ({phase}). {guidance} The request was not replayed.",
        "phase": phase,
        "operation": operation,
    }
    trace = current_tool_trace()
    if trace is not None:
        result["trace_id"] = trace.trace_id
    return result


async def safe_tool_call(fn: Callable[[], Awaitable[str]]) -> str:
    """Run an MCP tool body and translate exceptions to error envelopes."""
    try:
        return await fn()
    except httpx.TimeoutException as e:
        sentry_capture(e)
        return format_response(
            data=None,
            status="error",
            error=_timeout_error(e),
        )
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
