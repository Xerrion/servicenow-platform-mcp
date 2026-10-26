"""Synthetic source-derived native alias DTOs; static authoring, never execution."""

import copy
from typing import Any, override

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import connection_inputs as connections
from servicenow_mcp.capabilities.flows import tree
from servicenow_mcp.capabilities.mutations.models import fingerprint
from servicenow_mcp.platform.client import ServiceNowClient

FLOW = "1" * 32
ACTION = "2" * 32
SUBFLOW = "3" * 32
SOURCE = "4" * 32
CHILD = "5" * 32
OTHER_SOURCE = "6" * 32
OTHER_CHILD = "7" * 32
MAPPING = "8" * 32
OTHER_MAPPING = "9" * 32


def alias(identity: str = SOURCE, child: str = CHILD) -> dict[str, Any]:
    return {
        "alias_id": identity,
        "display_name": "Fixture service",
        "name": "fixture_service",
        "type": "CONNECTION",
        "scope_name": "global",
        "configured": True,
        "configuration_template": "a" * 32,
        "child_aliases": [
            {"alias_id": child, "display_name": "Fixture secondary", "configured": True}
        ],
    }


def definition(*, subflow: bool = False) -> dict[str, Any]:
    return {
        "id": SUBFLOW if subflow else ACTION,
        "name": "Fixture connection call",
        "security": {"can_read": True},
        "connection_configurations": [alias(), alias(OTHER_SOURCE, OTHER_CHILD)],
        **(
            {"type": "subflow", "snapshot": True, "isFlowEntitled": True}
            if subflow
            else {"is_snapshot": True, "entitled": True}
        ),
    }


def component(*, subflow: bool = False, uid: str = "call-one") -> dict[str, Any]:
    return {
        "id": "b" * 32,
        "uiUniqueIdentifier": uid,
        "flowSysId": FLOW,
        "type": "subflow" if subflow else "action",
        "subflowSysId" if subflow else "actionTypeSysId": SUBFLOW
        if subflow
        else ACTION,
        "subFlow" if subflow else "actionType": definition(subflow=subflow),
    }


def flow() -> dict[str, Any]:
    return {
        "id": FLOW,
        "connection_configurations": [alias(), alias(OTHER_SOURCE, OTHER_CHILD)],
        "actionInstances": [component()],
        "subFlowInstances": [],
    }


def mapping(
    identity: str = MAPPING, source: str = SOURCE, target: str = CHILD
) -> dict[str, Any]:
    # The table is opaque server response metadata, not a constructor default.
    return {
        "id": identity,
        "sourceAlias": source,
        "targetAlias": target,
        "sourceTable": "sys_hub_action_instance",
        "sourceId": "b" * 32,
        "retained_server_metadata": {"revision": 1},
    }


def set_mappings(owner: dict[str, Any], *rows: dict[str, Any]) -> None:
    owner["aliasOverride"] = {
        "aliasOverrideMappings": list(rows),
        "retained_override_metadata": "keep",
    }


def mappings(owner: dict[str, Any]) -> list[dict[str, Any]]:
    return owner.get("aliasOverride", {}).get("aliasOverrideMappings", [])


class MetadataClient(ServiceNowClient):
    def __init__(self) -> None:
        self.native_flow = flow()
        self.action = definition()
        self.subflow = definition(subflow=True)
        self.reads: list[tuple[str, bool]] = []

    @override
    async def studio_request(
        self,
        resource: str,
        *,
        data: dict[str, Any] | None = None,
        create: bool = False,
        minimal: bool = False,
    ) -> dict[str, Any]:
        assert data is None and create is False, (
            "Connection authoring must only read metadata"
        )
        self.reads.append((resource, minimal))
        if resource == f"flow/{FLOW}":
            return copy.deepcopy(self.native_flow)
        if resource == f"action/action_types/{ACTION}":
            return copy.deepcopy(self.action)
        if resource == f"flow/{SUBFLOW}":
            return copy.deepcopy(self.subflow)
        raise AssertionError(f"Unexpected nonmetadata request: {resource}")


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "default",
        {SOURCE: "default"},
        {"raw": CHILD},
        {SOURCE: {}},
        {SOURCE: SOURCE},
    ],
)
def test_public_field_accepts_only_alias_sys_ids_and_null(value: Any) -> None:
    with pytest.raises(CapabilityError):
        connections.parse(value)
    assert connections.parse({SOURCE: CHILD, OTHER_SOURCE: None}) == {
        SOURCE: CHILD,
        OTHER_SOURCE: None,
    }


async def test_instance_constructor_uses_independent_metadata_and_native_new_alias_id() -> (
    None
):
    client = MetadataClient()
    current = flow()
    selected = current["actionInstances"][0]
    selected["actionType"]["connection_configurations"] = []
    set_mappings(current, mapping())
    root_before = copy.deepcopy(current["aliasOverride"])
    definition_before = copy.deepcopy(client.action)

    evidence = await connections.apply(
        current, tree.Step(selected, "action"), {SOURCE: CHILD}, client=client
    )

    assert mappings(selected) == [
        {"id": "", "sourceAlias": SOURCE, "targetAlias": CHILD}
    ]
    assert connections.NEW_ALIAS_ID == ""
    assert "sourceTable" not in mappings(selected)[0]
    assert current["aliasOverride"] == root_before
    assert selected["actionType"]["connection_configurations"] == []
    assert evidence["scope"] == "instance"
    assert evidence["component_uid"] == "call-one"
    assert evidence["definitions"] == [
        {"subflow": False, "id": ACTION, "metadata": definition_before}
    ]
    assert evidence["targets"] == [
        {
            "source_alias": SOURCE,
            "target_alias": CHILD,
            "metadata": alias()["child_aliases"][0],
        }
    ]
    assert client.action == definition_before
    assert client.reads == [(f"action/action_types/{ACTION}", False)]


async def test_saved_mapping_edit_preserves_ids_source_tables_and_unrelated_fields() -> (
    None
):
    client = MetadataClient()
    current = flow()
    selected = current["actionInstances"][0]
    extra_child = "c" * 32
    client.action["connection_configurations"][0]["child_aliases"].append(
        {"alias_id": extra_child, "display_name": "New child"}
    )
    original = mapping()
    unrelated = mapping(OTHER_MAPPING, OTHER_SOURCE, OTHER_CHILD)
    set_mappings(selected, copy.deepcopy(original), copy.deepcopy(unrelated))

    await connections.apply(current, selected, {SOURCE: extra_child}, client=client)

    assert mappings(selected) == [{**original, "targetAlias": extra_child}, unrelated]
    assert selected["aliasOverride"]["retained_override_metadata"] == "keep"


@pytest.mark.parametrize("instance", [True, False])
async def test_saved_clear_is_identity_preserving_native_tombstone_in_both_scopes(
    instance: bool,
) -> None:
    client = MetadataClient()
    current = flow()
    owner = current["actionInstances"][0] if instance else current
    original = mapping()
    set_mappings(owner, copy.deepcopy(original))

    await connections.apply(
        current, owner if instance else None, {SOURCE: None}, client=client
    )

    assert mappings(owner) == [{**original, "sourceAlias": "", "targetAlias": ""}]


@pytest.mark.parametrize("instance", [True, False])
async def test_draft_clear_removes_match_and_first_default_uses_native_scope_specific_state(
    instance: bool,
) -> None:
    client = MetadataClient()
    current = flow()
    owner = current["actionInstances"][0] if instance else current
    set_mappings(owner, {"id": "", "sourceAlias": SOURCE, "targetAlias": CHILD})

    await connections.apply(
        current, owner if instance else None, {SOURCE: None}, client=client
    )
    assert mappings(owner) == []
    del owner["aliasOverride"]
    await connections.apply(
        current, owner if instance else None, {SOURCE: None}, client=client
    )
    if instance:
        assert mappings(owner) == [{"id": "", "sourceAlias": "", "targetAlias": ""}]
    else:
        assert "aliasOverride" not in owner


async def test_root_alias_ids_use_only_independently_fetched_current_flow_offerings() -> (
    None
):
    client = MetadataClient()
    current = flow()
    selected = current["actionInstances"][0]
    selected["aliasIds"] = [SOURCE]
    current["connection_configurations"][0]["child_aliases"] = []

    evidence = await connections.apply(current, None, {SOURCE: CHILD}, client=client)

    assert mappings(current) == [
        {"id": "", "sourceAlias": SOURCE, "targetAlias": CHILD}
    ]
    assert "aliasOverride" not in selected
    assert evidence["scope"] == "flow"
    assert (
        evidence["connection_configurations"]
        == client.native_flow["connection_configurations"]
    )
    assert client.reads == [(f"flow/{FLOW}", False)]
    with pytest.raises(CapabilityError, match="does not offer"):
        await connections.apply(
            current, None, {OTHER_SOURCE: OTHER_CHILD}, client=client
        )


async def test_root_uses_independent_subflow_definition_and_rejects_unused_native_aliases() -> (
    None
):
    client = MetadataClient()
    current = flow()
    current["actionInstances"] = []
    current["subFlowInstances"] = [component(subflow=True)]
    client.subflow["connection_configurations"] = [alias()]

    evidence = await connections.apply(current, None, {SOURCE: CHILD}, client=client)

    assert evidence["definitions"][0]["metadata"] == client.subflow
    assert client.reads == [(f"flow/{FLOW}", False), (f"flow/{SUBFLOW}", True)]
    with pytest.raises(CapabilityError, match="does not offer"):
        await connections.apply(
            current, None, {OTHER_SOURCE: OTHER_CHILD}, client=client
        )


@pytest.mark.parametrize("target", [OTHER_CHILD, OTHER_SOURCE, "d" * 32])
async def test_targets_cannot_cross_source_relations_or_use_unrelated_aliases(
    target: str,
) -> None:
    current = flow()
    selected = current["actionInstances"][0]
    before = copy.deepcopy(current)
    with pytest.raises(CapabilityError, match="declared child"):
        await connections.apply(
            current, selected, {SOURCE: target}, client=MetadataClient()
        )
    assert current == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "malformed",
        "duplicate",
        "shared_child",
        "credential",
        "unreadable",
        "wrong_id",
    ],
)
async def test_unknown_malformed_ambiguous_or_unreadable_metadata_fails_closed(
    mutation: str,
) -> None:
    client = MetadataClient()
    current = flow()
    if mutation == "missing":
        del client.action["connection_configurations"]
    elif mutation == "malformed":
        client.action["connection_configurations"][0]["child_aliases"] = {}
    elif mutation == "duplicate":
        client.action["connection_configurations"].append(alias())
    elif mutation == "shared_child":
        client.action["connection_configurations"][1]["child_aliases"] = copy.deepcopy(
            alias()["child_aliases"]
        )
    elif mutation == "credential":
        client.action["connection_configurations"][0]["type"] = "CREDENTIAL"
    elif mutation == "unreadable":
        client.action["security"]["can_read"] = False
    else:
        client.action["id"] = "e" * 32
    with pytest.raises(CapabilityError):
        await connections.apply(
            current, current["actionInstances"][0], {SOURCE: CHILD}, client=client
        )
    assert "aliasOverride" not in current["actionInstances"][0]


async def test_definition_cache_reuses_only_matching_independent_snapshot() -> None:
    client = MetadataClient()
    current = flow()
    cache = {(False, ACTION): definition()}
    evidence = await connections.apply(
        current,
        current["actionInstances"][0],
        {SOURCE: CHILD},
        client=client,
        definitions=cache,
    )
    assert client.reads == []
    assert evidence["definitions"][0]["metadata"] == cache[(False, ACTION)]
    cache[(False, ACTION)]["id"] = "f" * 32
    with pytest.raises(CapabilityError, match="identity"):
        await connections.apply(
            current,
            current["actionInstances"][0],
            {SOURCE: None},
            client=client,
            definitions=cache,
        )


async def test_full_definition_and_target_metadata_drift_changes_revision_evidence() -> (
    None
):
    client = MetadataClient()
    current = flow()
    selected = current["actionInstances"][0]
    first = await connections.apply(current, selected, {SOURCE: CHILD}, client=client)
    client.action["independent_revision_metadata"] = {"revision": 2}
    second = await connections.apply(current, selected, {SOURCE: CHILD}, client=client)
    assert fingerprint(first) != fingerprint(second)
    client.action["connection_configurations"][0]["child_aliases"][0][
        "display_name"
    ] = "Renamed native child"
    third = await connections.apply(current, selected, {SOURCE: CHILD}, client=client)
    assert fingerprint(second) != fingerprint(third)
    assert (
        first["definitions"][0]["metadata"].get("independent_revision_metadata") is None
    )


async def test_root_full_metadata_drift_changes_evidence_even_for_untouched_alias() -> (
    None
):
    client = MetadataClient()
    current = flow()
    current["actionInstances"][0]["aliasIds"] = [SOURCE]
    first = await connections.apply(current, None, {SOURCE: CHILD}, client=client)
    client.native_flow["connection_configurations"][1]["scope_name"] = "changed_scope"
    second = await connections.apply(current, None, {SOURCE: CHILD}, client=client)
    assert fingerprint(first) != fingerprint(second)


async def test_independent_native_readback_uses_persisted_aliases_not_local_map_identity() -> (
    None
):
    client = MetadataClient()
    current = flow()
    selected = current["actionInstances"][0]
    await connections.apply(current, selected, {SOURCE: CHILD}, client=client)
    # Synthetic server assignment changes only mapping persistence metadata.
    client.native_flow = copy.deepcopy(current)
    persisted = mappings(client.native_flow["actionInstances"][0])[0]
    persisted.update(
        id=MAPPING, sourceTable="sys_hub_action_instance", sourceId=selected["id"]
    )
    observed = await client.studio_request(f"flow/{FLOW}")
    assert connections.effective(observed, observed["actionInstances"][0]) == {
        SOURCE: CHILD
    }
    assert mappings(selected)[0]["id"] == ""
    persisted["targetAlias"] = OTHER_CHILD
    observed = await client.studio_request(f"flow/{FLOW}")
    assert connections.effective(
        observed, observed["actionInstances"][0]
    ) != connections.effective(current, selected)


def test_provider_effective_map_has_native_whole_scope_precedence_and_exact_tombstones() -> (
    None
):
    current = flow()
    selected = current["actionInstances"][0]
    set_mappings(current, mapping(), mapping(OTHER_MAPPING, OTHER_SOURCE, OTHER_CHILD))
    assert connections.effective(current, selected) == {
        SOURCE: CHILD,
        OTHER_SOURCE: OTHER_CHILD,
    }
    set_mappings(selected, {"id": "", "sourceAlias": SOURCE, "targetAlias": "c" * 32})
    assert connections.effective(current, selected) == {SOURCE: "c" * 32}
    set_mappings(selected, {"id": MAPPING, "sourceAlias": "", "targetAlias": ""})
    assert connections.effective(current, selected) == {"": ""}
    set_mappings(selected)
    assert connections.effective(current, selected) == {
        SOURCE: CHILD,
        OTHER_SOURCE: OTHER_CHILD,
    }


def test_provider_context_binds_owner_not_provider_aliases_and_full_child_metadata() -> (
    None
):
    owner = definition()
    original = copy.deepcopy(owner)
    context, evidence = connections.provider_context(
        owner, {SOURCE: CHILD, OTHER_SOURCE: None}
    )
    assert context == {SOURCE: CHILD}
    assert evidence["owner_definition_id"] == ACTION
    assert evidence["connection_configurations"] == owner["connection_configurations"]
    assert (
        evidence["targets"][0]["metadata"]
        == owner["connection_configurations"][0]["child_aliases"][0]
    )
    owner["connection_configurations"][0]["child_aliases"][0]["configured"] = False
    _, changed = connections.provider_context(
        owner, {SOURCE: CHILD, OTHER_SOURCE: None}
    )
    assert fingerprint(evidence) != fingerprint(changed)
    assert (
        evidence["connection_configurations"] == original["connection_configurations"]
    )
    with pytest.raises(CapabilityError, match="declared child"):
        connections.provider_context(owner, {SOURCE: OTHER_CHILD})


def test_empty_provider_context_without_alias_metadata_authorizes_no_overrides() -> (
    None
):
    owner = definition()
    del owner["connection_configurations"]
    context, evidence = connections.provider_context(owner, None)
    assert context == {}
    assert evidence["connection_configurations"] is None
    assert evidence["connection_configurations_present"] is False
    with pytest.raises(CapabilityError, match="unavailable"):
        connections.provider_context(owner, {SOURCE: CHILD})


def test_cleanup_preserves_shared_alias_until_all_action_and_subflow_references_are_removed() -> (
    None
):
    current = flow()
    first = current["actionInstances"][0]
    second = component(subflow=True, uid="call-two")
    first["aliasIds"] = [SOURCE]
    second["aliasIds"] = [SOURCE]
    current["subFlowInstances"] = [second]
    original = mapping()
    set_mappings(current, copy.deepcopy(original))
    connections.cleanup_removed(current, [first])
    assert mappings(current) == [original]
    connections.cleanup_removed(current, [first, second])
    assert mappings(current) == [{**original, "sourceAlias": "", "targetAlias": ""}]


def test_cleanup_whole_subtree_removes_new_mappings_preserves_unrelated_rows_and_tombstones() -> (
    None
):
    current = flow()
    first = current["actionInstances"][0]
    first["aliasIds"] = [SOURCE]
    unrelated = mapping(OTHER_MAPPING, OTHER_SOURCE, OTHER_CHILD)
    tombstone = {
        "id": "c" * 32,
        "sourceAlias": "",
        "targetAlias": "",
        "sourceTable": "sys_hub_flow",
    }
    set_mappings(
        current,
        {"id": "", "sourceAlias": SOURCE, "targetAlias": CHILD},
        copy.deepcopy(unrelated),
        copy.deepcopy(tombstone),
    )
    connections.cleanup_removed(
        current, [tree.Step(first, "action")], components=[tree.Step(first, "action")]
    )
    assert mappings(current) == [unrelated, tombstone]
    assert current["aliasOverride"]["retained_override_metadata"] == "keep"


def test_cleanup_definition_fallback_and_explicit_predelete_reference_counts() -> None:
    current = flow()
    first = current["actionInstances"][0]
    second = component(uid="call-two")
    second["actionType"]["connection_configurations"] = [alias()]
    original = mapping()
    set_mappings(current, copy.deepcopy(original))
    # The caller's working flat collections can lag behind its edited tree.
    current["actionInstances"] = []
    connections.cleanup_removed(current, [first], components=[first, second])
    assert mappings(current) == [original]
    connections.cleanup_removed(current, [first, second], components=[first, second])
    assert mappings(current)[0] == {**original, "sourceAlias": "", "targetAlias": ""}


def test_ambiguous_existing_mappings_and_invalid_reference_counts_are_rejected() -> (
    None
):
    current = flow()
    set_mappings(current, mapping(), mapping(OTHER_MAPPING))
    with pytest.raises(CapabilityError, match="ambiguous"):
        connections.effective(current)
    set_mappings(current, mapping())
    with pytest.raises(CapabilityError, match="pre-delete"):
        connections.cleanup_removed(current, [component(), component(uid="extra")])


async def test_new_call_shell_needs_flow_identity_but_not_saved_or_ui_identity() -> (
    None
):
    current = flow()
    shell = {"flowSysId": FLOW, "type": "action", "actionTypeSysId": ACTION}
    await connections.apply(current, shell, {SOURCE: CHILD}, client=MetadataClient())
    assert mappings(shell) == [{"id": "", "sourceAlias": SOURCE, "targetAlias": CHILD}]
    shell["flowSysId"] = "f" * 32
    with pytest.raises(CapabilityError, match="another flow"):
        await connections.apply(current, shell, {SOURCE: None}, client=MetadataClient())


@pytest.mark.parametrize(
    "field,value",
    [("sourceTable", []), ("sourceTable", "bad.table"), ("sourceId", "not-an-id")],
)
def test_malformed_saved_mapping_metadata_is_not_silently_rewritten(
    field: str, value: Any
) -> None:
    current = flow()
    native = mapping()
    native[field] = value
    set_mappings(current, native)
    before = copy.deepcopy(current)
    with pytest.raises(CapabilityError):
        connections.effective(current)
    assert current == before


def test_duplicate_deletion_cannot_consume_another_calls_shared_alias_reference() -> (
    None
):
    current = flow()
    first = current["actionInstances"][0]
    second = component(uid="call-two")
    current["actionInstances"].append(second)
    original = mapping()
    set_mappings(current, copy.deepcopy(original))
    with pytest.raises(CapabilityError, match="unique references"):
        connections.cleanup_removed(current, [first, first])
    assert mappings(current) == [original]


def test_provider_root_projection_excludes_tombstones_but_local_projection_retains_them() -> (
    None
):
    current = flow()
    selected = current["actionInstances"][0]
    tombstone = {"id": OTHER_MAPPING, "sourceAlias": "", "targetAlias": ""}
    set_mappings(current, mapping(), copy.deepcopy(tombstone))
    assert connections.effective(current) == {SOURCE: CHILD}
    set_mappings(
        selected,
        {"id": "", "sourceAlias": OTHER_SOURCE, "targetAlias": OTHER_CHILD},
        tombstone,
    )
    assert connections.effective(current, selected) == {
        OTHER_SOURCE: OTHER_CHILD,
        "": "",
    }


async def test_explicit_instance_default_with_inherited_root_override_keeps_native_suppression_marker() -> (
    None
):
    current = flow()
    selected = current["actionInstances"][0]
    set_mappings(current, mapping())
    evidence = await connections.apply(
        current, selected, {SOURCE: None}, client=MetadataClient()
    )
    assert mappings(selected) == [{"id": "", "sourceAlias": "", "targetAlias": ""}]
    assert connections.effective(current, selected) == {"": ""}
    assert connections.effective(current) == {SOURCE: CHILD}
    assert evidence["targets"] == [
        {"source_alias": SOURCE, "target_alias": None, "metadata": None}
    ]


async def test_multiple_explicit_defaults_share_one_native_draft_scope_marker() -> None:
    current = flow()
    selected = current["actionInstances"][0]
    client = MetadataClient()
    await connections.apply(
        current, selected, {SOURCE: None, OTHER_SOURCE: None}, client=client
    )
    assert mappings(selected) == [{"id": "", "sourceAlias": "", "targetAlias": ""}]
    before = copy.deepcopy(selected)
    await connections.apply(current, selected, {SOURCE: None}, client=client)
    assert selected == before
    assert connections.effective(current, selected) == {"": ""}


async def test_existing_saved_deletion_identity_is_not_reused_as_new_default_scope_marker() -> (
    None
):
    current = flow()
    selected = current["actionInstances"][0]
    tombstone = {
        "id": MAPPING,
        "sourceAlias": "",
        "targetAlias": "",
        "sourceTable": "sys_hub_action_instance",
    }
    set_mappings(selected, copy.deepcopy(tombstone))
    await connections.apply(
        current, selected, {SOURCE: None, OTHER_SOURCE: None}, client=MetadataClient()
    )
    assert mappings(selected) == [
        tombstone,
        {"id": "", "sourceAlias": "", "targetAlias": ""},
    ]


@pytest.mark.parametrize("instance", [True, False])
async def test_validate_existing_binds_internal_tombstones_without_rewriting_or_public_parsing(
    instance: bool,
) -> None:
    current = flow()
    selected = current["actionInstances"][0]
    owner = selected if instance else current
    tombstone = {
        "id": MAPPING,
        "sourceAlias": "",
        "targetAlias": "",
        "sourceTable": "sys_hub_flow",
    }
    draft_marker = {"id": "", "sourceAlias": "", "targetAlias": ""}
    set_mappings(
        owner,
        copy.deepcopy(tombstone),
        copy.deepcopy(draft_marker),
        mapping(OTHER_MAPPING, OTHER_SOURCE, OTHER_CHILD),
    )
    before = copy.deepcopy(current)
    evidence = await connections.validate_existing(
        current, selected if instance else None, client=MetadataClient()
    )
    assert current == before
    assert evidence["native_mappings"] == mappings(owner)
    assert evidence["targets"] == [
        {
            "source_alias": OTHER_SOURCE,
            "target_alias": OTHER_CHILD,
            "metadata": alias(OTHER_SOURCE, OTHER_CHILD)["child_aliases"][0],
        }
    ]
    with pytest.raises(CapabilityError):
        connections.parse({"": ""})


async def test_validate_existing_rejects_live_target_relation_drift_and_binds_tombstone_identity_drift() -> (
    None
):
    current = flow()
    selected = current["actionInstances"][0]
    client = MetadataClient()
    set_mappings(
        selected,
        {"id": MAPPING, "sourceAlias": "", "targetAlias": ""},
        mapping(OTHER_MAPPING),
    )
    first = await connections.validate_existing(current, selected, client=client)
    mappings(selected)[0]["sourceId"] = "f" * 32
    second = await connections.validate_existing(current, selected, client=client)
    assert fingerprint(first) != fingerprint(second)
    client.action["connection_configurations"][0]["child_aliases"] = []
    with pytest.raises(CapabilityError, match="declared child"):
        await connections.validate_existing(current, selected, client=client)
