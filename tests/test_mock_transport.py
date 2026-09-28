"""The unit-test HTTP mock routes, queues, raises, records and fails closed."""

import re

import httpx2
import pytest

from tests._mock_transport import HTTPMock, http_mock


BASE = "https://test.service-now.com/api/now/table"


async def test_route_matches_method_and_path_and_records_calls() -> None:
    route = http_mock.get(f"{BASE}/incident").respond(200, json={"result": []})
    async with httpx2.AsyncClient() as client:
        response = await client.get(f"{BASE}/incident", params={"sysparm_limit": "1"})
    assert response.json() == {"result": []}
    assert route.call_count == 1
    assert http_mock.calls.last.request.url.params["sysparm_limit"] == "1"


async def test_queued_responses_and_raised_exceptions() -> None:
    http_mock.post(f"{BASE}/incident").mock(
        side_effect=[httpx2.Response(500), httpx2.ConnectError("synthetic"), httpx2.Response(201)]
    )
    async with httpx2.AsyncClient() as client:
        assert (await client.post(f"{BASE}/incident")).status_code == 500
        with pytest.raises(httpx2.ConnectError):
            await client.post(f"{BASE}/incident")
        assert (await client.post(f"{BASE}/incident")).status_code == 201


async def test_regex_and_async_handler() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=request.url.path)

    http_mock.get(re.compile(r"/api/now/table/[^/]+$")).mock(side_effect=handler)
    async with httpx2.AsyncClient() as client:
        assert (await client.get(f"{BASE}/problem")).text == "/api/now/table/problem"


async def test_unexpected_request_fails() -> None:
    mock = HTTPMock()
    with pytest.raises(AssertionError, match="Unexpected HTTP request"):
        await mock.handle(httpx2.Request("GET", f"{BASE}/incident"))
    assert len(mock.unexpected) == 1


async def test_method_mismatch_is_unexpected() -> None:
    mock = HTTPMock()
    mock.get(f"{BASE}/incident")
    with pytest.raises(AssertionError):
        await mock.handle(httpx2.Request("DELETE", f"{BASE}/incident"))
