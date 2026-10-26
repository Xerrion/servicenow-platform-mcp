"""New native metadata binds only through exact scoped flow readback equality."""

import copy
from dataclasses import dataclass, field
from typing import Any, override

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import authoring_ownership, semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests.test_flow_editing import FLOW, base_flow

ROOT = "af6a9a83-183b-57c9-b171-e43d39be0367"
TRANSFORM = "249ea5be-4248-5f56-b6c4-670210708c53"
ROOT_RECORD = "c" * 32
TRANSFORM_RECORD = "d" * 32
SOURCE = "e" * 32
TARGET = "f" * 32
MAPPING = "6" * 32
OLD_MAPPING = "7" * 32
OLD_COMPOUND = "8" * 32


def _compounds(flow):
    return flow["pharmacy"]["pharmacyCompound"]


def _mappings(owner):
    return owner["aliasOverride"]["aliasOverrideMappings"]


def _case(*, owner="flow", new_call=False):
    expected = base_flow()
    expected["pharmacy"] = {
        "unknown": {"retained": True},
        "pharmacyCompound": {
            "old": {
                "id": OLD_COMPOUND,
                "uiId": "old",
                "order": 0,
                "prescription": "{{ui-1.status}}",
                "opaque": {"retained": True},
            },
            ROOT: {"uiId": ROOT, "order": 0, "prescription": "{{ui-1.status}}"},
            TRANSFORM: {
                "uiId": TRANSFORM,
                "sourceUiId": ROOT,
                "order": 1,
                "transformDefinitionId": "b" * 32,
                "compositions": [
                    {
                        "id": "9" * 32,
                        "order": 0,
                        "parameters": [
                            {
                                "name": "fixture",
                                "type": "string",
                                "value": "private fixture parameter",
                                "order": 0,
                                "unknown": {"keep": True},
                            }
                        ],
                    }
                ],
            },
        },
    }
    uid = None if owner == "flow" else "ui-1" if owner == "action" else "ui-4"
    component = (
        expected
        if uid is None
        else expected["actionInstances"][0]
        if owner == "action"
        else expected["subFlowInstances"][0]
    )
    if uid is not None:
        component["flowSysId"] = FLOW
        if new_call:
            component["id"] = ""
    component["aliasOverride"] = {
        "opaque": {"retained": True},
        "aliasOverrideMappings": [
            {
                "id": OLD_MAPPING,
                "sourceAlias": "0" * 32,
                "targetAlias": "1" * 32,
                "sourceTable": "opaque_saved_table",
                "sourceId": "2" * 32,
                "opaque": {"retained": True},
            },
            {"id": "", "sourceAlias": SOURCE, "targetAlias": TARGET},
        ],
    }
    saved = copy.deepcopy(expected)
    for key, identity in ((ROOT, ROOT_RECORD), (TRANSFORM, TRANSFORM_RECORD)):
        _compounds(saved)[key].update(id=identity, attachedTo=FLOW, scopeId="global")
    saved_owner = (
        saved
        if uid is None
        else saved["actionInstances"][0]
        if owner == "action"
        else saved["subFlowInstances"][0]
    )
    # Synthetic opaque metadata, not an asserted backend ownership-table contract.
    _mappings(saved_owner)[1].update(
        id=MAPPING, sourceTable="fixture_owner", sourceId=FLOW
    )
    if new_call:
        saved_owner["id"] = "1" * 32
    manifest = {
        "transforms": [ROOT, TRANSFORM],
        "connections": [{"component_ui_id": uid, "source_alias": SOURCE}],
    }
    return expected, saved, manifest


@dataclass
class ReadbackClient(ServiceNowClient):
    saved: dict[str, Any]
    flow_reads: list[str] = field(default_factory=list)

    @override
    async def get_record(
        self,
        table: str,
        sys_id: str,
        fields: list[str] | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        raise AssertionError(
            "Authoring-row binding must not perform Table API ownership reads"
        )

    @override
    async def studio_request(
        self,
        resource: str,
        *,
        data: dict[str, Any] | None = None,
        create: bool = False,
        minimal: bool = False,
    ) -> dict[str, Any]:
        assert data is None and create is False and minimal is False
        assert resource == f"flow/{FLOW}"
        self.flow_reads.append(resource)
        return copy.deepcopy(self.saved)


@pytest.mark.parametrize("owner", ["flow", "action", "subflow"])
async def test_consumer_accepts_explicit_new_metadata_only_after_scoped_flow_get(owner):
    expected, saved, manifest = _case(owner=owner)
    before = copy.deepcopy(expected)
    client = ReadbackClient(saved)
    assert (
        await semantics.verify_saved(
            client,
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )
        == saved
    )
    assert expected == before
    assert client.flow_reads == [f"flow/{FLOW}"]


async def test_new_call_id_remains_the_existing_step_verifiers_responsibility():
    expected, saved, manifest = _case(owner="action", new_call=True)
    client = ReadbackClient(saved)
    bound = await authoring_ownership.bind(client, expected, saved, manifest)
    assert bound["actionInstances"][0]["id"] == ""
    assert _mappings(bound["actionInstances"][0]) == _mappings(
        saved["actionInstances"][0]
    )
    assert not client.flow_reads
    assert (
        await semantics.verify_saved(
            client,
            expected,
            saved,
            generated_ids={""},
            generated_authoring_rows=manifest,
        )
        == saved
    )


@pytest.mark.parametrize(
    "defect",
    [
        "old_compound_id",
        "old_mapping_id",
        "old_compound_unknown",
        "old_mapping_unknown",
        "new_compound_unknown",
        "new_mapping_unknown",
        "parameter",
        "composition",
        "source_ui",
        "order",
        "definition",
        "prescription",
        "source_alias",
        "target_alias",
        "owner_definition",
        "owner_uid",
        "alias_ids",
        "top_level",
        "new_id_uppercase",
        "new_id_nonstring",
        "duplicate_transform_id",
        "duplicate_mapping_id",
    ],
)
async def test_existing_rows_and_every_nonidentity_field_remain_strict(defect):
    expected, saved, manifest = _case()
    if defect == "old_compound_id":
        _compounds(saved)["old"]["id"] = "0" * 32
    elif defect == "old_mapping_id":
        _mappings(saved)[0]["id"] = "0" * 32
    elif defect == "old_compound_unknown":
        _compounds(saved)["old"]["opaque"]["retained"] = False
    elif defect == "old_mapping_unknown":
        _mappings(saved)[0]["opaque"]["retained"] = False
    elif defect == "new_compound_unknown":
        _compounds(saved)[ROOT]["unknown"] = "private unexpected content"
    elif defect == "new_mapping_unknown":
        _mappings(saved)[1]["unknown"] = "private unexpected content"
    elif defect == "parameter":
        _compounds(saved)[TRANSFORM]["compositions"][0]["parameters"][0]["value"] = (
            "private changed parameter"
        )
    elif defect == "composition":
        _compounds(saved)[TRANSFORM]["compositions"][0]["order"] = 1
    elif defect == "source_ui":
        _compounds(saved)[TRANSFORM]["sourceUiId"] = "wrong-source"
    elif defect == "order":
        _compounds(saved)[TRANSFORM]["order"] = True
    elif defect == "definition":
        _compounds(saved)[TRANSFORM]["transformDefinitionId"] = "0" * 32
    elif defect == "prescription":
        _compounds(saved)[ROOT]["prescription"] = "{{ui-2.status}}"
    elif defect == "source_alias":
        _mappings(saved)[1]["sourceAlias"] = "0" * 32
    elif defect == "target_alias":
        _mappings(saved)[1]["targetAlias"] = "0" * 32
    elif defect == "owner_definition":
        saved["actionInstances"][0]["actionTypeSysId"] = "0" * 32
    elif defect == "owner_uid":
        saved["actionInstances"][0]["uiUniqueIdentifier"] = "wrong-call"
    elif defect == "alias_ids":
        saved["actionInstances"][0]["aliasIds"] = [SOURCE]
    elif defect == "top_level":
        saved["unrelated_opaque"] = "private root content"
    elif defect == "new_id_uppercase":
        _compounds(saved)[ROOT]["id"] = ROOT_RECORD.upper()
    elif defect == "new_id_nonstring":
        _compounds(saved)[ROOT]["id"] = {"value": ROOT_RECORD}
    elif defect == "duplicate_transform_id":
        _compounds(saved)[ROOT]["id"] = OLD_COMPOUND
    else:
        _mappings(saved)[1]["id"] = OLD_MAPPING
    with pytest.raises(CapabilityError) as failure:
        await semantics.verify_saved(
            ReadbackClient(saved),
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )
    assert failure.value.code == "UNKNOWN_OUTCOME"
    assert "private" not in str(failure.value)


@pytest.mark.parametrize(
    "surface,field,value",
    [
        ("transform", "id", "C" * 32),
        ("transform", "attachedTo", {}),
        ("transform", "scopeId", True),
        ("connection", "id", "invalid"),
        ("connection", "sourceId", {"value": FLOW}),
        ("connection", "sourceTable", "sys_user.name"),
        ("connection", "sourceTable", None),
    ],
)
async def test_new_known_metadata_requires_canonical_ids_and_native_types(
    surface, field, value
):
    expected, saved, manifest = _case()
    row = _compounds(saved)[ROOT] if surface == "transform" else _mappings(saved)[1]
    row[field] = value
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


@pytest.mark.parametrize(
    "manifest", [None, {}, {"transforms": []}, {"connections": []}]
)
async def test_missing_manifest_never_infers_new_identity_assignments(manifest):
    expected, saved, _ = _case()
    client = ReadbackClient(saved)
    bound = await authoring_ownership.bind(client, expected, saved, manifest)
    assert bound == expected and bound is not expected
    assert not client.flow_reads
    with pytest.raises(CapabilityError):
        await semantics.verify_saved(
            client,
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )


@pytest.mark.parametrize(
    "manifest",
    [
        {"transforms": [ROOT, ROOT]},
        {"transforms": ["old"]},
        {"transforms": ["missing"]},
        {"transforms": [ROOT], "unknown": []},
        {"connections": [{"component_ui_id": None, "source_alias": SOURCE}] * 2},
        {"connections": [{"component_ui_id": None, "source_alias": "0" * 32}]},
        {"connections": [{"component_ui_id": "missing", "source_alias": SOURCE}]},
        {
            "connections": [
                {"component_ui_id": None, "source_alias": SOURCE, "id": MAPPING}
            ]
        },
        {"connections": [{"component_ui_id": None, "source_alias": SOURCE.upper()}]},
    ],
)
async def test_manifest_cannot_select_existing_ambiguous_or_unrequested_rows(manifest):
    expected, saved, _ = _case()
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


@pytest.mark.parametrize("field,value", [("id", "0" * 32), ("scope", "0" * 32)])
async def test_binding_requires_the_exact_requested_flow_and_scope(field, value):
    expected, saved, manifest = _case()
    saved[field] = value
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


@pytest.mark.parametrize(
    "surface,field,value",
    [
        ("transform", "id", "0" * 32),
        ("transform", "attachedTo", "0" * 32),
        ("transform", "scopeId", "0" * 32),
        ("connection", "id", "0" * 32),
        ("connection", "sourceTable", "another_fixture_owner"),
        ("connection", "sourceId", "0" * 32),
    ],
)
async def test_echo_and_independent_get_must_agree_on_every_assigned_field(
    surface, field, value
):
    expected, echo, manifest = _case()
    actual = copy.deepcopy(echo)
    row = _compounds(actual)[ROOT] if surface == "transform" else _mappings(actual)[1]
    row[field] = value
    with pytest.raises(CapabilityError) as failure:
        await semantics.verify_saved(
            ReadbackClient(actual),
            expected,
            echo,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )
    assert "echo.readback" in str(failure.value)
    assert failure.value.code == "UNKNOWN_OUTCOME"


@pytest.mark.parametrize(
    "surface,field,value",
    [
        ("transform", "attachedTo", "0" * 32),
        ("transform", "scopeId", "0" * 32),
        ("connection", "sourceTable", "existing_table"),
        ("connection", "sourceId", "0" * 32),
    ],
)
async def test_metadata_already_present_in_the_request_cannot_change(
    surface, field, value
):
    expected, saved, manifest = _case()
    planned = (
        _compounds(expected)[ROOT] if surface == "transform" else _mappings(expected)[1]
    )
    planned[field] = value
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


async def test_mapping_ids_are_unique_across_root_and_instance_scopes():
    expected, saved, manifest = _case()
    duplicate = {
        "aliasOverrideMappings": [
            {"id": MAPPING, "sourceAlias": SOURCE, "targetAlias": TARGET}
        ]
    }
    expected["actionInstances"][0]["aliasOverride"] = copy.deepcopy(duplicate)
    saved["actionInstances"][0]["aliasOverride"] = copy.deepcopy(duplicate)
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


@pytest.mark.parametrize("aliases", [[], [SOURCE, TARGET]])
async def test_new_action_alias_ids_use_exact_flow_get_without_table_or_definition_reads(
    aliases,
):
    expected, saved, manifest = _case(owner="action", new_call=True)
    manifest["alias_ids"] = ["ui-1"]
    saved["actionInstances"][0]["aliasIds"] = aliases
    before = copy.deepcopy(expected)
    client = ReadbackClient(saved)
    assert (
        await semantics.verify_saved(
            client,
            expected,
            saved,
            generated_ids={""},
            generated_authoring_rows=manifest,
        )
        == saved
    )
    assert expected == before
    assert client.flow_reads == [f"flow/{FLOW}"]


@pytest.mark.parametrize(
    "defect",
    [
        "old",
        "present",
        "duplicate",
        "missing_uid",
        "not_list",
        "bad_alias",
        "definition",
        "unknown",
    ],
)
async def test_new_action_alias_ids_do_not_authorize_old_rows_or_other_changes(defect):
    expected, saved, manifest = _case(owner="action", new_call=True)
    manifest["alias_ids"] = ["ui-1"]
    saved["actionInstances"][0]["aliasIds"] = [SOURCE]
    if defect == "old":
        expected["actionInstances"][0]["id"] = saved["actionInstances"][0]["id"]
    elif defect == "present":
        expected["actionInstances"][0]["aliasIds"] = []
    elif defect == "duplicate":
        manifest["alias_ids"] *= 2
    elif defect == "missing_uid":
        manifest["alias_ids"] = ["missing"]
    elif defect == "not_list":
        saved["actionInstances"][0]["aliasIds"] = SOURCE
    elif defect == "bad_alias":
        saved["actionInstances"][0]["aliasIds"] = [{"value": SOURCE}]
    elif defect == "definition":
        saved["actionInstances"][0]["actionTypeSysId"] = "0" * 32
    else:
        saved["actionInstances"][0]["unknown"] = "private unrelated field"
    with pytest.raises(CapabilityError) as failure:
        await semantics.verify_saved(
            ReadbackClient(saved),
            expected,
            saved,
            generated_ids={""},
            generated_authoring_rows=manifest,
        )
    assert "private" not in str(failure.value)


async def test_new_action_alias_ids_must_agree_between_echo_and_independent_get():
    expected, echo, manifest = _case(owner="action", new_call=True)
    manifest["alias_ids"] = ["ui-1"]
    echo["actionInstances"][0]["aliasIds"] = [SOURCE]
    actual = copy.deepcopy(echo)
    actual["actionInstances"][0]["aliasIds"] = [TARGET]
    with pytest.raises(CapabilityError) as failure:
        await semantics.verify_saved(
            ReadbackClient(actual),
            expected,
            echo,
            generated_ids={""},
            generated_authoring_rows=manifest,
        )
    assert "echo.readback" in str(failure.value)


async def test_native_observed_empty_slots_bind_only_for_explicit_new_compounds():
    expected, saved, manifest = _case()
    _compounds(saved)[ROOT].update(
        sourceUiId="", transformDefinitionId="", compositions=[]
    )
    _compounds(saved)[TRANSFORM]["prescription"] = ""
    before = copy.deepcopy(expected)
    assert (
        await semantics.verify_saved(
            ReadbackClient(saved),
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )
        == saved
    )
    assert expected == before


@pytest.mark.parametrize(
    "uid,field,value",
    [
        (ROOT, "sourceUiId", "another-root"),
        (ROOT, "transformDefinitionId", "0" * 32),
        (ROOT, "compositions", [{}]),
        (ROOT, "compositions", None),
        (TRANSFORM, "prescription", "{{ui-2.status}}"),
    ],
)
async def test_empty_slot_evidence_does_not_authorize_nonempty_or_different_types(
    uid, field, value
):
    expected, saved, manifest = _case()
    _compounds(saved)[uid][field] = value
    with pytest.raises(semantics.SemanticMismatch) as failure:
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)
    assert failure.value.path == "flow.pharmacy.pharmacyCompound"


async def test_observed_empty_slot_additions_are_not_normalized_for_old_rows():
    expected, saved, manifest = _case()
    _compounds(saved)["old"].update(
        sourceUiId="", transformDefinitionId="", compositions=[]
    )
    with pytest.raises(CapabilityError):
        await semantics.verify_saved(
            ReadbackClient(saved),
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )


@pytest.mark.parametrize("owner", ["action", "subflow"])
async def test_explicit_new_per_call_default_marker_binds_only_native_metadata(owner):
    expected, saved, manifest = _case(owner=owner)
    collection = "actionInstances" if owner == "action" else "subFlowInstances"
    _mappings(expected[collection][0])[1].update(sourceAlias="", targetAlias="")
    _mappings(saved[collection][0])[1].update(sourceAlias="", targetAlias="")
    manifest["connections"][0]["source_alias"] = ""
    assert (
        await semantics.verify_saved(
            ReadbackClient(saved),
            expected,
            saved,
            generated_ids=set(),
            generated_authoring_rows=manifest,
        )
        == saved
    )


@pytest.mark.parametrize("defect", ["root", "target", "duplicate", "existing"])
async def test_empty_source_manifest_cannot_authorize_root_nonempty_or_ambiguous_markers(
    defect,
):
    owner = "flow" if defect == "root" else "action"
    expected, saved, manifest = _case(owner=owner)
    expected_owner = expected if owner == "flow" else expected["actionInstances"][0]
    saved_owner = saved if owner == "flow" else saved["actionInstances"][0]
    _mappings(expected_owner)[1].update(sourceAlias="", targetAlias="")
    _mappings(saved_owner)[1].update(sourceAlias="", targetAlias="")
    manifest["connections"][0]["source_alias"] = ""
    if defect == "target":
        _mappings(expected_owner)[1]["targetAlias"] = TARGET
        _mappings(saved_owner)[1]["targetAlias"] = TARGET
    elif defect == "duplicate":
        _mappings(saved_owner).append(copy.deepcopy(_mappings(saved_owner)[1]))
    elif defect == "existing":
        _mappings(expected_owner)[1]["id"] = MAPPING
    with pytest.raises(CapabilityError):
        await authoring_ownership.bind(ReadbackClient(saved), expected, saved, manifest)


async def test_alias_getter_does_not_preempt_existing_step_serialization_comparison():
    expected, saved, manifest = _case(owner="action", new_call=True)
    manifest["alias_ids"] = ["ui-1"]
    planned = expected["actionInstances"][0]
    planned.update(tree.ui_metadata(tree.build_tree(expected))["ui-1"])
    saved["actionInstances"][0].update(aliasIds=[], order="1")
    before = copy.deepcopy(expected)
    client = ReadbackClient(saved)
    assert (
        await semantics.verify_saved(
            client,
            expected,
            saved,
            generated_ids={""},
            generated_authoring_rows=manifest,
        )
        == saved
    )
    assert expected == before
    assert client.flow_reads == [f"flow/{FLOW}"]
