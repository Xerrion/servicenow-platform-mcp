"""Source-derived REST metadata transport fixtures, not captured live responses.

Studio bundle:1071,140201-140205,336221-336231,476777-476805,477058-477087.
The GraphQL POST is a fixed metadata query, never the authored runtime endpoint.
"""

import copy
import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from pydantic import JsonValue

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.errors import AuthError, ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings

pytestmark = pytest.mark.asyncio

URL = "https://test.service-now.com/api/now/graphql"
QUERY = """query restTiggerInfoQuery($flowInfo: now_restTrigger_FlowInfoInput!) {
    now {
        restTrigger {
            generateBasePath(flowInfo: $flowInfo) {
                ...basePath
                __typename
            }
        }
    }
}

fragment basePath on now_restTrigger_BasePath {
    ... on now_restTrigger_ErrorResponse {
        message
    }
    ... on now_restTrigger_BasePathResponse {
        path
    }
}"""


def flow_info(value: str = "") -> dict[str, JsonValue]:
    return {
        "scopeId": "global",
        "flowName": "A native display name",
        "basePath": {"value": value},
    }


def envelope(result: JsonValue = None) -> dict[str, JsonValue]:
    if result is None:
        result = {
            "__typename": "now_restTrigger_BasePathResponse",
            "path": "/api/fixture/example",
        }
    return {"data": {"now": {"restTrigger": {"generateBasePath": result}}}}


@pytest.mark.parametrize("current_path", ["", "/api/fixture/current"])
@pytest.mark.parametrize(
    "returned_path", ["/api/fixture/generated", "api/fixture/generated"]
)
async def test_fixed_query_uses_native_flow_fields_and_oauth_headers(
    settings: Settings, current_path: str, returned_path: str
) -> None:
    info = flow_info(current_path)
    # A flow name is a variable value, never executable GraphQL text.
    info["flowName"] = 'Flow name ") { mutation { unwanted } }'
    original = copy.deepcopy(info)
    route = http_mock.post(URL).respond(
        200,
        json=envelope(
            {
                "__typename": "now_restTrigger_BasePathResponse",
                "path": returned_path,
            }
        ),
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.studio_rest_trigger_base_path(info)

    assert result == returned_path
    assert info == original
    assert route.call_count == 1
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "operationName": "restTiggerInfoQuery",
        "query": QUERY,
        "variables": {"flowInfo": info},
    }
    assert request.headers["authorization"] == "Bearer test-only-token"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-correlation-id"]
    assert "x-usertoken" not in request.headers
    assert "cookie" not in request.headers
    assert not request.url.query
    assert [
        (call.request.method, str(call.request.url)) for call in http_mock.calls
    ] == [("POST", URL)]


@pytest.mark.parametrize(
    "info",
    [
        None,
        [],
        {},
        {"scopeId": "global", "flowName": "Flow"},
        {**flow_info(), "id": "a" * 32},
        {**flow_info(), "internalName": "internal_name"},
        {**flow_info(), "query": "mutation { unwanted }"},
        {**flow_info(), "scopeId": None},
        {**flow_info(), "scopeId": "  "},
        {**flow_info(), "flowName": False},
        {**flow_info(), "flowName": ""},
        {**flow_info(), "basePath": None},
        {**flow_info(), "basePath": "/api/fixture"},
        {**flow_info(), "basePath": {}},
        {**flow_info(), "basePath": {"value": None}},
        {**flow_info(), "basePath": {"value": False}},
        {**flow_info(), "basePath": {"value": 1}},
        {**flow_info(), "basePath": {"value": "", "displayValue": ""}},
    ],
)
async def test_non_native_input_is_rejected_before_transport(
    settings: Settings, info: JsonValue
) -> None:
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ValueError, match="REST trigger"):
            _ = await client.studio_rest_trigger_base_path(
                cast(dict[str, JsonValue], info)
            )
    assert not http_mock.calls


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not an object",
        {},
        {"result": envelope()},
        {"data": None},
        {"data": {"now": None}},
        {"data": {"now": {"restTrigger": []}}},
        {"data": {"now": {"restTrigger": {}}}},
        {"data": {"now": {"restTrigger": {"generateBasePath": None}}}},
        envelope([]),
        {**envelope(), "errors": [{"message": "sensitive fixture detail"}]},
        {**envelope(), "errors": {}},
        {**envelope(), "errors": None},
        {**envelope(), "errors": ""},
        {**envelope(), "errors": False},
    ],
)
async def test_graphql_errors_and_malformed_envelopes_fail_closed(
    settings: Settings, payload: JsonValue
) -> None:
    route = http_mock.post(URL).respond(200, json=payload)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError) as caught:
            _ = await client.studio_rest_trigger_base_path(flow_info())
    assert "sensitive fixture detail" not in str(caught.value)
    assert route.call_count == 1


async def test_empty_graphql_errors_list_allows_success(settings: Settings) -> None:
    route = http_mock.post(URL).respond(200, json={**envelope(), "errors": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await client.studio_rest_trigger_base_path(flow_info())
            == "/api/fixture/example"
        )
    assert route.call_count == 1


INVALID_PATHS: tuple[JsonValue, ...] = (None, False, 42, [], {}, "", " \t\n")


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"path": "/api/fixture/example"},
        {"__typename": "unknown", "path": "/api/fixture/example"},
        {"__typename": "other_ErrorResponse", "message": "failure"},
        {"__typename": "now_restTrigger_BasePathResponse"},
        *[
            {"__typename": "now_restTrigger_BasePathResponse", "path": value}
            for value in INVALID_PATHS
        ],
        {
            "__typename": "now_restTrigger_BasePathResponse",
            "path": "/api/fixture/example",
            "message": None,
        },
        {"__typename": "now_restTrigger_ErrorResponse"},
        {"__typename": "now_restTrigger_ErrorResponse", "message": False},
        {"__typename": "now_restTrigger_ErrorResponse", "message": "  "},
        {
            "__typename": "now_restTrigger_ErrorResponse",
            "message": "failure",
            "path": "/api/fixture/example",
        },
    ],
)
async def test_union_discriminator_and_path_are_required(
    settings: Settings, result: JsonValue
) -> None:
    route = http_mock.post(URL).respond(200, json=envelope(result))
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(
            ServiceNowMCPError, match="incomplete REST trigger metadata"
        ):
            _ = await client.studio_rest_trigger_base_path(flow_info())
    assert route.call_count == 1


async def test_native_error_union_never_returns_a_path(settings: Settings) -> None:
    route = http_mock.post(URL).respond(
        200,
        json=envelope(
            {
                "__typename": "now_restTrigger_ErrorResponse",
                "message": "sensitive native fixture detail",
            }
        ),
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError, match="rejected REST trigger") as caught:
            _ = await client.studio_rest_trigger_base_path(flow_info())
    assert "sensitive native fixture detail" not in str(caught.value)
    assert route.call_count == 1


@pytest.mark.parametrize("content", [b"not JSON", b"<html>login</html>", b"{broken"])
async def test_invalid_json_remains_a_read_failure(
    settings: Settings, content: bytes
) -> None:
    route = http_mock.post(URL).respond(200, content=content)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(
            ServiceNowMCPError, match="invalid REST trigger metadata JSON"
        ) as caught:
            _ = await client.studio_rest_trigger_base_path(flow_info())
    assert "write" not in str(caught.value)
    assert route.call_count == 1


@pytest.mark.parametrize(
    "status", [201, 202, 204, 301, 302, 307, 308, 400, 401, 403, 404, 429, 500, 503]
)
async def test_incomplete_http_results_are_not_followed_or_retried(
    settings: Settings, status: int
) -> None:
    route = http_mock.post(URL).respond(
        status,
        json=envelope(),
        headers={"Location": "https://test.service-now.com/auth_redirect"},
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError) as caught:
            _ = await client.studio_rest_trigger_base_path(flow_info())
    if status == 401:
        assert isinstance(caught.value, AuthError)
    assert route.call_count == 1
    assert len(http_mock.calls) == 1


async def test_transport_failure_is_not_retried(settings: Settings) -> None:
    route = http_mock.post(URL).mock(side_effect=httpx2.ReadTimeout("fixture timeout"))
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(httpx2.ReadTimeout, match="fixture timeout"):
            _ = await client.studio_rest_trigger_base_path(flow_info())
    assert route.call_count == 1
