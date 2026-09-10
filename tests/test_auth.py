"""OAuth PKCE, token lifecycle, and real loopback receiver tests (no live credentials)."""

import asyncio
import base64
import hashlib
import socket
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx

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


@respx.mock
async def test_pkce_loopback_exchange_and_bearer_request(settings: Settings, redirect_uri: str) -> None:
    """Exercise real loopback HTTP, S256 exchange and the API bearer header together."""
    settings.servicenow_oauth_redirect_uri = redirect_uri
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
        assert set(form) == {"grant_type", "code", "client_id", "redirect_uri", "code_verifier"}
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
        return httpx.Response(200, json={"access_token": "test-token", "token_type": "Bearer", "expires_in": 3600})

    token_route = respx.post(f"{BASE_URL}/oauth_token.do").mock(side_effect=exchange)
    api_route = respx.get(f"{BASE_URL}/api/now/table/incident/test-id").respond(
        200, json={"result": {"sys_id": "test-id"}}
    )
    provider = create_auth(settings)
    assert isinstance(provider, OAuthPKCEProvider)
    with patch("servicenow_mcp.oauth_callback.asyncio.to_thread", side_effect=browser):
        async with ServiceNowClient(settings, provider) as client:
            assert await client.get_record("incident", "test-id") == {"sys_id": "test-id"}
            await client.get_record("incident", "test-id")
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
    ],
)
def test_invalid_token_response_rejected(payload: Any) -> None:
    if isinstance(payload, dict) and payload:
        payload = {"access_token": "test-token", "token_type": "Bearer", "expires_in": 60, **payload}
    with pytest.raises(AuthError):
        _parse_token(payload, 100)


def test_token_expiry_and_repr() -> None:
    token = _parse_token(
        {"access_token": "test-token", "token_type": "bearer", "expires_in": "100", "refresh_token": "ignored"}, 100
    )
    assert token.expires_at == 190
    assert "test-token" not in repr(token)
    assert not hasattr(token, "refresh_token")


async def test_expiry_requires_new_flow_and_concurrent_calls_share_it(settings: Settings) -> None:
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("expired", time.monotonic() - 1)
    authorize = AsyncMock(return_value=AccessToken("fresh", time.monotonic() + 3600))
    with patch.object(provider, "_authorize", authorize):
        results = await asyncio.gather(*(provider.get_headers() for _ in range(8)))
    authorize.assert_awaited_once()
    assert all(headers["Authorization"] == "Bearer fresh" for headers in results)


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
async def test_exchange_failure_is_sanitized(settings: Settings, status: int) -> None:
    route = respx.post(f"{BASE_URL}/oauth_token.do").respond(
        status, text="private", headers={"Location": "https://evil.invalid"}
    )
    with (
        patch("servicenow_mcp.auth.receive_authorization_code", return_value="test-code"),
        pytest.raises(AuthError, match=f"HTTP {status}") as exc,
    ):
        await OAuthPKCEProvider(settings).get_headers()
    assert "private" not in str(exc.value)
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
            await task
        assert await reader.read() == b""
        writer.close()
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
