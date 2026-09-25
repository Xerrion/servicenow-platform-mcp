"""Owned parameter and finding structures for the seven investigations."""

from __future__ import annotations

from typing import Any, ClassVar

from servicenow_mcp._contracts import ContractModel


class ParamSpec(ContractModel):
    """One ``PARAMS`` entry shown by ``describe``."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"required"})

    type: str
    required: bool | None = None
    default: Any = None
    description: str


def param_specs(**specs: ParamSpec) -> dict[str, dict[str, Any]]:
    """Dump param specs to the legacy ``PARAMS`` dict shape."""
    return {name: spec.to_payload() for name, spec in specs.items()}


class RecordFinding(ContractModel):
    """Finding for one record: stale_automations, performance_bottlenecks."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset({"br_count", "run_type"})

    category: str
    element_id: str
    name: Any
    detail: str
    br_count: int | None = None
    run_type: Any = None


class DeprecatedApiFinding(ContractModel):
    pattern: str
    element_id: str
    name: Any
    table: Any
    detail: str


class AclRef(ContractModel):
    sys_id: Any
    operation: Any
    condition: Any
    active: Any


class AclConflictFinding(ContractModel):
    category: str = "acl_conflict"
    name: str
    operation: str
    count: int
    acls: list[AclRef]
    detail: str


class ErrorClusterFinding(ContractModel):
    category: str = "error_cluster"
    source: Any
    frequency: int
    first_seen: Any
    last_seen: Any
    sample_messages: list[Any]
    element_id: str


class SlowPatternFinding(ContractModel):
    category: str
    table: str
    element_id: str
    name: Any
    count: Any
    detail: str
    sys_created_on: Any


class HealthIndicatorFinding(ContractModel):
    category: str = "health_indicator"
    detail: str
