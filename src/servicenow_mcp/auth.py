"""ServiceNow public OAuth PKCE with optional native OS refresh-token storage."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from servicenow_mcp._json import JSON
from servicenow_mcp.credential_cache import CredentialCache
from servicenow_mcp.errors import AuthError
from servicenow_mcp.oauth_callback import receive_authorization_code

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from typing import Any

    from servicenow_mcp.config import Settings


_required_generation: ContextVar[int | None] = ContextVar(
    "required_authorization_generation", default=None
)


@contextmanager
def require_authorization_generation(generation: int) -> Generator[None]:
    """Prevent a staged command from obtaining headers under a different grant."""
    token = _required_generation.set(generation)
    try:
        yield
    finally:
        _required_generation.reset(token)


class AccessToken(BaseModel):
    """A bearer token, conservative monotonic expiry, and optional refresh token."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    value: SecretStr = Field(repr=False)
    expires_at: float
    refresh_token: SecretStr | None = Field(default=None, repr=False)


class _TokenResponse(BaseModel):
    """Raw OAuth token response fields; value checks stay in ``_parse_token`` for curated errors."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    access_token: object = None
    token_type: object = None
    expires_in: object = None
    refresh_token: object = None


class _RefreshRejected(AuthError):
    """A refresh grant rejected by ServiceNow, requiring browser authorization."""


def _parse_token(
    payload: object, issued_at: float, fallback_refresh_token: str | None = None
) -> AccessToken:
    """Validate a token response and preserve an unrotated refresh token."""
    if not isinstance(payload, dict):
        raise AuthError("Invalid OAuth token response; expected a JSON object.")
    response = _TokenResponse.model_validate(payload)
    has_refresh_token = "refresh_token" in response.model_fields_set
    value = response.access_token
    token_type = response.token_type
    expires_in = response.expires_in
    refresh_token = (
        response.refresh_token if has_refresh_token else fallback_refresh_token
    )
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise AuthError("Invalid OAuth access token in response.")
    if has_refresh_token and (
        not isinstance(refresh_token, str)
        or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", refresh_token)
    ):
        raise AuthError("Invalid OAuth refresh token in response.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("OAuth response must specify the Bearer token type.")
    if isinstance(expires_in, str) and re.fullmatch(r"[0-9]{1,10}", expires_in):
        expires_in = int(expires_in)
    if type(expires_in) is not int or not 0 < expires_in <= 2**31:
        raise AuthError(
            "OAuth response must specify a positive expires_in lifetime in seconds."
        )
    return AccessToken(
        value=SecretStr(value),
        expires_at=issued_at + expires_in - min(30, expires_in / 10),
        refresh_token=SecretStr(refresh_token)
        if isinstance(refresh_token, str)
        else None,
    )


class OAuthPKCEProvider:
    """Authorize a public client in the local browser using PKCE S256.

    Concurrent requests share one authorization flow. Failures raise AuthError;
    cancellation closes the callback listener. Access tokens stay in memory.
    Refresh tokens may use an explicitly enabled native OS credential cache.
    Callback state is validated but never sent to the token endpoint.
    REST failures never replay the request.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings: Settings = settings
        self._token: AccessToken | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._generation = 0
        self._cache = (
            CredentialCache(settings)
            if settings.servicenow_oauth_cache == "os"
            else None
        )
        self._cache_loaded = False

    @property
    def authorization_generation(self) -> int:
        return self._generation

    async def get_headers(self) -> dict[str, str]:
        """Return Bearer headers, refreshing or authorizing when necessary."""
        async with self._lock:
            expected = _required_generation.get()
            if expected is not None and (
                expected != self._generation or self._token is None
            ):
                raise AuthError(
                    "Authorization changed. Create a new preview before applying a change."
                )
            if self._cache is not None:
                try:
                    if not self._cache_loaded:
                        cached = await self._cache_io(self._cache.load)
                        self._cache_loaded = True
                        if cached is not None:
                            self._token = AccessToken(
                                value=SecretStr(""), expires_at=0, refresh_token=cached
                            )
                            self._generation += 1
                    else:
                        await self._cache_io(self._cache.check_current)
                except AuthError:
                    self._token = None
                    self._generation += 1
                    raise
            if self._token is None:
                await self._accept(await self._authorize(), new_grant=True)
                self._generation += 1
            elif time.monotonic() >= self._token.expires_at:
                refresh_token = self._token.refresh_token
                if refresh_token is None:
                    if expected is not None:
                        raise AuthError(
                            "Browser authorization is required. Authorize on a read, then create a new preview.",
                        )
                    self._token = None
                    await self._accept(await self._authorize(), new_grant=True)
                    self._generation += 1
                else:
                    try:
                        await self._accept(
                            await self._refresh(refresh_token.get_secret_value())
                        )
                    except _RefreshRejected:
                        self._token = None
                        if self._cache is not None:
                            await self._cache_io(self._cache.clear)
                        if expected is not None:
                            raise AuthError(
                                "Authorization expired. Authorize on a read, then create a new preview.",
                            ) from None
                        await self._accept(await self._authorize(), new_grant=True)
                        self._generation += 1
            if self._token is None:
                raise AuthError(
                    "OAuth authorization is unavailable. Authorize on a read before creating new previews."
                )
            if time.monotonic() >= self._token.expires_at:
                self._token = None
                raise AuthError(
                    "OAuth token expired during authorization. Call the tool again to authorize."
                )
            return {
                "Authorization": f"Bearer {self._token.value.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    async def _accept(self, token: AccessToken, *, new_grant: bool = False) -> None:
        if self._cache is not None:
            try:
                await self._cache_io(
                    self._cache.save, token.refresh_token, new_grant=new_grant
                )
            except BaseException:
                self._token = None
                raise
        self._token = token

    async def _cache_io(
        self, operation: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        # A cancelled waiter must not release the provider lock while its native
        # store write still runs. No background credential writer is left behind.
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:  # noqa: BLE001, S110 - cancellation must not disclose backend errors
                pass
            raise

    def invalidate(self, authorization: str) -> None:
        """Expire a rejected token without invalidating a newer concurrent grant."""
        if self._token is not None and secrets.compare_digest(
            authorization,
            f"Bearer {self._token.value.get_secret_value()}",
        ):
            if self._token.refresh_token is None:
                self._token = None
            else:
                self._token = AccessToken(
                    value=SecretStr(""),
                    expires_at=0,
                    refresh_token=self._token.refresh_token,
                )

    def current_token_secret(self) -> str:
        """Return the stored access token's secret value, or empty when no token is held."""
        return self._token.value.get_secret_value() if self._token is not None else ""

    async def _authorize(self) -> AccessToken:
        settings = self._settings
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode()
        )
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
            async with httpx2.AsyncClient(
                timeout=settings.httpx_timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.post(
                    f"{settings.servicenow_instance_url}/oauth_token.do",
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx2.HTTPError:
            raise AuthError(
                "OAuth token exchange failed. Check connectivity before calling the tool again."
            ) from None
        if response.status_code != 200:
            raise AuthError(
                f"OAuth token exchange rejected (HTTP {response.status_code}). "
                "Check the public application client ID, PKCE S256, configured OAuth scope and registered redirect "
                "URI.",
            )
        try:
            payload = JSON.validate_json(response.content)
        except ValidationError:
            raise AuthError("OAuth token endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at)
        if time.monotonic() >= token.expires_at:
            raise AuthError(
                "OAuth token expired during exchange. Call the tool again to authorize."
            )
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
            async with httpx2.AsyncClient(
                timeout=settings.httpx_timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.post(
                    f"{settings.servicenow_instance_url}/oauth_token.do",
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx2.HTTPError:
            raise AuthError(
                "OAuth token refresh failed. Check connectivity before calling the tool again."
            ) from None
        if response.status_code in {400, 401}:
            raise _RefreshRejected(
                f"OAuth token refresh rejected (HTTP {response.status_code}); browser authorization is required.",
            )
        if response.status_code != 200:
            raise AuthError(
                f"OAuth token refresh failed (HTTP {response.status_code}). Call the tool again to retry."
            )
        try:
            payload = JSON.validate_json(response.content)
        except ValidationError:
            raise AuthError("OAuth refresh endpoint returned invalid JSON.") from None
        token = _parse_token(payload, issued_at, fallback_refresh_token=refresh_token)
        if time.monotonic() >= token.expires_at:
            raise AuthError(
                "OAuth token expired during refresh. Call the tool again to retry."
            )
        return token


def create_auth(settings: Settings) -> OAuthPKCEProvider:
    """Create a provider without authorization or credential-store I/O."""
    return OAuthPKCEProvider(settings)
