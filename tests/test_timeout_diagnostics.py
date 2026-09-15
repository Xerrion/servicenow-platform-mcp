"""Offline coverage for correlated, privacy-safe timeout diagnostics."""

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClientFactory
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.response import format_response
from servicenow_mcp.telemetry import (
    HttpTelemetry,
    TelemetryAsyncClient,
    configure_diagnostic_logging,
    current_tool_trace,
    trace_tool_call,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools.query import register_tools
from tests.helpers import decode_response, get_tool_functions


@pytest.fixture(autouse=True)
def _diagnostic_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("servicenow_mcp.telemetry", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "level", logger.level)
    configure_diagnostic_logging()


@pytest.mark.parametrize(
    ("error_type", "phase"),
    [
        (httpx.ConnectTimeout, "connect"),
        (httpx.ReadTimeout, "read"),
        (httpx.WriteTimeout, "write"),
        (httpx.PoolTimeout, "pool"),
        (httpx.TimeoutException, "unknown"),
    ],
)
async def test_timeout_error_is_correlated_and_redacted(
    error_type: type[httpx.TimeoutException], phase: str, caplog: pytest.LogCaptureFixture
) -> None:
    request = httpx.Request(
        "GET",
        "https://private.example/api/now/table/private_table?sysparm_query=private_filter",
        headers={"Authorization": "private_header"},
    )

    @tool_handler
    async def query() -> str:
        raise error_type("private_exception", request=request)

    result = decode_response(await query())
    error = result["error"]
    assert result["status"] == "error"
    assert error["code"] == "UPSTREAM_TIMEOUT"
    assert error["phase"] == phase
    assert error["operation"] == "records"
    assert str(UUID(error["trace_id"])) == error["trace_id"]
    assert "not replayed" in error["message"]
    assert f"trace_id={error['trace_id']}" in caplog.text
    assert "outcome=returned" in caplog.text
    for private in ("private.example", "private_table", "private_filter", "private_header", "private_exception"):
        assert private not in str(result)
        assert private not in caplog.text
    if phase in {"connect", "pool"}:
        assert "narrow" not in error["message"].lower()
    assert current_tool_trace() is None


@pytest.mark.parametrize(
    ("path", "operation"),
    [
        ("/api/now/table/sys_db_object", "table_metadata"),
        ("/api/now/table/sys_dictionary", "dictionary"),
        ("/api/now/table/sys_choice", "choices"),
        ("/api/now/table/sys_documentation", "documentation"),
    ],
)
async def test_metadata_timeout_does_not_blame_target_query(path: str, operation: str) -> None:
    @tool_handler
    async def describe() -> str:
        raise httpx.ReadTimeout("private", request=httpx.Request("GET", f"https://test.service-now.com{path}"))

    error = decode_response(await describe())["error"]
    assert error["operation"] == operation
    assert "metadata lookup" in error["message"]
    assert "narrow" not in error["message"].lower()


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_write_timeout_warns_about_unknown_remote_outcome(method: str) -> None:
    attempts = 0

    @tool_handler
    async def record_write() -> str:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout(
            "private", request=httpx.Request(method, "https://test.service-now.com/api/now/table/incident")
        )

    error = decode_response(await record_write())["error"]
    assert "may have completed remotely" in error["message"]
    assert "narrow" not in error["message"].lower()
    assert attempts == 1


async def test_parallel_tools_keep_separate_traces(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"result": {"sys_id": "synthetic"}})

    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry, is_shared_pool=True, transport=httpx.MockTransport(respond)
    ) as transport:
        factory = ServiceNowClientFactory(settings, OAuthPKCEProvider(settings), transport)

        @tool_handler
        async def query(sys_id: str) -> str:
            async with factory() as client:
                await client.get_record("incident", sys_id)
                await client.get_record("incident", sys_id)
            return format_response(data=None)

        results = await asyncio.gather(query("a" * 32), query("b" * 32))

    assert all(decode_response(result)["status"] == "success" for result in results)
    traces = {request.headers["X-Correlation-ID"] for request in requests}
    assert len(traces) == 2
    for trace_id in traces:
        matching = [request for request in requests if request.headers["X-Correlation-ID"] == trace_id]
        assert len(matching) == 2
        assert len({request.url.path for request in matching}) == 1
        assert f"trace_id={trace_id} request=1 operation=records" in caplog.text
        assert f"trace_id={trace_id} request=2 operation=records" in caplog.text
    assert caplog.text.count("http_requests=2") == 2
    assert "authorization started" in caplog.text
    assert "authorization finished" in caplog.text
    assert "/api/" not in caplog.text
    assert "test-only-token" not in caplog.text
    assert telemetry.snapshot().completed_request_count == 4
    assert current_tool_trace() is None


async def test_upstream_timeout_matches_http_failure_log(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private_exception", request=request)

    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry, is_shared_pool=True, transport=httpx.MockTransport(fail)
    ) as transport:
        factory = ServiceNowClientFactory(settings, OAuthPKCEProvider(settings), transport)

        @tool_handler
        async def query() -> str:
            async with factory() as client:
                await client.query_records("sys_dictionary", fields=["element"])
            return format_response(data=None)

        error = decode_response(await query())["error"]

    assert f"trace_id={error['trace_id']} request=1 operation=dictionary" in caplog.text
    assert "timeout_phase=read" in caplog.text
    assert "http_requests=1" in caplog.text
    assert "private_exception" not in caplog.text
    assert telemetry.snapshot().failed_request_count == 1


async def test_cancellation_is_not_reported_as_upstream_timeout(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    async def respond(_request: httpx.Request) -> httpx.Response:
        started.set()
        await never.wait()
        return httpx.Response(200, json={"result": []})

    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry, is_shared_pool=True, transport=httpx.MockTransport(respond)
    ) as transport:
        factory = ServiceNowClientFactory(settings, OAuthPKCEProvider(settings), transport)

        @tool_handler
        async def query() -> str:
            async with factory() as client:
                await client.query_records("incident")
            return format_response(data=None)

        task = asyncio.create_task(query())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert "HTTP request cancelled" in caplog.text
    assert "outcome=cancelled" in caplog.text
    assert "timeout_phase=none" in caplog.text
    assert "UPSTREAM_TIMEOUT" not in caplog.text
    assert telemetry.snapshot().failed_request_count == 1
    assert current_tool_trace() is None


async def test_skipped_metadata_timeout_warns_with_trace(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"result": []})),
    ) as transport:
        auth = OAuthPKCEProvider(settings)
        factory = ServiceNowClientFactory(settings, auth, transport)
        dictionary = DictionaryRegistry(settings, auth, factory)
        mcp = MCPServer("test")
        register_tools(mcp, settings, auth, dictionary=dictionary, client_factory=factory)
        with patch.object(dictionary, "get_fields", new=AsyncMock(side_effect=httpx.ReadTimeout("private_exception"))):
            result = decode_response(
                await get_tool_functions(mcp)["query"](
                    table="incident", encoded_query="number=synthetic", fields="number"
                )
            )

    assert result["status"] == "success"
    warning = result["warnings"][0]
    assert "HTTP timeout (read)" in warning
    assert "trace_id=" in warning
    assert "verify field names" in warning
    assert "private_exception" not in warning
    assert "private_exception" not in caplog.text
    assert "Query field validation skipped:" in caplog.text


def test_nested_trace_restores_parent_and_measures_elapsed_time(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("servicenow_mcp.telemetry.perf_counter", side_effect=[1.0, 1.01, 1.025, 1.03]),
        trace_tool_call("outer"),
    ):
        outer = current_tool_trace()
        assert outer is not None
        with trace_tool_call("inner"):
            inner = current_tool_trace()
            assert inner is not None
            assert inner.trace_id != outer.trace_id
        assert current_tool_trace() is outer
    assert current_tool_trace() is None
    assert "duration_ms=15.000" in caplog.text
    assert "duration_ms=30.000" in caplog.text


def test_logging_setup_targets_stderr_and_suppresses_raw_http_logs() -> None:
    with patch("servicenow_mcp.telemetry.logging.basicConfig") as configure:
        configure_diagnostic_logging()
    assert configure.call_args.kwargs["stream"] is sys.stderr
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING
