"""OAuth PKCE, token lifecycle, and real loopback receiver tests (no live credentials)."""

import asyncio
import base64
import hashlib
import socket
import time
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx
from pydantic import SecretStr

from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider, _parse_token, create_auth
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import AuthError
from servicenow_mcp.oauth_callback import _callback_result, receive_authorization_code


BASE_URL = "https://test.service-now.com"
REDIRECT = "http://127.0.0.1:8765/oauth/callback"


def _request(query: str, *, host: str = "127.0.0.1:8765", path: str = "/oauth/callback") -> bytes:
    return f"GET {path}?{query} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()


@pytest.fixture()
def redirect_uri() -> str:
    """Select an unused local port for an isolated callback test."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/oauth/callback"


async def _send(redirect_uri: str, query: str) -> bytes:
    url = urlsplit(redirect_uri)
    reader, writer = await asyncio.open_connection("127.0.0.1", url.port)
    try:
        writer.write(_request(query, host=url.netloc))
        await writer.drain()
        return await reader.read()
    finally:
        writer.close()
        await writer.wait_closed()


async def _assert_closed(redirect_uri: str) -> None:
    with pytest.raises(ConnectionRefusedError):
        await asyncio.open_connection("127.0.0.1", urlsplit(redirect_uri).port)
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", urlsplit(redirect_uri).port)
    server.close()
    await server.wait_closed()


@respx.mock
@pytest.mark.parametrize("has_refresh", [True, False])
async def test_query_401_then_reauthorize_on_same_loopback_port(
    settings: Settings, redirect_uri: str, has_refresh: bool
) -> None:
    """A tool retry renews a rejected token without a browser when refresh is available."""
    from mcp.server import MCPServer

    from servicenow_mcp.tools.query import register_tools
    from tests.helpers import decode_response, get_tool_functions

    settings.servicenow_oauth_redirect_uri = redirect_uri
    settings.servicenow_oauth_client_secret = SecretStr("test-only-secret")
    authorization: list[dict[str, list[str]]] = []

    async def browser(_open: Any, url: str, **kwargs: Any) -> bool:
        del kwargs
        params = parse_qs(urlsplit(url).query)
        authorization.append(params)
        assert params["redirect_uri"] == [redirect_uri]
        response = await _send(redirect_uri, urlencode({"state": params["state"][0], "code": "test-code"}))
        assert b"200 OK" in response
        return True

    token_route = respx.post(f"{BASE_URL}/oauth_token.do").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    **({"refresh_token": "test-refresh"} if has_refresh else {}),
                },
            )
            for token in ("rejected-token", "fresh-token")
        ]
    )
    api_route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
        side_effect=[httpx.Response(401, text="private"), httpx.Response(200, json={"result": []})]
    )
    provider = OAuthPKCEProvider(settings)
    server = MCPServer("test")
    register_tools(server, settings, provider)
    query = get_tool_functions(server)["query"]
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        first = decode_response(await query(table="incident", fields="sys_id"))
        assert first["status"] == "error"
        assert "authorize again" in str(first["error"])
        assert "REST request (HTTP 401)" in str(first["error"])
        assert "granted scopes" in str(first["error"])
        assert "private" not in str(first["error"])
        if has_refresh:
            assert provider._token is not None
            assert provider._token.expires_at == 0
        else:
            assert provider._token is None
        assert api_route.call_count == 1
        with pytest.raises(ConnectionRefusedError):
            await asyncio.open_connection("127.0.0.1", urlsplit(redirect_uri).port)
        second = decode_response(await query(table="incident", fields="sys_id"))
        assert second["status"] == "success", second
    assert token_route.call_count == 2
    assert api_route.call_count == 2
    assert api_route.calls.last.request.headers["Authorization"] == "Bearer fresh-token"
    assert len(authorization) == (1 if has_refresh else 2)
    if not has_refresh:
        assert authorization[0]["state"] != authorization[1]["state"]
        assert authorization[0]["code_challenge"] != authorization[1]["code_challenge"]
    forms = [parse_qs(call.request.content.decode()) for call in token_route.calls]
    assert forms[0]["grant_type"] == ["authorization_code"]
    assert forms[1]["grant_type"] == ["refresh_token" if has_refresh else "authorization_code"]
    assert all(form["client_secret"] == ["test-only-secret"] for form in forms)
    await _assert_closed(redirect_uri)


@respx.mock
@pytest.mark.parametrize("client_secret", ["", "test-only-client-secret"])
async def test_pkce_loopback_exchange_and_bearer_request(
    settings: Settings, redirect_uri: str, client_secret: str
) -> None:
    """Exercise real loopback HTTP, S256 exchange and the API bearer header together."""
    settings.servicenow_oauth_redirect_uri = redirect_uri
    if client_secret:
        settings = Settings.model_validate({**settings.model_dump(), "servicenow_oauth_client_secret": client_secret})
    authorization: dict[str, list[str]] = {}

    async def browser(_open: Any, url: str, **kwargs: Any) -> bool:
        del kwargs
        authorization.update(parse_qs(urlsplit(url).query))
        assert url.startswith(f"{BASE_URL}/oauth_auth.do?")
        invalid = await _send(redirect_uri, "state=wrong&code=attacker-code")
        assert b"400 Bad Request" in invalid
        query = urlencode({"state": authorization["state"][0], "code": "test-code"})
        response = await _send(redirect_uri, query)
        assert b"200 OK" in response
        assert b"Cache-Control: no-store" in response
        assert b"test-code" not in response
        return True

    def exchange(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        expected_fields = {"grant_type", "code", "client_id", "redirect_uri", "code_verifier"}
        if client_secret:
            expected_fields.add("client_secret")
            assert form["client_secret"] == [client_secret]
            assert client_secret not in str(authorization)
        assert set(form) == expected_fields
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["test-code"]
        assert form["redirect_uri"] == [redirect_uri]
        assert form["client_id"] == [settings.servicenow_oauth_client_id]
        assert "authorization" not in request.headers
        verifier = form["code_verifier"][0]
        assert 43 <= len(verifier) <= 128
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert authorization["code_challenge"] == [expected]
        assert authorization["code_challenge_method"] == ["S256"]
        assert authorization["response_type"] == ["code"]
        assert authorization["scope"] == [settings.servicenow_oauth_scope]
        assert len(authorization["state"][0]) >= 43
        return httpx.Response(
            200,
            json={
                "access_token": "test-token",
                "refresh_token": "test-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    token_route = respx.post(f"{BASE_URL}/oauth_token.do").mock(side_effect=exchange)
    api_route = respx.get(f"{BASE_URL}/api/now/table/incident/test-id").respond(
        200, json={"result": {"sys_id": "test-id"}}
    )
    provider = create_auth(settings)
    assert isinstance(provider, OAuthPKCEProvider)
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser) as launch:
        async with ServiceNowClient(settings, provider) as client:
            assert await client.get_record("incident", "test-id") == {"sys_id": "test-id"}
            await client.get_record("incident", "test-id")
        launch.assert_awaited_once()
    assert token_route.call_count == 1
    assert api_route.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert "x-sn-apikey" not in api_route.calls.last.request.headers
    assert "X-Correlation-ID" in api_route.calls.last.request.headers
    await _assert_closed(redirect_uri)


@pytest.mark.parametrize(
    "query",
    [
        "code=x",
        "state=wrong&code=x",
        "state=expected&state=expected&code=x",
        "state=expected&code=x&code=y",
        "state=%FF&code=x",
        "state=expected&bad",
    ],
)
def test_invalid_callback_does_not_consume_grant(query: str) -> None:
    assert _callback_result(_request(query), REDIRECT, "expected") is None


@pytest.mark.parametrize(
    "path", ["/favicon.ico", "http://evil.invalid/oauth/callback", "//evil.invalid/oauth/callback"]
)
def test_callback_path_is_exact(path: str) -> None:
    assert _callback_result(_request("state=expected&code=x", path=path), REDIRECT, "expected") is None


def test_callback_host_and_method_are_checked() -> None:
    assert _callback_result(_request("state=expected&code=x", host="evil.invalid"), REDIRECT, "expected") is None
    request = _request("state=expected&code=x").replace(b"GET ", b"POST ")
    assert _callback_result(request, REDIRECT, "expected") is None


@pytest.mark.parametrize(
    "query",
    ["state=expected&error=access_denied&error_description=private", "state=expected", "state=expected&code=%0D%0A"],
)
def test_callback_errors_are_sanitized(query: str) -> None:
    result = _callback_result(_request(query), REDIRECT, "expected")
    assert isinstance(result, AuthError)
    assert "private" not in str(result)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"access_token": "test\r\nheader"},
        {"token_type": "Basic"},
        {"expires_in": 0},
        {"expires_in": -1},
        {"expires_in": True},
        {"expires_in": float("inf")},
        {"expires_in": "NaN"},
        {"expires_in": None},
        {"refresh_token": ""},
        {"refresh_token": "private\r\n"},
        {"refresh_token": 123},
    ],
)
def test_invalid_token_response_rejected(payload: Any) -> None:
    if isinstance(payload, dict) and payload:
        payload = {"access_token": "test-token", "token_type": "Bearer", "expires_in": 60, **payload}
    with pytest.raises(AuthError):
        _parse_token(payload, 100)


def test_token_expiry_and_repr() -> None:
    token = _parse_token(
        {"access_token": "test-token", "token_type": "bearer", "expires_in": "100", "refresh_token": "test-refresh"},
        100,
    )
    assert token.expires_at == 190
    assert "test-token" not in repr(token)
    assert token.refresh_token == "test-refresh"
    assert "test-refresh" not in repr(token)


async def test_expiry_requires_new_flow_and_concurrent_calls_share_it(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("expired", time.monotonic() - 1)
    authorize = AsyncMock(return_value=AccessToken("fresh", time.monotonic() + 3600))
    with patch.object(provider, "_authorize", authorize):
        results = await asyncio.gather(*(provider.get_headers() for _ in range(8)))
    authorize.assert_awaited_once()
    assert all(headers["Authorization"] == "Bearer fresh" for headers in results)


@pytest.mark.parametrize("rotated", [True, False])
@respx.mock
async def test_expiry_refresh_is_single_flight_and_keeps_rotation(settings: Settings, rotated: bool) -> None:
    settings = Settings.model_validate(
        {**settings.model_dump(), "servicenow_oauth_client_secret": SecretStr("test-only-secret")}
    )
    provider = OAuthPKCEProvider(settings)
    provider._token = _parse_token(
        {"access_token": "old", "token_type": "Bearer", "expires_in": 60, "refresh_token": "old-refresh"}, 0
    )
    payload = {"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}
    if rotated:
        payload["refresh_token"] = "new-refresh"
    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(200, json=payload)
    with patch("servicenow_mcp.auth.receive_authorization_code") as browser:
        results = await asyncio.gather(*(provider.get_headers() for _ in range(8)))
        browser.assert_not_called()
    assert all(headers["Authorization"] == "Bearer fresh" for headers in results)
    assert route.call_count == 1
    assert parse_qs(route.calls.last.request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["old-refresh"],
        "client_id": [settings.servicenow_oauth_client_id],
        "client_secret": ["test-only-secret"],
    }
    assert provider._token is not None
    assert provider._token.refresh_token == ("new-refresh" if rotated else "old-refresh")


@respx.mock
async def test_rejected_rest_token_refreshes_on_next_call_without_replay(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = _parse_token(
        {"access_token": "old", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "refresh"},
        time.monotonic(),
    )
    api = respx.get(f"{BASE_URL}/api/now/table/incident/test-id").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"result": {"sys_id": "test-id"}})]
    )
    token = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}
    )
    with patch("servicenow_mcp.auth.receive_authorization_code") as browser:
        async with ServiceNowClient(settings, provider) as client:
            with pytest.raises(AuthError, match="HTTP 401"):
                await client.get_record("incident", "test-id")
            assert api.call_count == 1
            assert token.call_count == 0
            assert await client.get_record("incident", "test-id") == {"sys_id": "test-id"}
        browser.assert_not_called()
    assert api.calls.last.request.headers["Authorization"] == "Bearer fresh"
    assert token.call_count == 1
    provider.invalidate("Bearer old")
    assert (await provider.get_headers())["Authorization"] == "Bearer fresh"
    assert token.call_count == 1


@respx.mock
async def test_invalid_refresh_grant_authorizes_once_for_concurrent_calls(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = _parse_token(
        {"access_token": "old", "token_type": "Bearer", "expires_in": 60, "refresh_token": "revoked"}, 0
    )
    route = respx.post(f"{BASE_URL}/oauth_token.do").mock(
        side_effect=[
            httpx.Response(400, json={"error": "invalid_grant", "error_description": "private"}),
            httpx.Response(200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}),
        ]
    )
    with patch("servicenow_mcp.auth.receive_authorization_code", return_value="test-code") as browser:
        headers = await asyncio.gather(*(provider.get_headers() for _ in range(8)))
        browser.assert_awaited_once()
    assert all(item["Authorization"] == "Bearer fresh" for item in headers)
    assert route.call_count == 2
    assert parse_qs(route.calls[0].request.content.decode())["grant_type"] == ["refresh_token"]
    assert parse_qs(route.calls[1].request.content.decode())["grant_type"] == ["authorization_code"]


@pytest.mark.parametrize(
    "failure", ["client", "bad_grant", "server", "network", "json", "error_json", "token", "cancel"]
)
@respx.mock
async def test_refresh_failure_does_not_open_browser_or_send_expired_token(settings: Settings, failure: str) -> None:
    provider = OAuthPKCEProvider(settings)
    expired = _parse_token(
        {"access_token": "old", "token_type": "Bearer", "expires_in": 60, "refresh_token": "test-refresh"}, 0
    )
    provider._token = expired
    route = respx.post(f"{BASE_URL}/oauth_token.do")
    cancelled_exchange = asyncio.Event()
    if failure == "network":
        route.mock(side_effect=httpx.ConnectError("private"))
    elif failure == "cancel":

        async def cancel(_request: httpx.Request) -> httpx.Response:
            cancelled_exchange.set()
            raise asyncio.CancelledError

        route.mock(side_effect=cancel)
    elif failure == "json":
        route.respond(200, text="private")
    elif failure == "token":
        route.respond(200, json={"access_token": "private"})
    elif failure == "error_json":
        route.respond(400, text="private")
    elif failure == "bad_grant":
        route.respond(400, json={"error": "invalid_client", "error_description": "private"})
    else:
        route.respond(401 if failure == "client" else 503, json={"error": "invalid_client", "detail": "private"})
    with patch("servicenow_mcp.auth.receive_authorization_code") as browser:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else AuthError) as exc:
            await provider.get_headers()
        assert "private" not in str(exc.value)
        assert provider._token is expired
        if failure == "cancel":
            assert cancelled_exchange.is_set()
        else:
            assert route.call_count == 1
        route.calls.clear()
        route.respond(200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600})
        assert (await provider.get_headers())["Authorization"] == "Bearer fresh"
        browser.assert_not_called()
    assert route.call_count == 1


@respx.mock
async def test_refresh_expired_during_exchange_does_not_open_browser(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = _parse_token(
        {"access_token": "old", "token_type": "Bearer", "expires_in": 60, "refresh_token": "refresh"}, 0
    )
    respx.post(f"{BASE_URL}/oauth_token.do").respond(
        200, json={"access_token": "too-late", "token_type": "Bearer", "expires_in": 1}
    )
    with (
        patch("servicenow_mcp.auth.receive_authorization_code") as browser,
        patch("servicenow_mcp.auth.time.monotonic", side_effect=[100, 100, 102]),
        pytest.raises(AuthError, match="expired during exchange"),
    ):
        await provider.get_headers()
    browser.assert_not_called()


async def test_failed_authorization_cannot_reuse_expired_token(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("expired", 0)
    with (
        patch.object(provider, "_authorize", side_effect=AuthError("denied")),
        pytest.raises(AuthError, match="denied"),
    ):
        await provider.get_headers()
    assert provider._token is None


@respx.mock
async def test_401_invalidates_without_replaying_request(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("test-token", time.monotonic() + 3600)
    route = respx.get(f"{BASE_URL}/api/now/table/incident/test-id").respond(401, json={"error": "private"})
    async with ServiceNowClient(settings, provider) as client:
        with pytest.raises(AuthError, match="authorize again"):
            await client.get_record("incident", "test-id")
    assert provider._token is None
    assert route.call_count == 1


def test_stale_401_does_not_invalidate_new_token(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("new-token", 100)
    provider.invalidate("Bearer old-token")
    assert provider._token is not None


@pytest.mark.parametrize("status", [302, 400, 401, 500])
@respx.mock
async def test_exchange_failure_is_sanitized(settings: Settings, status: int, caplog: pytest.LogCaptureFixture) -> None:
    settings.servicenow_oauth_client_secret = SecretStr("test-only-secret")
    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        status,
        text="private test-only-secret test-code test-access test-refresh",
        headers={"Location": "https://evil.invalid"},
    )
    with (
        patch("servicenow_mcp.auth.receive_authorization_code", return_value="test-code"),
        pytest.raises(AuthError, match=f"HTTP {status}") as exc,
    ):
        await OAuthPKCEProvider(settings).get_headers()
    assert "private" not in str(exc.value)
    for sensitive in ("test-only-secret", "test-code", "test-access", "test-refresh"):
        assert sensitive not in str(exc.value)
        assert sensitive not in caplog.text
    assert route.call_count == 1


@respx.mock
async def test_exchange_network_and_json_errors(settings: Settings) -> None:
    route = respx.post(f"{BASE_URL}/oauth_token.do").mock(side_effect=httpx.ConnectError("private"))
    with patch("servicenow_mcp.auth.receive_authorization_code", return_value="test-code"):
        with pytest.raises(AuthError, match="connectivity") as exc:
            await OAuthPKCEProvider(settings).get_headers()
        assert "private" not in str(exc.value)
        route.respond(200, text="not JSON")
        with pytest.raises(AuthError, match="invalid JSON"):
            await OAuthPKCEProvider(settings).get_headers()


async def test_browser_failure_closes_listener(redirect_uri: str) -> None:
    with (
        patch("servicenow_mcp.oauth_callback.webbrowser.open", return_value=False),
        pytest.raises(AuthError, match="Cannot open"),
    ):
        await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
    await _assert_closed(redirect_uri)


async def test_timeout_closes_listener(redirect_uri: str) -> None:
    with (
        patch("servicenow_mcp.oauth_callback.webbrowser.open", return_value=True),
        pytest.raises(AuthError, match="timed out"),
    ):
        await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
    await _assert_closed(redirect_uri)


async def test_cancellation_closes_listener_and_partial_connections(redirect_uri: str) -> None:
    opened = asyncio.Event()

    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        opened.set()
        return True

    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        task = asyncio.create_task(receive_authorization_code("https://example.invalid", redirect_uri, "expected", 10))
        await opened.wait()
        reader, writer = await asyncio.open_connection("127.0.0.1", urlsplit(redirect_uri).port)
        writer.write(b"GET ")
        await writer.drain()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        # Closing a socket with unread input can produce either EOF or a reset.
        with suppress(ConnectionResetError):
            assert await reader.read() == b""
        writer.close()
        with suppress(ConnectionResetError):
            await writer.wait_closed()
    await _assert_closed(redirect_uri)


async def test_occupied_port_does_not_open_browser(redirect_uri: str) -> None:
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", urlsplit(redirect_uri).port)
    async with server:
        with (
            patch("servicenow_mcp.oauth_callback.webbrowser.open") as browser,
            pytest.raises(AuthError, match="Cannot bind"),
        ):
            await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
        browser.assert_not_called()


async def test_denial_closes_listener(redirect_uri: str) -> None:
    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        await _send(redirect_uri, "state=expected&error=access_denied&error_description=private")
        return True

    with (
        patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser),
        pytest.raises(AuthError, match="denied") as exc,
    ):
        await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
    assert "private" not in str(exc.value)
    await _assert_closed(redirect_uri)


@pytest.mark.parametrize("query", ["state=expected", "state=expected&code=%0D%0A"])
async def test_malformed_state_bound_callback_closes_listener(redirect_uri: str, query: str) -> None:
    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        await _send(redirect_uri, query)
        return True

    with (
        patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser),
        pytest.raises(AuthError, match="valid authorization code"),
    ):
        await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
    await _assert_closed(redirect_uri)


async def test_invalid_callback_times_out_and_releases_port(redirect_uri: str) -> None:
    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        assert b"400 Bad Request" in await _send(redirect_uri, "state=wrong&code=untrusted")
        return True

    with (
        patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser),
        pytest.raises(AuthError, match="timed out"),
    ):
        await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1)
    await _assert_closed(redirect_uri)


async def test_cancellation_during_listener_start_releases_port(redirect_uri: str) -> None:
    started = asyncio.Event()
    start_serving = asyncio.Server.start_serving

    async def start(server: asyncio.Server) -> None:
        await start_serving(server)
        started.set()
        await asyncio.Future[None]()

    with (
        patch("servicenow_mcp.oauth_callback.asyncio.Server.start_serving", start),
        patch("servicenow_mcp.oauth_callback.webbrowser.open") as browser,
    ):
        task = asyncio.create_task(receive_authorization_code("https://example.invalid", redirect_uri, "expected", 10))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        browser.assert_not_called()
    await _assert_closed(redirect_uri)


@pytest.mark.parametrize("failure", ["http", "network", "json", "token", "cancel"])
@respx.mock
async def test_exchange_failure_leaves_loopback_port_reusable(
    settings: Settings, redirect_uri: str, failure: str
) -> None:
    settings.servicenow_oauth_redirect_uri = redirect_uri
    provider = OAuthPKCEProvider(settings)

    async def browser(_open: Any, url: str, **kwargs: Any) -> bool:
        del kwargs
        state = parse_qs(urlsplit(url).query)["state"][0]
        await _send(redirect_uri, urlencode({"state": state, "code": "test-code"}))
        return True

    async def exchange(_request: httpx.Request) -> httpx.Response:
        await _assert_closed(redirect_uri)
        if failure == "network":
            raise httpx.ConnectError("private")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "http":
            return httpx.Response(400, text="private")
        if failure == "json":
            return httpx.Response(200, text="private")
        return httpx.Response(200, json={"access_token": "private"})

    route = respx.post(f"{BASE_URL}/oauth_token.do").mock(side_effect=exchange)
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else AuthError):
            await provider.get_headers()
        assert provider._token is None
        route.respond(200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600})
        assert (await provider.get_headers())["Authorization"] == "Bearer fresh"
    await _assert_closed(redirect_uri)


@respx.mock
async def test_concurrent_authorization_and_cancelled_waiter_use_one_listener(
    settings: Settings, redirect_uri: str
) -> None:
    settings.servicenow_oauth_redirect_uri = redirect_uri
    opened = asyncio.Event()
    authorization: dict[str, list[str]] = {}

    async def browser(_open: Any, url: str, **kwargs: Any) -> bool:
        del kwargs
        authorization.update(parse_qs(urlsplit(url).query))
        opened.set()
        return True

    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}
    )
    provider = OAuthPKCEProvider(settings)
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser) as launch:
        owner = asyncio.create_task(provider.get_headers())
        await opened.wait()
        waiters = [asyncio.create_task(provider.get_headers()) for _ in range(8)]
        await asyncio.sleep(0)
        waiters[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiters[0]
        await _send(redirect_uri, urlencode({"state": authorization["state"][0], "code": "test-code"}))
        headers = await asyncio.gather(owner, *waiters[1:])
        assert all(item["Authorization"] == "Bearer fresh" for item in headers)
        launch.assert_awaited_once()
    assert route.call_count == 1
    await _assert_closed(redirect_uri)


@respx.mock
async def test_cancelled_owner_allows_waiting_call_to_reauthorize(settings: Settings, redirect_uri: str) -> None:
    settings.servicenow_oauth_redirect_uri = redirect_uri
    opened = asyncio.Event()
    states: list[str] = []

    async def browser(_open: Any, url: str, **kwargs: Any) -> bool:
        del kwargs
        states.append(parse_qs(urlsplit(url).query)["state"][0])
        opened.set()
        return True

    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}
    )
    provider = OAuthPKCEProvider(settings)
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        owner = asyncio.create_task(provider.get_headers())
        await opened.wait()
        opened.clear()
        waiter = asyncio.create_task(provider.get_headers())
        # A rejected request leaves TCP cleanup to exercise reuse after cancellation.
        assert b"400 Bad Request" in await _send(redirect_uri, "state=wrong&code=untrusted")
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(owner), timeout=0.5)
        await asyncio.wait_for(opened.wait(), timeout=1)
        assert b"400 Bad Request" in await _send(redirect_uri, urlencode({"state": states[0], "code": "old"}))
        await _send(redirect_uri, urlencode({"state": states[1], "code": "new"}))
        assert (await waiter)["Authorization"] == "Bearer fresh"
    assert states[0] != states[1]
    assert route.call_count == 1
    await _assert_closed(redirect_uri)


@pytest.mark.parametrize("has_callback", [True, False])
async def test_flow_exit_closes_idle_browser_connection(redirect_uri: str, has_callback: bool) -> None:
    opened = asyncio.Event()

    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        opened.set()
        return True

    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        task = asyncio.create_task(receive_authorization_code("https://example.invalid", redirect_uri, "expected", 1))
        await opened.wait()
        reader, writer = await asyncio.open_connection("127.0.0.1", urlsplit(redirect_uri).port)
        try:
            if has_callback:
                await _send(redirect_uri, "state=expected&code=accepted")
                assert await asyncio.wait_for(asyncio.shield(task), timeout=0.5) == "accepted"
            else:
                with pytest.raises(AuthError, match="timed out"):
                    await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    await _assert_closed(redirect_uri)


async def test_expired_exchange_result_is_never_sent(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    with (
        patch.object(provider, "_authorize", return_value=AccessToken("expired", 0)),
        pytest.raises(AuthError, match="expired during authorization"),
    ):
        await provider.get_headers()
    assert provider._token is None


@respx.mock
async def test_reauthorization_uses_new_state_and_verifier(settings: Settings) -> None:
    urls: list[str] = []

    async def receive(url: str, *_args: Any) -> str:
        urls.append(url)
        return "test-code"

    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        200, json={"access_token": "test-token", "token_type": "Bearer", "expires_in": 100}
    )
    provider = OAuthPKCEProvider(settings)
    with (
        patch("servicenow_mcp.auth.receive_authorization_code", side_effect=receive),
        patch("servicenow_mcp.auth.time.monotonic", return_value=100) as clock,
    ):
        await provider.get_headers()
        clock.return_value = 200
        await provider.get_headers()
    assert route.call_count == 2
    first, second = [parse_qs(urlsplit(url).query) for url in urls]
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]
    for call in route.calls:
        form = parse_qs(call.request.content.decode())
        assert "refresh_token" not in form
        assert form["grant_type"] == ["authorization_code"]


async def test_oversized_and_replayed_callbacks_do_not_replace_code(redirect_uri: str) -> None:
    async def browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        url = urlsplit(redirect_uri)
        reader, writer = await asyncio.open_connection("127.0.0.1", url.port)
        writer.write(b"X" * 9000 + b"\r\n\r\n")
        await writer.drain()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
        assert b"200 OK" in await _send(redirect_uri, "state=expected&code=first")
        assert b"400 Bad Request" in await _send(redirect_uri, "state=expected&code=replay")
        return True

    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        assert await receive_authorization_code("https://example.invalid", redirect_uri, "expected", 2) == "first"
    await _assert_closed(redirect_uri)


@respx.mock
async def test_auth_failure_keeps_tool_error_envelope(settings: Settings) -> None:
    from mcp.server import MCPServer

    from servicenow_mcp.tools.query import register_tools
    from tests.helpers import decode_response, get_tool_functions

    provider = OAuthPKCEProvider(settings)
    server = MCPServer("test")
    register_tools(server, settings, provider)
    with patch.object(provider, "_authorize", side_effect=AuthError("Authorization denied")):
        result = decode_response(await get_tool_functions(server)["query"](table="incident", fields="sys_id"))
    assert result["status"] == "error"
    assert result["data"] is None
    assert result["correlation_id"]
    assert "Authorization denied" in str(result["error"])
    assert not respx.calls
