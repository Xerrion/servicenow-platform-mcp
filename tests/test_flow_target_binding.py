"""Target drift and independent persisted-state checks replace capture selection policy."""

import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import target
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_lifecycle import status_header


def command(**kwargs):
    return FlowCommand(
        action="edit",
        sys_id=FLOW,
        edits=[
            {"op": "update_inputs", "step": "1", "inputs": {"log_message": "Saved"}}
        ],
        **kwargs,
    )


async def test_studio_scope_must_match_the_explicit_target_scope(settings):
    flow = typed_flow()
    flow["scope"] = "1" * 32
    _, tools = fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            expected_scope="global",
            edits=[
                {"op": "update_inputs", "step": "1", "inputs": {"log_message": "Saved"}}
            ],
        )
    )
    assert result["error"]["code"] == "CONTEXT_MISMATCH"
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "fields",
    [
        {"expected_scope": "global"},
        {"expected_revision": "7"},
        {"expected_scope": "global", "expected_revision": "7"},
    ],
)
async def test_each_target_binding_works_without_session_or_update_set(
    settings, fields
):
    route = http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        bound = await target.read_binding(command(**fields), client)
        await target.require_current(command(**fields), client, bound)
    assert route.call_count == len(http_mock.calls) == 2


@pytest.mark.parametrize(
    "defect", ["identity", "scope", "revision", "partial", "acl", "updated"]
)
async def test_target_drift_still_blocks_before_write(settings, defect):
    header = status_header()
    route = http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": header}
    )
    cmd = command(expected_scope="global", expected_revision="7")
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        before = await target.read_binding(cmd, client)
        if defect == "identity":
            header["sys_id"] = "1" * 32
        elif defect == "scope":
            header["sys_scope"] = "1" * 32
        elif defect == "revision":
            header["sys_mod_count"] = "8"
        elif defect == "partial":
            header.pop("sys_mod_count")
        elif defect == "updated":
            header["sys_updated_on"] = "2026-10-22 10:00:00"
        route.respond(403 if defect == "acl" else 200, json={"result": header})
        with pytest.raises(ServiceNowMCPError) as error:
            await target.require_current(cmd, client, before)
        if isinstance(error.value, CapabilityError):
            assert (
                error.value.code
                == {
                    "identity": "UNAVAILABLE",
                    "scope": "CONTEXT_MISMATCH",
                    "revision": "STALE_PREVIEW",
                    "partial": "UNAVAILABLE",
                    "updated": "STALE_PREVIEW",
                }[defect]
            )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect", [None, "scope", "revision_jump", "unchanged", "race", "acl"]
)
async def test_verified_save_checks_target_not_capture(settings, defect):
    _, tools = fixtures(settings, typed_flow())
    header = status_header()
    header_read = http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": header}
    )

    def save(request):
        body = json.loads(request.content)
        persist(body)
        after = {**header, "sys_mod_count": "8"}
        if defect == "scope":
            after["sys_scope"] = "1" * 32
        elif defect == "revision_jump":
            after["sys_mod_count"] = "9"
        elif defect == "unchanged":
            after["sys_mod_count"] = "7"
        if defect == "race":
            header_read.mock(
                side_effect=[
                    httpx2.Response(200, json={"result": after}),
                    httpx2.Response(
                        200, json={"result": {**after, "sys_mod_count": "9"}}
                    ),
                ]
            )
        else:
            header_read.respond(403 if defect == "acl" else 200, json={"result": after})
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            **command(expected_scope="global", expected_revision="7").model_dump(
                include={
                    "action",
                    "sys_id",
                    "edits",
                    "expected_scope",
                    "expected_revision",
                }
            )
        )
    )
    assert preview["status"] == "success", preview
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    if defect:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    else:
        assert result["status"] == "success" and result["data"]["revision"] == "8", (
            result
        )
        assert "capture" not in result["data"]
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert write.call_count == 1
