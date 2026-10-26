"""Exact compiled execution ownership is independent of authoring and content evidence."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_lifecycle import BASE, FLOW, capture_routes

CONTEXT = "d" * 32
COMPILED = "e" * 32
AUTHORING = "6" * 32
OTHER = "7" * 32
MASTER = "b" * 32
LATEST = "c" * 32
MISSING = object()


def execution_context(
    *, owner: object = FLOW, state: object = "COMPLETE", snapshot: object = COMPILED
) -> dict[str, object]:
    context: dict[str, object] = {
        "sys_id": CONTEXT,
        "sys_created_on": "2026-10-14 09:00:00",
        "sys_updated_on": "2026-10-14 10:00:00",
    }
    for name, value in (("flow", owner), ("state", state), ("snapshot", snapshot)):
        if value is not MISSING:
            context[name] = value
    return context


def compiled_record(**changes: object) -> dict[str, object]:
    return {
        "sys_id": COMPILED,
        "parent": FLOW,
        "sys_mod_count": "3",
        "sys_updated_on": "2026-10-14 09:00:00",
        **changes,
    }


async def observe_execution(settings, mode, context):
    read = http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{CONTEXT}").respond(
        200, json={"result": context}
    )
    capture_routes()
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id=CONTEXT)
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert dict(read.calls.last.request.url.params) == {
        "sysparm_fields": read.calls.last.request.url.params["sysparm_fields"],
        "sysparm_display_value": "false",
    }
    assert set(read.calls.last.request.url.params["sysparm_fields"].split(",")) == {
        "sys_id",
        "flow",
        "state",
        "snapshot",
        "sys_created_on",
        "sys_updated_on",
    }
    return result


def assert_compiled_reads(snapshot_id):
    reads = [
        call.request
        for call in http_mock.calls
        if call.request.url.path.startswith("/api/now/table/sys_hub_snapshot")
    ]
    if snapshot_id is None:
        assert reads == []
    else:
        assert reads
        for request in reads:
            assert request.method == "GET"
            assert request.url.path == f"/api/now/table/sys_hub_snapshot/{snapshot_id}"
            assert request.url.params["sysparm_display_value"] == "false"
            assert set(request.url.params["sysparm_fields"].split(",")) == {
                "sys_id",
                "parent",
                "sys_mod_count",
                "sys_updated_on",
            }
            assert request.content == b""
    forbidden = {
        f"/api/now/processflow/flow/{COMPILED}",
        f"/api/now/processflow/flow/{MASTER}",
        f"/api/now/processflow/flow/{LATEST}",
    }
    assert all(
        call.request.url.path not in forbidden
        and (
            not call.request.url.path.startswith("/api/now/table/sys_hub_flow_snapshot")
        )
        for call in http_mock.calls
    )


def assert_snapshot_evidence(execution, *, snapshot_id, owner_verified):
    assert execution["snapshot_sys_id"] == snapshot_id
    assert execution["snapshot_owner_verified"] is owner_verified
    assert execution["snapshot_content_verified"] is False
    assert execution["version_verified"] is False


def assert_known_execution(result, mode):
    assert result["status"] == "success", result
    data = result["data"]
    execution = data["execution"]
    assert execution["context_owner_verified"] is True
    assert execution["state"] == "COMPLETE"
    assert execution["terminal"] is True
    assert execution["sys_id"] == CONTEXT
    assert data["replay_safe"] is False
    return (data, execution)


def assert_snapshot_diagnostic(data, code):
    assert data["complete"] is False
    assert [
        (item["code"], item["path"], item["incomplete"]) for item in data["diagnostics"]
    ] == [(code, "execution.snapshot", True)]


@pytest.mark.parametrize(
    "mode,owner,wrapped,state",
    [
        pytest.param(
            "status",
            AUTHORING,
            True,
            "COMPLETE",
            id="COMPLETE-reference-value-authoring-ancestor-status",
        )
    ],
)
async def test_exact_owned_compiled_snapshot_does_not_verify_content_or_version(
    settings, mode, owner, wrapped, state
):
    snapshot = {"value": COMPILED, "display_value": MASTER} if wrapped else COMPILED
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        200, json={"result": compiled_record()}
    )
    ancestor = http_mock.get(f"{BASE}/api/now/processflow/flow/{AUTHORING}").respond(
        200,
        json={
            "result": {"data": {"id": AUTHORING, "parentFlow": FLOW, "snapshot": True}}
        },
    )
    result = await observe_execution(
        settings, mode, execution_context(owner=owner, state=state, snapshot=snapshot)
    )
    assert result["status"] == "success", result
    data = result["data"]
    execution = data["execution"]
    assert data["complete"] is True and data["diagnostics"] == []
    assert execution["context_owner_verified"] is True
    assert execution["state"] == state
    assert execution["terminal"] is (state == "COMPLETE")
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=True)
    assert execution["sys_id"] == CONTEXT
    assert data["flow"]["published_snapshot"] == MASTER != COMPILED
    assert data["flow"]["latest_snapshot"] == LATEST != COMPILED
    assert data["replay_safe"] is False
    assert ancestor.called is (owner == AUTHORING)
    if owner == AUTHORING:
        assert dict(ancestor.calls.last.request.url.params) == {
            "use_definition": "0",
            "use_min_flow": "true",
        }
    assert_compiled_reads(COMPILED)


@pytest.mark.parametrize(
    "mode,record,http_status,code",
    [
        pytest.param(
            "status",
            compiled_record(parent=OTHER),
            200,
            "EXECUTION_SNAPSHOT_MISMATCH",
            id="wrong-parent-status",
        ),
        pytest.param(
            "status", None, 200, "MALFORMED_EXECUTION_SNAPSHOT", id="null-record-status"
        ),
        pytest.param(
            "status",
            {"sys_id": COMPILED},
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="missing-parent-status",
        ),
        pytest.param(
            "status",
            compiled_record(parent=""),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="empty-parent-status",
        ),
        pytest.param(
            "status",
            compiled_record(sys_id=COMPILED.upper()),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="uppercase-identity-status",
        ),
        pytest.param(
            "status", {}, 403, "UNREADABLE_EXECUTION_SNAPSHOT", id="denied-status"
        ),
    ],
)
async def test_limited_compiled_record_evidence_preserves_known_execution(
    settings, mode, record, http_status, code
):
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        http_status, json={"result": record}
    )
    result = await observe_execution(settings, mode, execution_context())
    data, execution = assert_known_execution(result, mode)
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=False)
    assert_snapshot_diagnostic(data, code)
    assert_compiled_reads(COMPILED)


@pytest.mark.parametrize(
    "mode,snapshot,code",
    [
        pytest.param("status", "", "PENDING_EXECUTION_SNAPSHOT", id="pending-status"),
        pytest.param(
            "status",
            {"display_value": COMPILED},
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="display-only-reference-status",
        ),
    ],
)
async def test_missing_pending_or_malformed_snapshot_never_hides_terminal_context(
    settings, mode, snapshot, code
):
    result = await observe_execution(
        settings, mode, execution_context(snapshot=snapshot)
    )
    data, execution = assert_known_execution(result, mode)
    assert_snapshot_evidence(execution, snapshot_id=None, owner_verified=False)
    assert_snapshot_diagnostic(data, code)
    assert_compiled_reads(None)


@pytest.mark.parametrize(
    "mode,owner,state",
    [pytest.param("status", "", "COMPLETE", id="COMPLETE-empty-status")],
)
async def test_compiled_owner_does_not_resolve_pending_context_flow_race(
    settings, mode, owner, state
):
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        200, json={"result": compiled_record()}
    )
    result = await observe_execution(
        settings, mode, execution_context(owner=owner, state=state)
    )
    assert result["status"] == "success", result
    data = result["data"]
    execution = data["execution"]
    assert data["complete"] is False
    assert execution["context_owner_verified"] is False
    assert execution["terminal"] is None
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=True)
    assert execution["state"] is None
    assert data["replay_safe"] is False
    assert_compiled_reads(COMPILED)


async def test_status_compiled_ownership_does_not_override_unrelated_authoring_ancestry(
    settings,
):
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        200, json={"result": compiled_record()}
    )
    ancestry = http_mock.get(f"{BASE}/api/now/processflow/flow/{AUTHORING}").respond(
        200,
        json={
            "result": {"data": {"id": AUTHORING, "parentFlow": OTHER, "snapshot": True}}
        },
    )
    result = await observe_execution(
        settings, "status", execution_context(owner=AUTHORING)
    )
    assert result["status"] == "success", result
    data = result["data"]
    execution = data["execution"]
    assert data["complete"] is False
    assert execution["context_owner_verified"] is False
    assert execution["state"] is None and execution["terminal"] is None
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=True)
    assert dict(ancestry.calls.last.request.url.params) == {
        "use_definition": "0",
        "use_min_flow": "true",
    }
    assert_compiled_reads(COMPILED)
