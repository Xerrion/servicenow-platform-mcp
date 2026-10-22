"""Owned response structures for Flow stages, nodes, triggers and bindings."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from servicenow_mcp._contracts import ContractModel


class StageDefinition(ContractModel):
    stage_id: str
    label: str
    value: str
    states: str
    type: str
    order: str
    component_indexes: str
    ancestor_component_id: str
    ancestor_stage_id: str
    ancestral_if_else_logic: str
    always_show: bool


class ActionTypeRef(ContractModel):
    omit_when_none: ClassVar[frozenset[str]] = frozenset(
        {"internal_name", "sys_scope", "category"}
    )

    sys_id: str
    name: str
    internal_name: str | None = None
    sys_scope: str | None = None
    category: str | None = None


class LogicDefinitionRef(ContractModel):
    sys_id: str
    name: str


class FlowNode(ContractModel):
    """V2 canvas node. ``values_decoded`` stays dynamic ServiceNow data."""

    omit_when_none: ClassVar[frozenset[str]] = frozenset(
        {
            "action_type",
            "logic_definition",
            "decode_error",
            "subflow",
            "wait_for_completion",
        },
    )

    kind: str
    version: str
    sys_id: str
    ui_uuid: str
    parent_ui_id: str
    order: str
    label: str
    name: str
    comment: str
    values_decoded: Any
    children: list[dict[str, Any]]
    action_type: ActionTypeRef | None = None
    logic_definition: LogicDefinitionRef | None = None
    subflow: LogicDefinitionRef | None = None
    wait_for_completion: bool | None = None
    decode_error: str | None = None


class V2Trigger(ContractModel):
    omit_when_none: ClassVar[frozenset[str]] = frozenset({"decode_error", "definition"})

    version: str = "v2"
    sys_id: str
    type: str
    active: bool | None
    table: str
    remote_trigger_id: str
    condition: str
    values_decoded: Any
    decode_error: str | None = None
    outputs_decoded: Any = None
    active_source: str | None = None
    metadata_complete: bool | None = None
    warnings: list[str] = Field(default_factory=list)
    definition: dict[str, Any] | None = None


class V1Trigger(ContractModel):
    version: str = "v1"
    sys_id: str
    type: str
    active: bool
    table: str
    condition: str


class ContractBinding(ContractModel):
    omit_when_none: ClassVar[frozenset[str]] = frozenset({"data_pills"})

    name: str
    label: str
    type: str
    required: bool
    value: Any
    data_pills: list[str] | None = None


class ContractTrigger(ContractModel):
    omit_when_none: ClassVar[frozenset[str]] = frozenset(
        {"configuration", "decode_error", "output_configuration"}
    )

    version: str
    type: str
    active: bool | None
    table: str
    condition: str
    configuration: list[dict[str, Any]] | None = None
    decode_error: str | None = None
    output_configuration: list[dict[str, Any]] | None = None
