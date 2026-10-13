"""Unified ``resolve_choice`` tool: expose ChoiceRegistry as a first-class tool.

Two modes, dispatched on whether ``label`` is set:

1. ``label`` non-empty -> resolve a single label to its underlying value.
2. ``label`` empty     -> return the full ``{label: value}`` map for the field.
"""

from __future__ import annotations

from typing import NoReturn

from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.platform.metadata.choices import ChoiceRegistry
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_identifier


def _error(message: str) -> NoReturn:
    """Reject an invalid operation."""
    raise CapabilityError(message)


class ResolveChoiceCapability:
    def __init__(
        self,
        choices: ChoiceRegistry | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""

        self.choices = choices

    async def resolve_choice(
        self,
        table: str,
        field: str,
        label: str | None = None,
    ) -> CapabilityResult:
        """Execute the resolve_choice capability with native typed inputs."""
        label = label or ""
        validate_identifier(table)
        validate_identifier(field)
        check_table_access(table)

        if self.choices is None:
            return _error("ChoiceRegistry not configured.")

        if not label:
            mapping = await self.choices.get_choices(table, field)
            return operation_result(
                data={"table": table, "field": field, "choices": mapping},
            )

        value = await self.choices.resolve(table, field, label)
        warnings: list[str] | None = None
        if value == label and not label.isdigit():
            warnings = [
                (
                    f"resolve_choice: '{field}={label}' did not resolve via ChoiceRegistry; "
                    f"returning the label verbatim as the value."
                ),
            ]
        return operation_result(
            data={"table": table, "field": field, "label": label, "value": value},
            warnings=warnings,
        )
