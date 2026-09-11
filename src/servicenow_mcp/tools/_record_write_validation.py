"""Action and input validation for unified record writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from servicenow_mcp.config import Settings
from servicenow_mcp.policy import gate_write
from servicenow_mcp.response import format_response
from servicenow_mcp.validation import validate_identifier, validate_sys_id


WriteAction = Literal["create", "update", "delete"]
_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "delete"})


@dataclass(frozen=True)
class WriteRequest:
    """Validated record-write arguments."""

    action: WriteAction
    table: str
    sys_id: str
    data: str
    preview: bool


def _error(message: str) -> str:
    return format_response(data=None, status="error", error=message)


def _validate_create_args(sys_id: str, data: str) -> str | None:
    if not data:
        return _error("data is required for action='create'.")
    if sys_id:
        return _error("sys_id must be empty for action='create'.")
    return None


def _validate_update_args(sys_id: str, data: str) -> str | None:
    if not sys_id:
        return _error("sys_id is required for action='update'.")
    if not data:
        return _error("data is required for action='update'.")
    return None


def _validate_delete_args(sys_id: str, data: str) -> str | None:
    if not sys_id:
        return _error("sys_id is required for action='delete'.")
    if data:
        return _error("data must be empty for action='delete'.")
    return None


def validate_write_request(
    action: str,
    table: str,
    sys_id: str,
    data: str,
    preview: bool,
    settings: Settings,
) -> WriteRequest | str:
    """Return trusted write arguments or the existing MCP error envelope."""
    if action not in _VALID_ACTIONS:
        return _error(f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.")
    if not table:
        return _error("table is required.")

    if action == "create":
        validated_action: WriteAction = "create"
        error = _validate_create_args(sys_id, data)
    elif action == "update":
        validated_action = "update"
        error = _validate_update_args(sys_id, data)
    else:
        validated_action = "delete"
        error = _validate_delete_args(sys_id, data)
    if error:
        return error

    validate_identifier(table)
    blocked = gate_write(table, settings)
    if blocked:
        return blocked
    if sys_id:
        validate_sys_id(sys_id)

    return WriteRequest(action=validated_action, table=table, sys_id=sys_id, data=data, preview=preview)
