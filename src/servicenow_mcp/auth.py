"""ServiceNow public OAuth PKCE with memory-only access and refresh tokens."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from servicenow_mcp.config import Settings
from servicenow_mcp.errors import AuthError
from servicenow_mcp.oauth_callback import receive_authorization_code


@dataclass(frozen=True)
class AccessToken:
    """A bearer token, conservative monotonic expiry, and optional refresh token."""

    value: str = field(repr=False)
    expires_at: float
    refresh_token: str | None = field(default=None, repr=False)


class _RefreshRejected(AuthError):
    """A refresh grant rejected by ServiceNow, requiring browser authorization."""


def _parse_token(payload: object, issued_at: float, fallback_refresh_token: str | None = None) -> AccessToken:
    """Validate a token response and preserve an unrotated refresh token."""
    if not isinstance(payload, dict):
        raise AuthError("Invalid OAuth token response; expected a JSON object.")
    value = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    refresh_token = payload.get("refresh_token") if "refresh_token" in payload else fallback_refresh_token
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise AuthError("Invalid OAuth access token in response.")
    if "refresh_token" in payload and (
        not isinstance(refresh_token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", refresh_token)
    ):
        raise AuthError("Invalid OAuth refresh token in response.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("OAuth response must specify the Bearer token type.")
    if isinstance(expires_in, str) and re.fullmatch(r"[0-9]{1,10}", expires_in):
        expires_in = int(expires_in)
    if type(expires_in) is not int or not 0 < expires_in <= 2**31:
        raise AuthError("OAuth response must specify a positive expires_in lifetime in seconds.")
    return AccessToken(value, issued_at + expires_in - min(30, expires_in / 10), refresh_token)


class OAuthPKCEProvider:
    """Authorize a public client in the local browser using PKCE S256.

    Concurrent requests share one authorization flow. Failures raise AuthError;
    cancellation closes the callback listener. Access and refresh tokens stay in
    memory only. Expiry or invalidation refreshes the access token when possible.
    Callback state is validated but never sent to the token endpoint.
    REST failures never replay the request.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings: Settings = settings
        self._token: AccessToken | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_headers(self) -> dict[str, str]:
        """Return Bearer headers, refreshing or authorizing when necessary."""
        async with self._lock:
            if self._token is None:
                self._token = await self._authorize()
            elif time.monotonic() >= self._token.expires_at:
                refresh_token = self._token.refresh_token
                if refresh_token is None:
                    self._token = None
                    self._token = await self._authorize()
                else:
                    try:
                        self._token = await self._refresh(refresh_token)
                    except _RefreshRejected:
                        self._token = None
                        self._token = await self._authorize()
            if time.monotonic() >= self._token.expires_at:
                self._token = None
                raise AuthError("OAuth token expired during authorization. Call the tool again to authorize.")
            return {
                "Authorization": f"Bearer {self._token.value}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    def invalidate(self, authorization: str) -> None:
        """Expire a rejected token without invalidating a newer concurrent grant."""
        if self._token is not None and secrets.compare_digest(authorization, f"Bearer {self._token.value}"):
            if self._token.refresh_token is None:
                self._token = None
            else:
                self._token = AccessToken("", 0, self._token.refresh_token)

    async def _authorize(self) -> AccessToken:
        settings = self._settings
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        query = {
            "response_type": "code",
            "client_id": settings.servicenow_oauth_client_id,
            "redirect_uri": settings.servicenow_oauth_redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": settings.servicenow_oauth_scope,
            "state": state,
        }
        code = await receive_authorization_code(
            f"{settings.servicenow_instance_url}/oauth_auth.do?{urlencode(query)}",
            settings.servicenow_oauth_redirect_uri,
            state,
            settings.servicenow_oauth_timeout_seconds,
        )
        return await self._exchange(code, verifier)

    async def _exchange(self, code: str, verifier: str) -> AccessToken:
        settings = self._settings
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.servicenow_oauth_redirect_uri,
            "client_id": settings.servicenow_oauth_client_id,
            "code_verifier": verifier,
        }
        issued_at = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=settings.httpx_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.servicenow_instance_url}/oauth_token.do",
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError:
            raise AuthError("OAuth token exchange failed. Check connectivity before calling the tool again.") from None
        if response.status_code != 200:
            raise AuthError(
                f"OAuth token exchange rejected (HTTP {response.status_code}). "
                "Check the public application client ID, PKCE S256, configured OAuth scope and registered redirect URI."
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise AuthError("OAuth token endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at)
        if time.monotonic() >= token.expires_at:
            raise AuthError("OAuth token expired during exchange. Call the tool again to authorize.")
        return token

    async def _refresh(self, refresh_token: str) -> AccessToken:
        """Exchange a refresh token as a public client, retaining it if not rotated."""
        settings = self._settings
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.servicenow_oauth_client_id,
        }
        issued_at = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=settings.httpx_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.servicenow_instance_url}/oauth_token.do",
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError:
            raise AuthError("OAuth token refresh failed. Check connectivity before calling the tool again.") from None
        if response.status_code in {400, 401}:
            raise _RefreshRejected(
                f"OAuth token refresh rejected (HTTP {response.status_code}); browser authorization is required."
            )
        if response.status_code != 200:
            raise AuthError(f"OAuth token refresh failed (HTTP {response.status_code}). Call the tool again to retry.")
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise AuthError("OAuth refresh endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at, fallback_refresh_token=refresh_token)
        if time.monotonic() >= token.expires_at:
            raise AuthError("OAuth token expired during refresh. Call the tool again to retry.")
        return token


def create_auth(settings: Settings) -> OAuthPKCEProvider:
    """Create a memory-only OAuth provider; construction does not open a browser."""
    return OAuthPKCEProvider(settings)
