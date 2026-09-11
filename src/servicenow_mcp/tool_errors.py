"""MCP tool exception translation."""

import logging
from collections.abc import Awaitable, Callable

from servicenow_mcp.errors import ACLError, ForbiddenError, ServiceNowMCPError
from servicenow_mcp.response import format_response
from servicenow_mcp.sentry import capture_exception as sentry_capture


logger = logging.getLogger(__name__)


async def safe_tool_call(fn: Callable[[], Awaitable[str]]) -> str:
    """Run an MCP tool body and translate exceptions to error envelopes."""
    try:
        return await fn()
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
