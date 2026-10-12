"""Unified ``code_search`` tool: search ServiceNow script-bearing artifacts.

Three actions:

* ``search``      - search code across the configured ServiceNow Code Search tables.
* ``list_tables`` - list tables included by the Code Search API for a search group.
* ``describe``    - return the action registry without platform I/O.
"""

from __future__ import annotations

from typing import Any, Final, NoReturn

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.config import Settings
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_identifier


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


def _error(message: str) -> NoReturn:
    """Reject an invalid operation."""
    raise CapabilityError(message)


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


class CodeSearchCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.settings = settings

    async def code_search(
        self,
        action: str | None = "search",
        term: str | None = None,
        table: str | None = None,
        search_group: str | None = None,
        limit: int | None = 20,
        *,
        extended_matching: bool | None = False,
    ) -> CapabilityResult:
        """Execute the code_search capability with native typed inputs."""
        action = "search" if action is None else action
        term = "" if term is None else term
        table = "" if table is None else table
        search_group = "" if search_group is None else search_group
        limit = 20 if limit is None else limit
        extended_matching = False if extended_matching is None else extended_matching

        normalized_action = action.strip().lower()
        if normalized_action not in _VALID_ACTIONS:
            return _error(
                f"Unknown action {action!r}. Available: {sorted(_VALID_ACTIONS)}",
            )

        if normalized_action == "describe":
            return operation_result(data={"actions": _ACTION_REGISTRY})

        async with self.client_factory() as client:
            if normalized_action == "list_tables":
                result = await client.code_search_tables(search_group=search_group or None)
                return operation_result(data=result)

            stripped_term = term.strip()
            if not stripped_term:
                return _error("'term' is required for action='search'.")

            table_filter = _validate_table_filter(table)
            effective_limit = _effective_limit(limit, self.settings)
            result = await client.code_search(
                stripped_term,
                table=table_filter,
                search_group=search_group or None,
                limit=effective_limit,
                extended_matching=extended_matching,
            )
            return operation_result(data=result, pagination={"limit": effective_limit})
