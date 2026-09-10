"""Outbound ServiceNow OAuth with public PKCE or confidential grants and memory-only tokens."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode

import httpx

from servicenow_mcp.config import Settings
from servicenow_mcp.errors import AuthError
from servicenow_mcp.oauth_callback import receive_authorization_code


@dataclass(frozen=True)
class AccessToken:
    """A bearer token with a conservative monotonic expiry; never persisted."""

    value: str = field(repr=False)
    expires_at: float
    refresh_token: str | None = field(default=None, repr=False)


class _RefreshGrantRejected(AuthError):
    """The refresh grant is no longer usable; interactive authorization is needed."""


def _parse_token(payload: object, issued_at: float) -> AccessToken:
    if not isinstance(payload, dict):
        raise AuthError("Invalid OAuth token response; expected a JSON object.")
    value = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    refresh_token = payload.get("refresh_token")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise AuthError("Invalid OAuth access token in response.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("OAuth response must specify the Bearer token type.")
    if isinstance(expires_in, str) and re.fullmatch(r"[0-9]{1,10}", expires_in):
        expires_in = int(expires_in)
    if type(expires_in) is not int or not 0 < expires_in <= 2**31:
        raise AuthError("OAuth response must specify a positive expires_in lifetime in seconds.")
    if refresh_token is not None and (
        not isinstance(refresh_token, str) or not re.fullmatch(r"[\x21-\x7e]+", refresh_token)
    ):
        raise AuthError("Invalid OAuth refresh token in response.")
    return AccessToken(value, issued_at + expires_in - min(30, expires_in / 10), refresh_token)


class OAuthPKCEProvider:
    """Authorize in the local browser; renew confidential refresh grants in memory.

    Concurrent requests share one authorization flow. Failures raise AuthError;
    cancellation closes the callback listener. A configured client secret selects
    confidential authorization without PKCE and is sent only to the token endpoint.
    Without a secret, authorization uses public PKCE S256 without refresh grants.
    Both modes require scope and state. REST failures never replay the rejected request.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings: Settings = settings
        self._token: AccessToken | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_headers(self) -> dict[str, str]:
        """Return bearer headers, refreshing before opening a browser when possible."""
        async with self._lock:
            if self._token is None or time.monotonic() >= self._token.expires_at:
                self._token = await self._renew()
            if time.monotonic() >= self._token.expires_at:
                self._token = None
                raise AuthError("OAuth token expired during authorization. Retry to authorize again.")
            return {
                "Authorization": f"Bearer {self._token.value}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    async def _renew(self) -> AccessToken:
        if (
            self._settings.servicenow_oauth_client_secret.get_secret_value()
            and self._token is not None
            and self._token.refresh_token
        ):
            try:
                return await self._refresh(self._token.refresh_token)
            except _RefreshGrantRejected:
                self._token = None
        self._token = None
        return await self._authorize()

    def invalidate(self, authorization: str) -> None:
        """Discard a rejected token without invalidating a newer concurrent grant."""
        if self._token is not None and secrets.compare_digest(authorization, f"Bearer {self._token.value}"):
            self._token = replace(self._token, expires_at=0) if self._token.refresh_token else None

    async def _refresh(self, refresh_token: str) -> AccessToken:
        token = await self._exchange({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return replace(token, refresh_token=token.refresh_token or refresh_token)

    async def _authorize(self) -> AccessToken:
        settings = self._settings
        state = secrets.token_urlsafe(32)
        query = {
            "response_type": "code",
            "client_id": settings.servicenow_oauth_client_id,
            "redirect_uri": settings.servicenow_oauth_redirect_uri,
            "scope": settings.servicenow_oauth_scope,
            "state": state,
        }
        grant = {
            "grant_type": "authorization_code",
            "redirect_uri": settings.servicenow_oauth_redirect_uri,
            "state": state,
        }
        if not settings.servicenow_oauth_client_secret.get_secret_value():
            verifier = secrets.token_urlsafe(64)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
            )
            query.update(code_challenge=challenge, code_challenge_method="S256")
            grant["code_verifier"] = verifier
        grant["code"] = await receive_authorization_code(
            f"{settings.servicenow_instance_url}/oauth_auth.do?{urlencode(query)}",
            settings.servicenow_oauth_redirect_uri,
            state,
            settings.servicenow_oauth_timeout_seconds,
        )
        return await self._exchange(grant)

    async def _exchange(self, grant: dict[str, str]) -> AccessToken:
        settings = self._settings
        data = {**grant, "client_id": settings.servicenow_oauth_client_id}
        if settings.servicenow_oauth_client_secret.get_secret_value():
            data["client_secret"] = settings.servicenow_oauth_client_secret.get_secret_value()
        issued_at = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=settings.httpx_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.servicenow_instance_url}/oauth_token.do",
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError:
            raise AuthError("OAuth token exchange failed. Check connectivity and retry the tool call.") from None
        if response.status_code != 200:
            if grant["grant_type"] == "refresh_token" and response.status_code == 400:
                try:
                    error = response.json()
                except (ValueError, UnicodeDecodeError):
                    raise AuthError("OAuth token endpoint returned invalid JSON (HTTP 400).") from None
                if isinstance(error, dict) and error.get("error") == "invalid_grant":
                    raise _RefreshGrantRejected("OAuth refresh grant expired or was revoked.")
            raise AuthError(
                f"OAuth token exchange rejected (HTTP {response.status_code}). "
                "Check the application client ID, authorization mode, required scope and redirect URI. "
                "A confidential client requires SERVICENOW_OAUTH_CLIENT_SECRET; "
                "a public client requires PKCE S256 with no client secret."
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise AuthError("OAuth token endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at)
        if time.monotonic() >= token.expires_at:
            raise AuthError("OAuth token expired during exchange. Retry the tool call.")
        return token


def create_auth(settings: Settings) -> OAuthPKCEProvider:
    """Create a memory-only OAuth provider; construction does not open a browser."""
    return OAuthPKCEProvider(settings)
