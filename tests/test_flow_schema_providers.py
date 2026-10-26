"""Synthetic provider boundaries; no fixture represents a captured instance response."""

import copy
from asyncio import CancelledError
from typing import TYPE_CHECKING, Any, cast

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, data, dynamic_schemas
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient

if TYPE_CHECKING:
    from collections.abc import Callable

OWNER = "1" * 32
OWNER_PARENT = "2" * 32
PROVIDER = "3" * 32
PROVIDER_PARENT = "4" * 32
RECORD = "d" * 32
TABLE = "6" * 32
SOURCE_ALIAS = "b" * 32
CHILD_ALIAS = "c" * 32
OTHER_SOURCE_ALIAS = "e" * 32
OTHER_CHILD_ALIAS = "f" * 32


def revision(identity: str, **fields: Any) -> dict[str, Any]:
    return {
        "sys_id": identity,
        "sys_mod_count": "1",
        "sys_updated_on": "2026-10-25 12:00:00",
        **fields,
    }


class ProviderClient:
    def __init__(self, mode: str = "dynamic_inputs") -> None:
        provider_inputs = [
            {"name": "requested_for", "type": "reference", "reference": "sys_user"},
            {"name": "raw_record", "type": "string"},
            {"name": "prefix", "type": "string"},
        ]
        descriptor = {
            "actionId": PROVIDER_PARENT,
            "actionScope": "global",
            "actionInternalName": "schema_provider",
            "inputs": [
                {**provider_inputs[0], "value": "{{action.requester}}"},
                {**provider_inputs[1], "value": RECORD},
                {**provider_inputs[2], "value": "literal"},
            ],
        }
        self.definitions: dict[str, dict[str, Any]] = {
            OWNER: {
                "id": OWNER,
                "name": "Owner",
                "internal_name": "owner",
                "scope": "global",
                "parent_action": OWNER_PARENT,
                "state": "published",
                "is_snapshot": True,
                "entitled": True,
                "security": {"can_read": True},
                "inputs": [
                    {"name": "requester", "type": "reference", "reference": "sys_user"},
                    {"name": "fields", "type": mode, "dynamic": descriptor},
                ],
                "outputs": [],
            },
            PROVIDER: {
                "id": PROVIDER,
                "name": "Provider",
                "internal_name": "schema_provider",
                "scope": "global",
                "parent_action": PROVIDER_PARENT,
                "state": "published",
                "is_snapshot": True,
                "entitled": True,
                "security": {"can_read": True},
                "inputs": provider_inputs,
                "outputs": [{"name": "data", "type": "json"}],
            },
        }
        self.records = {
            (dynamic_schemas.SNAPSHOT_TABLE, OWNER): revision(
                OWNER,
                parent_action=OWNER_PARENT,
                master=False,
                internal_name="owner",
                sys_scope="global",
            ),
            (dynamic_schemas.PARENT_TABLE, OWNER_PARENT): revision(
                OWNER_PARENT,
                master_snapshot="7" * 32,
                latest_snapshot="8" * 32,
                state="draft",
                active=True,
                internal_name="owner",
                sys_scope="global",
            ),
            (dynamic_schemas.SNAPSHOT_TABLE, PROVIDER): revision(
                PROVIDER,
                parent_action=PROVIDER_PARENT,
                master=True,
                internal_name="schema_provider",
                sys_scope="global",
            ),
            (dynamic_schemas.PARENT_TABLE, PROVIDER_PARENT): revision(
                PROVIDER_PARENT,
                master_snapshot=PROVIDER,
                latest_snapshot="9" * 32,
                state="draft",
                active=True,
                internal_name="schema_provider",
                sys_scope="global",
            ),
            ("sys_user", RECORD): revision(RECORD),
        }
        self.tables = {
            "sys_user": revision(TABLE, name="sys_user", label="User"),
            "incident": revision("a" * 32, name="incident", label="Incident"),
        }
        self.descriptors = {
            "sys_user": {"displayField": "name"},
            "incident": {"displayField": "number"},
        }
        self.output: dict[str, Any] = {
            "data": [{"name": "summary", "label": "Summary", "type": "string"}]
        }
        self.reads: list[tuple[str, str]] = []
        self.executions: list[tuple[str, dict[str, Any]]] = []
        self.execute_error: BaseException | None = None
        self.after_execute: Callable[[], None] | None = None

    @property
    def client(self) -> ServiceNowClient:
        return cast(ServiceNowClient, cast(object, self))

    @property
    def descriptor(self) -> dict[str, Any]:
        return self.definitions[OWNER]["inputs"][1]["dynamic"]

    async def get_record(
        self, table: str, identity: str, *, fields: list[str]
    ) -> dict[str, Any]:
        self.reads.append((table, identity))
        row = self.records[(table, identity)]
        return copy.deepcopy(
            {key: value for key, value in row.items() if key in fields}
        )

    async def query_records(
        self, table: str, query: str, *, fields: list[str], limit: int
    ) -> dict[str, Any]:
        assert limit == 2
        self.reads.append((table, query))
        if table == dynamic_schemas.SNAPSHOT_TABLE:
            identity = query.removeprefix("sys_id=")
            row = self.records.get((table, identity))
        else:
            assert table == "sys_db_object"
            row = self.tables.get(query.removeprefix("name="))
        return {"records": [copy.deepcopy(row)] if row else []}

    async def studio_request(self, resource: str) -> dict[str, Any]:
        self.reads.append(("studio", resource))
        return copy.deepcopy(self.definitions[resource.rsplit("/", 1)[1]])

    async def studio_table_descriptor(self, table: str) -> dict[str, Any]:
        self.reads.append(("descriptor", table))
        return copy.deepcopy(self.descriptors[table])

    async def studio_schema_provider(
        self, name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        self.executions.append((name, copy.deepcopy(inputs)))
        if self.execute_error:
            raise self.execute_error
        if self.after_execute:
            self.after_execute()
        return copy.deepcopy(self.output)


def command(**changes: Any) -> FlowCommand:
    return FlowCommand(
        action="resolve_schema",
        action_type_sys_id=OWNER,
        input_name="fields",
        inputs={"requester": RECORD},
        **changes,
    )


async def resolved(fake: ProviderClient) -> dict[str, Any]:
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    return await dynamic_schemas.apply(plan, fake.client)


async def test_preview_uses_only_reads_and_native_reference_transforms() -> None:
    fake = ProviderClient()
    original = copy.deepcopy(fake.definitions)
    plan, summary = await dynamic_schemas.preview(command(), fake.client)
    assert fake.executions == []
    assert fake.definitions == original
    assert plan["dependencies"] == {
        "requested_for": {"table": "sys_user", "sys_id": RECORD},
        "raw_record": '{"value":"' + RECORD + '"}',
        "prefix": "literal",
    }
    assert plan["sibling_inputs"] == {"requester": RECORD}
    assert plan["record_bindings"][0]["record"]["sys_id"] == RECORD
    assert plan["reference_bindings"]["sys_user"]["record"]["sys_id"] == TABLE
    assert plan["provider"]["binding"]["snapshot"]["sys_id"] == PROVIDER
    assert summary["provider_executed"] is False
    assert summary["exact_execution_version_verified"] is False
    assert "not atomically verified" in summary["execution_version_limitation"]


async def test_native_first_match_uses_owner_order_and_binds_static_defaults() -> None:
    fake = ProviderClient()
    fake.definitions[OWNER]["inputs"].insert(
        0,
        {
            "name": "text_value",
            "type": "string",
            "defaultValue": RECORD,
        },
    )
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    assert plan["inputs"] == {"text_value": RECORD, "requester": RECORD}
    assert plan["requested_inputs"] == {"requester": RECORD}
    assert plan["dependencies"]["raw_record"] == RECORD
    evidence = await dynamic_schemas.apply(plan, fake.client)
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


async def test_readonly_defaults_are_bound_without_caller_write_permission() -> None:
    fake = ProviderClient()
    fake.definitions[OWNER]["inputs"].append(
        {
            "name": "default_user",
            "type": "reference",
            "reference": "sys_user",
            "readonly": True,
            "defaultValue": RECORD,
        }
    )
    evidence = await resolved(fake)
    assert evidence["plan"]["inputs"]["default_user"] == RECORD
    await dynamic_schemas.verify(evidence, fake.client)
    explicit = command().model_copy(
        update={"inputs": {"requester": RECORD, "default_user": RECORD}}
    )
    with pytest.raises(CapabilityError, match="read-only"):
        await dynamic_schemas.preview(explicit, fake.client)
    assert len(fake.executions) == 1


async def test_numeric_reference_normalization_cannot_silently_lose_identity() -> None:
    fake = ProviderClient()
    numeric_id = "1" * 32
    fake.records[("sys_user", numeric_id)] = revision(numeric_id)
    fake.descriptor["inputs"][1]["value"] = numeric_id
    request = command().model_copy(update={"inputs": {"requester": numeric_id}})
    with pytest.raises(CapabilityError, match="lose a numeric reference identity"):
        await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []


@pytest.mark.parametrize("mode", sorted(dynamic_schemas.MODES))
async def test_apply_executes_once_and_verify_never_executes(mode: str) -> None:
    fake = ProviderClient(mode)
    evidence = await resolved(fake)
    assert len(fake.executions) == 1
    assert fake.executions[0] == ("global.schema_provider", evidence["dependencies"])
    assert evidence["action_type_sys_id"] == OWNER
    assert evidence["mode"] == mode
    assert evidence["provider"]["descriptor"] == fake.descriptor
    assert evidence["schema"] == fake.output["data"]
    assert evidence["observed_binding_verified"] is True
    assert evidence["exact_execution_version_verified"] is False
    reads_before = len(fake.reads)
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.reads) > reads_before
    assert len(fake.executions) == 1


async def test_old_owner_snapshot_and_draft_provider_parent_keep_published_identity() -> (
    None
):
    fake = ProviderClient()
    evidence = await resolved(fake)
    owner = evidence["plan"]["owner"]
    assert owner["snapshot"]["master"] is False
    assert owner["parent"]["master_snapshot"] != OWNER
    assert evidence["provider"]["binding"]["parent"]["state"] == "draft"
    assert len(fake.executions) == 1


async def test_snapshot_parent_binding_does_not_require_optional_studio_facade_field() -> (
    None
):
    fake = ProviderClient()
    fake.definitions[OWNER].pop("parent_action")
    fake.definitions[PROVIDER].pop("parent_action")
    fake.records[(dynamic_schemas.PARENT_TABLE, OWNER_PARENT)]["internal_name"] = (
        "renamed_owner"
    )
    evidence = await resolved(fake)
    assert evidence["plan"]["owner"]["snapshot"]["internal_name"] == "owner"
    assert evidence["plan"]["owner"]["parent"]["internal_name"] == "renamed_owner"
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


async def test_descriptor_can_select_exact_current_provider_snapshot() -> None:
    fake = ProviderClient()
    fake.descriptor["actionId"] = PROVIDER
    evidence = await resolved(fake)
    assert evidence["provider"]["descriptor"]["actionId"] == PROVIDER
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "change",
    [
        "owner_revision",
        "owner_definition",
        "provider_revision",
        "provider_name",
        "provider_master",
        "provider_inactive",
        "dependency",
        "record_revision",
        "table_identity",
        "table_descriptor",
    ],
)
async def test_stale_bindings_fail_before_effect(change: str) -> None:
    fake = ProviderClient()
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    if change == "owner_revision":
        fake.records[(dynamic_schemas.SNAPSHOT_TABLE, OWNER)]["sys_mod_count"] = "2"
    elif change == "owner_definition":
        fake.definitions[OWNER]["inputs"][0]["mandatory"] = True
    elif change == "provider_revision":
        fake.records[(dynamic_schemas.PARENT_TABLE, PROVIDER_PARENT)][
            "sys_mod_count"
        ] = "2"
    elif change == "provider_name":
        fake.descriptor["actionInternalName"] = "different_provider"
    elif change == "provider_master":
        fake.records[(dynamic_schemas.PARENT_TABLE, PROVIDER_PARENT)][
            "master_snapshot"
        ] = "f" * 32
    elif change == "provider_inactive":
        fake.records[(dynamic_schemas.PARENT_TABLE, PROVIDER_PARENT)]["active"] = False
    elif change == "dependency":
        fake.descriptor["inputs"][2]["value"] = "different"
    elif change == "record_revision":
        fake.records[("sys_user", RECORD)]["sys_mod_count"] = "2"
    elif change == "table_identity":
        fake.tables["sys_user"]["sys_id"] = "b" * 32
    else:
        fake.descriptors["sys_user"]["displayField"] = "email"
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.apply(plan, fake.client)
    assert error.value.code == "STALE_PREVIEW"
    assert fake.executions == []


@pytest.mark.parametrize(
    "defect", ["type", "reference", "missing", "duplicate", "nested"]
)
async def test_descriptor_must_match_independent_provider_metadata(defect: str) -> None:
    fake = ProviderClient()
    if defect == "type":
        fake.definitions[PROVIDER]["inputs"][0]["type"] = "string"
    elif defect == "reference":
        fake.definitions[PROVIDER]["inputs"][0]["reference"] = "incident"
    elif defect == "missing":
        fake.definitions[PROVIDER]["inputs"].pop()
    elif defect == "duplicate":
        fake.descriptor["inputs"].append(copy.deepcopy(fake.descriptor["inputs"][0]))
    else:
        fake.definitions[PROVIDER]["inputs"][0]["dynamic"] = {"actionId": OWNER}
    with pytest.raises(CapabilityError):
        await dynamic_schemas.preview(command(), fake.client)
    assert fake.executions == []


@pytest.mark.parametrize(
    "value",
    ["{{action.requester}}", "{{step.pill}}", "broken}}", {"script": "{{pill}}"}],
)
async def test_request_dependencies_cannot_hide_pills(value: Any) -> None:
    fake = ProviderClient()
    request = command().model_copy(update={"inputs": {"requester": value}})
    with pytest.raises(CapabilityError):
        await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []


async def test_linked_dependency_must_be_explicit_and_static() -> None:
    fake = ProviderClient()
    request = command().model_copy(update={"inputs": {}})
    with pytest.raises(CapabilityError, match="explicit static"):
        await dynamic_schemas.preview(request, fake.client)
    fake.definitions[OWNER]["inputs"][0]["dynamic"] = {"actionId": PROVIDER_PARENT}
    with pytest.raises(CapabilityError, match="nested dynamic"):
        await dynamic_schemas.preview(command(), fake.client)
    assert fake.executions == []


async def test_reference_identity_must_agree_across_sibling_descriptor_and_provider() -> (
    None
):
    fake = ProviderClient()
    fake.definitions[OWNER]["inputs"][0]["reference"] = "incident"
    fake.records[("incident", RECORD)] = revision(RECORD)
    with pytest.raises(CapabilityError, match="reference identity"):
        await dynamic_schemas.preview(command(), fake.client)
    assert fake.executions == []


async def test_schema_reference_tables_are_independently_bound_and_rechecked() -> None:
    fake = ProviderClient()
    fake.output = {
        "data": [
            {
                "name": "ticket",
                "label": "Ticket",
                "type": "reference",
                "reference": "incident",
            }
        ]
    }
    evidence = await resolved(fake)
    binding = evidence["schema_reference_bindings"]["incident"]
    assert binding["record"]["sys_id"] == "a" * 32
    assert binding["descriptor"]["displayField"] == "number"
    fake.tables["incident"]["sys_mod_count"] = "2"
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.verify(evidence, fake.client)
    assert error.value.code == "STALE_PREVIEW"
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "failure", ["malformed_output", "changed_provider", "missing_schema_table"]
)
async def test_posteffect_failures_report_unknown_outcome_without_replay(
    failure: str,
) -> None:
    fake = ProviderClient()
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    if failure == "malformed_output":
        fake.output = {"data": "not a schema"}
    elif failure == "changed_provider":

        def change_provider() -> None:
            fake.records[(dynamic_schemas.SNAPSHOT_TABLE, PROVIDER)][
                "sys_mod_count"
            ] = "2"

        fake.after_execute = change_provider
    else:
        fake.output = {
            "data": [
                {
                    "name": "record",
                    "label": "Record",
                    "type": "reference",
                    "reference": "missing_table",
                }
            ]
        }
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.apply(plan, fake.client)
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert error.value.outcome == {
        "provider_executed": True,
        "provider_execution_attempted": True,
        "schema_retained": False,
    }
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "failure", [TimeoutError("request may have reached ServiceNow"), CancelledError()]
)
async def test_execution_transport_failure_is_uncertain_and_never_retried(
    failure: BaseException,
) -> None:
    fake = ProviderClient()
    fake.execute_error = failure
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.apply(plan, fake.client)
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert error.value.outcome is not None
    assert error.value.outcome["provider_executed"] is None
    assert error.value.outcome["schema_retained"] is False
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "defect", ["extra", "owner", "inputs", "schema", "claim", "missing_reference"]
)
async def test_evidence_parser_rejects_inconsistent_artifacts(defect: str) -> None:
    fake = ProviderClient()
    fake.output = {
        "data": [
            {
                "name": "ticket",
                "label": "Ticket",
                "type": "reference",
                "reference": "incident",
            }
        ]
    }
    evidence = await resolved(fake)
    if defect == "extra":
        evidence["unexpected"] = True
    elif defect == "owner":
        evidence["action_type_sys_id"] = "f" * 32
    elif defect == "inputs":
        evidence["sibling_inputs"]["requester"] = "f" * 32
    elif defect == "schema":
        evidence["schema"] = {"data": []}
    elif defect == "claim":
        evidence["exact_execution_version_verified"] = True
    else:
        evidence["schema_reference_bindings"] = {}
    with pytest.raises(CapabilityError):
        dynamic_schemas.validate_evidence(evidence)
    assert len(fake.executions) == 1


async def test_summary_never_exposes_private_plan_and_masks_sensitive_dependencies() -> (
    None
):
    fake = ProviderClient()
    for parameter in (
        fake.definitions[PROVIDER]["inputs"][2],
        fake.descriptor["inputs"][2],
    ):
        parameter["name"] = "api_key"
    fake.descriptor["inputs"][2]["value"] = "sensitive-literal"
    evidence = await resolved(fake)
    summary = dynamic_schemas.evidence_summary(evidence)
    assert "plan" not in summary
    assert "descriptor" not in summary["provider"]
    assert summary["dependencies"]["api_key"] == "***MASKED***"
    assert evidence["dependencies"]["api_key"] == "sensitive-literal"
    assert summary["exact_execution_version_verified"] is False


async def test_summary_masks_sensitive_field_defaults_without_mutating_evidence() -> (
    None
):
    fake = ProviderClient()
    fake.output = {
        "data": [
            {
                "name": "password",
                "label": "Password",
                "type": "password2",
                "defaultValue": "sensitive-value",
            }
        ]
    }
    evidence = await resolved(fake)
    summary = dynamic_schemas.evidence_summary(evidence)
    assert summary["schema"][0]["defaultValue"] == "***MASKED***"
    assert evidence["schema"][0]["defaultValue"] == "sensitive-value"


async def test_linked_value_is_validated_against_independent_provider_choices() -> None:
    fake = ProviderClient()
    owner_parameter = {
        "name": "requester",
        "type": "choice",
        "choices": [{"value": "outside", "label": "Outside"}],
    }
    provider_parameter = {
        "name": "requested_for",
        "type": "choice",
        "choices": [{"value": "inside", "label": "Inside"}],
    }
    fake.definitions[OWNER]["inputs"][0] = owner_parameter
    fake.definitions[PROVIDER]["inputs"][0] = provider_parameter
    fake.descriptor["inputs"][0] = {
        **provider_parameter,
        "value": "{{action.requester}}",
    }
    request = command().model_copy(update={"inputs": {"requester": "outside"}})
    with pytest.raises(CapabilityError, match="allowed choice"):
        await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []


async def test_verify_rechecks_dependency_record_identity_without_execution() -> None:
    fake = ProviderClient()
    evidence = await resolved(fake)
    fake.records[("sys_user", RECORD)]["sys_id"] = "f" * 32
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.verify(evidence, fake.client)
    assert error.value.code == "STALE_PREVIEW"
    assert len(fake.executions) == 1


def alias_context(fake: ProviderClient, *, declared: bool = True) -> None:
    if declared:
        fake.definitions[OWNER]["connection_configurations"] = [
            {
                "alias_id": SOURCE_ALIAS,
                "display_name": "Owner connection",
                "type": "CONNECTION",
                "child_aliases": [
                    {
                        "alias_id": CHILD_ALIAS,
                        "display_name": "Child connection",
                        "type": "CONNECTION",
                    }
                ],
            }
        ]
    for source, child in (
        (SOURCE_ALIAS, CHILD_ALIAS),
        (OTHER_SOURCE_ALIAS, OTHER_CHILD_ALIAS),
    ):
        fake.records[("sys_alias", source)] = revision(
            source, parent="", type="connection"
        )
        fake.records[("sys_alias", child)] = revision(
            child, parent=source, type="connection"
        )


@pytest.mark.parametrize("requested", [None, {}])
async def test_empty_connection_context_is_not_injected_or_executed_during_preview(
    requested: Any,
) -> None:
    fake = ProviderClient()
    request = command(connection_overrides=requested)
    plan, summary = await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []
    assert plan["connection_overrides"] == requested
    assert plan["connection_context"]["effective_overrides"] == {}
    assert (
        plan["connection_context"]["binding"]["connection_configurations_present"]
        is False
    )
    assert plan["connection_context"]["binding"]["connection_configurations"] is None
    assert dynamic_schemas.ALIAS_INPUT not in plan["dependencies"]
    assert summary["connection_context"]["effective_overrides"] == {}
    evidence = await dynamic_schemas.apply(plan, fake.client)
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


async def test_declared_connection_context_is_injected_once_and_revision_bound() -> (
    None
):
    fake = ProviderClient()
    alias_context(fake)
    overrides = {SOURCE_ALIAS: CHILD_ALIAS}
    plan, summary = await dynamic_schemas.preview(
        command(connection_overrides=overrides), fake.client
    )
    assert fake.executions == []
    assert plan["dependencies"][dynamic_schemas.ALIAS_INPUT] == overrides
    assert plan["connection_context"]["additional_sources"] == []
    assert {
        row["record"]["sys_id"] for row in plan["connection_context"]["alias_records"]
    } == {SOURCE_ALIAS, CHILD_ALIAS}
    assert summary["connection_context"]["effective_overrides"] == overrides
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert fake.executions[0][1][dynamic_schemas.ALIAS_INPUT] == overrides
    assert evidence["connection_context"] == plan["connection_context"]
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1
    assert all(
        table not in {"sys_connection", "sys_credentials", "sys_alias_connection"}
        for table, _ in fake.reads
    )
    public = dynamic_schemas.evidence_summary(evidence)
    assert "binding" not in public["connection_context"]
    assert public["connection_context"]["alias_revisions"][1]["parent"] == SOURCE_ALIAS


async def test_null_connection_context_binds_default_source_without_wire_override() -> (
    None
):
    fake = ProviderClient()
    alias_context(fake)
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={SOURCE_ALIAS: None}), fake.client
    )
    assert dynamic_schemas.ALIAS_INPUT not in plan["dependencies"]
    assert [
        row["record"]["sys_id"] for row in plan["connection_context"]["alias_records"]
    ] == [SOURCE_ALIAS]
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert evidence["connection_overrides"] == {SOURCE_ALIAS: None}
    assert dynamic_schemas.ALIAS_INPUT not in fake.executions[0][1]
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


@pytest.mark.parametrize("declared", [True, False])
async def test_full_root_connection_context_retains_foreign_source_hierarchy(
    declared: bool,
) -> None:
    fake = ProviderClient()
    alias_context(fake, declared=declared)
    overrides = {OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS}
    if declared:
        overrides[SOURCE_ALIAS] = CHILD_ALIAS
    plan, summary = await dynamic_schemas.preview(
        command(connection_overrides=overrides), fake.client
    )
    assert plan["connection_context"]["additional_sources"] == [OTHER_SOURCE_ALIAS]
    assert summary["connection_context"]["additional_sources"] == [OTHER_SOURCE_ALIAS]
    assert plan["connection_context"]["effective_overrides"] == overrides
    assert plan["dependencies"][dynamic_schemas.ALIAS_INPUT] == overrides
    evidence = await dynamic_schemas.apply(plan, fake.client)
    await dynamic_schemas.verify(evidence, fake.client)
    assert fake.executions[0][1][dynamic_schemas.ALIAS_INPUT] == overrides
    assert len(fake.executions) == 1


async def test_known_owner_alias_cannot_bypass_native_offered_child_authorization() -> (
    None
):
    fake = ProviderClient()
    alias_context(fake)
    fake.records[("sys_alias", OTHER_CHILD_ALIAS)]["parent"] = SOURCE_ALIAS
    with pytest.raises(CapabilityError, match="declared child"):
        await dynamic_schemas.preview(
            command(connection_overrides={SOURCE_ALIAS: OTHER_CHILD_ALIAS}), fake.client
        )
    assert fake.executions == []


@pytest.mark.parametrize(
    "defect",
    [
        "credential_source",
        "credential_child",
        "source_is_child",
        "wrong_parent",
        "missing_parent",
        "wrong_identity",
    ],
)
async def test_alias_context_requires_readable_connection_hierarchy_before_effect(
    defect: str,
) -> None:
    fake = ProviderClient()
    alias_context(fake, declared=False)
    source = fake.records[("sys_alias", OTHER_SOURCE_ALIAS)]
    target = fake.records[("sys_alias", OTHER_CHILD_ALIAS)]
    if defect == "credential_source":
        source["type"] = "credential"
    elif defect == "credential_child":
        target["type"] = "credential"
    elif defect == "source_is_child":
        source["parent"] = SOURCE_ALIAS
    elif defect == "wrong_parent":
        target["parent"] = SOURCE_ALIAS
    elif defect == "missing_parent":
        target.pop("parent")
    else:
        target["sys_id"] = SOURCE_ALIAS
    with pytest.raises(CapabilityError):
        await dynamic_schemas.preview(
            command(connection_overrides={OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS}),
            fake.client,
        )
    assert fake.executions == []


@pytest.mark.parametrize(
    "defect",
    ["source_revision", "target_revision", "credential", "hierarchy", "offered_child"],
)
async def test_connection_context_drift_fails_before_provider_effect(
    defect: str,
) -> None:
    fake = ProviderClient()
    alias_context(fake)
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={SOURCE_ALIAS: CHILD_ALIAS}), fake.client
    )
    if defect == "source_revision":
        fake.records[("sys_alias", SOURCE_ALIAS)]["sys_mod_count"] = "2"
    elif defect == "target_revision":
        fake.records[("sys_alias", CHILD_ALIAS)]["sys_mod_count"] = "2"
    elif defect == "credential":
        fake.records[("sys_alias", CHILD_ALIAS)]["type"] = "credential"
    elif defect == "hierarchy":
        fake.records[("sys_alias", CHILD_ALIAS)]["parent"] = OTHER_SOURCE_ALIAS
    else:
        fake.definitions[OWNER]["connection_configurations"][0]["child_aliases"] = []
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.apply(plan, fake.client)
    assert error.value.code == "STALE_PREVIEW"
    assert fake.executions == []


async def test_alias_posteffect_drift_is_unknown_outcome_and_never_replayed() -> None:
    fake = ProviderClient()
    alias_context(fake)
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={SOURCE_ALIAS: CHILD_ALIAS}), fake.client
    )

    def change_alias() -> None:
        fake.records[("sys_alias", CHILD_ALIAS)]["type"] = "credential"

    fake.after_execute = change_alias
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.apply(plan, fake.client)
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert error.value.outcome is not None
    assert error.value.outcome["provider_executed"] is True
    assert error.value.outcome["schema_retained"] is False
    assert len(fake.executions) == 1


async def test_verify_rechecks_full_root_foreign_alias_hierarchy_without_execution() -> (
    None
):
    fake = ProviderClient()
    alias_context(fake)
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS}),
        fake.client,
    )
    evidence = await dynamic_schemas.apply(plan, fake.client)
    fake.records[("sys_alias", OTHER_CHILD_ALIAS)]["parent"] = SOURCE_ALIAS
    with pytest.raises(CapabilityError) as error:
        await dynamic_schemas.verify(evidence, fake.client)
    assert error.value.code == "STALE_PREVIEW"
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "defect",
    [
        "effective_map",
        "owner",
        "targets",
        "missing_alias",
        "extra_source",
        "wire_input",
    ],
)
async def test_connection_evidence_rejects_context_and_wire_inconsistency(
    defect: str,
) -> None:
    fake = ProviderClient()
    alias_context(fake)
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={SOURCE_ALIAS: CHILD_ALIAS}), fake.client
    )
    evidence = await dynamic_schemas.apply(plan, fake.client)
    context = evidence["connection_context"]
    if defect == "effective_map":
        context["effective_overrides"] = {}
    elif defect == "owner":
        context["binding"]["owner_definition_id"] = PROVIDER
    elif defect == "targets":
        context["binding"]["targets"] = []
    elif defect == "missing_alias":
        context["alias_records"] = []
    elif defect == "extra_source":
        context["additional_sources"] = [OTHER_SOURCE_ALIAS]
    else:
        evidence["dependencies"][dynamic_schemas.ALIAS_INPUT] = {}
    evidence["plan"]["connection_context"] = copy.deepcopy(context)
    evidence["plan"]["dependencies"] = copy.deepcopy(evidence["dependencies"])
    with pytest.raises(CapabilityError):
        dynamic_schemas.validate_evidence(evidence)
    assert len(fake.executions) == 1


@pytest.mark.parametrize("with_alias", [False, True])
async def test_exact_native_tombstone_marker_is_retained_without_alias_identity_reads(
    with_alias: bool,
) -> None:
    fake = ProviderClient()
    alias_context(fake)
    overrides = {"": ""}
    if with_alias:
        overrides[SOURCE_ALIAS] = CHILD_ALIAS
    plan, summary = await dynamic_schemas.preview(
        command(connection_overrides=overrides), fake.client
    )
    assert fake.executions == []
    assert plan["connection_context"]["effective_overrides"] == overrides
    assert plan["dependencies"][dynamic_schemas.ALIAS_INPUT] == overrides
    assert summary["connection_context"]["effective_overrides"] == overrides
    assert not any(
        table == "sys_alias" and identity == "" for table, identity in fake.reads
    )
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert fake.executions[0][1][dynamic_schemas.ALIAS_INPUT] == overrides
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    "overrides", [{"": None}, {"": CHILD_ALIAS}, {SOURCE_ALIAS: ""}]
)
async def test_connection_context_rejects_other_empty_key_or_value_combinations(
    overrides: Any,
) -> None:
    fake = ProviderClient()
    request = command().model_copy(update={"connection_overrides": overrides})
    with pytest.raises(CapabilityError):
        await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []


async def test_connection_evidence_distinguishes_native_marker_from_no_override_context() -> (
    None
):
    fake = ProviderClient()
    plan, _ = await dynamic_schemas.preview(
        command(connection_overrides={"": ""}), fake.client
    )
    evidence = await dynamic_schemas.apply(plan, fake.client)
    evidence["connection_context"]["effective_overrides"] = {}
    evidence["plan"]["connection_context"] = copy.deepcopy(
        evidence["connection_context"]
    )
    with pytest.raises(CapabilityError, match="reviewed request"):
        dynamic_schemas.validate_evidence(evidence)
    assert len(fake.executions) == 1


@pytest.mark.parametrize(("kind", "value"), [("boolean", False), ("integer", 0)])
@pytest.mark.parametrize("linked", [False, True])
async def test_mandatory_false_and_zero_reach_provider_and_saved_call_unchanged(
    kind: str,
    value: Any,
    linked: bool,
) -> None:
    fake = ProviderClient()
    owner_parameter = {"name": "requester", "type": kind, "mandatory": True}
    provider_parameter = {"name": "requested_for", "type": kind, "mandatory": True}
    fake.definitions[OWNER]["inputs"][0] = copy.deepcopy(owner_parameter)
    fake.definitions[PROVIDER]["inputs"][0] = copy.deepcopy(provider_parameter)
    fake.descriptor["inputs"][0] = {
        **copy.deepcopy(provider_parameter),
        "value": "{{action.requester}}" if linked else value,
    }
    request = command().model_copy(update={"inputs": {"requester": value}})
    original = copy.deepcopy(fake.definitions)
    plan, _ = await dynamic_schemas.preview(request, fake.client)
    assert fake.executions == []
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert fake.executions[0][1]["requested_for"] == value
    assert type(fake.executions[0][1]["requested_for"]) is type(value)
    step = calls.step(
        {"id": "9" * 32, "scope": "global"},
        fake.definitions[OWNER],
        {"requester": value, "fields": {"summary": "saved"}},
        subflow=False,
        uid="provider-value-agreement",
        schema_evidence=[evidence],
    )
    binding = next(row for row in step.raw["inputs"] if row["name"] == "requester")
    assert binding["value"] == value
    assert type(binding["value"]) is type(value)
    assert fake.definitions == original
    assert len(fake.executions) == 1


@pytest.mark.parametrize(
    ("kind", "present", "fallback"),
    [
        ("string", "present", "fallback"),
        ("string", "", "fallback"),
        ("integer", 0, 9),
        ("boolean", False, True),
    ],
)
async def test_owner_present_default_value_matches_provider_context_and_saved_call(
    kind: str,
    present: Any,
    fallback: Any,
) -> None:
    fake = ProviderClient()
    fake.definitions[OWNER]["inputs"].append(
        {
            "name": "static_default",
            "type": kind,
            "value": copy.deepcopy(present),
            "defaultValue": copy.deepcopy(fallback),
        }
    )
    original = copy.deepcopy(fake.definitions)
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    assert plan["inputs"]["static_default"] == present
    assert type(plan["inputs"]["static_default"]) is type(present)
    assert "static_default" not in plan["requested_inputs"]
    evidence = await dynamic_schemas.apply(plan, fake.client)
    step = calls.step(
        {"id": "9" * 32, "scope": "global"},
        fake.definitions[OWNER],
        {"requester": RECORD, "fields": {"summary": "saved"}},
        subflow=False,
        uid="provider-default-agreement",
        schema_evidence=[evidence],
    )
    binding = next(row for row in step.raw["inputs"] if row["name"] == "static_default")
    assert binding["value"] == present
    assert type(binding["value"]) is type(present)
    assert fake.definitions == original
    assert len(fake.executions) == 1


@pytest.mark.parametrize(("kind", "value"), [("boolean", False), ("integer", 0)])
async def test_presence_default_beats_conflicting_fallback_in_provider_equal_value_transform(
    kind: str,
    value: Any,
) -> None:
    fake = ProviderClient()
    fake.definitions[OWNER]["inputs"].append(
        {
            "name": "static_default",
            "type": kind,
            "value": value,
            "defaultValue": True if kind == "boolean" else 99,
        }
    )
    provider_parameter = {"name": "prefix", "type": kind, "mandatory": True}
    fake.definitions[PROVIDER]["inputs"][2] = copy.deepcopy(provider_parameter)
    fake.descriptor["inputs"][2] = {**copy.deepcopy(provider_parameter), "value": value}
    evidence = await resolved(fake)
    assert evidence["plan"]["inputs"]["static_default"] == value
    assert fake.executions[0][1]["prefix"] == value
    assert type(fake.executions[0][1]["prefix"]) is type(value)
    await dynamic_schemas.verify(evidence, fake.client)
    assert len(fake.executions) == 1


async def test_present_null_default_is_not_replaced_by_a_valid_string_fallback() -> (
    None
):
    fake = ProviderClient()
    parameter = {
        "name": "static_default",
        "type": "string",
        "value": None,
        "defaultValue": "fallback",
    }
    fake.definitions[OWNER]["inputs"].append(copy.deepcopy(parameter))
    original = copy.deepcopy(fake.definitions)
    assert data.default_value(parameter) is None
    plan, _ = await dynamic_schemas.preview(command(), fake.client)
    assert plan["inputs"]["static_default"] is None
    assert "static_default" not in plan["requested_inputs"]
    assert fake.executions == []
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert fake.executions[0][1] == {
        "requested_for": {"table": "sys_user", "sys_id": RECORD},
        "raw_record": '{"value":"' + RECORD + '"}',
        "prefix": "literal",
    }
    step = calls.step(
        {"id": "9" * 32, "scope": "global"},
        fake.definitions[OWNER],
        {"requester": RECORD, "fields": {"summary": "saved"}},
        subflow=False,
        uid="provider-null-default-agreement",
        schema_evidence=[evidence],
    )
    binding = next(row for row in step.raw["inputs"] if row["name"] == "static_default")
    assert binding["value"] is None
    assert fake.definitions == original
    assert len(fake.executions) == 1


async def test_present_reference_default_drives_literal_provider_normalization_and_saved_call() -> (
    None
):
    fake = ProviderClient()
    present_id, fallback_id = "a" * 32, "b" * 32
    fake.definitions[OWNER]["inputs"][0].update(
        readonly=True,
        value=present_id,
        defaultValue=fallback_id,
    )
    # No linked dependency: native prep still matches this literal to the omitted owner reference.
    fake.descriptor["inputs"][0]["value"] = present_id
    fake.descriptor["inputs"][1]["value"] = present_id
    fake.records[("sys_user", present_id)] = revision(present_id)
    request = command().model_copy(update={"inputs": {}})
    original = copy.deepcopy(fake.definitions)
    plan, _ = await dynamic_schemas.preview(request, fake.client)
    assert plan["requested_inputs"] == {}
    assert plan["inputs"]["requester"] == present_id
    assert plan["sibling_inputs"] == {}
    assert fake.executions == []
    evidence = await dynamic_schemas.apply(plan, fake.client)
    assert fake.executions[0][1]["raw_record"] == '{"value":"' + present_id + '"}'
    assert fallback_id not in fake.executions[0][1]["raw_record"]
    step = calls.step(
        {"id": "9" * 32, "scope": "global"},
        fake.definitions[OWNER],
        {"fields": {"summary": "saved"}},
        subflow=False,
        uid="provider-reference-default-agreement",
        schema_evidence=[evidence],
    )
    binding = next(row for row in step.raw["inputs"] if row["name"] == "requester")
    assert binding["value"] == present_id == plan["inputs"]["requester"]
    assert fake.definitions == original
    assert len(fake.executions) == 1
