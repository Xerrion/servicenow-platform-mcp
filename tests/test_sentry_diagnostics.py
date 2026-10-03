"""Offline regressions for useful, bounded Sentry evidence and tool isolation."""

import asyncio
import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import httpx2
import pytest
import sentry_sdk
from sentry_sdk import Client
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

import servicenow_mcp.sentry as sentry_mod
from servicenow_mcp._http_diagnostics import request_diagnostics, response_diagnostics
from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientFactory
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.sentry import sentry_tool_scope, set_sentry_context
from servicenow_mcp.telemetry import HttpTelemetry, TelemetryAsyncClient
from tests.helpers import decode_response


BASE_URL = "https://test.service-now.com"
TRANSACTION_ID = "0123456789abcdef0123456789abcdef"


class _MemoryTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                self.events.append(dict(event))


@pytest.fixture()
def sentry_events(monkeypatch: pytest.MonkeyPatch) -> Generator[list[dict[str, Any]], None, None]:
    """Exercise the actual SDK event pipeline with an in-memory-only transport."""
    transport = _MemoryTransport()
    client = Client(
        dsn="https://public@example.invalid/1",
        transport=transport,
        default_integrations=False,
        auto_enabling_integrations=False,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0,
        max_breadcrumbs=50,
    )
    monkeypatch.setattr(sentry_mod, "_initialized", True)
    try:
        with sentry_sdk.isolation_scope() as scope:
            scope.set_client(client)
            yield transport.events
    finally:
        client.close(timeout=0)


def test_query_features_do_not_disclose_filter_values() -> None:
    query = "123TEXTQUERY321=private-search^ORactive=true^NQsys_created_on>=javascript:gs.daysAgoStart(1)^ORDERBYsys_id"
    request = httpx2.Request(
        "GET",
        f"{BASE_URL}/api/now/table/alm_asset",
        params={"sysparm_query": query, "sysparm_limit": "100", "sysparm_suppress_pagination_header": "true"},
        headers={"Authorization": "private-authorization"},
    )

    diagnostic = request_diagnostics(request)

    assert diagnostic["parameters"] == {"sysparm_limit": 100, "sysparm_suppress_pagination_header": "true"}
    assert diagnostic["query_summary"] == {
        "present": True,
        "character_count": len(query),
        "inspection": "shape only",
        "clause_count": 4,
        "has_text_search": True,
        "has_javascript": True,
        "has_or": True,
        "has_new_query": True,
        "has_ordering": True,
    }
    for value in ("private-search", "private-authorization", query, "gs.daysAgoStart"):
        assert value not in json.dumps(diagnostic)


@pytest.mark.parametrize("query", ["short_description=123TEXTQUERY321=private", "ORDERBYsys_id", "active=true"])
def test_query_features_do_not_mistake_values_or_ordering_for_text_search(query: str) -> None:
    request = httpx2.Request("GET", BASE_URL, params={"sysparm_query": query})
    summary = request_diagnostics(request)["query_summary"]

    assert isinstance(summary, dict)
    assert summary["has_text_search"] is False
    assert summary["has_or"] is False


@pytest.mark.parametrize("value", ["private", "1" * 1000, "NaN", "1.5", "TRUE"])
def test_unrecognized_parameter_values_are_omitted(value: str) -> None:
    request = httpx2.Request("GET", BASE_URL, params={"sysparm_limit": value, "sysparm_no_count": value})
    diagnostic = request_diagnostics(request)

    assert diagnostic["parameters"] == {
        "sysparm_limit": "[omitted: unrecognized or unsafe]",
        "sysparm_no_count": "[omitted: unrecognized or unsafe]",
    }


def test_duplicate_and_oversized_queries_are_not_inspected() -> None:
    duplicate = request_diagnostics(
        httpx2.Request("GET", BASE_URL, params=[("sysparm_query", "private-1"), ("sysparm_query", "private-2")])
    )
    oversized = request_diagnostics(httpx2.Request("GET", BASE_URL, params={"sysparm_query": "x" * 8193}))

    assert duplicate["query_summary"] == {"present": True, "inspection": "omitted: duplicate query parameters"}
    assert oversized["query_summary"] == {
        "present": True,
        "character_count": 8193,
        "inspection": "omitted: query exceeds 8192 characters",
    }


def test_safe_response_evidence_retains_transaction_id_and_omits_private_detail() -> None:
    response = httpx2.Response(
        400,
        request=httpx2.Request("GET", BASE_URL),
        json={"error": {"message": "Pagination not supported", "detail": "private customer@example.com"}},
        headers={"X-Transaction-ID": TRANSACTION_ID, "Set-Cookie": "private-session", "X-Arbitrary": "private"},
    )

    diagnostic = response_diagnostics(response, ("test-only-token",))

    assert diagnostic["x-transaction-id"] == TRANSACTION_ID
    assert diagnostic["body_format"] == "JSON"
    assert diagnostic["content_type"] == "application/json"
    assert diagnostic["error"] == {
        "message": "Pagination not supported",
        "detail": "[omitted: unrecognized or unsafe]",
    }
    assert "private" not in json.dumps(diagnostic)
    assert "customer@example.com" not in json.dumps(diagnostic)


@pytest.mark.parametrize("source", ["token", "query", "request_body", "cookie", "set_cookie"])
def test_response_trace_ids_cannot_reflect_sensitive_values(source: str) -> None:
    request = httpx2.Request(
        "POST",
        BASE_URL,
        params={"sysparm_query": TRANSACTION_ID if source == "query" else "active=true"},
        headers={"Cookie": TRANSACTION_ID if source == "cookie" else "test-cookie"},
        json={"value": TRANSACTION_ID if source == "request_body" else "test-value"},
    )
    response = httpx2.Response(
        400,
        request=request,
        json={"error": {"message": "Pagination not supported"}},
        headers={"X-Transaction-ID": TRANSACTION_ID, "Set-Cookie": TRANSACTION_ID if source == "set_cookie" else ""},
    )
    diagnostic = response_diagnostics(response, (TRANSACTION_ID if source == "token" else "test-only-token",))

    assert "x-transaction-id" not in diagnostic
    assert TRANSACTION_ID not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    ("body", "body_format"),
    [
        (b"private customer@example.com", "non-JSON or malformed"),
        (b'{"error":', "non-JSON or malformed"),
        (b'{"error":{"message":"Pagination not supported"}}' + b" " * 8192, "omitted: body exceeds 8192 bytes"),
    ],
)
def test_response_body_inspection_is_bounded(body: bytes, body_format: str) -> None:
    diagnostic = response_diagnostics(httpx2.Response(400, request=httpx2.Request("GET", BASE_URL), content=body), ())

    assert diagnostic["body_format"] == body_format
    assert diagnostic["body_bytes"] == len(body)
    assert "private" not in json.dumps(diagnostic)
    assert "error" not in diagnostic


def test_phrase_shaped_credentials_veto_response_text() -> None:
    diagnostic = response_diagnostics(
        httpx2.Response(401, request=httpx2.Request("GET", BASE_URL), json={"error": {"message": "Unauthorized"}}),
        ("Unauthorized",),
    )

    assert diagnostic["error"] == {"message": "[omitted: unrecognized or unsafe]"}


async def test_tool_context_includes_positional_defaults_and_redaction(sentry_events: list[dict[str, Any]]) -> None:
    @tool_handler
    async def query(table: str, encoded_query: str, limit: int = 20, resolve_labels: str | None = None) -> str:
        raise ServiceNowMCPError("Pagination not supported", status_code=400)

    result = decode_response(await query("alm_asset", "private-query", resolve_labels="private-label"))

    assert result["status"] == "error"
    assert len(sentry_events) == 1
    event = sentry_events[0]
    assert event["transaction"] == "tools/call query"
    assert event["contexts"]["tool"] == {
        "name": "query",
        "args": {
            "table": "alm_asset",
            "encoded_query": "***REDACTED***",
            "limit": 20,
            "resolve_labels": "***REDACTED***",
        },
    }
    assert event["contexts"]["tool_trace"]["http_requests"] == 0
    assert event["contexts"]["tool_trace"]["duration_ms"] >= 0
    assert "private-query" not in json.dumps(event["contexts"])
    assert "private-label" not in json.dumps(event["contexts"])


async def test_concurrent_tools_restore_parent_and_keep_separate_error_context(
    sentry_events: list[dict[str, Any]],
) -> None:
    parent = sentry_sdk.get_isolation_scope()
    parent.set_transaction_name("tools/call previous")
    parent.set_context("http", {"owner": "parent"})
    parent.set_tag("http.status_code", "500")
    ready = asyncio.Event()

    @tool_handler
    async def query() -> str:
        set_sentry_context("http", {"owner": "query"})
        ready.set()
        await asyncio.sleep(0)
        raise ServiceNowMCPError("Pagination not supported")

    @tool_handler
    async def describe() -> str:
        await ready.wait()
        raise ServiceNowMCPError("Resource not found")

    await asyncio.gather(query(), describe())
    sentry_mod.capture_exception(RuntimeError("outside"))

    assert len(sentry_events) == 3
    events = {event["tags"]["tool.name"]: event for event in sentry_events[:2]}
    assert events["query"]["transaction"] == "tools/call query"
    assert events["describe"]["transaction"] == "tools/call describe"
    assert events["query"]["contexts"]["http"] == {"owner": "query"}
    assert "http" not in events["describe"]["contexts"]
    assert "http.status_code" not in events["describe"]["tags"]
    assert (
        events["query"]["contexts"]["tool_trace"]["trace_id"]
        != (events["describe"]["contexts"]["tool_trace"]["trace_id"])
    )
    assert sentry_events[-1]["contexts"]["http"] == {"owner": "parent"}
    assert "tool.name" not in sentry_events[-1].get("tags", {})
    assert sentry_events[-1]["transaction"] == "tools/call previous"
    assert sentry_events[-1]["tags"]["http.status_code"] == "500"


@pytest.mark.parametrize("is_timeout", [False, True])
async def test_failed_request_has_correlated_history_and_actual_controls(
    settings: Settings, sentry_events: list[dict[str, Any]], is_timeout: bool
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/sys_dictionary"):
            return httpx2.Response(200, json={"result": []})
        if is_timeout:
            raise httpx2.ReadTimeout("private exception", request=request)
        return httpx2.Response(
            400,
            json={"error": {"message": "Pagination not supported", "detail": "private detail"}},
            headers={"X-Transaction-ID": TRANSACTION_ID},
        )

    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry, is_shared_pool=True, transport=httpx2.MockTransport(respond)
    ) as transport:
        factory = ServiceNowClientFactory(settings, OAuthPKCEProvider(settings), transport)

        @tool_handler
        async def query(table: str, encoded_query: str) -> str:
            async with factory() as client:
                await client.query_records("sys_dictionary", fields=["element"], limit=1)
                await client.query_records(table, encoded_query, fields=["sys_id"], limit=100)
            return "unreachable"

        result = decode_response(await query(table="alm_asset", encoded_query="123TEXTQUERY321=private-search"))

    assert result["status"] == "error"
    assert len(sentry_events) == 1
    event = sentry_events[0]
    trace = event["contexts"]["tool_trace"]
    attempt = event["contexts"]["http_request"]
    history = [item for item in event["breadcrumbs"]["values"] if item["category"] == "servicenow.http"]
    assert trace["http_requests"] == 2
    assert attempt["trace_id"] == trace["trace_id"]
    assert attempt["request_number"] == 2
    assert attempt["operation"] == "records"
    assert attempt["parameters"] == {
        "sysparm_limit": 100,
        "sysparm_display_value": "false",
        "sysparm_suppress_pagination_header": "true",
    }
    assert attempt["query_summary"]["has_text_search"] is True
    assert len(history) == 2
    assert [item["data"]["request_number"] for item in history] == [1, 2]
    assert history[0]["data"]["operation"] == "dictionary"
    assert all(item["data"]["trace_id"] == trace["trace_id"] for item in history)
    assert "private" not in json.dumps({"contexts": event["contexts"], "breadcrumbs": history})
    if is_timeout:
        assert attempt["outcome"] == "failed"
        assert attempt["timeout_phase"] == "read"
        assert "status_code" not in attempt
        assert telemetry.snapshot().failed_request_count == 1
        assert telemetry.snapshot().http_error_count == 0
    else:
        assert attempt["outcome"] == "http_error"
        assert attempt["status_code"] == 400
        assert event["tags"]["http.status_code"] == "400"
        assert event["contexts"]["http"]["response"]["x-transaction-id"] == TRANSACTION_ID
        assert telemetry.snapshot().completed_request_count == 2
        assert telemetry.snapshot().failed_request_count == 0
        assert telemetry.snapshot().http_error_count == 1


@pytest.mark.parametrize(("is_initialized", "has_sdk"), [(False, True), (True, False)])
def test_tool_scope_and_breadcrumbs_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch, is_initialized: bool, has_sdk: bool
) -> None:
    monkeypatch.setattr(sentry_mod, "_initialized", is_initialized)
    monkeypatch.setattr(sentry_mod, "HAS_SENTRY", has_sdk)
    with patch.object(sentry_mod, "sentry_sdk") as sdk:
        with sentry_tool_scope("query"):
            sentry_mod.add_sentry_breadcrumb("servicenow.http", {"status_code": 400}, is_error=True)
        sdk.isolation_scope.assert_not_called()
        sdk.add_breadcrumb.assert_not_called()


async def test_http_history_is_capped_at_fifty_requests(
    settings: Settings, sentry_events: list[dict[str, Any]]
) -> None:
    telemetry = HttpTelemetry()
    async with TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={"result": []})),
    ) as transport:
        factory = ServiceNowClientFactory(settings, OAuthPKCEProvider(settings), transport)

        @tool_handler
        async def query() -> str:
            async with factory() as client:
                for _ in range(60):
                    await client.query_records("sys_dictionary", fields=["element"], limit=1)
            raise ServiceNowMCPError("Request failed")

        await query()

    event = sentry_events[0]
    history = [item for item in event["breadcrumbs"]["values"] if item["category"] == "servicenow.http"]
    assert len(history) == 50
    assert history[0]["data"]["request_number"] == 11
    assert history[-1]["data"]["request_number"] == 60
    assert event["contexts"]["tool_trace"]["http_requests"] == 60


def test_http_failure_context_uses_the_actual_request(settings: Settings) -> None:
    request = httpx2.Request(
        "GET",
        f"{BASE_URL}/api/now/table/alm_asset",
        params={"sysparm_limit": "100", "sysparm_query": "123TEXTQUERY321=private-search"},
    )
    response = httpx2.Response(
        400, request=request, json={"error": {"message": "Pagination not supported", "detail": "private detail"}}
    )
    client = ServiceNowClient(settings, OAuthPKCEProvider(settings))
    with (
        patch("servicenow_mcp._client_transport.set_sentry_context") as context,
        pytest.raises(ServiceNowMCPError, match="Pagination not supported"),
    ):
        client._raise_for_status(response)

    data = context.call_args.args[1]
    assert data["parameters"] == {"sysparm_limit": 100}
    assert data["query_summary"]["has_text_search"] is True
    assert "private" not in json.dumps(data)
