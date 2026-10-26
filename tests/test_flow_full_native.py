"""Whole native creation and follow-up editing, not primitive DTO passthroughs."""

import copy

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import (
    calls,
    data,
    editing,
    fields,
    native,
    semantics,
    tree,
)
from servicenow_mcp.platform.client import ServiceNowClient
from tests._native_full_fixture import (
    CAPTURE,
    NativeFullModel,
    create_request,
    edit_request,
)
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup


def differences(left, right, path="flow"):
    """Enumerate the whole object. Do not stop at the first differing index."""
    if type(left) is not type(right):
        return [(path, "type")]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                result.append((path + "." + key, "missing"))
            else:
                result.extend(differences(left[key], right[key], path + "." + key))
        return result
    if isinstance(left, list):
        result = [] if len(left) == len(right) else [(path, "count")]
        for i, (a, b) in enumerate(zip(left, right)):
            result.extend(differences(a, b, f"{path}[{i}]"))
        return result
    return [] if left == right else [(path, "value")]


def test_all_fresh_action_conditional_and_trigger_fields_match_the_actual_capture():
    """Reconstruct constructors, not the unavailable historical full request."""
    flow = copy.deepcopy(CAPTURE["flow"])
    all_differences = []
    for actual in flow["actionInstances"]:
        fresh = calls.step(
            flow,
            CAPTURE["action"],
            {r["name"]: r["value"] for r in actual["inputs"]},
            subflow=False,
            uid=actual["uiUniqueIdentifier"],
        )
        expected = native.action(fresh.raw, flow)
        # The native constructor omits getter-owned alias metadata.
        assert "aliasIds" not in expected
        assert actual["aliasIds"] == []
        expected["aliasIds"] = []
        expected.update({key: actual[key] for key in ("id", "order", "parent")})
        all_differences.extend(
            differences(expected, actual, "ACTION[" + actual["order"] + "]")
        )
    for actual in flow["flowLogicInstances"]:
        definition = next(
            r for r in CAPTURE["logic"] if r["id"] == actual["definitionId"]
        )
        assignment = definition["type"] == "SETFLOWVARIABLES"
        fresh = tree.new_logic_step(
            flow["id"],
            definition,
            {} if assignment else {r["name"]: r["value"] for r in actual["inputs"]},
        )
        if assignment:
            variable = flow["flowVariables"][0]
            parameter = fields._string_variable_dto(
                {
                    "name": "message",
                    "label": "Message",
                    "uiUniqueId": variable["attributes"]["uiUniqueId"],
                }
            )
            parameter["id"] = variable["id"]
            row = {
                "id": "",
                "name": "message",
                "parameter": parameter,
                "children": [],
                "scriptActive": False,
            }
            data.set_value(row, actual["inputs"][0]["value"])
            fresh.raw["inputs"] = [row]
            editing._native_variable_step(fresh)
            expected = fresh.raw
        else:
            expected = native.logic(fresh.raw)
        expected.update(
            {
                key: actual[key]
                for key in (
                    "id",
                    "order",
                    "parent",
                    "uiUniqueIdentifier",
                    "flowBlockId",
                )
            }
        )
        if assignment:
            # The existing source-proven String presentation slots are equivalent.
            semantics._variable_type_labels({"flowLogicInstances": [expected]})
            semantics._variable_type_labels({"flowLogicInstances": [actual]})
        all_differences.extend(differences(expected, actual, definition["type"]))
    definition = editing.trigger_catalog(CAPTURE["trigger_categories"])[
        "service_catalog"
    ]
    expected = native.catalog_trigger(
        editing.new_trigger(flow, definition, {}), definition
    )
    expected["id"] = flow["triggerInstances"][0]["id"]
    all_differences.extend(
        differences(expected, flow["triggerInstances"][0], "TRIGGER")
    )
    # The retained attempt sent the getter-only UI label. 777a618 now sends
    # type_label (471829). Report that historical difference explicitly, then
    # construct the single getter slot from the corrected writable field.
    assert all_differences == [
        ("TRIGGER.inputs[0].parameter.uiDisplayTypeLabel", "missing")
    ]
    wire = native.rest_payload({"active": False, "triggerInstances": [expected]})
    corrected = copy.deepcopy(flow["triggerInstances"][0])
    corrected["inputs"][0]["parameter"]["uiDisplayTypeLabel"] = wire[
        "triggerInstances"
    ][0]["inputs"][0]["parameter"]["type_label"]
    assert differences(expected, corrected, "TRIGGER") == []


async def test_whole_native_seven_step_roundtrip_then_reorder_insert_update(
    settings, monkeypatch
):
    original_compare = semantics.compare_saved
    compared = []

    def capture_compare(expected, actual, **kwargs):
        original_compare(expected, actual, **kwargs)
        nodes = len(expected["actionInstances"]) + len(expected["flowLogicInstances"])
        if nodes in {7, 8}:
            compared.append(
                (
                    nodes,
                    bool(kwargs.get("allow_updated_advance")),
                    kwargs["generated_ids"],
                )
            )

    monkeypatch.setattr(semantics, "compare_saved", capture_compare)
    _, _, tools = setup(settings)
    model = NativeFullModel()
    model.install()
    preview = decode_response(await tools["flow_preview"](**create_request()))
    created = await apply(tools, preview)
    assert created["status"] == "success", created.get("error", {}).get(
        "message", created
    )
    assert model.flow is not None
    parameter = model.bodies[1]["triggerInstances"][0]["inputs"][0]["parameter"]
    assert parameter["type_label"] == "Choice" and "uiDisplayTypeLabel" not in parameter
    assert (
        len(model.flow["actionInstances"]) == 4
        and len(model.flow["flowLogicInstances"]) == 3
    )
    assert compared == [(7, True, {""}), (7, True, {""}), (7, False, set())]
    model.flow["actionInstances"][0]["metadata"]["opaqueExisting"] = {"preserve": True}
    model.flow["flowLogicInstances"][1]["metadata"]["opaqueExisting"] = {
        "preserve": True
    }
    before = copy.deepcopy(model.flow)
    changed = await apply(
        tools,
        decode_response(await tools["flow_preview"](**edit_request(created["data"]))),
    )
    assert changed["status"] == "success", changed.get("error", {}).get(
        "message", changed
    )
    assert len(model.flow["actionInstances"]) == 5
    after = model.flow
    assert (
        before["inputs"] == after["inputs"]
        and before["flowVariables"] == after["flowVariables"]
    )
    assert before["triggerInstances"] == after["triggerInstances"]
    old = {s.uid: s.raw for s in tree.walk(tree.build_tree(before))}
    new = {s.uid: s.raw for s in tree.walk(tree.build_tree(after))}
    for uid, row in old.items():
        assert row["id"] == new[uid]["id"]
        assert row["metadata"] == new[uid]["metadata"]
        if row.get("flowBlockId"):
            assert row["flowBlockId"] == new[uid]["flowBlockId"]
    assert (
        not after["active"]
        and not after["isPublished"]
        and not after["masterSnapshotId"]
    )
    assert compared[-3:] == [(8, True, {""}), (8, True, {""}), (8, False, set())]


def test_native_trigger_wire_label_mapping_is_scoped_and_rejects_conflicting_aliases():
    expected = copy.deepcopy(CAPTURE["flow"])
    expected["triggerInstances"][0]["inputs"][0]["parameter"]["uiDisplayTypeLabel"] = (
        "Choice"
    )
    before = copy.deepcopy(expected)
    wire = native.rest_payload(expected)
    assert wire["actionInstances"] == expected["actionInstances"]
    assert expected == before
    parameter = wire["triggerInstances"][0]["inputs"][0]["parameter"]
    assert parameter["type_label"] == "Choice" and "uiDisplayTypeLabel" not in parameter
    expected["triggerInstances"][0]["inputs"][0]["parameter"]["type_label"] = (
        "Conflicting"
    )
    with pytest.raises(semantics.CapabilityError, match="type labels conflict"):
        native.rest_payload(expected)


async def test_conflicting_wire_labels_reject_before_any_effect(settings):
    expected = copy.deepcopy(CAPTURE["flow"])
    expected["triggerInstances"][0]["inputs"][0]["parameter"]["uiDisplayTypeLabel"] = (
        "Choice"
    )
    expected["triggerInstances"][0]["inputs"][0]["parameter"]["type_label"] = (
        "Conflicting"
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(
            semantics.CapabilityError, match="type labels conflict"
        ) as error:
            await semantics.save_draft(client, expected)
    assert error.value.code == "UNAVAILABLE"


async def test_exact_input_owner_failure_names_the_actual_record_field(settings):
    _, _, tools = setup(settings)
    model = NativeFullModel()
    # A wrong storage element is a real declaration mismatch. The storage name
    # is the shared virtual table and is deliberately different from DTO.name.
    model.input_record_element = "synthetic_wrong_element"
    model.install()
    result = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert (
        "flow.inputs[0].owner.element TRIGGER_INPUT_OWNER_MISMATCH"
        in result["error"]["message"]
    )


@pytest.mark.parametrize(
    "drift",
    [
        "value",
        "type",
        "default",
        "reference",
        "name",
        "pill",
        "order",
        "parent",
        "unknown_action",
        "unknown_logic",
        "unknown_trigger",
        "unknown_root",
        "block",
        "input_id",
        "variable_id",
    ],
)
async def test_actual_native_shape_semantic_and_unknown_metadata_drift_is_rejected(
    settings, drift
):
    _, _, tools = setup(settings)
    model = NativeFullModel()
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    assert model.flow is not None
    actual = copy.deepcopy(model.flow)
    assert actual is not None
    a = actual["actionInstances"][0]
    b = a["inputs"][1]
    if drift == "value":
        b["value"] = "Changed"
    elif drift == "type":
        b["parameter"]["type"] = "integer"
    elif drift == "default":
        b["parameter"]["defaultValue"] = "Changed"
    elif drift == "reference":
        actual["inputs"][1]["reference"] = "sys_user"
    elif drift == "name":
        b["name"] = "changed"
    elif drift == "pill":
        b["value"] = "{{flow_variable.changed}}"
    elif drift == "order":
        a["order"] = "7"
    elif drift == "parent":
        a["parent"] = actual["flowLogicInstances"][1]["uiUniqueIdentifier"]
    elif drift == "unknown_action":
        a["actionType"]["outputs"][0]["attributes"]["unknown"] = None
    elif drift == "unknown_logic":
        actual["flowLogicInstances"][1]["metadata"]["unknown"] = False
    elif drift == "unknown_trigger":
        actual["triggerInstances"][0]["outputs"][0]["parameter"]["attributes"][
            "unknown"
        ] = False
    elif drift == "unknown_root":
        actual["attributes"]["unknown"] = {}
    elif drift == "block":
        actual["flowLogicInstances"][1]["flowBlockId"] = "f" * 32
    elif drift == "input_id":
        actual["inputs"][1]["id"] = "f" * 32
    else:
        actual["flowVariables"][0]["id"] = "f" * 32
    with pytest.raises(semantics.CapabilityError):
        semantics.compare_saved(model.flow, actual, generated_ids=set())


@pytest.mark.parametrize(
    "field",
    [
        "sys_id",
        "flow",
        "ui_id",
        "sys_class_name",
        "logic_definition",
        "block",
        "block.sys_id",
    ],
)
@pytest.mark.parametrize("kind", ["IF", "ELSE", "SETFLOWVARIABLES"])
async def test_new_conditional_and_assignment_blocks_require_exact_owner_records(
    settings, field, kind
):
    _, _, tools = setup(settings)
    model = NativeFullModel()
    model.owner_defect = field
    model.owner_kind = kind
    model.install()
    result = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert (
        result["status"] == "error" and result["error"]["code"] == "UNKNOWN_OUTCOME"
    ), result
    assert "BLOCK_OWNERSHIP_MISMATCH" in result["error"]["message"]
    assert len(model.bodies) == 2
