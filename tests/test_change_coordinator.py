"""Preview effects, apply preconditions, and one-shot failure semantics."""

import asyncio
import base64
import time
from contextvars import Context
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from mcp.server import MCPServer
from pydantic import SecretStr

from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider, _RefreshRejected
from servicenow_mcp.capabilities.changes.models import RecordCommand
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.errors import AuthError
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.policy import DENIED_TABLES
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions, register_changes

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
SYS_ID = "a" * 32
ITEM = "b" * 32
RECORD = f"{BASE}/api/now/table/incident/{SYS_ID}"
ATTACHMENT = f"{BASE}/api/now/attachment/{SYS_ID}"
SC = f"{BASE}/api/sn_sc/servicecatalog"


def tools_for(settings: Settings):
    auth = OAuthPKCEProvider(settings)
    dictionary = AsyncMock(spec=DictionaryRegistry)
    dictionary.get_fields.return_value = []
    dictionary.get_chain.return_value = []
    server = MCPServer("change-test")
    coordinator = register_changes(
        server,
        settings,
        auth,
        frozenset({"records", "attachments", "catalog"}),
        dictionary=dictionary,
    )
    return auth, coordinator, get_tool_functions(server)


def fixture_routes():
    state = {
        "record": {"sys_id": SYS_ID, "state": "1", "password": "private-password"},
        "attachment": {
            "sys_id": SYS_ID,
            "table_name": "incident",
            "table_sys_id": SYS_ID,
            "file_name": "fixture.txt",
        },
        "cart": {"items": [{"sys_id": ITEM, "quantity": "1"}]},
        "item": {"sys_id": ITEM, "name": "Fixture item"},
    }
    for url, key in [
        (RECORD, "record"),
        (ATTACHMENT, "attachment"),
        (f"{SC}/cart", "cart"),
        (f"{SC}/items/{ITEM}", "item"),
    ]:
        http_mock.get(url).mock(
            side_effect=lambda request, key=key: httpx2.Response(
                200, json={"result": state[key]}
            )
        )
    http_mock.get(f"{SC}/items/{ITEM}/variables").respond(200, json={"result": []})
    return state


async def test_disabled_change_family_rejects_before_network(settings: Settings):
    _, coordinator, _ = tools_for(settings)
    coordinator.families = frozenset()
    command = RecordCommand(action="create", table="incident", data={"active": False})

    with pytest.raises(CapabilityError) as failure:
        await coordinator.preview(command)

    assert failure.value.code == "CHANGE_DISABLED"
    assert not http_mock.calls


CASES = [
    (
        "record_preview",
        {"action": "create", "table": "incident", "data": {"active": False}},
        "POST",
        f"{BASE}/api/now/table/incident",
    ),
    (
        "record_preview",
        {
            "action": "update",
            "table": "incident",
            "sys_id": SYS_ID,
            "data": {"state": "2"},
        },
        "PATCH",
        RECORD,
    ),
    (
        "record_preview",
        {"action": "delete", "table": "incident", "sys_id": SYS_ID},
        "DELETE",
        RECORD,
    ),
    (
        "attachment_preview",
        {
            "action": "upload",
            "table": "incident",
            "table_sys_id": SYS_ID,
            "file_name": "fixture.txt",
            "content_base64": base64.b64encode(b"fixture").decode(),
        },
        "POST",
        f"{BASE}/api/now/attachment/file",
    ),
    (
        "attachment_preview",
        {"action": "delete", "sys_id": SYS_ID},
        "DELETE",
        ATTACHMENT,
    ),
    (
        "service_catalog_preview",
        {"action": "order_now", "item_sys_id": ITEM, "variables": {"active": False}},
        "POST",
        f"{SC}/items/{ITEM}/order_now",
    ),
    (
        "service_catalog_preview",
        {"action": "add_to_cart", "item_sys_id": ITEM},
        "POST",
        f"{SC}/items/{ITEM}/add_to_cart",
    ),
    (
        "service_catalog_preview",
        {"action": "cart_submit"},
        "POST",
        f"{SC}/cart/submit_order",
    ),
    (
        "service_catalog_preview",
        {"action": "cart_checkout"},
        "POST",
        f"{SC}/cart/checkout",
    ),
]


@pytest.mark.parametrize(("tool", "arguments", "method", "url"), CASES)
async def test_each_preview_is_observational_and_apply_is_one_shot(
    settings: Settings,
    tool,
    arguments,
    method,
    url,
) -> None:
    fixture_routes()
    change = http_mock.route(method=method, url=url).respond(
        204 if method == "DELETE" else 200,
        json=None if method == "DELETE" else {"result": {"sys_id": SYS_ID}},
    )
    _, coordinator, tools = tools_for(settings)
    preview = decode_response(await tools[tool](**arguments))
    assert preview["status"] == "success"
    assert not change.called
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert "private-password" not in str(preview)
    token = preview["data"]["preview_token"]
    assert (
        decode_response(await tools["apply_change"](preview_token=token))["status"]
        == "success"
    )
    assert change.call_count == 1
    assert (
        decode_response(await tools["apply_change"](preview_token=token))["status"]
        == "error"
    )
    assert change.call_count == 1
    assert coordinator.store.retained_payload_bytes == 0


@pytest.mark.parametrize(
    ("tool", "arguments", "method", "url", "key"),
    [
        (*CASES[1], "record"),
        (*CASES[2], "record"),
        (*CASES[4], "attachment"),
        (*CASES[5], "item"),
        (*CASES[7], "cart"),
        (*CASES[8], "cart"),
    ],
)
async def test_detectable_drift_consumes_token_without_mutating(
    settings: Settings,
    tool,
    arguments,
    method,
    url,
    key,
) -> None:
    state = fixture_routes()
    change = http_mock.route(method=method, url=url).respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    _, _, tools = tools_for(settings)
    preview = decode_response(await tools[tool](**arguments))
    state[key]["changed"] = True
    failure = await tools["apply_change"](
        preview_token=preview["data"]["preview_token"]
    )
    assert failure.is_error
    assert failure.structured_content["error"]["code"] == "STALE_PREVIEW"
    assert not change.called
    assert (
        await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    ).is_error


@pytest.mark.parametrize(
    "change", ["production", "instance", "family", "table_policy", "authorization"]
)
async def test_apply_rechecks_scope_and_policy(settings: Settings, change: str) -> None:
    fixture_routes()
    auth, coordinator, tools = tools_for(settings)
    preview = decode_response(
        await tools["record_preview"](
            action="update", table="incident", sys_id=SYS_ID, data={"state": "2"}
        ),
    )
    if change == "production":
        settings.servicenow_env = "prod"
    elif change == "instance":
        settings.servicenow_instance_url = "https://other.service-now.com"
    elif change == "family":
        coordinator.families = frozenset()
    elif change == "authorization":
        auth._token = None
        await auth.get_headers()
    else:
        DENIED_TABLES.add("incident")
    try:
        assert (
            await tools["apply_change"](preview_token=preview["data"]["preview_token"])
        ).is_error
        assert all(call.request.method == "GET" for call in http_mock.calls)
        assert len(coordinator.store) == 0
    finally:
        if change == "table_policy":
            DENIED_TABLES.remove("incident")


async def test_reauthorization_during_precondition_read_prevents_dispatch(
    settings: Settings,
) -> None:
    fixture_routes()
    auth, _, tools = tools_for(settings)
    preview = decode_response(
        await tools["record_preview"](
            action="update", table="incident", sys_id=SYS_ID, data={"state": "2"}
        ),
    )

    async def change_grant(request):
        auth._token = None
        await Context().run(asyncio.create_task, auth.get_headers())
        return httpx2.Response(
            200,
            json={
                "result": {
                    "sys_id": SYS_ID,
                    "state": "1",
                    "password": "private-password",
                },
            },
        )

    http_mock.get(RECORD).mock(side_effect=change_grant)
    change = http_mock.patch(RECORD).respond(200, json={"result": {"sys_id": SYS_ID}})
    assert (
        await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert not change.called


@pytest.mark.parametrize(
    "failure", ["timeout", "invalid_json", "missing_result", "cancel"]
)
async def test_uncertain_outcome_never_replays(
    settings: Settings, failure: str
) -> None:
    fixture_routes()
    _, coordinator, tools = tools_for(settings)
    url = f"{BASE}/api/now/table/incident"

    async def respond(request):
        if failure == "timeout":
            raise httpx2.ReadTimeout("private marker", request=request)
        if failure == "cancel":
            raise asyncio.CancelledError
        return httpx2.Response(
            200, content=b"private marker" if failure == "invalid_json" else b"{}"
        )

    change = http_mock.post(url).mock(side_effect=respond)
    preview = decode_response(
        await tools["record_preview"](action="create", table="incident", data={})
    )
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    else:
        result = await tools["apply_change"](
            preview_token=preview["data"]["preview_token"]
        )
        assert result.is_error
        assert "unknown" in str(result).lower()
        assert "private marker" not in str(result)
    assert change.call_count == 1
    assert len(coordinator.store) == 0
    assert (
        await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert change.call_count == 1


async def test_invalidated_grant_cannot_open_browser_during_apply(
    settings: Settings,
) -> None:
    _, coordinator, _ = tools_for(settings)
    preview = await coordinator.preview(
        RecordCommand(action="create", table="incident", data={})
    )
    coordinator.auth._token = None
    with pytest.raises(AuthError, match="Authorization changed"):
        await coordinator.apply(preview.data["preview_token"])
    assert len(coordinator.store) == 0


@pytest.mark.parametrize("rejected", [False, True])
async def test_refresh_does_not_change_preview_grant_or_authorize_again(
    settings: Settings, rejected: bool
) -> None:
    auth, coordinator, tools = tools_for(settings)
    change = http_mock.post(f"{BASE}/api/now/table/incident").respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    preview = decode_response(
        await tools["record_preview"](action="create", table="incident", data={})
    )
    generation = auth.authorization_generation
    auth._token = AccessToken(
        value=SecretStr("fixture-expired"),
        expires_at=0,
        refresh_token=SecretStr("fixture-refresh"),
    )
    fresh = AccessToken(
        value=SecretStr("fixture-fresh"), expires_at=time.monotonic() + 3600
    )
    with (
        patch.object(auth, "_authorize", new_callable=AsyncMock) as browser,
        patch.object(
            auth, "_refresh", new_callable=AsyncMock, return_value=fresh
        ) as refresh,
    ):
        if rejected:
            refresh.side_effect = _RefreshRejected("Refresh rejected")
        applied = await tools["apply_change"](
            preview_token=preview["data"]["preview_token"]
        )
    assert applied.is_error is rejected
    assert change.call_count == int(not rejected)
    assert auth.authorization_generation == generation
    assert len(coordinator.store) == 0
    browser.assert_not_awaited()


async def test_policy_change_during_precondition_read_prevents_dispatch(
    settings: Settings,
) -> None:
    state = fixture_routes()
    _, _, tools = tools_for(settings)
    preview = decode_response(
        await tools["record_preview"](
            action="update", table="incident", sys_id=SYS_ID, data={"state": "2"}
        ),
    )

    def change_policy(request):
        settings.servicenow_env = "prod"
        return httpx2.Response(200, json={"result": state["record"]})

    http_mock.get(RECORD).mock(side_effect=change_policy)
    change = http_mock.patch(RECORD).respond(200, json={"result": {"sys_id": SYS_ID}})
    assert (
        await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert not change.called


async def test_concurrent_applications_make_one_change_request(
    settings: Settings,
) -> None:
    _, _, tools = tools_for(settings)
    change = http_mock.post(f"{BASE}/api/now/table/incident").respond(
        200, json={"result": {"sys_id": SYS_ID}}
    )
    preview = decode_response(
        await tools["record_preview"](action="create", table="incident", data={})
    )
    results = await asyncio.gather(
        *(
            tools["apply_change"](preview_token=preview["data"]["preview_token"])
            for _ in range(10)
        ),
    )
    assert sum(not value.is_error for value in results) == 1
    assert change.call_count == 1


@pytest.mark.parametrize(("case", "response"), [(3, {}), (3, []), (5, {}), (7, [])])
async def test_incomplete_change_response_reports_unknown_outcome(
    settings: Settings, case: int, response
) -> None:
    fixture_routes()
    _, _, tools = tools_for(settings)
    tool, arguments, method, url = CASES[case]
    change = http_mock.route(method=method, url=url).respond(
        200, json={"result": response}
    )
    preview = decode_response(await tools[tool](**arguments))
    result = await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    assert result.is_error
    assert result.structured_content["error"]["code"] == "UNKNOWN_OUTCOME"
    assert change.call_count == 1
    assert (
        await tools["apply_change"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert change.call_count == 1
