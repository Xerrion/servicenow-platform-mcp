"""Shared HTTP lifecycle and error mapping for ServiceNow API clients."""

import json
import logging
import re
import uuid
from typing import Any, Self

import httpx

from servicenow_mcp._rest_auth_evidence import rest_auth_evidence
from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import (
    ACLError,
    AuthError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    ServiceNowMCPError,
)
from servicenow_mcp.sentry import set_sentry_context
from servicenow_mcp.validation import validate_identifier


logger = logging.getLogger(__name__)

_ACL_INDICATOR_RE: re.Pattern[str] = re.compile(r"\b(?:acl|access control)\b")


class ServiceNowRequestClient:
    """Own shared transport state, request headers, and HTTP error mapping."""

    _settings: Settings
    _auth_provider: OAuthPKCEProvider
    _http_client: httpx.AsyncClient | None
    _owns_http_client: bool

    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._http_client = http_client
        self._owns_http_client = http_client is None

    async def __aenter__(self) -> Self:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._settings.httpx_timeout_seconds)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http_client and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the initialized HTTP client."""
        if self._http_client is None:
            raise RuntimeError("Client not initialized. Use 'async with ServiceNowClient(...)' as context manager.")
        return self._http_client

    async def _headers(self) -> dict[str, str]:
        """Build isolated request headers with authorization and correlation data."""
        headers = await self._auth_provider.get_headers()
        headers["X-Correlation-ID"] = str(uuid.uuid4())
        return headers

    def _table_url(self, table: str, sys_id: str | None = None) -> str:
        """Build a validated Table API resource URL."""
        validate_identifier(table)
        url = f"{self._settings.servicenow_instance_url}/api/now/table/{table}"
        return f"{url}/{sys_id}" if sys_id else url

    @staticmethod
    def _extract_result(data: dict[str, Any]) -> Any:
        """Extract the result value from a ServiceNow response payload."""
        try:
            return data["result"]
        except KeyError:
            raise ServerError("Unexpected API response format: missing 'result' key") from None

    def _extract_json_result(self, response: httpx.Response) -> Any:
        """Parse a result without disclosing response bodies or query values in errors."""
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ServerError(
                f"Invalid JSON response from {response.request.method} {response.request.url.path} "
                f"(HTTP {response.status_code}). Check the endpoint response and authentication; "
                "this does not establish an ACL denial."
            ) from None
        if not isinstance(payload, dict):
            raise ServerError(
                f"Unexpected JSON response from {response.request.url.path}: expected an object with 'result'."
            )
        return self._extract_result(payload)

    @staticmethod
    def _parse_total_count(response: httpx.Response) -> int:
        """Parse X-Total-Count, defaulting to zero when absent or invalid."""
        try:
            return int(response.headers.get("X-Total-Count", "0"))
        except (TypeError, ValueError):
            return 0

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map ServiceNow HTTP failures to caller-facing exceptions."""
        if response.status_code < 400:
            return

        url = str(response.request.url).split("?", 1)[0]
        set_sentry_context(
            "http",
            {
                "status_code": response.status_code,
                "method": response.request.method,
                "url": url,
            },
        )

        if response.status_code == 401:
            authorization = response.request.headers.get("Authorization", "")
            token = self._auth_provider._token
            self._auth_provider.invalidate(authorization)
            evidence = rest_auth_evidence(
                response,
                (
                    authorization.removeprefix("Bearer "),
                    token.value if token else "",
                    self._settings.servicenow_oauth_client_id,
                ),
            )
            raise AuthError(
                "ServiceNow rejected the OAuth token on a REST request (HTTP 401). "
                "The request was not replayed. On the next tool call, authorize again in the browser. "
                "If a newly issued token is rejected again, "
                "ask the ServiceNow administrator to check the granted scopes, REST API access policy, "
                "and user access on the configured instance. A successful token exchange does not establish REST access. "
                f"Safe response evidence: {evidence}"
            )
        if response.status_code == 403:
            message = self._extract_error_message(response, "Access forbidden")
            if self._is_acl_error_response(response):
                raise ACLError(message)
            raise ForbiddenError(message)
        if response.status_code == 404:
            raise NotFoundError(self._extract_error_message(response, "Resource not found"))
        if response.status_code >= 500:
            raise ServerError(
                self._extract_error_message(response, "ServiceNow server error"),
                status_code=response.status_code,
            )
        raise ServiceNowMCPError(
            self._extract_error_message(response, "Request failed"),
            status_code=response.status_code,
        )

    @staticmethod
    def _is_acl_error_response(response: httpx.Response) -> bool:
        """Return whether a ServiceNow 403 response explicitly reports an ACL denial."""
        try:
            payload = response.json()
        except Exception:
            logger.debug("Could not parse ServiceNow error body for ACL detection", exc_info=True)
            return False

        values: list[str] = []

        def collect_strings(value: Any) -> None:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect_strings(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_strings(nested)

        collect_strings(payload)
        return bool(_ACL_INDICATOR_RE.search("\n".join(values).lower()))

    @staticmethod
    def _extract_error_message(response: httpx.Response, default: str) -> str:
        """Extract a ServiceNow error message when the response shape permits it."""
        try:
            body = response.json()
            if "error" in body and "message" in body["error"]:
                return body["error"]["message"]
        except Exception:
            logger.debug("Could not parse ServiceNow error body", exc_info=True)
        return default
