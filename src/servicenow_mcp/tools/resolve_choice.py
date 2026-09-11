"""Unified ``resolve_choice`` tool: expose ChoiceRegistry as a first-class tool.

Two modes, dispatched on whether ``label`` is set:

1. ``label`` non-empty -> resolve a single label to its underlying value.
2. ``label`` empty     -> return the full ``{label: value}`` map for the field.
"""

from __future__ import annotations

from mcp.server import MCPServer

from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.response import format_response
from servicenow_mcp.validation import validate_identifier


TOOL_NAMES: list[str] = ["resolve_choice"]


def _error(message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, status="error", error=message)


def register_tools(
    mcp: MCPServer,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register ``resolve_choice``; calls return an error if no registry is supplied."""

    @mcp.tool()
    @tool_handler
    async def resolve_choice(
        table: str,
        field: str,
        label: str = "",
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
            return _error("ChoiceRegistry not configured.")

        if not label:
            mapping = await choices.get_choices(table, field)
            return format_response(
                data={"table": table, "field": field, "choices": mapping},
            )

        value = await choices.resolve(table, field, label)
        warnings: list[str] | None = None
        if value == label and not label.isdigit():
            warnings = [
                (
                    f"resolve_choice: '{field}={label}' did not resolve via ChoiceRegistry; "
                    f"returning the label verbatim as the value."
                ),
            ]
        return format_response(
            data={"table": table, "field": field, "label": label, "value": value},
            warnings=warnings,
        )
