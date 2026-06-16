"""Unified ``code_search`` tool: search ServiceNow script-bearing artifacts.

Three actions:

* ``search``      - search code across the configured ServiceNow Code Search tables.
* ``list_tables`` - list tables included by the Code Search API for a search group.
* ``describe``    - return the action registry without platform I/O.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = ["code_search"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"search", "list_tables", "describe"})

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "search": {
        "description": "Search code across ServiceNow script-bearing artifacts via the Code Search API.",
        "params": {
            "term": "str",
            "table": "str (optional)",
            "search_group": "str (optional)",
            "limit": "int (default 20)",
        },
    },
    "list_tables": {
        "description": "List the tables searched by the ServiceNow Code Search API.",
        "params": {"search_group": "str (optional)"},
    },
    "describe": {
        "description": "Return this action registry without making any platform calls.",
        "params": {},
    },
}


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _effective_limit(limit: int, settings: Settings) -> tuple[int, list[str] | None]:
    """Validate and cap the requested result limit."""
    if limit <= 0:
        raise ValueError("limit must be greater than 0.")
    if limit <= settings.max_row_limit:
        return limit, None
    return settings.max_row_limit, [f"Limit capped at {settings.max_row_limit}"]


def _validate_table_filter(table: str) -> str | None:
    """Validate an optional Code Search table filter."""
    if not table:
        return None
    validate_identifier(table)
    check_table_access(table)
    return table


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``code_search`` tool on the MCP server."""
    del choices, dictionary  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def code_search(
        action: str = "search",
        term: str = "",
        table: str = "",
        search_group: str = "",
        limit: int = 20,
        *,
        correlation_id: str = "",
    ) -> str:
        """Search ServiceNow code or inspect Code Search table coverage.

        Args:
            action: One of 'search', 'list_tables', or 'describe'.
            term: Search term for action='search'.
            table: Optional table filter for action='search' (e.g. 'sys_script_include').
            search_group: Optional ServiceNow Code Search group.
            limit: Max search results for action='search'. Default 20.
        """
        normalized_action = action.strip().lower()
        if normalized_action not in _VALID_ACTIONS:
            return _error(correlation_id, f"Unknown action {action!r}. Available: {sorted(_VALID_ACTIONS)}")

        if normalized_action == "describe":
            return format_response(data={"actions": _ACTION_REGISTRY}, correlation_id=correlation_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            if normalized_action == "list_tables":
                result = await client.code_search_tables(search_group=search_group or None)
                return format_response(data=result, correlation_id=correlation_id)

            stripped_term = term.strip()
            if not stripped_term:
                return _error(correlation_id, "'term' is required for action='search'.")

            table_filter = _validate_table_filter(table)
            effective_limit, warnings = _effective_limit(limit, settings)
            result = await client.code_search(
                stripped_term,
                table=table_filter,
                search_group=search_group or None,
                limit=effective_limit,
            )
            return format_response(data=result, correlation_id=correlation_id, warnings=warnings)
