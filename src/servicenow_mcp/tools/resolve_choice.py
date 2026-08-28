"""Unified ``resolve_choice`` tool: expose ChoiceRegistry as a first-class tool.

Two modes, dispatched on whether ``label`` is set:

1. ``label`` non-empty -> resolve a single label to its underlying value.
2. ``label`` empty     -> return the full ``{label: value}`` map for the field.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = ["resolve_choice"]


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``resolve_choice`` tool on the MCP server.

    The registry is the only collaborator this tool needs; ``settings`` and
    ``auth_provider`` are accepted to match the loader contract used by
    ``server.py`` for every unified tool.
    """
    del settings, auth_provider, dictionary, client_factory  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def resolve_choice(
        table: str,
        field: str,
        label: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Resolve a choice label to its underlying value via ChoiceRegistry.

        Args:
            table: ServiceNow table name.
            field: Field name on that table.
            label: Choice label to resolve. When empty, returns the full {label: value}
                mapping for the field.
        """
        validate_identifier(table)
        validate_identifier(field)
        check_table_access(table)

        if choices is None:
            return _error(correlation_id, "ChoiceRegistry not configured.")

        if not label:
            mapping = await choices.get_choices(table, field)
            return format_response(
                data={"table": table, "field": field, "choices": mapping},
                correlation_id=correlation_id,
            )

        value = await choices.resolve(table, field, label)
        warnings: list[str] | None = None
        if value == label and not label.isdigit():
            warnings = [
                f"resolve_choice: '{field}={label}' did not resolve via ChoiceRegistry; "
                f"returning the label verbatim as the value.",
            ]
        return format_response(
            data={"table": table, "field": field, "label": label, "value": value},
            correlation_id=correlation_id,
            warnings=warnings,
        )
