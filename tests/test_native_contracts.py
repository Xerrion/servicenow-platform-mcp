"""Native boundary behavior for masks, strict JSON, and upstream failures."""

from typing import TYPE_CHECKING, Any

import pytest

from servicenow_mcp.errors import ServerError
from servicenow_mcp.platform._client_transport import ServiceNowRequestClient
from servicenow_mcp.policy import MASK_VALUE, mask_record
from tests.helpers import decode_response

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


def test_masking_is_recursive_and_audit_aware() -> None:
    record = {
        "password": "p",
        "nested": {"api_token": "t"},
        "rows": [{"secret": "s"}],
        "name": "n",
    }
    assert mask_record("incident", record) == {
        "password": MASK_VALUE,
        "nested": {"api_token": MASK_VALUE},
        "rows": [{"secret": MASK_VALUE}],
        "name": "n",
    }
    audit = {"fieldname": "password", "oldvalue": "a", "newvalue": "b"}
    assert mask_record("sys_audit", audit) == {
        "fieldname": "password",
        "oldvalue": MASK_VALUE,
        "newvalue": MASK_VALUE,
    }


def test_missing_result_is_distinct_from_null_result() -> None:
    assert ServiceNowRequestClient._extract_result({"result": None}) is None
    with pytest.raises(ServerError, match="missing 'result' key"):
        ServiceNowRequestClient._extract_result({})


def test_request_body_is_compact_strict_json() -> None:
    from servicenow_mcp._json import dump_request_body

    assert (
        dump_request_body({"a": "æ", "b": [1, None]})
        == '{"a":"æ","b":[1,null]}'.encode()
    )
    with pytest.raises(ValueError, match="not JSON compliant"):
        dump_request_body({"a": float("nan")})


_SAFE_RESPONSE_BODIES = [
    b"<html>secret-body</html>",
    b"\xffsecret-body",
    b'["secret-body"]',
    b'"secret-body"',
    b"null",
]


@pytest.mark.parametrize(
    "body,call,path",
    [
        pytest.param(
            _SAFE_RESPONSE_BODIES[2],
            lambda c: c.sc_get_catalogs(),
            "/api/sn_sc/servicecatalog/catalogs",
            id='catalog-["secret-body"]',
        )
    ],
)
async def test_malformed_or_non_object_responses_raise_curated_server_error(
    settings: Settings,
    body: bytes,
    call: Any,
    path: str,
) -> None:
    """Approved contract: curated ServerError; response content never echoed."""
    from unittest.mock import AsyncMock

    import httpx2

    from servicenow_mcp.mcp.errors import safe_tool_call
    from servicenow_mcp.platform.client import ServiceNowClient

    auth = AsyncMock()
    auth.get_headers = AsyncMock(return_value={})
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, content=body))
    async with httpx2.AsyncClient(transport=transport) as http:
        client = ServiceNowClient(settings, auth, http_client=http)
        with pytest.raises(ServerError) as exc:
            await call(client)
        envelope = await safe_tool_call(lambda: call(client))
    message = str(exc.value)
    assert message.startswith(
        ("Invalid JSON response from GET ", "Unexpected JSON response from ")
    )
    assert path in message
    for text in (message, str(envelope)):
        assert "secret-body" not in text
        assert "0xff" not in text
        assert "\\xff" not in text
    assert decode_response(envelope)["error"]["message"] == message
