"""Outbound ServiceNow public OAuth authorization-code PKCE S256 with memory-only access tokens."""

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
    """A bearer token with a conservative monotonic expiry; never persisted."""

    value: str = field(repr=False)
    expires_at: float


def _parse_token(payload: object, issued_at: float) -> AccessToken:
    if not isinstance(payload, dict):
        raise AuthError("Invalid OAuth token response; expected a JSON object.")
    value = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise AuthError("Invalid OAuth access token in response.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("OAuth response must specify the Bearer token type.")
    if isinstance(expires_in, str) and re.fullmatch(r"[0-9]{1,10}", expires_in):
        expires_in = int(expires_in)
    if type(expires_in) is not int or not 0 < expires_in <= 2**31:
        raise AuthError("OAuth response must specify a positive expires_in lifetime in seconds.")
    return AccessToken(value, issued_at + expires_in - min(30, expires_in / 10))


class OAuthPKCEProvider:
    """Authorize a public client in the local browser using PKCE S256.

    Concurrent requests share one authorization flow. Failures raise AuthError;
    cancellation closes the callback listener. Access tokens stay in memory only.
    Expiry or invalidation requires new browser authorization on the next call.
    Callback state is validated but never sent to the token endpoint.
    REST failures never replay the request.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings: Settings = settings
        self._token: AccessToken | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_headers(self) -> dict[str, str]:
        """Return Bearer headers; open browser authorization when no usable token exists."""
        async with self._lock:
            if self._token is None or time.monotonic() >= self._token.expires_at:
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
        """Discard a rejected token without invalidating a newer concurrent grant."""
        if self._token is not None and secrets.compare_digest(authorization, f"Bearer {self._token.value}"):
            self._token = None

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
                "Check the public application client ID, PKCE S256, useraccount scope and registered redirect URI."
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise AuthError("OAuth token endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at)
        if time.monotonic() >= token.expires_at:
            raise AuthError("OAuth token expired during exchange. Call the tool again to authorize.")
        return token


def create_auth(settings: Settings) -> OAuthPKCEProvider:
    """Create a memory-only OAuth provider; construction does not open a browser."""
    return OAuthPKCEProvider(settings)
