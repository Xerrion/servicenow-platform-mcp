"""Preview-token lifecycle and apply-time policy checks for record writes."""

from __future__ import annotations

from typing import Any

from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, write_gate
from servicenow_mcp.response import format_response
from servicenow_mcp.state import PreviewTokenStore


class RecordWritePreviewManager:
    """Create and atomically consume record-write preview tokens."""

    _store: PreviewTokenStore

    def __init__(self, store: PreviewTokenStore) -> None:
        self._store = store

    async def create(self, payload: dict[str, Any]) -> str:
        """Store a preview payload and return its single-use token."""
        return await self._store.create(payload)

    async def consume_for_apply(self, preview_token: str, settings: Settings) -> dict[str, Any] | str:
        """Consume one token and re-check policy before mutation execution."""
        payload = await self._store.consume(preview_token)
        if payload is None:
            return format_response(data=None, status="error", error="Invalid or expired preview token")

        table = payload["table"]
        check_table_access(table)
        blocked = write_gate(table, settings)
        if blocked:
            return blocked
        return payload
