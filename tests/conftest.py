"""Shared test fixtures and helpers."""

import ipaddress
import socket
import time
import webbrowser
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import httpx2
import pytest
from pydantic import SecretStr

from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider
from servicenow_mcp.config import Settings
from tests._mock_transport import http_mock


@pytest.fixture(autouse=True)
def _disable_sentry_capture() -> Generator[None, None, None]:
    """Prevent Sentry from capturing exceptions during tests.

    Resets the module-level ``_initialized`` flag so that
    ``capture_exception()`` short-circuits before reaching the real SDK.
    """
    import servicenow_mcp.sentry as _sentry_mod

    _sentry_mod._initialized = False
    yield
    _sentry_mod._initialized = False


@pytest.fixture()
def settings() -> Settings:
    """Create test settings with valid defaults."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        "SERVICENOW_ENV": "dev",
        "MCP_TOOL_PACKAGE": "full",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def prod_settings() -> Settings:
    """Create test settings for production environment."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://prod.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        "SERVICENOW_ENV": "prod",
        "MCP_TOOL_PACKAGE": "full",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def prod_auth_provider(prod_settings: Settings) -> OAuthPKCEProvider:
    """Create a OAuthPKCEProvider from production test settings."""
    return OAuthPKCEProvider(prod_settings)


@pytest.fixture(autouse=True)
def _isolate_unit_settings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not load developer credentials or telemetry settings in unit tests."""
    if "integration" not in request.node.path.parts:
        monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _stub_user_authorization(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Keep unit tests offline; authentication and integration tests use the real provider."""
    if request.node.path.name == "test_auth.py" or "integration" in request.node.path.parts:
        yield
        return
    with patch.object(
        OAuthPKCEProvider,
        "_authorize",
        return_value=AccessToken(value=SecretStr("test-only-token"), expires_at=time.monotonic() + 3600),
    ):
        yield


def _is_loopback(address: Any) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX paths never leave the host.
    host = str(address[0])
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _fail_closed_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block real network, browser and telemetry side effects in unit tests.

    Loopback sockets are allowed only for tests marked ``loopback``.
    """
    if "integration" in request.node.path.parts:
        return
    is_loopback_allowed = request.node.get_closest_marker("loopback") is not None
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo

    def check(address: Any) -> None:
        if not _is_loopback(address):
            raise RuntimeError(f"Unit tests must not open non-loopback connections: {address!r}")
        if not is_loopback_allowed:
            raise RuntimeError(f"Loopback connection requires @pytest.mark.loopback: {address!r}")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        check(address)
        original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        check(address)
        return original_connect_ex(self, address)

    def guarded_sendto(self: socket.socket, data: Any, *args: Any) -> int:
        check(args[-1])
        return original_sendto(self, data, *args)

    def guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        # Name resolution can leak to a remote DNS server before any connect() is attempted.
        if host is not None and not _is_loopback((host.decode() if isinstance(host, bytes) else host, port)):
            raise RuntimeError(f"Unit tests must not resolve non-loopback hosts: {host!r}")
        return original_getaddrinfo(host, port, *args, **kwargs)

    def blocked_browser(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("Unit tests must not open a browser")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", guarded_sendto)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(webbrowser, "open", blocked_browser)
    for name in ("SENTRY_DSN", "SENTRY_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    try:
        import sentry_sdk
    except ImportError:
        return

    def blocked_sentry_init(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Unit tests must not initialize remote Sentry telemetry")

    monkeypatch.setattr(sentry_sdk, "init", blocked_sentry_init)


@pytest.fixture(autouse=True)
def _mock_http(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Route every default httpx2 transport through ``http_mock``; unmatched requests fail the test.

    Tests marked ``real_http`` keep the real transport for loopback TLS and proxy fixtures.
    """
    if "integration" in request.node.path.parts or request.node.get_closest_marker("real_http"):
        yield
        return
    http_mock.reset()

    async def handle_async_request(_self: httpx2.AsyncHTTPTransport, req: httpx2.Request) -> httpx2.Response:
        await req.aread()
        return await http_mock.handle(req)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle_async_request)
    yield
    unexpected = [f"{req.method} {req.url}" for req in http_mock.unexpected]
    http_mock.reset()
    if unexpected:
        pytest.fail(f"Unexpected HTTP requests: {unexpected}")
