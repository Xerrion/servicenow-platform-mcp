"""Owned record review structures."""

from pydantic import JsonValue

from servicenow_mcp._contracts import ContractModel


class DiffEntry(ContractModel):
    """Stored and proposed values for one field, including masked values."""

    old: JsonValue
    new: JsonValue
