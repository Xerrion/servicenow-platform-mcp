"""Source-compatible schema wrappers must not look like dependency drift.

These are synthetic projections, not captured target Studio responses.
Local docs/designer-bundle.min.js getInputsOutputs (227266-227287) wraps a
parameter. remapInstanceValues (156150-156185) also copies its fields outward.
subflowSelectedSaga (210122-210132) reads the selected snapshot with minimal
definition flags. buildNewSubflowInstance (156242-156250) retains its ID.
"""

import copy
from typing import Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import lifecycle, semantics
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_editing import BASE, FLOW, fixtures
from tests.test_flow_lifecycle import status_header, validation_flow, validation_routes

SNAPSHOT = "8" * 32
PARENT = "9" * 32


def wrapped(parameter):
    return {
        "name": parameter["name"],
        "type": parameter["type"],
        "parameter": copy.deepcopy(parameter),
    }


def saved_schema_flow():
    flow = copy.deepcopy(validation_flow())
    # Studio can copy definition fields outward without changing the schema.
    for output in flow["triggerInstances"][0]["outputs"]:
        output.update(copy.deepcopy(output["parameter"]))
    return flow


async def validate(settings, flow, *, snapshot=None, status=200):
    validation_routes(flow)
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    if snapshot is not None:
        http_mock.get(f"{BASE}/api/now/processflow/flow/{SNAPSHOT}").respond(
            status,
            json={"result": {"data": snapshot}},
        )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result["data"]


async def test_registered_trigger_validation_compares_parameter_schema_not_ui_copies(
    settings,
):
    flow = saved_schema_flow()
    result = await validate(settings, flow)
    assert result["valid"] is True, result
    assert result["complete"] is True


def subflow_flow():
    flow = copy.deepcopy(validation_flow())
    inputs = [
        {
            "name": "action",
            "type": "choice",
            "mandatory": True,
            "maxsize": 40,
            "choices": [{"value": "REMOVE", "label": "Remove"}],
        },
    ]
    outputs = [
        {
            "name": "result",
            "type": "reference",
            "reference": "sc_req_item",
            "mandatory": False,
            "readonly": True,
            "maxsize": 32,
        },
    ]
    snapshot: dict[str, Any] = {
        "id": SNAPSHOT,
        "parentFlow": PARENT,
        "type": "subflow",
        "snapshot": True,
        "masterSnapshot": True,
        "security": {"can_read": True},
        "userCanRead": True,
        "isFlowEntitled": True,
        "scope": "global",
        "inputs": inputs,
        "outputs": outputs,
    }
    embedded = copy.deepcopy(snapshot)
    for key in ("inputs", "outputs"):
        embedded[key] = [wrapped(parameter) for parameter in snapshot[key]]
    flow["subFlowInstances"] = [
        {
            "id": "7" * 32,
            "uiUniqueIdentifier": "sub-ui",
            "order": 4,
            "parent": "",
            "type": "subflow",
            "subflowSysId": SNAPSHOT,
            "subFlow": embedded,
            "waitForCompletion": {"value": True},
            "inputs": [{**wrapped(inputs[0]), "value": "REMOVE"}],
        },
    ]
    return flow, snapshot


async def test_registered_subflow_validation_reads_persisted_snapshot_and_normalizes_wrappers(
    settings,
):
    flow, snapshot = subflow_flow()
    result = await validate(settings, flow, snapshot=snapshot)
    assert result["valid"] is True, result
    assert result["complete"] is True
    reads = [
        c.request for c in http_mock.calls if c.request.url.path.endswith(SNAPSHOT)
    ]
    assert reads
    assert all(
        dict(r.url.params) == {"use_definition": "0", "use_min_flow": "true"}
        for r in reads
    )
    assert not any(c.request.url.path.endswith(PARENT) for c in http_mock.calls)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "records"),
        ("reference", "incident"),
        ("mandatory", False),
        ("readonly", True),
        ("maxsize", 64),
    ],
)
async def test_real_trigger_parameter_drift_is_not_normalized(settings, field, value):
    flow = saved_schema_flow()
    flow["triggerInstances"][0]["outputs"][0]["parameter"][field] = value
    flow["triggerInstances"][0]["outputs"][0][field] = value
    result = await validate(settings, flow)
    assert result["valid"] is None
    assert any(d["code"] == "TRIGGER_SCHEMA_DRIFT" for d in result["diagnostics"])


@pytest.mark.parametrize(
    "defect",
    [
        "identity",
        "unreadable",
        "entitlement",
        "unpublished",
        "schema",
        "missing_schema",
        "acl",
        "missing",
    ],
)
async def test_subflow_dependency_failure_has_safe_specific_reason(settings, defect):
    flow, snapshot = subflow_flow()
    status = 200
    if defect == "identity":
        snapshot["id"] = "6" * 32
    elif defect == "unreadable":
        snapshot["security"]["can_read"] = False
    elif defect == "entitlement":
        snapshot["isFlowEntitled"] = False
    elif defect == "unpublished":
        snapshot["snapshot"] = False
    elif defect == "schema":
        snapshot["outputs"][0]["reference"] = "incident"
    elif defect == "missing_schema":
        snapshot.pop("outputs")
    else:
        status = 403 if defect == "acl" else 404
    result = await validate(settings, flow, snapshot=snapshot, status=status)
    assert result["valid"] is None
    assert result["complete"] is False
    diagnostic = next(
        d for d in result["diagnostics"] if d["code"] == "UNVERIFIED_DEPENDENCY"
    )
    assert diagnostic["path"] == "subFlowInstances[sub-ui]"
    expected_reason = {
        "identity": "IDENTITY_MISMATCH",
        "unreadable": "READ_ACCESS_UNVERIFIED",
        "entitlement": "ENTITLEMENT_UNVERIFIED",
        "unpublished": "PUBLICATION_UNVERIFIED",
        "schema": "SCHEMA_MISMATCH",
        "missing_schema": "SCHEMA_MISMATCH",
        "acl": "DEFINITION_READ_FAILED",
        "missing": "DEFINITION_READ_FAILED",
    }[defect]
    assert diagnostic["reason"] == expected_reason
    if status != 200:
        assert diagnostic["http_status"] == status


@pytest.mark.parametrize("outputs", [None, [], ""])
async def test_absent_or_empty_trigger_outputs_are_not_replaced_from_current_catalog(
    settings, outputs
):
    flow = copy.deepcopy(validation_flow())
    if outputs is None:
        flow["triggerInstances"][0].pop("outputs")
    else:
        flow["triggerInstances"][0]["outputs"] = outputs
    result = await validate(settings, flow)
    assert result["valid"] is None
    assert result["complete"] is False
    assert any(
        d["code"] in {"TRIGGER_SCHEMA_DRIFT", "UNREADABLE_TRIGGER_SCHEMA"}
        for d in result["diagnostics"]
    )


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_dependency_read_failure_does_not_echo_remote_error_body(
    settings, status
):
    flow, snapshot = subflow_flow()
    snapshot.update({"error": "private-sensitive-marker"})
    result = await validate(settings, flow, snapshot=snapshot, status=status)
    assert result["valid"] is None
    diagnostic = next(
        d for d in result["diagnostics"] if d["code"] == "UNVERIFIED_DEPENDENCY"
    )
    assert diagnostic["reason"] == "DEFINITION_READ_FAILED"
    assert diagnostic["http_status"] == status
    assert "private-sensitive-marker" not in str(result)


async def test_subflow_schema_normalization_preserves_flow_revision_race_detection(
    settings,
):
    flow, snapshot = subflow_flow()
    result = await validate(settings, flow, snapshot=snapshot)
    assert result["valid"] is True
    changed = {**status_header(), "sys_mod_count": "8"}
    reads = iter([status_header(), changed])
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(200, json={"result": next(reads)}),
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))[
        "data"
    ]
    assert result["valid"] is None
    assert result["complete"] is False
    assert any(d["code"] == "READ_RACE" for d in result["diagnostics"])
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_ui_schema_normalization_does_not_relax_independent_saved_comparison(
    settings,
):
    flow, _ = subflow_flow()
    changed = copy.deepcopy(flow)
    changed["subFlowInstances"][0]["subFlow"]["outputs"][0]["parameter"][
        "reference"
    ] = "incident"
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": changed}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(
                client, flow, copy.deepcopy(flow), generated_ids=set()
            )
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
    assert all(call.request.method == "GET" for call in http_mock.calls)


def test_schema_signature_keeps_nested_constraints():
    parameter: dict[str, Any] = {
        "name": "payload",
        "type": "object",
        "children": [
            {
                "name": "status",
                "type": "choice",
                "choices": [{"value": "ok", "label": "OK"}],
                "mandatory": True,
            },
        ],
    }
    assert lifecycle.schema_types([parameter]) == lifecycle.schema_types(
        [wrapped(parameter)]
    )
    changed = copy.deepcopy(parameter)
    changed["children"][0]["choices"][0]["value"] = "other"
    assert lifecycle.schema_types([parameter]) != lifecycle.schema_types([changed])


@pytest.mark.parametrize(
    ("property_name", "value", "kind"),
    [
        ("type", "records", "VALUE_MISMATCH"),
        ("reference", "incident", "VALUE_MISMATCH"),
        ("mandatory", False, "VALUE_MISMATCH"),
        ("choices", [], "CHOICE_VALUE_SET_MISMATCH"),
        ("readonly", "false", "REPRESENTATION_MISMATCH"),
        ("maxsize", "40", "REPRESENTATION_MISMATCH"),
    ],
)
def test_schema_delta_preserves_signature_constraints(property_name, value, kind):
    parameter = {
        "name": "field",
        "type": "choice",
        "reference": "sc_req_item",
        "mandatory": True,
        "choices": [{"value": "ok", "label": "OK"}],
        "readonly": False,
        "maxsize": 40,
    }
    saved = {"inputs": [wrapped(parameter)], "outputs": []}
    current = {"inputs": [{**parameter, property_name: value}], "outputs": []}
    assert lifecycle.schema_types(saved["inputs"]) != lifecycle.schema_types(
        current["inputs"]
    )
    assert lifecycle.schema_difference(saved, current) == {
        "side": "both",
        "path": f"inputs[field].{property_name}",
        "kind": kind,
    }


def test_schema_delta_identifies_nested_choices_without_returning_values():
    saved: dict[str, Any] = {
        "inputs": [],
        "outputs": [
            {
                "name": "payload",
                "type": "object",
                "children": [
                    {
                        "name": "state",
                        "type": "choice",
                        "choices": [{"value": "ok", "label": "OK"}],
                    },
                ],
            },
        ],
    }
    current = copy.deepcopy(saved)
    current["outputs"][0]["children"][0]["choices"][0]["value"] = (
        "private-sensitive-marker"
    )
    mismatch = lifecycle.schema_difference(saved, current)
    assert mismatch == {
        "side": "both",
        "path": "outputs[payload].children[state].choices",
        "kind": "CHOICE_VALUE_SET_MISMATCH",
    }
    assert "private-sensitive-marker" not in str(mismatch)


@pytest.mark.parametrize("side", ["saved", "current"])
def test_schema_delta_identifies_absent_fields_and_incomplete_side(side):
    saved = {"inputs": [{"name": "field", "type": "string"}], "outputs": []}
    current = copy.deepcopy(saved)
    target = saved if side == "saved" else current
    target["inputs"] = []
    mismatch = lifecycle.schema_difference(saved, current)
    assert mismatch == {
        "side": side,
        "path": "inputs[field]" if side == "saved" else "inputs[0]",
        "kind": "MISSING_FIELD",
    }


def test_incomplete_saved_schema_uses_index_instead_of_untrusted_name():
    saved = {"inputs": [{"name": "private_sensitive_marker"}], "outputs": []}
    current = {"inputs": [], "outputs": []}
    assert lifecycle.schema_difference(saved, current) == {
        "side": "saved",
        "path": "inputs[0].type",
        "kind": "INCOMPLETE_SCHEMA",
    }


def choice_definitions():
    """Synthetic Studio projection. Live snapshot Table API agrees on these values.

    sys_hub_flow_input c902df488715cb508643202cbbbb35b8 is model-bound to
    snapshot 8502df488715cb508643202cbbbb35b6. Its sys_choice rows and the
    stored V2 parameter both declare empty, ADD, REMOVE. This is not a capture
    of the saved subFlow or minimal Studio schema.
    designer-bundle.min.js:331055 sorts presentation order, 331105/331118
    separates label from value, and 256927/256934 uses value for the option.
    """
    saved = {
        "inputs": [
            {
                "name": "action",
                "type": "choice",
                "choices": [
                    {"label": "None", "value": "", "order": 1},
                    {"label": "Add", "value": "ADD", "order": 2},
                    {"label": "Remove", "value": "REMOVE", "order": 3},
                ],
            },
        ],
        "outputs": [],
    }
    current = copy.deepcopy(saved)
    current["inputs"][0]["choices"] = [
        {"label": "Fjern", "value": "REMOVE", "order": "3"},
        {"label": "Tilføj", "value": "ADD", "order": "2"},
        {"label": "Ingen", "value": ""},
    ]
    return saved, current


def test_static_choice_presentation_does_not_change_declared_value_contract():
    saved, current = choice_definitions()
    before = copy.deepcopy((saved, current))
    assert lifecycle.schema_types(saved["inputs"]) == lifecycle.schema_types(
        current["inputs"]
    )
    assert lifecycle.schema_difference(saved, current) is None
    assert (saved, current) == before


@pytest.mark.parametrize(
    "defect", ["value", "duplicate", "type", "dependent", "disabled", "unknown"]
)
def test_static_choice_comparison_retains_semantic_distinctions(defect):
    saved, current = choice_definitions()
    choice = current["inputs"][0]["choices"][0]
    if defect == "value":
        choice["value"] = "private-sensitive-marker"
    elif defect == "duplicate":
        current["inputs"][0]["choices"].append(copy.deepcopy(choice))
    elif defect == "type":
        choice["value"] = 1
    else:
        choice[
            {
                "dependent": "dependent_value",
                "disabled": "disabled",
                "unknown": "vendorSetting",
            }[defect]
        ] = True
    mismatch = lifecycle.schema_difference(saved, current)
    assert mismatch is not None
    assert mismatch["path"] == "inputs[action].choices"
    expected_kind = {
        "duplicate": "INCOMPLETE_SCHEMA",
        "value": "CHOICE_VALUE_SET_MISMATCH",
        "type": "CHOICE_VALUE_SET_MISMATCH",
    }.get(defect, "CHOICE_METADATA_MISMATCH")
    assert mismatch["kind"] == expected_kind
    assert "private-sensitive-marker" not in str(mismatch)


def test_typed_choice_values_do_not_equate_boolean_and_integer():
    saved, current = choice_definitions()
    saved["inputs"][0]["choices"][0]["value"] = True
    current["inputs"][0]["choices"][-1]["value"] = 1
    assert lifecycle.schema_difference(saved, current) is not None


@pytest.mark.parametrize("property_name", ["label", "value"])
async def test_choice_dependency_normalization_does_not_relax_save_readback(
    settings, property_name
):
    flow, _ = subflow_flow()
    changed = copy.deepcopy(flow)
    changed["subFlowInstances"][0]["subFlow"]["inputs"][0]["parameter"]["choices"][0][
        property_name
    ] = "different"
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": changed}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(
                client, flow, copy.deepcopy(flow), generated_ids=set()
            )
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
