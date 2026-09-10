"""Privacy and parsing regressions for REST authentication diagnostics."""

import json
import time
from unittest.mock import patch

import httpx
import pytest
import respx
from pydantic import SecretStr

from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import AuthError
from servicenow_mcp.utils import safe_tool_call


BASE_URL = "https://test.service-now.com"
TRANSACTION_ID = "0123456789abcdef0123456789abcdef"


@respx.mock
async def test_safe_evidence_reaches_tool_error_without_replay(settings: Settings) -> None:
    """Only selected diagnostics reach the error envelope; refresh stays deferred."""
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("test-access", time.monotonic() + 3600, "test-refresh")
    route = respx.get(f"{BASE_URL}/api/now/table/incident").respond(
        401,
        json={"error": {"message": "User Not Authenticated", "detail": "private customer data"}},
        headers={
            "WWW-Authenticate": 'Bearer realm="private", error="invalid_token", '
            'error_description="The access token expired"',
            "X-Transaction-ID": TRANSACTION_ID,
            "Set-Cookie": "session=private",
        },
    )
    async with ServiceNowClient(settings, provider) as client:

        async def call() -> str:
            await client.query_records("incident", "short_description=private")
            return "unreachable"

        result = json.loads(await safe_tool_call(call, "test-correlation"))

    assert result["status"] == "error"
    message = result["error"]["message"]
    assert '"message": "User Not Authenticated"' in message
    assert '"scheme": "Bearer"' in message
    assert '"error": "invalid_token"' in message
    assert '"error_description": "The access token expired"' in message
    assert f'"x-transaction-id": "{TRANSACTION_ID}"' in message
    assert "private" not in message
    assert "test-access" not in message
    assert "test-refresh" not in message
    assert "not replayed" in message
    assert route.call_count == 1
    assert provider._token is not None
    assert provider._token.expires_at == 0
    assert provider._token.refresh_token == "test-refresh"


def _error(settings: Settings, response: httpx.Response) -> str:
    response.request = httpx.Request(
        "GET",
        f"{BASE_URL}/api/now/table/incident?sysparm_query=private-query",
        headers={"Authorization": "Bearer test-access", "Cookie": "session=private-cookie"},
    )
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken("test-access", time.monotonic() + 3600, "test-refresh")
    with pytest.raises(AuthError) as exc:
        ServiceNowClient(settings, provider)._raise_for_status(response)
    assert provider._token is not None
    assert provider._token.expires_at == 0
    return str(exc.value)


@pytest.mark.parametrize("body", [b"", b"<html>private customer data</html>", b"\xff", b'{"error":'])
def test_non_json_body_is_never_echoed(settings: Settings, body: bytes) -> None:
    message = _error(settings, httpx.Response(401, content=body, headers={"WWW-Authenticate": "Basic"}))
    assert '"scheme": "Basic"' in message
    assert "private" not in message
    assert "<html>" not in message
    assert "HTTP 401" in message


@pytest.mark.parametrize(
    "payload",
    [[], None, "private", {"error": "private"}, {"error": []}, {"error": {"message": ["private"]}}],
)
def test_unexpected_json_shape_is_not_echoed(settings: Settings, payload: object) -> None:
    message = _error(settings, httpx.Response(401, content=json.dumps(payload)))
    assert "private" not in message
    assert "HTTP 401" in message


@pytest.mark.parametrize(
    "value",
    [
        "Bearer test-access",
        "test-refresh",
        "client_secret=test-secret",
        "client_id=test-client",
        "Cookie: session=private-cookie",
        "https://private.invalid/oauth_auth.do?code=private-code",
        "User Not Authenticated for customer Jane Smith",
        "Unauthorized\r\nSet-Cookie: private-cookie",
        "Unauthorized\x00private",
        "Unauthorized\x7fprivate",
        "Unauthorized\x1b[31m",
        "Unauthorized\u202eprivate",
        "Unauthorized\u200b",
        "Unauthorized " + "x" * 10000,
    ],
)
def test_hostile_message_and_description_are_omitted(settings: Settings, value: str) -> None:
    response = httpx.Response(
        401,
        json={"error": {"message": value}},
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description={json.dumps(value)}'},
    )
    message = _error(settings, response)
    for forbidden in (value, "test-access", "test-refresh", "test-secret", "private", "Jane Smith", "test-client"):
        assert forbidden not in message
    assert all(" " <= char <= "~" for char in message)
    assert len(message) < 2048


@pytest.mark.parametrize(
    "header",
    [
        "bEaReR\t",
        'Bearer error = "invalid_token", error_description = "The access token expired"',
        'Basic realm="private, \\"quoted\\"", bEaReR ERROR=invalid_token, error_description="The access token expired"',
        'Negotiate cHJpdmF0ZQ==, Bearer error="invalid_token", error_description="The access token expired"',
        ', , Bearer error="invalid_token",, error_description="The access token expired", ',
    ],
)
def test_challenge_grammar(settings: Settings, header: str) -> None:
    message = _error(settings, httpx.Response(401, headers={"WWW-Authenticate": header}))
    assert '"scheme": "Bearer"' in message
    if "invalid_token" in header:
        assert '"error": "invalid_token"' in message
        assert '"error_description": "The access token expired"' in message
    assert "private" not in message
    assert "cHJpdmF0ZQ" not in message


def test_repeated_challenge_headers_and_escaped_value(settings: Settings) -> None:
    message = _error(
        settings,
        httpx.Response(
            401,
            headers=[
                ("WWW-Authenticate", 'Basic realm="private"'),
                ("WWW-Authenticate", 'Bearer error="invalid_token", error_description="The access token expire\\d"'),
            ],
        ),
    )
    assert '"scheme": "Basic"' in message
    assert '"scheme": "Bearer"' in message
    assert '"error_description": "The access token expired"' in message


@pytest.mark.parametrize(
    "header",
    [
        'Bearer error="invalid_token"\r\nSet-Cookie: private',
        'Bearer error="invalid_token"\x00',
        'Bearer error="invalid_token"\x7f',
        'Bearer error="invalid_token',
        'Bearer error="invalid_token" trailing',
        'Bearer error="invalid_token", ERROR="insufficient_scope"',
        'error="invalid_token"',
        'Bearer private==, error="invalid_token"',
        'Bearer\terror="invalid_token"',
        ",".join(['Bearer error="invalid_token"'] * 5),
        "Bearer " + "x" * 10000,
    ],
)
def test_malformed_or_oversized_challenge_is_not_partly_trusted(settings: Settings, header: str) -> None:
    message = _error(settings, httpx.Response(401, headers={"WWW-Authenticate": header}))
    assert '"scheme"' not in message
    assert '"error": "invalid_token"' not in message
    assert "private" not in message
    assert all(" " <= char <= "~" for char in message)
    assert len(message) < 2048


@pytest.mark.parametrize("header", ["test-access", "Bearer test-access", 'Bearer error="test-refresh"'])
def test_scheme_token68_and_unknown_error_cannot_leak(settings: Settings, header: str) -> None:
    message = _error(settings, httpx.Response(401, headers={"WWW-Authenticate": header}))
    assert "test-access" not in message
    assert "test-refresh" not in message


@pytest.mark.parametrize("name", ["X-Transaction-ID", "X-Request-ID", "X-Correlation-ID"])
@pytest.mark.parametrize("value", [TRANSACTION_ID, "550e8400-e29b-41d4-a716-446655440000"])
def test_explicit_trace_header_allowlist(settings: Settings, name: str, value: str) -> None:
    message = _error(settings, httpx.Response(401, headers={name: value, "X-Arbitrary": "private"}))
    assert f'"{name.lower()}": "{value}"' in message
    assert "private" not in message
    assert "x-arbitrary" not in message


@pytest.mark.parametrize(
    "value",
    ["test-access", "Bearer test-access", "customer@example.com", "1234\r\nprivate", "1234\x00", "a" * 10000],
)
def test_trace_header_values_are_not_free_text(settings: Settings, value: str) -> None:
    message = _error(settings, httpx.Response(401, headers={"X-Request-ID": value}))
    assert value not in message
    assert '"x-request-id"' not in message


def test_duplicate_trace_header_is_omitted(settings: Settings) -> None:
    message = _error(
        settings,
        httpx.Response(401, headers=[("X-Request-ID", TRANSACTION_ID), ("X-Request-ID", TRANSACTION_ID)]),
    )
    assert '"x-request-id"' not in message


@pytest.mark.parametrize(
    "body", [b"[" * 2000 + b"]" * 2000, b'{"error":{"message":"Unauthorized"},"other":"' + b"x" * 10000 + b'"}']
)
def test_body_budget_and_depth_fail_closed(settings: Settings, body: bytes) -> None:
    message = _error(settings, httpx.Response(401, content=body, headers={"WWW-Authenticate": "Bearer"}))
    assert '"scheme": "Bearer"' in message
    assert '"message": "Unauthorized"' not in message
    assert len(message) < 2048


@pytest.mark.parametrize(
    "source", ["access", "refresh", "client_id", "client_secret", "rejected", "cookie", "set_cookie", "query"]
)
def test_trace_shaped_secret_is_not_echoed(settings: Settings, source: str) -> None:
    """A hex credential is still a credential when reflected into an allowed header."""
    provider = OAuthPKCEProvider(settings)
    provider._token = AccessToken(
        TRANSACTION_ID if source == "access" else "test-access",
        time.monotonic() + 3600,
        TRANSACTION_ID if source == "refresh" else "test-refresh",
    )
    if source == "client_id":
        settings.servicenow_oauth_client_id = TRANSACTION_ID
    if source == "client_secret":
        settings.servicenow_oauth_client_secret = SecretStr(TRANSACTION_ID)
    authorization = TRANSACTION_ID if source == "rejected" else provider._token.value
    request = httpx.Request(
        "GET",
        f"{BASE_URL}/api/now/table/incident",
        params={"sysparm_query": TRANSACTION_ID if source == "query" else "private-query"},
        headers={
            "Authorization": f"Bearer {authorization}",
            "Cookie": f"session={TRANSACTION_ID}" if source == "cookie" else "session=private-cookie",
        },
    )
    response = httpx.Response(
        401,
        request=request,
        json={"error": {"message": "User Not Authenticated"}},
        headers={
            "X-Transaction-ID": TRANSACTION_ID.upper(),
            "Set-Cookie": f"session={TRANSACTION_ID}" if source == "set_cookie" else "session=private-cookie",
        },
    )
    with pytest.raises(AuthError) as exc:
        ServiceNowClient(settings, provider)._raise_for_status(response)
    assert TRANSACTION_ID not in str(exc.value).lower()
    assert '"x-transaction-id"' not in str(exc.value)
    assert '"message": "User Not Authenticated"' in str(exc.value)
    assert provider._token is not None
    if source == "rejected":
        assert provider._token.expires_at > 0
    else:
        assert provider._token.expires_at == 0


def test_phrase_shaped_secret_is_not_echoed(settings: Settings) -> None:
    settings.servicenow_oauth_client_secret = SecretStr("Unauthorized")
    message = _error(settings, httpx.Response(401, json={"error": {"message": "Unauthorized"}}))
    assert "Unauthorized" not in message
    assert "[omitted: credential overlap]" in message


def test_diagnostics_do_not_log_raw_response(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    with patch("servicenow_mcp.client.set_sentry_context") as context:
        message = _error(
            settings,
            httpx.Response(
                401,
                content=b"private-body",
                headers={"WWW-Authenticate": 'Bearer error_description="private-header"'},
            ),
        )
    assert caplog.text == ""
    assert "private" not in message
    assert context.call_args.args == (
        "http",
        {"status_code": 401, "method": "GET", "url": f"{BASE_URL}/api/now/table/incident"},
    )


def test_aggregate_header_budget_and_single_trace_id(settings: Settings) -> None:
    message = _error(
        settings,
        httpx.Response(
            401,
            headers=[("WWW-Authenticate", "Basic") for _ in range(1000)]
            + [(name, TRANSACTION_ID) for name in ("X-Transaction-ID", "X-Request-ID", "X-Correlation-ID")],
        ),
    )
    assert "[omitted: malformed or oversized]" in message
    assert '"x-transaction-id"' in message
    assert '"x-request-id"' not in message
    assert '"x-correlation-id"' not in message
    assert len(message) < 2048


def test_largest_allowed_evidence_remains_bounded(settings: Settings) -> None:
    challenge = (
        'Bearer error="insufficient_scope", error_description="The access token provided is expired, '
        'revoked, malformed, or invalid for other reasons"'
    )
    message = _error(
        settings,
        httpx.Response(
            401,
            json={"error": {"message": "User Not Authenticated"}},
            headers={"WWW-Authenticate": ",".join([challenge] * 4), "X-Transaction-ID": "f" * 64},
        ),
    )
    assert message.count('"scheme": "Bearer"') == 4
    assert message.count('"error_description"') == 4
    assert all(" " <= char <= "~" for char in message)
    assert len(message) < 2048
