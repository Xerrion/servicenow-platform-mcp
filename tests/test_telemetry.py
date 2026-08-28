"""Tests for shared ServiceNow HTTP transport and telemetry."""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientFactory, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.telemetry import HttpTelemetry, TelemetryAsyncClient
from tests.helpers import decode_response, get_tool_functions


class _CloseTrackingAsyncClient(httpx.AsyncClient):
    """Track transport close calls without changing HTTPX behavior."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.close_count = 0

    async def aclose(self) -> None:
        """Count and perform one close call."""
        self.close_count += 1
        await super().aclose()


class _McpProtocolServer(Protocol):
    """FastMCP protocol server subset used to enter its real lifespan."""

    lifespan: Callable[[Any], AbstractAsyncContextManager[Any]]


class _FastMcpWithRuntime(Protocol):
    """FastMCP runtime attributes used by lifecycle tests."""

    _mcp_server: _McpProtocolServer
    _sn_client_factory: ServiceNowClientProvider


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


@pytest.mark.asyncio()
async def test_owned_client_closes_transport(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A directly constructed client closes the transport it creates."""
    transport = AsyncMock(spec=httpx.AsyncClient)

    with patch("servicenow_mcp.client.httpx.AsyncClient", return_value=transport):
        async with ServiceNowClient(settings, auth_provider):
            pass

    transport.aclose.assert_awaited_once_with()


@pytest.mark.asyncio()
async def test_shared_client_reuses_transport_without_closing_it(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """Separate ServiceNow client contexts reuse and do not close shared transport."""
    transport = _CloseTrackingAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    factory = ServiceNowClientFactory(settings, auth_provider, transport)

    async with factory() as first:
        assert first._ensure_client() is transport
    async with factory() as second:
        assert second._ensure_client() is transport

    assert transport.close_count == 0
    await transport.aclose()
    assert transport.close_count == 1


@pytest.mark.asyncio()
async def test_shared_transport_isolates_request_headers_and_records_telemetry(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-request headers stay isolated while bounded aggregates record both calls."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": "34"},
            json={"result": {"sys_id": "abc123"}},
        )

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        timeout=settings.httpx_timeout_seconds,
        transport=httpx.MockTransport(handle),
    )
    factory = ServiceNowClientFactory(settings, auth_provider, transport)
    headers = [
        {"Authorization": "Bearer first", "Accept": "application/json"},
        {"Authorization": "Bearer second", "Accept": "application/json"},
    ]

    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    with patch.object(auth_provider, "get_headers", new=AsyncMock(side_effect=headers)):
        async with factory() as first:
            await first.get_record("incident", "abc123")
        async with factory() as second:
            await second.get_record("incident", "abc123")

    assert [request.headers["Authorization"] for request in requests] == ["Bearer first", "Bearer second"]
    correlation_ids = [request.headers["X-Correlation-ID"] for request in requests]
    assert len(set(correlation_ids)) == 2
    assert "Authorization" not in transport.headers
    assert "X-Correlation-ID" not in transport.headers

    snapshot = telemetry.snapshot()
    assert snapshot.request_count == 2
    assert snapshot.completed_request_count == 2
    assert snapshot.failed_request_count == 0
    assert snapshot.response_bytes > 0
    assert snapshot.total_duration_ms >= 0
    assert snapshot.shared_pool_request_count == 2

    logs = caplog.text
    assert "shared_pool=True" in logs
    assert "Bearer" not in logs
    assert "abc123" not in logs
    assert "incident" not in logs
    await transport.aclose()


@pytest.mark.asyncio()
async def test_repeated_tool_calls_share_one_transport(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """Separate MCP tool calls use the same server-provided HTTP transport."""
    from servicenow_mcp.tools.query import register_tools

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        timeout=settings.httpx_timeout_seconds,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                json={"result": [{"sys_id": "abc123"}]},
            )
        ),
    )
    factory = ServiceNowClientFactory(settings, auth_provider, transport)
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, client_factory=factory)
    query = get_tool_functions(mcp)["query"]

    first = decode_response(await query(table="incident", encoded_query="active=true"))
    second = decode_response(await query(table="incident", encoded_query="active=true"))

    assert first["status"] == "success"
    assert second["status"] == "success"
    snapshot = telemetry.snapshot()
    assert snapshot.request_count == 2
    assert snapshot.shared_pool_request_count == 2
    assert not transport.is_closed
    await transport.aclose()


@pytest.mark.asyncio()
async def test_failed_request_records_bounded_telemetry(settings: Settings) -> None:
    """A transport failure increments failure totals without recording response data."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        transport=httpx.MockTransport(fail),
        timeout=settings.httpx_timeout_seconds,
    )

    with pytest.raises(httpx.ConnectError):
        await transport.get("https://test.service-now.com/api/now/table/incident")

    snapshot = telemetry.snapshot()
    assert snapshot.request_count == 1
    assert snapshot.completed_request_count == 0
    assert snapshot.failed_request_count == 1
    assert snapshot.response_bytes == 0
    assert snapshot.shared_pool_request_count == 1
    await transport.aclose()


@pytest.mark.asyncio()
async def test_fastmcp_lifespan_closes_shared_transport_once_on_exception() -> None:
    """The real FastMCP lifespan closes shared HTTP state during exceptional shutdown."""
    from servicenow_mcp.server import create_mcp_server

    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "s3cret",
        "MCP_TOOL_PACKAGE": "none",
    }
    with patch.dict("os.environ", env, clear=True):
        mcp = create_mcp_server()

    runtime = cast("_FastMcpWithRuntime", cast("object", mcp))
    service_client = runtime._sn_client_factory()
    transport = service_client._ensure_client()

    with (
        patch.object(transport, "aclose", new=AsyncMock(wraps=transport.aclose)) as close,
        pytest.raises(RuntimeError, match="shutdown"),
    ):
        async with runtime._mcp_server.lifespan(runtime._mcp_server):
            raise RuntimeError("shutdown")

    async with runtime._mcp_server.lifespan(runtime._mcp_server):
        pass

    close.assert_awaited_once_with()
