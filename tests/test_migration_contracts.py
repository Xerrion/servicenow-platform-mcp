"""Characterize public contracts that the HTTPX2 and Pydantic migration must preserve."""

import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import ServerError
from servicenow_mcp.policy import MASK_VALUE, mask_record
from servicenow_mcp.response import format_response, serialize
from servicenow_mcp.tools._payload import MAX_JSON_DEPTH, MAX_JSON_PAYLOAD_BYTES, parse_payload_json


SCHEMA_SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_input_schemas.json"


def _error(raw: str, *, field_name: str = "data") -> dict[str, Any]:
    result = parse_payload_json(raw, field_name=field_name)
    assert isinstance(result, str)
    return json.loads(result)["error"]


def _nested(depth: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth - 1):
        value = {"k": value}
    return value


async def test_tool_input_schemas_match_snapshot(settings: Settings) -> None:
    from servicenow_mcp.server import create_mcp_server

    with patch("servicenow_mcp.server.Settings", return_value=settings):
        mcp = create_mcp_server()
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server):
        schemas = {tool.name: tool.input_schema for tool in await mcp.list_tools()}
    assert schemas == json.loads(SCHEMA_SNAPSHOT.read_text())


def test_envelope_key_order_and_omissions_are_byte_stable() -> None:
    raw = format_response(
        data={"a": 1}, status="error", error="boom", pagination={"offset": 0}, warnings=["w"], truncation={"x": 1}
    )
    assert raw == (
        '{"status":"error","data":{"a":1},"error":{"message":"boom"},'
        '"pagination":{"offset":0},"warnings":["w"],"truncation":{"x":1}}'
    )
    assert format_response(data=None, warnings=[], truncation={}) == '{"status":"success","data":null}'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (False, "false"),
        (0, "0"),
        ("", '""'),
        ([], "[]"),
        ({}, "{}"),
        (2**70, "1180591620717411303424"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        ("æ€", '"æ€"'),
        # Pydantic native forms (approved D2): ISO 8601 replaces str() for datetime.
        (datetime(2026, 1, 2, tzinfo=UTC), '"2026-01-02T00:00:00Z"'),
        (date(2026, 1, 2), '"2026-01-02"'),
        (Decimal("1.5"), '"1.5"'),
        (UUID(int=1), '"00000000-0000-0000-0000-000000000001"'),
        ((1, 2), "[1,2]"),
        ({1: 2}, '{"1":2}'),
        ({1}, "[1]"),
        (b"x", '"x"'),
    ],
)
def test_serialize_edge_values(value: Any, expected: str) -> None:
    assert serialize(value) == expected


def test_serialize_failure_returns_safe_envelope() -> None:
    circular: list[Any] = []
    circular.append(circular)
    with patch("servicenow_mcp.response.sentry_capture") as capture:
        assert serialize(circular) == '{"status": "error", "error": {"message": "Serialization failed"}}'
    capture.assert_called_once()


def test_masking_is_recursive_and_audit_aware() -> None:
    record = {"password": "p", "nested": {"api_token": "t"}, "rows": [{"secret": "s"}], "name": "n"}
    assert mask_record("incident", record) == {
        "password": MASK_VALUE,
        "nested": {"api_token": MASK_VALUE},
        "rows": [{"secret": MASK_VALUE}],
        "name": "n",
    }
    audit = {"fieldname": "password", "oldvalue": "a", "newvalue": "b"}
    assert mask_record("sys_audit", audit) == {"fieldname": "password", "oldvalue": MASK_VALUE, "newvalue": MASK_VALUE}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a":null,"b":false,"c":0,"d":"","e":[],"f":{}}', {"a": None, "b": False, "c": 0, "d": "", "e": [], "f": {}}),
        ('{"a":123456789012345678901234567890}', {"a": 123456789012345678901234567890}),
        ('{"a":1,"a":2}', {"a": 2}),
    ],
)
def test_payload_accepts_edge_values(raw: str, expected: dict[str, Any]) -> None:
    assert parse_payload_json(raw, field_name="data") == expected


def test_payload_accepts_non_finite_numbers() -> None:
    parsed = parse_payload_json('{"a":NaN,"b":Infinity}', field_name="data")
    assert isinstance(parsed, dict)
    assert math.isnan(parsed["a"])
    assert parsed["b"] == math.inf


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        # Approved D1: fixed safe message without parser detail; BOM keeps its own message.
        ('\ufeff{"a":1}', "data is not valid JSON: unexpected UTF-8 BOM"),
        ('{"a":', "data is not valid JSON"),
        ("", "data is not valid JSON"),
        ('{"a":"\\ud800"}', "data is not valid JSON"),
        ("null", "data must be a JSON object"),
    ],
)
def test_payload_rejects_malformed_input(raw: str, message: str) -> None:
    assert _error(raw) == {"message": message}


def test_payload_depth_boundary() -> None:
    assert isinstance(parse_payload_json(json.dumps(_nested(MAX_JSON_DEPTH)), field_name="data"), dict)
    assert _error(json.dumps(_nested(MAX_JSON_DEPTH + 1))) == {"message": "data exceeds maximum nesting depth of 32"}


def test_payload_extreme_depth_reports_depth_error() -> None:
    assert _error('{"a":' * 2000 + "1" + "}" * 2000) == {"message": "data exceeds maximum nesting depth of 32"}


def test_payload_size_boundary() -> None:
    padding = MAX_JSON_PAYLOAD_BYTES - len('{"a":""}')
    assert isinstance(parse_payload_json('{"a":"' + "x" * padding + '"}', field_name="data"), dict)
    assert _error('{"a":"' + "x" * (padding + 1) + '"}') == {"message": "data exceeds maximum size of 262144 bytes"}


def test_missing_result_is_distinct_from_null_result() -> None:
    assert ServiceNowRequestClient._extract_result({"result": None}) is None
    with pytest.raises(ServerError, match="missing 'result' key"):
        ServiceNowRequestClient._extract_result({})


def test_request_body_is_compact_strict_json() -> None:
    from servicenow_mcp._json import dump_request_body

    assert dump_request_body({"a": "æ", "b": [1, None]}) == '{"a":"æ","b":[1,null]}'.encode()
    with pytest.raises(ValueError, match="not JSON compliant"):
        dump_request_body({"a": float("nan")})


_SAFE_RESPONSE_BODIES = [b"<html>secret-body</html>", b"\xffsecret-body", b'["secret-body"]', b'"secret-body"', b"null"]


@pytest.mark.parametrize("body", _SAFE_RESPONSE_BODIES)
@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda c: c.get_attachment("0" * 32), "/api/now/attachment/"),
        (lambda c: c.sc_get_catalogs(), "/api/sn_sc/servicecatalog/catalogs"),
        (lambda c: c.code_search("term"), "/api/sn_codesearch/"),
    ],
    ids=["attachment", "catalog", "code_search"],
)
async def test_malformed_or_non_object_responses_raise_curated_server_error(
    settings: Settings, body: bytes, call: Any, path: str
) -> None:
    """Approved contract: curated ServerError; response content never echoed."""
    from unittest.mock import AsyncMock

    import httpx2

    from servicenow_mcp.client import ServiceNowClient
    from servicenow_mcp.tool_errors import safe_tool_call

    auth = AsyncMock()
    auth.get_headers = AsyncMock(return_value={})
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, content=body))
    async with httpx2.AsyncClient(transport=transport) as http:
        client = ServiceNowClient(settings, auth, http_client=http)
        with pytest.raises(ServerError) as exc:
            await call(client)
        envelope = await safe_tool_call(lambda: call(client))
    message = str(exc.value)
    assert message.startswith(("Invalid JSON response from GET ", "Unexpected JSON response from "))
    assert path in message
    for text in (message, envelope):
        assert "secret-body" not in text
        assert "0xff" not in text
        assert "\\xff" not in text
    assert json.loads(envelope)["error"] == {"message": message}
