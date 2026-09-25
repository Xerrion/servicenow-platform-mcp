"""The unit-test guard blocks real network, browser and telemetry side effects."""

import socket
import webbrowser

import pytest
import sentry_sdk


def test_non_loopback_connection_is_blocked() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="non-loopback"):
        sock.connect(("203.0.113.1", 443))


def test_non_loopback_connect_ex_is_blocked() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="non-loopback"):
        sock.connect_ex(("203.0.113.1", 443))


def test_non_loopback_dns_lookup_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="resolve non-loopback"):
        socket.getaddrinfo("example.invalid", 443)


async def test_async_non_loopback_dns_lookup_is_blocked() -> None:
    import asyncio

    with pytest.raises(RuntimeError, match="resolve non-loopback"):
        await asyncio.get_running_loop().getaddrinfo("example.invalid", 443)


def test_loopback_dns_lookup_is_allowed() -> None:
    assert socket.getaddrinfo("127.0.0.1", 443)


def test_non_loopback_datagram_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, pytest.raises(RuntimeError, match="non-loopback"):
        sock.sendto(b"x", ("203.0.113.1", 53))


def test_loopback_connection_requires_marker() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match=r"mark\.loopback"):
        sock.connect(("127.0.0.1", 9))


@pytest.mark.loopback()
def test_marked_loopback_connection_is_allowed() -> None:
    with socket.socket() as server, socket.socket() as client:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.connect(server.getsockname())


@pytest.mark.loopback()
def test_marked_test_still_blocks_non_loopback() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="non-loopback"):
        sock.connect(("203.0.113.1", 443))


def test_browser_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="browser"):
        webbrowser.open("https://example.invalid")


def test_sentry_init_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="Sentry"):
        sentry_sdk.init(dsn="https://public@example.invalid/1")


def test_unit_settings_ignore_env_files() -> None:
    from servicenow_mcp.config import Settings

    assert Settings.model_config.get("env_file") is None
