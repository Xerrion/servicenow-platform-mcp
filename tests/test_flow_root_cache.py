"""Native root cache projection. Never ignore authoritative or unknown settings."""

import copy
import json
from pathlib import Path
from typing import override

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import handlers, semantics, tree
from servicenow_mcp.platform.client import ServiceNowClient
from tests._complete_flow_fixture import STUDIO, create_request, edit_request
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup
from tests.test_flow_editing import BASE, FLOW, base_flow
from tests.test_flow_variable_dto import VariableDTOModel

DTO = json.loads(
    (Path(__file__).parent / "fixtures/flow_root_cache_dto.json").read_text()
)
FIELD = DTO["root_field"]


def native_flow():
    flow = base_flow()
    flow["label_cache"] = copy.deepcopy(DTO["native_cache"])
    flow[FIELD] = json.dumps(flow["label_cache"], ensure_ascii=False)
    flow.update(DTO["native_boundary"])
    return flow


def test_cache_string_encoding_and_native_nullable_fields_are_derived_only():
    expected = native_flow()
    actual = copy.deepcopy(expected)
    for row, fields in zip(actual["label_cache"], DTO["planned_nullable_fields"]):
        row.update(dict.fromkeys(fields))
    actual[FIELD] = json.dumps(actual["label_cache"], sort_keys=True, indent=2)
    before = copy.deepcopy((expected, actual))
    semantics.compare_saved(expected, actual, generated_ids=set())
    assert (expected, actual) == before


@pytest.mark.parametrize(
    "defect",
    [
        "invalid_json",
        "wrong_type",
        "mirror_value",
        "mirror_unknown",
        "real_type",
        "real_provider",
        "real_usage",
        "real_unknown",
        "nonempty_optional",
        "other_root",
        "wrong_scope",
        "boundary",
    ],
)
def test_derived_root_cache_never_hides_configuration_or_mirror_corruption(defect):
    expected = native_flow()
    actual = copy.deepcopy(expected)
    if defect == "invalid_json":
        actual[FIELD] = "not json"
    elif defect == "wrong_type":
        actual[FIELD] = 7
    elif defect.startswith("mirror"):
        mirror = json.loads(actual[FIELD])
        if defect == "mirror_value":
            mirror[0]["type"] = "reference"
        else:
            mirror[0]["unknown"] = False
        actual[FIELD] = json.dumps(mirror)
    elif defect == "other_root":
        actual["unknownRoot"] = False
    elif defect == "wrong_scope":
        expected["actionInstances"][0][FIELD] = "first"
        actual["actionInstances"][0][FIELD] = "second"
    elif defect == "boundary":
        actual["startingIndexOfErrorHandlingInstances"] = 99
    else:
        row = actual["label_cache"][1]
        if defect == "real_type":
            row["type"] = "integer"
        elif defect == "real_provider":
            row["attributes"]["element_mapping_provider"] = "Different.Provider"
        elif defect == "real_usage":
            row["usedInstances"] = {}
        elif defect == "real_unknown":
            row["unknown"] = False
        else:
            row["column_name"] = "meaningful"
        actual[FIELD] = json.dumps(actual["label_cache"])
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


def test_native_boundary_does_not_invent_an_editor_alias_for_an_ordinary_edit():
    original = native_flow()
    payload = copy.deepcopy(original)
    handlers.serialize(payload, tree.build_tree(original), original)
    assert "startOfErrorHandlingInstances" not in payload
    assert payload["startingIndexOfErrorHandlingInstances"] == -1


async def test_wire_omits_only_the_fixed_root_mirror_without_mutating_input(settings):
    flow = native_flow()
    flow["actionInstances"][0][FIELD] = "unrelated step metadata"
    before = copy.deepcopy(flow)
    captured = {}

    def save(request):
        captured.update(json.loads(request.content))
        result = copy.deepcopy(captured)
        result[FIELD] = json.dumps(result["label_cache"])
        return httpx2.Response(200, json={"result": {"data": result}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await semantics.save_draft(client, flow)
    assert FIELD not in captured
    assert captured["actionInstances"][0][FIELD] == "unrelated step metadata"
    assert flow == before


class NativeCacheModel(VariableDTOModel):
    """Native getter cache and omitted optional fields on the mock HTTP server."""

    @override
    def handle(self, request):
        response = super().handle(request)
        if (
            request.url.path in {STUDIO + "/flow", STUDIO + "/flow/" + FLOW}
            and response.status_code == 200
        ):
            result = response.json()["result"]
            if isinstance(result, dict) and isinstance(result.get("data"), dict):
                flow = result["data"]
                for value in (flow, self.flow):
                    if value is None:
                        continue
                    value.setdefault("startingIndexOfErrorHandlingInstances", -1)
                    for row in value["label_cache"]:
                        for key in (
                            "attributes",
                            "choices",
                            "column_name",
                            "parent_table_name",
                        ):
                            if row.get(key, False) is None:
                                row.pop(key)
                    value[FIELD] = json.dumps(
                        value["label_cache"], sort_keys=True, indent=1
                    )
                return httpx2.Response(
                    200, json={"result": {"data": flow}}, request=request
                )
        return response


async def test_native_cache_complete_creation_then_incremental_edit(settings):
    _, _, tools = setup(settings)
    model = NativeCacheModel()
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    edited = await apply(
        tools,
        decode_response(await tools["flow_preview"](**edit_request(created["data"]))),
    )
    assert edited["status"] == "success" and edited["data"]["revision"] == "15", edited
    assert all(FIELD not in body for body in model.bodies)
