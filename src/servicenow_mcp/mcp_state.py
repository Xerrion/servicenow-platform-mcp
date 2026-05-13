"""Typed helpers for ServiceNow-specific FastMCP state."""

from typing import Protocol, cast

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.tools._dictionary import DictionaryRegistry


class _ServiceNowStateCarrier(Protocol):
    """FastMCP instance with ServiceNow-specific state attached."""

    _sn_settings: Settings
    _sn_auth: BasicAuthProvider
    _sn_choices: ChoiceRegistry
    _sn_dictionary: DictionaryRegistry


def attach_servicenow_state(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry,
    dictionary: DictionaryRegistry,
) -> None:
    """Attach the typed ServiceNow runtime state to an MCP instance."""
    typed_mcp = cast("_ServiceNowStateCarrier", cast("object", mcp))
    typed_mcp._sn_settings = settings
    typed_mcp._sn_auth = auth_provider
    typed_mcp._sn_choices = choices
    typed_mcp._sn_dictionary = dictionary
