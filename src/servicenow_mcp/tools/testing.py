"""ATF (Automated Test Framework) tools for introspection, execution, and intelligence."""

import json
import logging

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import write_blocked_reason
from servicenow_mcp.utils import format_response

logger = logging.getLogger(__name__)

# ATF polling constants
ATF_POLL_INTERVAL = 5
ATF_MAX_POLL_DURATION = 300


def _write_gate(table: str, settings: Settings, correlation_id: str) -> str | None:
    """Check write access and return a JSON error envelope if blocked, or None if allowed."""
    reason = write_blocked_reason(table, settings)
    if reason:
        return json.dumps(
            format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=reason,
            )
        )
    return None


def _atf_execution_gate(settings: Settings, correlation_id: str) -> str | None:
    """Gate ATF execution tools - running tests creates result records."""
    reason = write_blocked_reason("sys_atf_test_result", settings)
    if reason:
        return json.dumps(
            format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=reason,
            )
        )
    return None


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register ATF (Automated Test Framework) tools on the MCP server."""
    pass
