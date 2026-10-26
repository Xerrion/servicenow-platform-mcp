"""Studio definitions have a separate bound from caller-supplied mutations."""

import copy
import json

import pytest

from servicenow_mcp.capabilities.contracts import (
    MAX_JSON_PAYLOAD_BYTES,
    CapabilityError,
    validate_payload,
)
from servicenow_mcp.capabilities.flows import editing, semantics
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_completion import effect_routes
from tests.test_flow_editing import base_flow


def sized_flow(size: int):
    flow = base_flow()
    flow["componentInstances"] = ["private-sensitive-marker"]
    length = len(
        json.dumps(flow, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    flow["componentInstances"][0] += "x" * (size - length)
    return flow


@pytest.mark.parametrize("size", [MAX_JSON_PAYLOAD_BYTES + 1, 304455, 4 * 1024 * 1024])
def test_complete_studio_definitions_above_caller_limit_are_preserved(size):
    flow = sized_flow(size)
    before = copy.deepcopy(flow)
    assert (
        len(json.dumps(flow, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        == size
    )
    semantics.studio_semantics(flow)
    semantics.compare_saved(flow, copy.deepcopy(flow), generated_ids=set())
    assert flow == before
    with pytest.raises(CapabilityError, match="exceeds maximum size of 262144 bytes"):
        validate_payload(flow, field_name="inputs", validate_keys=False)


def test_large_studio_settings_remain_strictly_compared():
    flow = sized_flow(304455)
    actual = copy.deepcopy(flow)
    actual["componentInstances"][0] += "changed"
    with pytest.raises(semantics.SemanticMismatch) as error:
        semantics.compare_saved(flow, actual, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert error.value.path == "flow.componentInstances[0]"
    assert "private-sensitive-marker" not in str(error.value)


def test_draft_authoring_uses_the_shared_studio_definition_limit():
    flow = sized_flow(304455)
    flow["subFlowInstances"] = []
    editing.require_editable(flow, flow["id"])


def test_caller_edits_keep_the_smaller_input_limit():
    with pytest.raises(CapabilityError, match="exceeds maximum size of 262144 bytes"):
        FlowCommand(
            action="edit",
            sys_id="a" * 32,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "x" * MAX_JSON_PAYLOAD_BYTES},
                }
            ],
        )


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("size", "exceeds maximum size of 4194304 bytes"),
        ("depth", "exceeds maximum nesting depth of 32"),
        ("value", "contains an invalid JSON value"),
    ],
)
def test_studio_payload_rejections_retain_safe_specific_reason(defect, message):
    flow = base_flow()
    if defect == "size":
        flow = sized_flow(4 * 1024 * 1024 + 1)
    elif defect == "depth":
        nested = flow["componentInstances"] = {}
        for _ in range(32):
            child = {}
            nested["private-sensitive-marker"] = child
            nested = child
    else:
        flow["componentInstances"] = float("nan")
    with pytest.raises(semantics.IncompleteSemantics) as error:
        semantics.studio_semantics(flow)
    assert error.value.path == "flow"
    assert message in str(error.value)
    assert "private-sensitive-marker" not in str(error.value)


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_deactivation_preflight_payload_rejection_never_dispatches(
    settings, phase
):
    coordinator, tools, flow, _, args = effect_routes(settings, "deactivate")
    token = None
    if phase == "apply":
        preview = decode_response(await tools["flow_preview"](**args))
        assert preview["status"] == "success", preview
        token = preview["data"]["preview_token"]
    flow["componentInstances"] = ["private-sensitive-marker" * 200000]
    result = decode_response(
        await tools["flow_preview"](**args)
        if token is None
        else await tools["mutation_apply"](preview_token=token)
    )
    assert result["status"] == "error", result
    assert result["error"]["code"] == "UNAVAILABLE", result
    assert "exceeds maximum size of 4194304 bytes" in result["error"]["message"]
    assert "No lifecycle request was sent" in result["error"]["message"]
    assert "private-sensitive-marker" not in str(result)
    assert len(coordinator.store) == 0
    assert all(
        call.request.method == "GET"
        and not call.request.url.path.endswith("/deactivate")
        for call in http_mock.calls
    )
