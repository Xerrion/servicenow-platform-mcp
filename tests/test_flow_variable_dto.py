"""Native scalar-variable request DTOs. Unknown metadata remains strict."""

import copy
import json
from pathlib import Path
from typing import override

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, editing, fields, semantics
from servicenow_mcp.platform.client import ServiceNowClient
from tests._complete_flow_fixture import STUDIO, create_request, edit_request
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup
from tests.test_flow_editing import BASE, FLOW, base_flow
from tests.test_flow_variable_identity import (
    VariableIdentityModel,
    identity_flow,
    stored_identities,
)

ASSIGNMENT = json.loads(
    (Path(__file__).parent / "fixtures/flow_native_assignment_dto.json").read_text()
)

DTO = json.loads(
    (Path(__file__).parent / "fixtures/flow_string_variable_dto.json").read_text()
)["variable"]
UID = DTO["attributes"]["uiUniqueId"]


def request_field():
    flow = base_flow()
    flow["flowVariables"] = []
    fields.edit_field(
        flow,
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "message",
            "label": "Message",
            "type": "string",
        },
        [],
        UID,
    )
    return flow["flowVariables"][0]


def pair():
    expected, manifest = identity_flow()
    row = request_field()
    expected["flowVariables"] = [row]
    for key in ("inputs", "flowVariables"):
        expected["flowLogicInstances"][0][key][0]["parameter"] = copy.deepcopy(row)
    actual = copy.deepcopy(expected)
    actual["flowVariables"] = [copy.deepcopy(DTO)]
    for key in ("inputs", "flowVariables"):
        actual["flowLogicInstances"][0][key][0]["parameter"] = copy.deepcopy(DTO)
    return expected, actual, manifest


def test_new_string_variable_is_the_native_request_dto_not_an_ignored_response_diff():
    wanted = copy.deepcopy(DTO)
    wanted.pop("id")
    wanted["uiUniqueId"] = UID
    assert request_field() == wanted


def test_complete_native_variable_dto_roundtrip():
    expected, actual, manifest = pair()
    before = copy.deepcopy((expected, actual))
    semantics.compare_saved(
        expected, actual, generated_ids=set(), generated_fields=manifest
    )
    assert (expected, actual) == before


@pytest.mark.parametrize(
    "change",
    [
        "provider",
        "ui_type",
        "ui_label",
        "remove_provider",
        "unknown_added",
        "unknown_removed",
        "nonempty_source_missing",
        "reference",
        "default",
        "value",
        "name",
        "wrong_location",
    ],
)
def test_native_variable_metadata_remains_semantic_and_location_specific(change):
    expected, actual, manifest = pair()
    row = actual["flowVariables"][0]
    if change == "provider":
        row["attributes"]["element_mapping_provider"] = "different.Provider"
    elif change == "ui_type":
        row["attributes"]["uiType"] = "reference"
    elif change == "ui_label":
        row["attributes"]["uiTypeLabel"] = "Document ID"
    elif change == "remove_provider":
        row["attributes"].pop("element_mapping_provider")
    elif change == "unknown_added":
        row["attributes"]["unknown"] = "not ignored"
    elif change == "unknown_removed":
        expected["flowVariables"][0]["attributes"]["unknown"] = "not ignored"
    elif change == "nonempty_source_missing":
        expected["flowVariables"][0]["attributes"]["sourceId"] = "b" * 32
    elif change == "reference":
        row["reference"] = "sys_user"
    elif change == "default":
        row["defaultValue"] = "not empty"
    elif change == "value":
        row["value"] = "not empty"
    elif change == "name":
        row["name"] = "message_message"
    else:
        actual["flowLogicInstances"][0]["attributes"] = {
            "uiType": "string",
            "element_mapping_provider": DTO["attributes"]["element_mapping_provider"],
        }
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(
            expected, actual, generated_ids=set(), generated_fields=manifest
        )


class VariableDTOModel(VariableIdentityModel):
    """Actual saved DTO shape for the existing offline HTTP model."""

    @override
    def handle(self, request):
        prefix = "/api/now/table/sys_hub_flow_logic_instance_v2/"
        if request.method == "GET" and request.url.path.startswith(prefix):
            self.calls.append((request.method, request.url.path))
            assert self.flow is not None
            row = next(
                r
                for r in self.flow["flowLogicInstances"]
                if r["id"] == request.url.path.removeprefix(prefix)
            )
            return httpx2.Response(
                200,
                json={
                    "result": {
                        "sys_id": row["id"],
                        "flow": self.flow["id"],
                        "ui_id": row["uiUniqueIdentifier"],
                        "sys_class_name": "sys_hub_flow_logic_instance_v2",
                        "logic_definition": row["definitionId"],
                        "block": row["flowBlockId"],
                        "block.sys_id": row["flowBlockId"],
                    }
                },
                request=request,
            )
        response = super().handle(request)
        if (
            request.method == "PUT"
            and request.url.path == STUDIO + "/flow"
            and response.status_code == 200
        ):
            echo = response.json()["result"]["data"]
            assert self.flow is not None
            for flow in (echo, self.flow):
                for index, row in enumerate(flow["flowVariables"]):
                    if row["type"] == "string":
                        native = copy.deepcopy(DTO)
                        native.update(
                            id=row["id"], name=row["name"], label=row["label"]
                        )
                        native["attributes"]["uiUniqueId"] = row["attributes"][
                            "uiUniqueId"
                        ]
                        flow["flowVariables"][index] = native
                for section in ("flowLogicInstances", "componentInstances"):
                    for step in flow.get(section, []):
                        if (
                            step.get("flowLogicDefinition", {}).get("type")
                            == "SETFLOWVARIABLES"
                        ):
                            step.setdefault(
                                "flowBlockId", f"{int(step['id'], 16) + 4096:032x}"
                            )
            return httpx2.Response(
                200, json={"result": {"data": echo}}, request=request
            )
        return response


@pytest.mark.parametrize("native_existing", [False, True, "nonempty_flow_block"])
async def test_full_create_then_one_incremental_edit_with_second_native_variable(
    settings,
    native_existing,
):
    _, _, tools = setup(settings)
    model = VariableDTOModel()
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    assert model.flow is not None
    if native_existing:
        for row in model.flow["actionInstances"]:
            for key in ("isNew", "isDirty", "errorHandlingInstance"):
                row.pop(key, None)
            row.update(
                displayText="",
                deleted=False,
                metadata={},
                comment="",
                generationSource="",
                uiComponentIndex=0,
                snapshot=False,
                compiledSnapshot="",
                aliasIds=[],
            )
            for index, binding in enumerate(row["inputs"]):
                row["inputs"][index] = {
                    key: copy.deepcopy(binding[key])
                    for key in (
                        "id",
                        "name",
                        "value",
                        "displayValue",
                        "children",
                        "scriptActive",
                        "parameter",
                    )
                }
                row["inputs"][index]["parameter"] = editing._native_parameter(
                    binding["parameter"]
                )
        for row in model.flow["flowLogicInstances"]:
            if row["flowLogicDefinition"]["type"] == "SETFLOWVARIABLES":
                for key in (
                    "isNew",
                    "isDirty",
                    "errorHandlingInstance",
                    "validation_errors",
                    "data",
                ):
                    row.pop(key, None)
                row.update(
                    name="SetFlowVariables",
                    internalName="",
                    displayText="",
                    deleted=False,
                    metadata={},
                    comment="",
                    generationSource="",
                    uiComponentIndex=0,
                    snapshot=False,
                    outputsToAssign=[],
                    variables=[],
                    errors=[],
                    connectedTo="",
                    workflowReference="",
                    workflowInputs=[],
                    workflowInformation={},
                    decisionTableReference="",
                    decisionTableInputs=[],
                    dynamicInputs=[],
                    dynamicOutputs=[],
                    decisionTableInformation={},
                    afterTopLevelCatch=False,
                    goBackTo=False,
                    updateFlowTemplate=False,
                    quiescence=row["flowLogicDefinition"].get("quiescence"),
                    **{"continue": False, "break": False},
                )
                if native_existing == "nonempty_flow_block":
                    # The live retained fixture has this nonempty native field.
                    # Its ownership contract is not established by the DTO map.
                    row["flowBlockId"] = "e" * 32
                    row["unknownExistingMetadata"] = {"preserve": "synthetic"}
    original_assignments = copy.deepcopy(
        [
            r
            for r in model.flow["flowLogicInstances"]
            if r["flowLogicDefinition"]["type"] == "SETFLOWVARIABLES"
        ]
    )
    request = edit_request(created["data"])
    request["edits"] = [
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "roundtrip_probe",
            "label": "Roundtrip probe",
            "type": "string",
        },
        {
            "op": "set_variables",
            "inputs": {"roundtrip_probe": "Synthetic DTO verification"},
            "before": created["data"]["refs"]["source"]["ui_id"],
        },
        *request["edits"][:1],
        {
            "op": "insert_action",
            "action_type_sys_id": model.action["id"],
            "parent": created["data"]["refs"]["branch"]["ui_id"],
            "ref": "probe_log",
            "inputs": {"message": "{{flow_variable.roundtrip_probe}}"},
        },
        {
            "op": "update_inputs",
            "step": created["data"]["refs"]["tail"]["ui_id"],
            "inputs": {"message": "Updated synthetic tail"},
        },
    ]
    preview = decode_response(await tools["flow_preview"](**request))
    edited = await apply(tools, preview)
    assert edited["status"] == "success" and edited["data"]["revision"] == "15", edited
    assert edited["data"]["refs"]["probe_log"]["sys_id"]
    assert model.flow is not None and len(model.flow["flowVariables"]) == 2
    assert model.calls.count(("POST", STUDIO + "/flow")) == 1
    assert model.calls.count(("PUT", STUDIO + "/flow")) == 2
    for original in original_assignments:
        saved = next(
            r for r in model.flow["flowLogicInstances"] if r["id"] == original["id"]
        )
        assert {
            k: v
            for k, v in saved.items()
            if k not in {"order", *editing.tree.UI_FIELDS}
        } == {
            k: v
            for k, v in original.items()
            if k not in {"order", *editing.tree.UI_FIELDS}
        }


def test_fresh_assignment_uses_catalog_and_native_defaults_without_saved_ownership():
    saved = copy.deepcopy(ASSIGNMENT["instance"])
    step = editing.tree.new_logic_step(
        saved["flowSysId"], saved["flowLogicDefinition"], {}
    )
    binding = {
        "id": "",
        "name": "message",
        "value": "Synthetic new assignment",
        "displayValue": "Synthetic new assignment",
        "children": [],
        "scriptActive": False,
        "parameter": copy.deepcopy(ASSIGNMENT["parameter"]),
    }
    step.raw["inputs"] = [binding]
    editing._native_variable_step(step)
    wanted = copy.deepcopy(saved)
    for key in ("id", "flowBlockId", "parent", "order"):
        wanted.pop(key)
    wanted.update(
        uiUniqueIdentifier=step.uid,
        inputs=[binding],
        flowVariables=[copy.deepcopy(binding)],
    )
    assert step.raw == wanted
    assert saved == ASSIGNMENT["instance"]


@pytest.mark.parametrize("key", ["type", "userCanRead", "inputs", "name", "id"])
def test_fresh_assignment_requires_an_independent_complete_catalog_definition(key):
    saved = ASSIGNMENT["instance"]
    step = editing.tree.new_logic_step(
        saved["flowSysId"], copy.deepcopy(saved["flowLogicDefinition"]), {}
    )
    step.raw["flowLogicDefinition"].pop(key)
    with pytest.raises(CapabilityError, match="catalog definition is incomplete"):
        editing._native_variable_step(step)


def test_fresh_assignment_does_not_discard_a_conflicting_nonempty_ui_identity():
    saved = ASSIGNMENT["instance"]
    step = editing.tree.new_logic_step(
        saved["flowSysId"], saved["flowLogicDefinition"], {}
    )
    parameter = copy.deepcopy(ASSIGNMENT["parameter"])
    parameter["uiUniqueId"] = "00000000-0000-4000-8000-000000000099"
    step.raw["inputs"] = [{"parameter": parameter}]
    with pytest.raises(CapabilityError, match="UI identities conflict"):
        editing._native_variable_step(step)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_owner",
        "wrong_definition",
        "wrong_type",
        "wrong_ui",
        "wrong_block",
        "missing_reference",
        "changed_inputs",
        "unknown_metadata",
        "readback_block",
    ],
)
async def test_new_assignment_block_relation_and_semantic_values_remain_strict(
    settings, change
):
    expected, manifest = identity_flow()
    step = editing.tree.Step(expected["flowLogicInstances"][0], "flowlogic")
    step.raw["flowLogicDefinition"]["userCanRead"] = True
    editing._native_variable_step(step)
    expected["flowLogicInstances"] = [step.raw]
    echo = stored_identities(copy.deepcopy(expected))
    row = echo["flowLogicInstances"][0]
    row.update(id="b" * 32, flowBlockId="e" * 32, order=1, parent="")
    expected["flowLogicInstances"][0].update(order=1, parent="")
    record = {
        "sys_id": row["id"],
        "flow": FLOW,
        "ui_id": row["uiUniqueIdentifier"],
        "sys_class_name": "sys_hub_flow_logic_instance_v2",
        "logic_definition": row["definitionId"],
        "block": row["flowBlockId"],
        "block.sys_id": row["flowBlockId"],
    }
    keys = {
        "wrong_owner": "flow",
        "wrong_definition": "logic_definition",
        "wrong_type": "sys_class_name",
        "wrong_ui": "ui_id",
        "wrong_block": "block",
        "missing_reference": "block.sys_id",
    }
    if change in keys:
        record[keys[change]] = "f" * 32
    elif change == "changed_inputs":
        row["inputs"][0]["value"] = "Unexpected value"
        row["flowVariables"] = copy.deepcopy(row["inputs"])
    elif change == "unknown_metadata":
        row["unknown"] = "Unexpected metadata"
    actual = copy.deepcopy(echo)
    if change == "readback_block":
        actual["flowLogicInstances"][0]["flowBlockId"] = "f" * 32
    http_mock.get(
        f"{BASE}/api/now/table/sys_hub_flow_logic_instance_v2/{row['id']}"
    ).respond(200, json={"result": record})
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(
            CapabilityError, match="Independent saved-flow verification"
        ):
            await semantics.verify_saved(
                client, expected, echo, generated_ids={""}, generated_fields=manifest
            )


@pytest.mark.parametrize(
    "change", ["unknown_setting", "metadata", "alias", "provider", "input_name"]
)
def test_native_instance_profile_requires_neutral_settings_and_independent_schema(
    change,
):
    flow = base_flow()
    definition = {
        "id": "b" * 32,
        "name": "Log",
        "inputs": [
            {
                "id": "c" * 32,
                "name": "message",
                "type": "string",
                "attributes": {"element_mapping_provider": "Expected.Provider"},
            }
        ],
    }
    step = calls.step(
        flow, definition, {"message": "Synthetic"}, subflow=False, uid="new-ui"
    )
    prototype = copy.deepcopy(step.raw)
    for key in ("isNew", "isDirty", "errorHandlingInstance"):
        prototype.pop(key, None)
    prototype.update(
        id="d" * 32,
        displayText="",
        deleted=False,
        metadata={},
        comment="",
        generationSource="",
        uiComponentIndex=0,
        snapshot=False,
        compiledSnapshot="",
        aliasIds=[],
    )
    prototype["inputs"] = [
        {
            key: copy.deepcopy(prototype["inputs"][0][key])
            for key in (
                "id",
                "name",
                "value",
                "displayValue",
                "children",
                "scriptActive",
                "parameter",
            )
        }
    ]
    prototype["inputs"][0]["parameter"] = editing._native_parameter(
        prototype["inputs"][0]["parameter"]
    )
    flow["actionInstances"] = [prototype]
    if change == "unknown_setting":
        prototype["unknown"] = False
    elif change == "metadata":
        prototype["metadata"] = {"unknown": "not copied"}
    elif change == "alias":
        prototype["aliasIds"] = ["a" * 32]
    elif change == "provider":
        prototype["inputs"][0]["parameter"]["attributes"][
            "element_mapping_provider"
        ] = "Different.Provider"
    else:
        prototype["inputs"][0]["name"] = "different"
    with pytest.raises(CapabilityError, match="native"):
        editing._native_existing_step(flow, step)
