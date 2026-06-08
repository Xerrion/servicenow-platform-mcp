"""Unified ``code_search`` tool: search script-bearing tables for code.

Three actions dispatched on ``action``:

* ``search``  - search for ``term`` across script-bearing tables.
* ``tables``  - return the list of searchable tables.
* ``describe`` - return tool action metadata without making API calls.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import format_response


TOOL_NAMES: list[str] = ["code_search"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"search", "tables", "describe"})

_MAX_SEARCH_LIMIT: Final[int] = 500
_MIN_SEARCH_LIMIT: Final[int] = 1

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "search": {
        "description": "Search for code across script-bearing ServiceNow tables.",
        "params": {
            "term": "str (required) — search term",
            "table": "str (optional) — limit search to a specific table",
            "search_group": "str (optional) — name of a search group scope",
            "limit": f"int (default 50, 1..{_MAX_SEARCH_LIMIT})",
        },
    },
    "tables": {
        "description": "Return the list of searchable tables for the Code Search API.",
        "params": {},
    },
    "describe": {
        "description": "Return this action registry without making any platform calls.",
        "params": {},
    },
}


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _clamp_limit(limit: int) -> int:
    """Clamp *limit* to ``[_MIN_SEARCH_LIMIT, _MAX_SEARCH_LIMIT]``."""
    return max(_MIN_SEARCH_LIMIT, min(limit, _MAX_SEARCH_LIMIT))


async def _action_search(
    *,
    term: str,
    table: str,
    search_group: str,
    limit: int,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    """Execute a code search via the ServiceNow Code Search API."""
    if not term:
        return _error(correlation_id, "term is required for action='search'.")

    effective_limit = _clamp_limit(limit)
    table_param: str | None = table if table else None
    search_group_param: str | None = search_group if search_group else None

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.code_search(
            term=term,
            table=table_param,
            search_group=search_group_param,
            limit=effective_limit,
        )

    return format_response(data=result, correlation_id=correlation_id)


async def _action_tables(
    *,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    """Fetch the list of searchable tables via the Code Search API."""
    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.code_search_tables()

    return format_response(data=result, correlation_id=correlation_id)


def _action_describe(correlation_id: str) -> str:
    """Return the action registry without making any platform calls."""
    return format_response(
        data={"actions": _ACTION_REGISTRY},
        correlation_id=correlation_id,
    )


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``code_search`` tool on the MCP server.

    ``choices`` and ``dictionary`` are accepted only to satisfy the
    uniform loader contract in ``server.py``; this tool needs neither.
    """
    del choices, dictionary  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def code_search(
        action: str = "search",
        term: str = "",
        table: str = "",
        search_group: str = "",
        limit: int = 50,
        *,
        correlation_id: str = "",
    ) -> str:
        """Search script-bearing ServiceNow tables for code matches.

        Three modes: ``search`` (default) queries the Code Search API,
        ``tables`` lists the searchable tables, and ``describe`` returns
        metadata about this tool without making any platform calls.

        Args:
            action: 'search' | 'tables' | 'describe'. Defaults to 'search'.
            term: Search term (required for action='search').
            table: Table name to restrict the search (optional).
            search_group: Name of a search group scope (optional).
            limit: Maximum results for action='search' (1..500, default 50).
        """
        if action not in _VALID_ACTIONS:
            return _error(
                correlation_id,
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "describe":
            return _action_describe(correlation_id)

        if action == "tables":
            return await _action_tables(
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        return await _action_search(
            term=term,
            table=table,
            search_group=search_group,
            limit=limit,
            settings=settings,
            auth_provider=auth_provider,
            correlation_id=correlation_id,
        )
