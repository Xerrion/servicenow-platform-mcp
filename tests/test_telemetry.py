"""Tests for shared ServiceNow HTTP transport and telemetry."""

import asyncio
import json
import logging
import os
import stat
import sys
from typing import TYPE_CHECKING, Any, Protocol, cast, override
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp import telemetry as trace_module
from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientFactory
from servicenow_mcp.telemetry import (
    HttpTelemetry,
    TelemetryAsyncClient,
    trace_flow_preview,
    trace_flow_preview_boundary,
)
from tests.helpers import decode_response, get_tool_functions

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from servicenow_mcp.config import Settings


class _CloseTrackingAsyncClient(httpx2.AsyncClient):
    """Track transport close calls without changing HTTPX behavior."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.close_count = 0

    @override
    async def aclose(self) -> None:
        """Count and perform one close call."""
        self.close_count += 1
        await super().aclose()


class _McpProtocolServer(Protocol):
    """MCP protocol server subset used to enter its real lifespan."""

    lifespan: Callable[[Any], AbstractAsyncContextManager[Any]]


class _McpServerWithRuntime(Protocol):
    """MCPServer runtime attributes used by lifecycle tests."""

    _lowlevel_server: _McpProtocolServer


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """Create a OAuthPKCEProvider from test settings."""
    return OAuthPKCEProvider(settings)


@pytest.mark.asyncio()
async def test_owned_client_closes_transport(
    settings: Settings, auth_provider: OAuthPKCEProvider
) -> None:
    """A directly constructed client closes the transport it creates."""
    transport = AsyncMock(spec=httpx2.AsyncClient)

    with patch(
        "servicenow_mcp.platform._client_transport.httpx2.AsyncClient",
        return_value=transport,
    ):
        async with ServiceNowClient(settings, auth_provider):
            pass

    transport.aclose.assert_awaited_once_with()


@pytest.mark.asyncio()
async def test_shared_client_reuses_transport_without_closing_it(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
) -> None:
    """Separate ServiceNow client contexts reuse and do not close shared transport."""
    transport = _CloseTrackingAsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200))
    )
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
    auth_provider: OAuthPKCEProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-request headers stay isolated while bounded aggregates record both calls."""
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"Content-Length": "34"},
            json={"result": {"sys_id": "abc123"}},
        )

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        timeout=settings.httpx_timeout_seconds,
        transport=httpx2.MockTransport(handle),
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

    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer first",
        "Bearer second",
    ]
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
    auth_provider: OAuthPKCEProvider,
) -> None:
    """Separate MCP tool calls use the same server-provided HTTP transport."""
    from servicenow_mcp.mcp.tools.query import register_tools

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        timeout=settings.httpx_timeout_seconds,
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200,
                headers={"X-Total-Count": "1"},
                json={"result": [{"sys_id": "abc123"}]},
            ),
        ),
    )
    factory = ServiceNowClientFactory(settings, auth_provider, transport)
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider, client_factory=factory)
    query = get_tool_functions(mcp)["query"]

    first = decode_response(
        await query(table="incident", encoded_query="active=true", fields=["number"])
    )
    second = decode_response(
        await query(table="incident", encoded_query="active=true", fields=["number"])
    )

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

    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    telemetry = HttpTelemetry()
    transport = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        transport=httpx2.MockTransport(fail),
        timeout=settings.httpx_timeout_seconds,
    )

    with pytest.raises(httpx2.ConnectError):
        await transport.get("https://test.service-now.com/api/now/table/incident")

    snapshot = telemetry.snapshot()
    assert snapshot.request_count == 1
    assert snapshot.completed_request_count == 0
    assert snapshot.failed_request_count == 1
    assert snapshot.response_bytes == 0
    assert snapshot.shared_pool_request_count == 1
    await transport.aclose()


@pytest.mark.asyncio()
async def test_mcp_server_lifespan_closes_shared_transport_once_on_exception() -> None:
    """The real MCPServer lifespan closes shared HTTP state during exceptional shutdown."""
    from servicenow_mcp.server import create_mcp_server

    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        "SERVICENOW_OAUTH_SCOPE": "useraccount",
        "MCP_TOOL_PACKAGE": "none",
    }
    transport = TelemetryAsyncClient(telemetry=HttpTelemetry(), is_shared_pool=True)
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "servicenow_mcp.bootstrap.stdio.TelemetryAsyncClient",
            return_value=transport,
        ),
    ):
        mcp = create_mcp_server()

    runtime = cast("_McpServerWithRuntime", cast("object", mcp))

    with (
        patch.object(
            transport, "aclose", new=AsyncMock(wraps=transport.aclose)
        ) as close,
        pytest.raises(RuntimeError, match="shutdown"),
    ):
        async with runtime._lowlevel_server.lifespan(runtime._lowlevel_server):
            raise RuntimeError("shutdown")

    async with runtime._lowlevel_server.lifespan(runtime._lowlevel_server):
        pass

    close.assert_awaited_once_with()


def test_flow_preview_trace_uses_fixed_metadata_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    private = "private-sensitive-marker"
    with trace_flow_preview():
        trace_flow_preview_boundary(
            "protocol",
            arguments={
                "action": private,
                "edits": [{private: {"password": private}}],
                private: private,
            },
        )
        trace_flow_preview_boundary(
            "planner.in",
            action="edit",
            edits=private,
            flow={
                "actionInstances": [{"name": private}],
                "flowLogicInstances": [],
                "subFlowInstances": [],
            },
        )
        trace_flow_preview_boundary(
            "wire.structured",
            summary={
                "changes": [private],
                "before": [private],
                "after": [private],
                "data_pills": [private],
                "preview_token": private,
                private: private,
            },
        )
    records = [
        json.loads(record.getMessage().removeprefix("Flow preview boundary "))
        for record in caplog.records
    ]
    assert records[0]["argument_keys"] == ["action", "edits"]
    assert (records[0]["action"], records[0]["edit_count"]) == ("other", 1)
    assert records[1]["edit_count"] == -1
    assert records[1]["node_counts"] == {"action": 1, "flowlogic": 0, "subflow": 0}
    assert records[2]["change_count"] == records[2]["pill_count"] == 1
    assert private not in caplog.text
    assert "password" not in caplog.text
    assert "preview_token" not in caplog.text


def test_flow_preview_trace_restores_context_after_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        with trace_flow_preview():
            trace_flow_preview_boundary("protocol", action="edit", edits=[])
            raise RuntimeError("private-sensitive-marker")

    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    trace_flow_preview_boundary("protocol", action="edit", edits=[])
    assert not caplog.records
    with trace_flow_preview():
        trace_flow_preview_boundary("protocol", action="edit", edits=[])
        with pytest.raises(RuntimeError):
            fail()
        trace_flow_preview_boundary("command", action="edit", edits=[])
    trace_flow_preview_boundary("protocol", action="edit", edits=[])
    records = [
        json.loads(record.getMessage().removeprefix("Flow preview boundary "))
        for record in caplog.records
    ]
    assert len(records) == 3
    assert records[0]["trace_id"] == records[2]["trace_id"] != records[1]["trace_id"]
    assert "private-sensitive-marker" not in caplog.text


async def test_parallel_flow_preview_traces_do_not_share_correlation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")

    async def run() -> None:
        with trace_flow_preview():
            trace_flow_preview_boundary("protocol", action="edit", edits=[])
            await asyncio.sleep(0)
            trace_flow_preview_boundary("command", action="edit", edits=[])

    await asyncio.gather(run(), run())
    records = [
        json.loads(record.getMessage().removeprefix("Flow preview boundary "))
        for record in caplog.records
    ]
    assert len(records) == 4
    assert len({record["trace_id"] for record in records}) == 2
    for trace_id in {record["trace_id"] for record in records}:
        assert [
            record["boundary"] for record in records if record["trace_id"] == trace_id
        ] == ["protocol", "command"]


def test_flow_preview_trace_writes_stderr_not_protocol_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = logging.getLogger("servicenow_mcp.telemetry")
    handler = logging.StreamHandler(sys.stderr)
    with (
        patch.object(logger, "handlers", [handler]),
        patch.object(logger, "propagate", False),
        patch.object(logger, "level", logging.INFO),
        trace_flow_preview(),
    ):
        trace_flow_preview_boundary("protocol", action="edit", edits=[])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Flow preview boundary ")
    metadata = json.loads(captured.err.removeprefix("Flow preview boundary "))
    assert (metadata["boundary"], metadata["edit_count"]) == ("protocol", 0)


def test_preview_sink_is_owner_only_bounded_and_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trace_module, "_FLOW_TRACE_ROOT", tmp_path)
    monkeypatch.setattr(trace_module, "_FLOW_TRACE_MAX_BYTES", 2048)
    private = "private-sensitive-marker"
    with trace_flow_preview():
        for _ in range(20):
            trace_flow_preview_boundary(
                "protocol", arguments={"action": "edit", "edits": [{private: private}]}
            )
    directory = tmp_path / "servicenow-flow-preview-trace"
    target = directory / "boundaries.jsonl"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.stat().st_uid == os.getuid()
    content = target.read_text()
    assert 0 < len(content.encode()) <= 2048
    assert content.endswith("\n")
    records = [json.loads(line) for line in content.splitlines()]
    assert 0 < len(records) < 20
    assert all(record["edit_count"] == 1 for record in records)
    assert private not in content


@pytest.mark.parametrize(
    "unsafe",
    [
        "root_symlink",
        "directory_symlink",
        "file_symlink",
        "hardlink",
        "permissions",
        "directory_permissions",
        "root_permissions",
        "fifo",
    ],
)
def test_preview_sink_refuses_unsafe_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    monkeypatch.setattr(trace_module, "_FLOW_TRACE_ROOT", tmp_path)
    directory = tmp_path / "servicenow-flow-preview-trace"
    directory.mkdir(mode=0o700)
    target = directory / "boundaries.jsonl"
    other = tmp_path / "untouched"
    other.write_text("untouched")
    if unsafe == "root_symlink":
        root_link = tmp_path / "root_link"
        root_link.symlink_to(tmp_path, target_is_directory=True)
        monkeypatch.setattr(trace_module, "_FLOW_TRACE_ROOT", root_link)
    elif unsafe == "directory_symlink":
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "file_symlink":
        target.symlink_to(other)
    elif unsafe == "hardlink":
        other.chmod(0o600)
        os.link(other, target)
    elif unsafe == "permissions":
        target.write_text("untouched")
        target.chmod(0o644)
    elif unsafe == "directory_permissions":
        directory.chmod(0o755)
    elif unsafe == "root_permissions":
        tmp_path.chmod(0o777)
    else:
        os.mkfifo(target, mode=0o600)
    with trace_flow_preview():
        trace_flow_preview_boundary("protocol", action="edit", edits=[])
    assert other.read_text() == "untouched"
    if unsafe == "permissions":
        assert target.read_text() == "untouched"
    assert not (tmp_path / "boundaries.jsonl").exists()


def test_preview_sink_rejects_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trace_module, "_FLOW_TRACE_ROOT", tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: tmp_path.stat().st_uid + 1)
    with trace_flow_preview():
        trace_flow_preview_boundary("protocol", action="edit", edits=[])
    assert not (tmp_path / "servicenow-flow-preview-trace").exists()


@pytest.mark.parametrize("failure", ["open", "lock", "write"])
def test_preview_sink_failure_does_not_fail_trace_or_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    import fcntl

    monkeypatch.setattr(trace_module, "_FLOW_TRACE_ROOT", tmp_path)
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("private-sensitive-marker")

    module, attribute = (fcntl, "flock") if failure == "lock" else (os, failure)
    monkeypatch.setattr(module, attribute, fail)
    with trace_flow_preview():
        trace_flow_preview_boundary("protocol", action="edit", edits=[])
    assert "Flow preview boundary " in caplog.text
    assert "private-sensitive-marker" not in caplog.text
