"""Bound capture selects fresh metadata without conflating historical updates."""

import copy
import json

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import capture
from servicenow_mcp.capabilities.mutations.models import FlowCommand, fingerprint
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_lifecycle import capture_routes, status_header


@pytest.mark.parametrize("reverse", [False, True])
async def test_bound_capture_selects_unique_fresh_record_among_same_set_history(
    settings, reverse
):
    update_set, current = capture_routes()
    older = {
        **current,
        "sys_id": "7" * 32,
        "sys_updated_on": "2026-01-01 09:00:00",
        "sys_mod_count": "7",
    }
    rows = [older, current] if reverse else [current, older]
    http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200, json={"result": rows}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        found = await capture.read_capture(
            client,
            FLOW,
            expected_update_set=update_set,
            expected_scope="global",
            minimum_updated_on=status_header()["sys_updated_on"],
        )
    assert found == current
    request = http_mock.calls[-1].request
    query = request.url.params["sysparm_query"]
    assert f"name=sys_hub_flow_{FLOW}" in query
    assert f"update_set={update_set}" in query
    assert "application=global" in query
    assert request.url.params["sysparm_limit"] == "101"
    assert "payload" not in request.url.params["sysparm_fields"]


@pytest.mark.parametrize(
    "defect",
    [
        "tie",
        "partial",
        "wrong_set",
        "wrong_scope",
        "wrong_name",
        "stale",
        "duplicate",
        "cap",
    ],
)
async def test_bound_capture_never_selects_first_row_from_ambiguous_or_incomplete_evidence(
    settings, defect
):
    update_set, current = capture_routes()
    rows = [copy.deepcopy(current)]
    if defect == "tie":
        rows.append({**current, "sys_id": "7" * 32})
    elif defect == "partial":
        rows[0].pop("application")
    elif defect == "wrong_set":
        rows[0]["update_set"] = "7" * 32
    elif defect == "wrong_scope":
        rows[0]["application"] = "7" * 32
    elif defect == "wrong_name":
        rows[0]["name"] = "sys_hub_flow_" + "7" * 32
    elif defect == "stale":
        rows[0]["sys_updated_on"] = "2026-01-01 09:00:00"
    elif defect == "duplicate":
        rows.append(copy.deepcopy(current))
    else:
        rows *= 101
    http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200, json={"result": rows}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await capture.read_capture(
                client,
                FLOW,
                expected_update_set=update_set,
                expected_scope="global",
                minimum_updated_on=status_header()["sys_updated_on"],
            )
    assert error.value.code == "UNAVAILABLE"
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "older_advanced",
        "new_record",
        "multiple_changed",
        "missing_history",
        "post_partial",
        "post_acl",
        "post_wrong_set",
        "post_wrong_scope",
        "post_duplicate",
        "post_cap",
        "new_revision",
        "late_context",
        "late_header",
        "regression",
        "timestamp_regression",
        "post_tie",
        "post_stale",
        "post_revision",
        "post_revision_jump",
        "post_identity",
        "flow_revision",
        "prewrite_context",
        "prewrite_history",
    ],
)
async def test_bound_native_preview_apply_selects_history_but_checks_fresh_capture(
    settings, defect
):
    flow = typed_flow()
    fixtures(settings, flow)
    update_set, current = capture_routes()
    if defect == "older_advanced":
        current["sys_mod_count"] = "2"
    history = {
        **current,
        "sys_id": "7" * 32,
        "sys_updated_on": "2026-01-01 09:00:00",
        "sys_mod_count": "7",
    }
    capture_read = http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200,
        json={"result": [history, current]},
    )

    def save(request):
        payload = json.loads(request.content)
        assert payload["actionInstances"][0]["inputs"][0]["value"] == "bound save"
        persist(payload)
        header = {
            **status_header(),
            "sys_updated_on": "2026-10-14 10:01:00",
            "sys_mod_count": "8",
        }
        if defect == "flow_revision":
            header["sys_mod_count"] = "9"
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
            200, json={"result": header}
        )
        after = {
            **current,
            "sys_updated_on": header["sys_updated_on"],
            "sys_mod_count": "8",
        }
        if defect == "post_revision":
            after["sys_mod_count"] = current["sys_mod_count"]
        elif defect == "post_revision_jump":
            after["sys_mod_count"] = "9"
        elif defect == "post_stale":
            after["sys_updated_on"] = current["sys_updated_on"]
        elif defect == "post_identity":
            after["sys_id"] = "6" * 32
        rows = [history, after]
        if defect == "older_advanced":
            # Actual observed server behavior: keep the formerly newest row unchanged,
            # advance the existing historical row's own revision, not the selected ID.
            rows = [
                current,
                {
                    **history,
                    "sys_updated_on": header["sys_updated_on"],
                    "sys_mod_count": "8",
                },
            ]
        elif defect == "new_record":
            rows = [
                history,
                current,
                {**after, "sys_id": "6" * 32, "sys_mod_count": "0"},
            ]
        elif defect == "multiple_changed":
            rows = [
                {
                    **history,
                    "sys_updated_on": "2026-10-14 10:00:59",
                    "sys_mod_count": "8",
                },
                after,
            ]
        elif defect == "regression":
            rows = [{**history, "sys_mod_count": "6"}, after]
        elif defect == "timestamp_regression":
            rows = [
                {
                    **history,
                    "sys_updated_on": "2025-01-01 09:00:00",
                    "sys_mod_count": "8",
                },
                after,
            ]
        elif defect == "missing_history":
            rows = [after]
        elif defect == "post_partial":
            rows[0] = {
                key: value for key, value in history.items() if key != "application"
            }
        elif defect == "post_wrong_set":
            rows[0] = {**history, "update_set": "6" * 32}
        elif defect == "post_wrong_scope":
            rows[0] = {**history, "application": "6" * 32}
        elif defect == "post_duplicate":
            rows.append(copy.deepcopy(after))
        elif defect == "post_cap":
            rows *= 51
        elif defect == "new_revision":
            rows = [
                history,
                current,
                {**after, "sys_id": "6" * 32, "sys_mod_count": "1"},
            ]
        elif defect == "late_header":
            headers = iter([header, {**header, "sys_mod_count": "9"}])
            http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").mock(
                side_effect=lambda _: httpx2.Response(
                    200, json={"result": next(headers)}
                ),
            )
        elif defect == "late_context":
            contexts = iter([update_set, "6" * 32])
            http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").mock(
                side_effect=lambda _: httpx2.Response(
                    200,
                    json={
                        "result": {
                            "currentApplication": {"sysId": "global"},
                            "currentUpdateSet": {"sysId": next(contexts)},
                        },
                    },
                ),
            )
        if defect == "post_tie":
            rows.append({**after, "sys_id": "6" * 32})
        capture_read.respond(
            403 if defect == "post_acl" else 200, json={"result": rows}
        )
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    settings.mcp_tool_package = "flow_write"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            preview = await session.call_tool(
                "flow_preview",
                {
                    "action": "edit",
                    "sys_id": FLOW,
                    "expected_scope": "global",
                    "expected_update_set": update_set,
                    "expected_revision": "7",
                    "edits": [
                        {
                            "op": "update_inputs",
                            "step": "1",
                            "inputs": {"log_message": "bound save"},
                        }
                    ],
                },
            )
            assert not preview.is_error, preview
            assert (
                preview.structured_content["data"]["summary"]["context"]["state"]
                == "bound"
            )
            assert not write.called
            if defect == "prewrite_context":
                http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
                    200,
                    json={
                        "result": {
                            "currentApplication": {"sysId": "global"},
                            "currentUpdateSet": {"sysId": "5" * 32},
                        },
                    },
                )
            elif defect == "prewrite_history":
                capture_read.respond(
                    200, json={"result": [{**history, "sys_mod_count": "8"}, current]}
                )
            result = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", result)
            if defect not in {None, "older_advanced", "new_record"}:
                assert result.is_error
                code = result.structured_content["error"]["code"]
                if defect in {"prewrite_context", "prewrite_history"}:
                    assert code in {"CONTEXT_MISMATCH", "STALE_PREVIEW"}
                else:
                    assert code == "UNKNOWN_OUTCOME"
            else:
                assert not result.is_error, result
                evidence = result.structured_content["data"]["capture"]
                assert evidence["state"] == "metadata_verified"
                assert evidence["update_set"] == update_set
                selected_id = {
                    "older_advanced": history["sys_id"],
                    "new_record": "6" * 32,
                }.get(
                    defect,
                    current["sys_id"],
                )
                assert evidence["customer_update_sys_id"] == selected_id
                assert evidence["scope"] == "flow_header_customer_update"
                assert "payload content are not verified" in evidence["limitation"]
            assert write.call_count == (
                0 if defect in {"prewrite_context", "prewrite_history"} else 1
            )
            if write.called:
                write_index = next(
                    i
                    for i, call in enumerate(http_mock.calls)
                    if call.request.method == "PUT"
                )
                assert any(
                    call.request.method == "GET"
                    and call.request.url.path == f"/api/now/processflow/flow/{FLOW}"
                    for call in http_mock.calls[write_index + 1 :]
                )
        tasks.cancel_scope.cancel()


async def test_bound_snapshot_retains_all_candidates_and_is_independent_of_response_order(
    settings,
):
    update_set, current = capture_routes()
    older = {
        **current,
        "sys_id": "7" * 32,
        "sys_updated_on": "2026-01-01 09:00:00",
        "sys_mod_count": "7",
    }
    route = http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200, json={"result": [current, older]}
    )
    command = FlowCommand(
        action="edit",
        sys_id=FLOW,
        expected_scope="global",
        expected_update_set=update_set,
        expected_revision="7",
        edits=[
            {
                "op": "update_inputs",
                "step": "1",
                "inputs": {"log_message": "bound save"},
            }
        ],
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        first = await capture.read_binding(command, client)
        route.respond(200, json={"result": [older, current]})
        second = await capture.read_binding(command, client)
    assert first is not None
    assert {row["sys_id"] for row in first["captures"]} == {
        current["sys_id"],
        older["sys_id"],
    }
    assert fingerprint(first) == fingerprint(second)


@pytest.mark.parametrize(
    "defect", [None, "wrong_action", "same_timestamp_no_revision", "replaced_identity"]
)
async def test_reconcile_observed_metadata_without_replaying_the_save(settings, defect):
    """Public header metadata observed before/after the authorized 2026-10-16 save.

    This replays read fixtures only. It cannot retroactively validate a consumed token
    or establish child-artifact/payload capture.
    """
    target = "e2861e13c3bb8f105cf89fcd2b0131d6"
    update_set = "29fd021387b343108643202cbbbb358b"
    newer = {
        "sys_id": "9ea1f4008700d7108643202cbbbb35b6",
        "name": f"sys_hub_flow_{target}",
        "update_set": update_set,
        "application": "global",
        "sys_updated_on": "2026-10-15 13:35:25",
        "sys_mod_count": "2",
        "action": "INSERT_OR_UPDATE",
    }
    older = {
        **newer,
        "sys_id": "fa861e13c3bb8f105cf89fcd2b0131df",
        "sys_updated_on": "2026-10-14 07:11:21",
        "sys_mod_count": "7",
    }
    before_header = {
        "sys_id": target,
        "sys_scope": "global",
        "sys_mod_count": "10",
        "sys_updated_on": "2026-10-15 13:28:50",
    }
    after_header = {
        **before_header,
        "sys_mod_count": "11",
        "sys_updated_on": "2026-10-16 06:57:59",
    }
    advanced = {**older, "sys_mod_count": "8", "sys_updated_on": "2026-10-16 06:58:00"}
    rows = [newer, advanced]
    if defect == "wrong_action":
        advanced["action"] = "DELETE"
    elif defect == "same_timestamp_no_revision":
        advanced["sys_mod_count"] = "7"
    elif defect == "replaced_identity":
        rows = [newer, {**advanced, "sys_id": "6" * 32, "sys_mod_count": "0"}]
    before = {
        "header": before_header,
        "session": {"scope": "global", "update_set": update_set},
        "captures": [newer, older],
    }
    capture_routes()
    http_mock.get(f"{BASE}/api/now/ui/concoursepicker/current").respond(
        200,
        json={
            "result": {
                "currentApplication": {"sysId": "global"},
                "currentUpdateSet": {"sysId": update_set},
            },
        },
    )
    http_mock.get(f"{BASE}/api/now/table/sys_update_set/{update_set}").respond(
        200,
        json={
            "result": {
                "sys_id": update_set,
                "state": "in progress",
                "application": "global",
            }
        },
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{target}").respond(
        200, json={"result": after_header}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_update_xml").respond(
        200, json={"result": rows}
    )
    command = FlowCommand(
        action="edit",
        sys_id=target,
        expected_scope="global",
        expected_update_set=update_set,
        expected_revision="10",
        edits=[{"op": "update_inputs", "step": "1", "inputs": {}}],
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        if defect:
            with pytest.raises(CapabilityError) as error:
                await capture.verify_after(command, client, target, before)
            assert error.value.code == "UNKNOWN_OUTCOME"
        else:
            evidence = await capture.verify_after(command, client, target, before)
            assert evidence["state"] == "metadata_verified"
            assert evidence["customer_update_sys_id"] == older["sys_id"]
            assert evidence["update_set"] == update_set
    assert all(call.request.method == "GET" for call in http_mock.calls)
