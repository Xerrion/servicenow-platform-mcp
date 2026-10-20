"""Read-only lifecycle validation/status and fail-closed update capture checks."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import BASE, DEFS, FLOW, fixtures, persist


def validation_flow():
    flow = typed_flow()
    definition = next(
        t
        for c in DEFS["trigger_categories"]
        for t in c["triggers"].values()
        if t["type"] == "service_catalog"
    )
    flow["triggerInstances"] = [
        {
            "name": "Service Catalog",
            "type": "service_catalog",
            "triggerDefinitionId": definition["id"],
            "inputs": [
                {
                    "name": p["name"],
                    "parameter": p["parameter"],
                    "value": p.get("default_value", ""),
                    "children": [],
                }
                for p in definition["inputs"]
            ],
            "outputs": definition["outputs"],
        },
    ]
    flow["subFlowInstances"] = []
    for step in flow["actionInstances"]:
        step["actionType"]["inputs"] = [
            {"name": i["name"], **i["parameter"]} for i in step["inputs"]
        ]
        step["actionType"].setdefault("outputs", [])
    return flow


def validation_routes(flow):
    for step in flow["actionInstances"]:
        # Give fixture steps distinct definitions so different input schemas are not conflated.
        step["actionTypeSysId"] = step["id"]
        definition = {
            **copy.deepcopy(step["actionType"]),
            "id": step["id"],
            "security": {"can_read": True},
        }
        definition.update(entitled=True, state="published", is_snapshot=True)
        step["actionType"]["id"] = step["id"]
        http_mock.get(
            f"{BASE}/api/now/processflow/action/action_types/{step['id']}"
        ).respond(
            200,
            json={"result": definition},
        )


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "dangling",
        "type",
        "unknown_family",
        "acl",
        "mandatory",
        "orphan",
        "duplicate",
        "script",
        "stage",
        "interface",
    ],
)
async def test_local_validation_never_executes_or_repairs_saved_state(settings, defect):
    flow = validation_flow()
    if defect == "dangling":
        flow["actionInstances"][1]["inputs"][1]["value"] = "{{missing.count}}"
    elif defect == "type":
        flow["actionInstances"][1]["inputs"][1]["value"] = True
    elif defect == "unknown_family":
        flow["triggerInstances"][0]["type"] = "daily"
    elif defect == "acl":
        flow["contentACLFilteredOrders"] = [1]
    elif defect == "mandatory":
        flow["actionInstances"][0]["inputs"][0].update(
            value="", parameter={"type": "string", "mandatory": True}
        )
        flow["actionInstances"][0]["actionType"]["inputs"][0]["mandatory"] = True
    elif defect == "orphan":
        flow["actionInstances"][0]["parent"] = "missing"
    elif defect == "duplicate":
        flow["actionInstances"][1]["order"] = 1
    elif defect == "script":
        flow["actionInstances"][0]["inputs"][0]["scriptActive"] = True
    elif defect == "stage":
        flow["stages"] = {"stage-id": {"componentIndexes": [9]}}
    elif defect == "interface":
        flow["inputs"] = [{"name": "partial"}]
    validation_routes(flow)
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["status"] == "success", result
    data = result["data"]
    assert data["mode"] == "local"
    assert data["remote_compile"] == "not_performed"
    assert data["valid"] is True if defect is None else data["valid"] is not True, data
    assert data["diagnostics"] if defect else not data["diagnostics"]
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


def status_header():
    return {
        "sys_id": FLOW,
        "type": "flow",
        "sys_scope": "global",
        "active": "false",
        "status": "draft",
        "master_snapshot": "b" * 32,
        "latest_snapshot": "c" * 32,
        "sys_updated_on": "2026-10-14 10:00:00",
        "sys_mod_count": "7",
        "sys_update_name": f"sys_hub_flow_{FLOW}",
    }


@pytest.mark.parametrize("state", ["COMPLETE", "WAITING", "UNRECOGNIZED"])
async def test_status_reads_exact_execution_context_without_starting_it(
    settings, state
):
    capture_routes()
    execution = "d" * 32
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{execution}").respond(
        200,
        json={
            "result": {
                "sys_id": execution,
                "flow": FLOW,
                "state": state,
                "sys_created_on": "2026-10-14 09:00:00",
                "sys_updated_on": "2026-10-14 10:00:00",
            },
        },
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id=execution)
    )
    assert result["status"] == "success", result
    data = result["data"]
    assert data["flow"]["active"] is False
    assert data["flow"]["draft"] is True
    assert data["flow"]["has_published_snapshot"] is True
    assert data["execution"]["state"] == state
    assert data["complete"] is (state != "UNRECOGNIZED")
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_status_rejects_unrelated_execution_and_partial_active(settings):
    capture_routes()
    header = status_header()
    header.pop("active")
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": header}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200,
        json={"result": {"sys_id": "d" * 32, "flow": "e" * 32, "state": "COMPLETE"}},
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id="d" * 32)
    )
    assert result["data"]["complete"] is False
    assert result["data"]["flow"]["active"] is None
    assert result["data"]["execution"]["state"] is None


def capture_routes(*, scope="global", state="in progress", revision="7"):
    update_set = "9" * 32
    http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
        200,
        json={
            "result": {
                "currentApplication": {"sysId": scope},
                "currentUpdateSet": {"sysId": update_set},
            },
        },
    )
    http_mock.get(f"{BASE}/api/now/table/sys_update_set/{update_set}").respond(
        200,
        json={
            "result": {
                "sys_id": update_set,
                "application": "global",
                "state": state,
                "sys_updated_on": "2026-10-14 09:00:00",
            },
        },
    )
    header = status_header()
    header["sys_mod_count"] = revision
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": header}
    )
    row = {
        "sys_id": "8" * 32,
        "name": f"sys_hub_flow_{FLOW}",
        "update_set": update_set,
        "application": "global",
        "sys_updated_on": "2026-10-14 10:00:00",
        "sys_mod_count": "7",
        "action": "INSERT_OR_UPDATE",
    }
    http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200, json={"result": [row]}
    )
    return update_set, row


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "missing",
        "wrong_set",
        "stale",
        "context_changed",
        "capture_acl",
        "async",
        "concurrent_save",
    ],
)
async def test_bound_save_verifies_fresh_capture_without_replay(settings, defect):
    _, tools = fixtures(settings, typed_flow())
    update_set, row = capture_routes()

    def save(request):
        body = json.loads(request.content)
        persist(body)
        header = status_header()
        header["sys_mod_count"] = "8"
        if defect == "concurrent_save":
            header["sys_mod_count"] = "9"
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200, json={"result": header}
        )
        captured = {**row, "sys_mod_count": "8"}
        if defect == "wrong_set":
            captured["update_set"] = "7" * 32
        if defect == "stale":
            captured = row
        http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
            200,
            json={"result": [] if defect == "missing" else [captured]},
        )
        if defect == "context_changed":
            http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
                200,
                json={
                    "result": {
                        "currentApplication": {"sysId": "global"},
                        "currentUpdateSet": {"sysId": "7" * 32},
                    },
                },
            )
        if defect == "capture_acl":
            http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(403)
        if defect == "async":
            http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
                202, json={"result": [captured]}
            )
        return httpx2.Response(200, json={"result": {"data": body}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
        ),
    )
    assert result["status"] == "success", result
    applied = decode_response(
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["data"]["capture"]["state"] == "metadata_verified", applied
    assert route.call_count == 1
    assert (
        await tools["mutation_apply"](preview_token=result["data"]["preview_token"])
    ).is_error


@pytest.mark.parametrize("defect", ["scope", "state", "revision", "unreadable"])
async def test_bound_preview_blocks_wrong_or_unknown_context(settings, defect):
    coordinator, tools = fixtures(settings, typed_flow())
    update_set, _ = capture_routes(
        scope="1" * 32 if defect == "scope" else "global",
        state="complete" if defect == "state" else "in progress",
        revision="8" if defect == "revision" else "7",
    )
    if defect == "unreadable":
        http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(401)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
        ),
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_unknown_execution_is_not_inferred_from_flow_or_replayed(settings):
    capture_routes()
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        202, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="status", sys_id=FLOW))
    assert result["data"]["complete"] is False
    assert result["data"]["execution"] is None
    assert result["data"]["replay_safe"] is False


async def test_production_validate_status_are_reads_only(prod_settings):
    http_mock.get(
        "https://prod.service-now.com/api/now/processflow/flow/" + FLOW
    ).respond(403)
    http_mock.get(
        "https://prod.service-now.com/api/now/table/sys_hub_flow/" + FLOW
    ).respond(403)
    tools = _register_and_get_tools(prod_settings, OAuthPKCEProvider(prod_settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is None
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("status", [202, 500])
async def test_effect_response_unknown_never_replays(settings, status):
    _, tools = fixtures(settings, typed_flow())
    route = http_mock.route(
        method="PUT", url=f"{BASE}/api/now/processflow/flow"
    ).respond(
        status,
        json={"result": {"data": typed_flow()}},
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
        ),
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert route.call_count == 1
    assert (
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    ).is_error


async def test_status_and_validate_revision_race_is_incomplete(settings):
    flow = validation_flow()
    validation_routes(flow)
    fixtures(settings, flow)
    capture_routes()
    changed = {**status_header(), "sys_mod_count": "8"}
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
        side_effect=[
            httpx2.Response(200, json={"result": status_header()}),
            httpx2.Response(200, json={"result": changed}),
        ],
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is None
    assert any(d["code"] == "READ_RACE" for d in result["data"]["diagnostics"])


async def test_context_change_after_preview_blocks_put(settings):
    _, tools = fixtures(settings, typed_flow())
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
        ),
    )
    capture_routes(scope="1" * 32)
    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow")
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] in {"STALE_PREVIEW", "CONTEXT_MISMATCH"}
    assert not route.called


async def test_effectful_lifecycle_not_accepted_as_draft_action(settings):
    _, tools = fixtures(settings, typed_flow())
    for action in (
        "publish",
        "activate",
        "deactivate",
        "test",
        "run",
        "validate_remote",
    ):
        assert (await tools["flow_preview"](action=action, sys_id=FLOW)).is_error
    assert not http_mock.calls


async def test_validation_diagnostics_do_not_echo_binding_values(settings):
    flow = validation_flow()
    validation_routes(flow)
    flow["actionInstances"][1]["inputs"][1]["value"] = "private-sensitive-marker"
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is False, result
    assert "private-sensitive-marker" not in str(result)


async def test_incomplete_dependency_never_reports_valid(settings):
    flow = validation_flow()
    validation_routes(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'1' * 32}").respond(
        403
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is None
    assert result["data"]["complete"] is False
    assert any(
        d["code"] == "UNVERIFIED_DEPENDENCY" for d in result["data"]["diagnostics"]
    )


@pytest.mark.parametrize(
    "field", ["expected_scope", "expected_update_set", "expected_revision"]
)
async def test_incomplete_context_binding_is_rejected_before_reads(settings, field):
    coordinator, tools = fixtures(settings, typed_flow())
    args = {
        "expected_scope": "global",
        "expected_update_set": "9" * 32,
        "expected_revision": "7",
    }
    args.pop(field)
    result = await tools["flow_preview"](
        action="edit",
        sys_id=FLOW,
        edits=[{"op": "update_inputs", "step": "1", "inputs": {}}],
        **args,
    )
    assert result.is_error
    assert len(coordinator.store) == 0
    assert not http_mock.calls


async def test_context_read_shape_and_capture_query_are_bounded(settings):
    from servicenow_mcp.capabilities.flows import capture
    from servicenow_mcp.platform.client import ServiceNowClient

    update_set, _ = capture_routes()
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await client.flow_session_context() == {
            "scope": "global",
            "update_set": update_set,
        }
        row = await capture.read_capture(client, FLOW)
        assert row is not None
        assert row["name"] == f"sys_hub_flow_{FLOW}"
    request = next(
        c.request
        for c in http_mock.calls
        if c.request.url.path.endswith("sys_update_xml")
    )
    assert request.url.params["sysparm_limit"] == "2"
    assert "payload" not in request.url.params["sysparm_fields"]


async def test_capture_ambiguity_and_unreadable_current_context_are_explicit(settings):
    from servicenow_mcp.capabilities.contracts import CapabilityError
    from servicenow_mcp.capabilities.flows import capture
    from servicenow_mcp.platform.client import ServiceNowClient

    _, row = capture_routes()
    http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200,
        json={"result": [row, {**row, "sys_id": "7" * 32}]},
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError, match="ambiguous"):
            await capture.read_capture(client, FLOW)


async def test_lifecycle_ledger_does_not_alias_effects(settings):
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="describe"))
    ledger = result["data"]["authoring"]["lifecycle"]
    assert ledger["validate_local"]["available"] is True
    assert ledger["status"]["available"] is True
    for operation in (
        "validate_remote",
        "run",
    ):
        assert ledger[operation]["available"] is False
        assert ledger[operation]["reason"]
    for operation in ("publish", "activate", "deactivate", "test"):
        assert ledger[operation]["available"] is True
        assert ledger[operation]["live_verified"] is False
        assert ledger[operation]["reason"]
    assert ledger["test"]["effect"] == "execution"
    assert not http_mock.calls


async def test_validation_checks_real_logic_family_constraints(settings):
    from tests.test_flow_data_edits import family_definitions

    flow = validation_flow()
    validation_routes(flow)
    fixtures(settings, flow)
    definition = next(d for d in family_definitions() if d["type"] == "BREAK")
    flow["flowLogicInstances"] = [
        {
            "id": "7" * 32,
            "uiUniqueIdentifier": "break",
            "type": "flowlogic",
            "parent": "",
            "order": 4,
            "definitionId": definition["id"],
            "flowLogicDefinition": definition,
            "children": [],
            "inputs": [],
        },
    ]
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": family_definitions()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(await tools["flow"](action="validate", sys_id=FLOW))
    assert result["data"]["valid"] is False, result
    assert any(d["code"] == "INVALID_TOPOLOGY" for d in result["data"]["diagnostics"])


async def test_bound_write_production_gate_unchanged(settings):
    coordinator, tools = fixtures(settings, typed_flow())
    update_set, _ = capture_routes()
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "changed"},
                },
            ],
            expected_scope="global",
            expected_update_set=update_set,
            expected_revision="7",
        ),
    )
    settings.servicenow_env = "prod"
    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow")
    assert (
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert not route.called
    assert len(coordinator.store) == 0
