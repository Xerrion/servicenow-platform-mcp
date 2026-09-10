"""Unified ``code_search`` tool: search ServiceNow script-bearing artifacts.

Three actions:

* ``search``      - search code across the configured ServiceNow Code Search tables.
* ``list_tables`` - list tables included by the Code Search API for a search group.
* ``describe``    - return the action registry without platform I/O.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
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
            "extended_matching": "bool (default false; include additional context fields)",
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


def _error(message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, status="error", error=message)


def _effective_limit(limit: int, settings: Settings) -> int:
    """Validate and cap the requested result limit."""
    if limit <= 0:
        raise ValueError("limit must be greater than 0.")
    return min(limit, settings.max_row_limit)


def _validate_table_filter(table: str) -> str | None:
    """Validate an optional Code Search table filter."""
    if not table:
        return None
    validate_identifier(table)
    check_table_access(table)
    return table


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``code_search`` tool on the MCP server."""
    del choices, dictionary  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def code_search(
        action: str = "search",
        term: str = "",
        table: str = "",
        search_group: str = "",
        limit: int = 20,
        *,
        extended_matching: bool = False,
    ) -> str:
        """Search ServiceNow code or inspect Code Search table coverage.

        Args:
            action: One of 'search', 'list_tables', or 'describe'.
            term: Search term for action='search'.
            table: Optional table filter for action='search' (e.g. 'sys_script_include').
            search_group: ServiceNow Code Search group; empty uses sn_codesearch.Default Search Group.
            limit: Max search results for action='search'. Default 20.
            extended_matching: Include additional Code Search context fields. Default false.
                Set true when the extra context is needed.
        """
        normalized_action = action.strip().lower()
        if normalized_action not in _VALID_ACTIONS:
            return _error(
                f"Unknown action {action!r}. Available: {sorted(_VALID_ACTIONS)}",
            )

        if normalized_action == "describe":
            return format_response(data={"actions": _ACTION_REGISTRY})

        async with client_factory() as client:
            if normalized_action == "list_tables":
                result = await client.code_search_tables(search_group=search_group or None)
                return format_response(data=result)

            stripped_term = term.strip()
            if not stripped_term:
                return _error("'term' is required for action='search'.")

            table_filter = _validate_table_filter(table)
            effective_limit = _effective_limit(limit, settings)
            result = await client.code_search(
                stripped_term,
                table=table_filter,
                search_group=search_group or None,
                limit=effective_limit,
                extended_matching=extended_matching,
            )
            return format_response(data=result, pagination={"limit": effective_limit})
