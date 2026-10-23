"""Source-grounded declaration identity projection. No historical echo or live writes."""

import copy
import json
from pathlib import Path
from typing import override

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._complete_flow_fixture import (
    STUDIO,
    StudioModel,
    create_request,
    edit_request,
)
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup
from tests.test_flow_editing import BASE, FLOW, base_flow

PROJECTION = json.loads(
    (
        Path(__file__).parent / "fixtures/flow_variable_identity_response.json"
    ).read_text()
)


def identity_flow(*, two=False):
    flow = base_flow()
    flow.update(actionInstances=[], subFlowInstances=[])
    field = copy.deepcopy(PROJECTION["variable"])
    field.pop("id")
    field["uiUniqueId"] = field["attributes"]["uiUniqueId"]
    flow["flowVariables"] = [field]
    if two:
        second = copy.deepcopy(field)
        second.update(name="second", uiUniqueId="00000000-0000-4000-8000-000000000002")
        second["attributes"]["uiUniqueId"] = second["uiUniqueId"]
        flow["flowVariables"].append(second)
    definition = {
        "id": "1" * 32,
        "type": "SETFLOWVARIABLES",
        "name": "Set Flow Variables",
        "inputs": [],
    }
    step = tree.new_logic_step(FLOW, definition, {})
    step.raw.update(id="2" * 32, order=1, parent="")
    step.raw.pop("data")
    step.raw["inputs"] = [
        {
            "id": "",
            "name": row["name"],
            "value": "synthetic assignment",
            "parameter": copy.deepcopy(row),
        }
        for row in flow["flowVariables"]
    ]
    step.raw["flowVariables"] = copy.deepcopy(step.raw["inputs"])
    flow["flowLogicInstances"] = [step.raw]
    manifest = [
        {
            "collection": "flowVariables",
            "name": row["name"],
            "uiUniqueId": row["uiUniqueId"],
        }
        for row in flow["flowVariables"]
    ]
    return flow, manifest


def stored_identities(flow):
    """Only the observed identity projection, not an invented full server serializer."""
    owners = {}
    for index, row in enumerate(flow["flowVariables"]):
        uid = row.pop("uiUniqueId", row.get("attributes", {}).get("uiUniqueId"))
        row.setdefault(
            "id", PROJECTION["variable"]["id"] if index == 0 else f"{index:032x}"
        )
        owners[uid] = row
    for collection in ("flowLogicInstances", "componentInstances"):
        for step in flow.get(collection, []):
            if step.get("flowLogicDefinition", {}).get("type") != "SETFLOWVARIABLES":
                continue
            for key in ("inputs", "flowVariables"):
                for row in step[key]:
                    parameter = row["parameter"]
                    uid = parameter.pop(
                        "uiUniqueId", parameter.get("attributes", {}).get("uiUniqueId")
                    )
                    parameter["id"] = owners[uid]["id"]
    return flow


@pytest.mark.parametrize("two", [False, True])
def test_saved_attribute_identity_and_parameter_database_id_match_the_request(two):
    expected, manifest = identity_flow(two=two)
    actual = stored_identities(copy.deepcopy(expected))
    assert (
        actual["flowLogicInstances"][0]["inputs"][0]["parameter"]
        == PROJECTION["assignment_parameter"]
    )
    before = copy.deepcopy((expected, actual))
    semantics.compare_saved(
        expected, actual, generated_ids=set(), generated_fields=manifest
    )
    assert (expected, actual) == before


@pytest.mark.parametrize(
    "defect",
    [
        "name",
        "duplicate_name",
        "duplicate_ui",
        "duplicate_database",
        "ui_conflict",
        "parameter_database",
        "parameter_name",
        "parameter_schema",
        "value",
        "default",
        "opaque",
    ],
)
def test_attribute_identity_does_not_hide_real_declaration_or_binding_changes(defect):
    expected, manifest = identity_flow(two=True)
    actual = stored_identities(copy.deepcopy(expected))
    rows = actual["flowVariables"]
    binding = actual["flowLogicInstances"][0]["inputs"][0]
    if defect == "name":
        rows[0]["name"] = "message_message"
    elif defect == "duplicate_name":
        rows[1]["name"] = rows[0]["name"]
    elif defect == "duplicate_ui":
        rows[1]["attributes"]["uiUniqueId"] = rows[0]["attributes"]["uiUniqueId"]
    elif defect == "duplicate_database":
        rows[1]["id"] = rows[0]["id"]
    elif defect == "ui_conflict":
        rows[0]["uiUniqueId"] = "different"
    elif defect == "parameter_database":
        binding["parameter"]["id"] = "f" * 32
    elif defect == "parameter_name":
        binding["parameter"]["name"] = "second"
    elif defect == "parameter_schema":
        binding["parameter"]["type"] = "integer"
    elif defect == "value":
        binding["value"] = "changed"
    elif defect == "default":
        rows[0]["defaultValue"] = "changed"
    else:
        rows[0]["vendorSetting"] = {"private": "not echoed"}
    actual["flowLogicInstances"][0]["flowVariables"] = copy.deepcopy(
        actual["flowLogicInstances"][0]["inputs"]
    )
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(
            expected, actual, generated_ids=set(), generated_fields=manifest
        )


async def test_assigned_variable_id_must_agree_between_echo_and_independent_get(
    settings,
):
    expected, manifest = identity_flow(two=True)
    echo = stored_identities(copy.deepcopy(expected))
    actual = copy.deepcopy(echo)
    actual["flowVariables"][0]["id"] = "f" * 32
    for key in ("inputs", "flowVariables"):
        actual["flowLogicInstances"][0][key][0]["parameter"]["id"] = "f" * 32
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(semantics.CapabilityError, match="echo.readback"):
            await semantics.verify_saved(
                client, expected, echo, generated_ids=set(), generated_fields=manifest
            )


class VariableIdentityModel(StudioModel):
    """The live-observed identity shape on the existing offline full-flow HTTP model."""

    def __init__(self):
        super().__init__()
        self.field_readback_race = None

    @override
    def handle(self, request):
        response = super().handle(request)
        if (
            request.method == "PUT"
            and request.url.path == STUDIO + "/flow"
            and response.status_code == 200
        ):
            echo = response.json()["result"]["data"]
            stored_identities(echo)
            assert self.flow is not None
            stored_identities(self.flow)
            if self.field_readback_race == "id":
                self.flow["flowVariables"][0]["id"] = "f" * 32
                stored_identities(self.flow)
            elif self.field_readback_race == "ui":
                self.flow["flowVariables"][0]["attributes"]["uiUniqueId"] = "different"
            elif self.field_readback_race == "pill":
                self.flow["actionInstances"][0]["inputs"][0]["value"] = (
                    "{{flow_variable.different}}"
                )
            elif self.field_readback_race == "stage":
                next(iter(self.flow["stages"].values()))["value"] = "different"
            return httpx2.Response(
                200, json={"result": {"data": echo}}, request=request
            )
        return response


@pytest.mark.parametrize("two", [False, True])
async def test_full_create_and_later_edit_with_attribute_only_variable_identity(
    settings, two
):
    _, coordinator, tools = setup(settings)
    model = VariableIdentityModel()
    model.component_mirror = True
    model.install()
    request = create_request()
    if two:
        request["edits"].insert(
            2,
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "second",
                "label": "Second",
                "type": "string",
            },
        )
        for edit in request["edits"]:
            if edit["op"] == "set_variables":
                edit["inputs"]["second"] = "Second synthetic assignment"
            elif edit.get("ref") == "source":
                edit["inputs"]["message"] = (
                    "{{flow_variable.message}} / {{flow_variable.second}}"
                )
    preview = decode_response(await tools["flow_preview"](**request))
    created = await apply(tools, preview)
    assert created["status"] == "success", created
    assert (
        created["data"]["revision"] == "14"
        and created["data"]["refs"]["source"]["sys_id"]
    )
    assert model.flow is not None
    variable = model.flow["flowVariables"][0]
    assert "uiUniqueId" not in variable
    assert (
        variable["attributes"]["uiUniqueId"]
        == preview["data"]["summary"]["fields"]["flowVariables"][0]["uiUniqueId"]
    )
    assert len({row["id"] for row in model.flow["flowVariables"]}) == (2 if two else 1)
    assert (
        model.flow["stages"]
        and not model.flow["active"]
        and not model.flow["isPublished"]
    )
    result = await apply(
        tools,
        decode_response(await tools["flow_preview"](**edit_request(created["data"]))),
    )
    assert result["status"] == "success" and result["data"]["revision"] == "15", result
    assert len(model.bodies) == 3 and len(coordinator.store) == 0


@pytest.mark.parametrize("race", ["id", "ui", "pill", "stage"])
async def test_variable_identity_failure_preserves_partial_create_and_consumed_token(
    settings, race
):
    _, coordinator, tools = setup(settings)
    model = VariableIdentityModel()
    model.field_readback_race = race
    model.install()
    preview = decode_response(await tools["flow_preview"](**create_request()))
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    outcome = result["error"]["outcome"]
    assert (
        outcome["sys_id"] == FLOW
        and outcome["header"] == "verified"
        and outcome["definition"] == "unknown"
    )
    assert model.calls.count(("POST", STUDIO + "/flow")) == 1
    assert model.calls.count(("PUT", STUDIO + "/flow")) == 1
    assert (await apply(tools, preview))["status"] == "error"
    assert len(model.bodies) == 2 and len(coordinator.store) == 0


def test_existing_database_identity_remains_strict_with_attribute_only_ui():
    source, _ = identity_flow()
    expected = stored_identities(source)
    actual = copy.deepcopy(expected)
    actual["flowVariables"][0]["id"] = "f" * 32
    stored_identities(actual)
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(expected, actual, generated_ids=set())
