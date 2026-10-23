"""Native scalar-variable request DTOs. Unknown metadata remains strict."""

import copy
import json
from pathlib import Path
from typing import override

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, editing, fields, semantics
from tests._complete_flow_fixture import STUDIO, create_request, edit_request
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup
from tests.test_flow_editing import base_flow
from tests.test_flow_variable_identity import VariableIdentityModel, identity_flow

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
                            for key in ("inputs", "flowVariables"):
                                for binding in step[key]:
                                    binding["parameter"] = copy.deepcopy(
                                        next(
                                            row
                                            for row in flow["flowVariables"]
                                            if row["name"] == binding["name"]
                                        )
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
    if native_existing == "nonempty_flow_block":
        assert preview["error"]["code"] == "UNAVAILABLE", preview
        assert "non-default or unknown settings" in preview["error"]["message"]
        assert model.calls.count(("PUT", STUDIO + "/flow")) == 1
        assert len(model.flow["flowVariables"]) == 1
        return
    edited = await apply(tools, preview)
    assert edited["status"] == "success" and edited["data"]["revision"] == "15", edited
    assert edited["data"]["refs"]["probe_log"]["sys_id"]
    assert model.flow is not None and len(model.flow["flowVariables"]) == 2
    assert model.calls.count(("POST", STUDIO + "/flow")) == 1
    assert model.calls.count(("PUT", STUDIO + "/flow")) == 2


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
