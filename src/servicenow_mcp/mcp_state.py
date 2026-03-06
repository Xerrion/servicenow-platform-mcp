"""Typed helpers for ServiceNow-specific FastMCP state."""

from typing import Protocol, cast

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.state import QueryTokenStore


class _ServiceNowStateCarrier(Protocol):
    """FastMCP instance with ServiceNow-specific state attached."""

    _sn_settings: Settings
    _sn_auth: BasicAuthProvider
    _sn_query_store: QueryTokenStore
    _sn_choices: ChoiceRegistry


def attach_servicenow_state(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    query_store: QueryTokenStore,
    choices: ChoiceRegistry,
) -> None:
    """Attach the typed ServiceNow runtime state to an MCP instance."""
    typed_mcp = cast("_ServiceNowStateCarrier", cast("object", mcp))
    typed_mcp._sn_settings = settings
    typed_mcp._sn_auth = auth_provider
    typed_mcp._sn_query_store = query_store
    typed_mcp._sn_choices = choices


def attach_query_store(mcp: FastMCP, query_store: QueryTokenStore) -> None:
    """Attach a query store to an MCP instance used in tests."""
    typed_mcp = cast("_ServiceNowStateCarrier", cast("object", mcp))
    typed_mcp._sn_query_store = query_store


def get_query_store(mcp: FastMCP) -> QueryTokenStore:
    """Return the typed query token store attached to an MCP instance."""
    typed_mcp = cast("_ServiceNowStateCarrier", cast("object", mcp))
    store = getattr(typed_mcp, "_sn_query_store", None)
    if store is None:
        raise RuntimeError(
            "QueryTokenStore not found on FastMCP instance. "
            "Call attach_servicenow_state() before accessing the query store."
        )
    return store
