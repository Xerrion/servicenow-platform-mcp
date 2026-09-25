"""Owned response structures for audit verdicts and evidence."""

from __future__ import annotations

from typing import Any, ClassVar

from servicenow_mcp._contracts import ContractModel


class FieldAttributes(ContractModel):
    no_audit: bool
    raw: str


class FieldActivity(ContractModel):
    window_days: int
    since: str
    field_change_count: int
    table_change_count: int
    positive_control_passed: bool


class BatchWindow(ContractModel):
    window_days: int
    since: str


class FieldVerdict(ContractModel):
    """check_field payload."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"reason"})

    table: str
    field: str
    super_class_chain: list[str]
    verdict: str
    table_audit: bool | None
    field_audit: bool | None
    raw_field_audit: bool | None
    inherited_from: str | None
    field_attributes: FieldAttributes
    explanation: str
    window_note: str
    recent_activity: FieldActivity
    reason: str | None = None


class BatchFieldVerdict(ContractModel):
    """One check_fields result entry."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"reason"})

    field: str
    verdict: str
    field_audit: bool | None
    raw_field_audit: bool | None
    inherited_from: str | None
    field_attributes: FieldAttributes
    field_change_count: int
    explanation: str
    reason: str | None = None


class BatchVerdicts(ContractModel):
    table: str
    super_class_chain: list[str]
    table_audit: bool | None
    table_change_count: int
    positive_control_passed: bool
    window_note: str
    recent_activity: BatchWindow
    results: list[BatchFieldVerdict]


class FieldOverride(ContractModel):
    field: str
    field_audit: bool | None
    raw_field_audit: bool | None
    inherited_from: str | None
    reason: str
    field_attributes: FieldAttributes


class TableAudit(ContractModel):
    table: str
    super_class_chain: list[str]
    table_audit: bool | None
    field_overrides: list[FieldOverride]


class HistoryWindow(ContractModel):
    since: str
    window_days: int
    explicit_since: bool


class AuditHistory(ContractModel):
    table: str
    sys_id: str
    window: HistoryWindow
    window_note: str
    entry_count: int
    entries: list[dict[str, Any]]
