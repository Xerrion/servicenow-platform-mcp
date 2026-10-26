"""Exact compiled execution ownership is independent of authoring and content evidence."""

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools
from tests.test_flow_completion import effect_routes
from tests.test_flow_editing import BASE, FLOW
from tests.test_flow_lifecycle import capture_routes

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


async def apply_once(tools, args, effect):
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    assert not effect.called
    token = preview["data"]["preview_token"]
    result = decode_response(await tools["mutation_apply"](preview_token=token))
    assert effect.call_count == 1
    replay = decode_response(await tools["mutation_apply"](preview_token=token))
    assert replay["status"] == "error" and effect.call_count == 1
    assert [
        call.request.url.path
        for call in http_mock.calls
        if call.request.method != "GET"
    ] == [f"/api/now/processflow/flow/{FLOW}/test"]
    return result


async def observe_execution(settings, mode, context):
    read = http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{CONTEXT}").respond(
        200, json={"result": context}
    )
    if mode == "status":
        capture_routes()
        tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
        result = decode_response(
            await tools["flow"](action="status", sys_id=FLOW, execution_sys_id=CONTEXT)
        )
        assert all(call.request.method == "GET" for call in http_mock.calls)
    else:
        _, tools, _, _, args = effect_routes(settings, "test")
        effect = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
            200, json={"result": {"data": CONTEXT}}
        )
        result = await apply_once(tools, args, effect)
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
        and not call.request.url.path.startswith("/api/now/table/sys_hub_flow_snapshot")
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
    execution = data["execution"] if mode == "status" else data
    assert execution["context_owner_verified"] is True
    assert execution["state"] == "COMPLETE"
    assert execution["terminal"] is True
    if mode == "test":
        assert execution["execution_sys_id"] == CONTEXT
        assert execution["execution_succeeded"] is True
        assert execution["version_content_verified"] is False
    else:
        assert execution["sys_id"] == CONTEXT
        assert data["replay_safe"] is False
    return data, execution


def assert_snapshot_diagnostic(data, code):
    assert data["complete"] is False
    assert [
        (item["code"], item["path"], item["incomplete"]) for item in data["diagnostics"]
    ] == [(code, "execution.snapshot", True)]


@pytest.mark.parametrize("mode", ["status", "test"])
@pytest.mark.parametrize(
    "owner", [FLOW, AUTHORING], ids=["owner", "authoring-ancestor"]
)
@pytest.mark.parametrize("wrapped", [False, True], ids=["raw", "reference-value"])
@pytest.mark.parametrize("state", ["COMPLETE", "WAITING"])
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
    execution = data["execution"] if mode == "status" else data
    assert data["complete"] is True and data["diagnostics"] == []
    assert execution["context_owner_verified"] is True
    assert execution["state"] == state
    assert execution["terminal"] is (state == "COMPLETE")
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=True)
    if mode == "test":
        assert execution["execution_sys_id"] == CONTEXT
        assert execution["execution_succeeded"] is (
            True if state == "COMPLETE" else None
        )
        assert execution["version_content_verified"] is False
    else:
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


@pytest.mark.parametrize("mode", ["status", "test"])
@pytest.mark.parametrize(
    "record,http_status,code",
    [
        pytest.param(
            compiled_record(parent=OTHER),
            200,
            "EXECUTION_SNAPSHOT_MISMATCH",
            id="wrong-parent",
        ),
        pytest.param(
            compiled_record(sys_id=OTHER),
            200,
            "EXECUTION_SNAPSHOT_MISMATCH",
            id="wrong-identity",
        ),
        pytest.param({}, 200, "MALFORMED_EXECUTION_SNAPSHOT", id="missing-record"),
        pytest.param(None, 200, "MALFORMED_EXECUTION_SNAPSHOT", id="null-record"),
        pytest.param([], 200, "MALFORMED_EXECUTION_SNAPSHOT", id="non-record"),
        pytest.param(
            {"sys_id": COMPILED},
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="missing-parent",
        ),
        pytest.param(
            {"parent": FLOW},
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="missing-identity",
        ),
        pytest.param(
            compiled_record(parent=""),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="empty-parent",
        ),
        pytest.param(
            compiled_record(parent={"display_value": FLOW}),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="display-only-parent",
        ),
        pytest.param(
            compiled_record(sys_id=COMPILED.upper()),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="uppercase-identity",
        ),
        pytest.param(
            compiled_record(parent=FLOW + "\n"),
            200,
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="newline-parent",
        ),
        pytest.param({}, 403, "UNREADABLE_EXECUTION_SNAPSHOT", id="denied"),
        pytest.param({}, 404, "UNREADABLE_EXECUTION_SNAPSHOT", id="not-found"),
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


@pytest.mark.parametrize("mode", ["status", "test"])
@pytest.mark.parametrize(
    "snapshot,code",
    [
        pytest.param(MISSING, "MISSING_EXECUTION_SNAPSHOT", id="missing"),
        pytest.param(None, "MISSING_EXECUTION_SNAPSHOT", id="null"),
        pytest.param("", "PENDING_EXECUTION_SNAPSHOT", id="pending"),
        pytest.param("not-an-id", "MALFORMED_EXECUTION_SNAPSHOT", id="invalid"),
        pytest.param(COMPILED.upper(), "MALFORMED_EXECUTION_SNAPSHOT", id="uppercase"),
        pytest.param(
            " " + COMPILED, "MALFORMED_EXECUTION_SNAPSHOT", id="leading-space"
        ),
        pytest.param(
            COMPILED + " ", "MALFORMED_EXECUTION_SNAPSHOT", id="trailing-space"
        ),
        pytest.param(
            "f" * 32 + "\n", "MALFORMED_EXECUTION_SNAPSHOT", id="trailing-newline"
        ),
        pytest.param(
            {"display_value": COMPILED},
            "MALFORMED_EXECUTION_SNAPSHOT",
            id="display-only-reference",
        ),
        pytest.param(
            {"value": None}, "MALFORMED_EXECUTION_SNAPSHOT", id="null-reference-value"
        ),
        pytest.param([COMPILED], "MALFORMED_EXECUTION_SNAPSHOT", id="array-reference"),
        pytest.param(True, "MALFORMED_EXECUTION_SNAPSHOT", id="boolean-reference"),
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


@pytest.mark.parametrize("mode", ["status", "test"])
@pytest.mark.parametrize("owner", [MISSING, None, ""], ids=["missing", "null", "empty"])
@pytest.mark.parametrize("state", ["COMPLETE", "QUEUED"])
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
    execution = data["execution"] if mode == "status" else data
    assert data["complete"] is False
    assert execution["context_owner_verified"] is False
    assert execution["terminal"] is None
    assert_snapshot_evidence(execution, snapshot_id=COMPILED, owner_verified=True)
    if mode == "test":
        assert execution["execution_sys_id"] == CONTEXT
        assert execution["state"] == state
        assert execution["execution_succeeded"] is None
        assert execution["version_content_verified"] is False
        assert any(
            item["code"] == "PENDING_EXECUTION"
            and item["path"] == "execution"
            and item["incomplete"] is True
            for item in data["diagnostics"]
        )
    else:
        assert execution["state"] is None
        assert data["replay_safe"] is False
    assert_compiled_reads(COMPILED)


@pytest.mark.parametrize("state", [MISSING, None, ""], ids=["missing", "null", "empty"])
async def test_owned_compiled_snapshot_does_not_make_pending_test_state_terminal(
    settings, state
):
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        200, json={"result": compiled_record()}
    )
    result = await observe_execution(settings, "test", execution_context(state=state))
    assert result["status"] == "success", result
    data = result["data"]
    assert data["execution_sys_id"] == CONTEXT
    assert data["context_owner_verified"] is True
    assert data["state"] == (None if state is MISSING else state)
    assert data["terminal"] is None and data["execution_succeeded"] is None
    assert data["complete"] is False
    assert data["version_content_verified"] is False
    assert_snapshot_evidence(data, snapshot_id=COMPILED, owner_verified=True)
    assert [
        (item["code"], item["path"], item["incomplete"]) for item in data["diagnostics"]
    ] == [("PENDING_EXECUTION", "execution", True)]
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


@pytest.mark.parametrize("defect", ["denied", "owner"])
async def test_later_draft_readback_failure_keeps_accepted_execution_and_one_effect(
    settings, defect
):
    _, tools, flow, _, args = effect_routes(settings, "test")
    dispatched = False

    def draft(request):
        if dispatched and defect == "denied":
            return httpx2.Response(403, json={"error": "private-readback"})
        body = {**flow, "id": OTHER} if dispatched else flow
        return httpx2.Response(200, json={"result": {"data": body}})

    def effect(request):
        nonlocal dispatched
        dispatched = True
        return httpx2.Response(200, json={"result": {"data": CONTEXT}})

    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").mock(side_effect=draft)
    context = http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{CONTEXT}").respond(
        200, json={"result": execution_context()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{COMPILED}").respond(
        200, json={"result": compiled_record()}
    )
    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").mock(
        side_effect=effect
    )
    result = await apply_once(tools, args, write)
    assert result["status"] == "error", result
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert CONTEXT in result["error"]["message"]
    assert "private-readback" not in str(result)
    assert context.called
