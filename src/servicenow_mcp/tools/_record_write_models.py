"""Owned response structures for record write previews, results and diffs."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from servicenow_mcp._contracts import ContractModel


class DiffEntry(ContractModel):
    """One field of an update preview diff. Values stay as stored or masked."""

    old: Any
    new: Any


class WritePreview(ContractModel):
    """Preview body. Exactly one of the three keys is present per action."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"data", "diff", "record_snapshot"})

    data: dict[str, Any] | None = None
    diff: dict[str, DiffEntry] | None = None
    record_snapshot: dict[str, Any] | None = None


class WritePreviewResult(ContractModel):
    """Response data for a staged write."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"sys_id"})

    action: Literal["create", "update", "delete"]
    table: str
    sys_id: str | None = None
    preview_token: str
    preview: WritePreview


class WriteResult(ContractModel):
    """Response data for an executed write."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"record", "deleted"})

    action: Literal["create", "update", "delete"]
    table: str
    sys_id: Any
    record: dict[str, Any] | None = None
    deleted: bool | None = None
