"""Native-cache lifecycle tests use synthetic secrets and no OS-user credentials."""

import asyncio
import json
import sys
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from servicenow_mcp import credential_cache
from servicenow_mcp.auth import OAuthPKCEProvider, require_authorization_generation
from servicenow_mcp.credential_cache import CredentialCache
from servicenow_mcp.errors import AuthError
from tests._mock_transport import http_mock


class Store:
    """Memory fixture only. Production never selects this backend."""

    def __init__(self):
        self.records = {}
        self.fail = None

    def get_password(self, service, account):
        if self.fail == "read":
            raise RuntimeError("synthetic-sensitive-backend-error")
        return self.records.get((service, account))

    def set_password(self, service, account, value):
        if self.fail == "write":
            raise RuntimeError("synthetic-sensitive-backend-error")
        self.records[service, account] = value

    def delete_password(self, service, account):
        if self.fail == "delete":
            raise RuntimeError("synthetic-sensitive-backend-error")
        del self.records[service, account]


@pytest.fixture()
def native_store(monkeypatch):
    store = Store()
    monkeypatch.setattr(credential_cache, "_native_backend", lambda: store)
    return store


def os_settings(settings):
    return settings.model_copy(update={"servicenow_oauth_cache": "os"})


def seed(settings, store, refresh="synthetic-refresh", expires=None):
    cache = CredentialCache(settings)
    store.records[cache.service, cache.account] = json.dumps(
        {
            "version": 1,
            "refresh_token": refresh,
            "expires_at": int(time.time()) + 3600 if expires is None else expires,
        }
    )
    return cache


def token_route(refresh="synthetic-rotated"):
    return http_mock.post("https://test.service-now.com/oauth_token.do").respond(
        200,
        json={
            "access_token": "synthetic-access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh,
        },
    )


def test_cache_is_opt_in_and_construction_does_no_io(settings):
    assert settings.servicenow_oauth_cache == "memory"
    with patch.object(credential_cache, "_native_backend", side_effect=AssertionError):
        assert OAuthPKCEProvider(settings)._cache is None
        assert OAuthPKCEProvider(os_settings(settings))._cache is not None


@pytest.mark.parametrize("value", [pytest.param("plaintext", id="plaintext")])
def test_cache_setting_rejects_unknown_backends(settings, value):
    from servicenow_mcp.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, **{**settings.model_dump(), "servicenow_oauth_cache": value}
        )


def test_cache_partition(settings):
    for field, value in [
        ("servicenow_instance_url", "https://other.service-now.com"),
        ("servicenow_oauth_client_id", "other-client"),
        ("servicenow_oauth_scope", "other-scope"),
        ("servicenow_oauth_redirect_uri", "http://127.0.0.1:8766/oauth/callback"),
    ]:
        assert (
            CredentialCache(settings).service
            != CredentialCache(settings.model_copy(update={field: value})).service
        )


def test_origin_and_scope_canonicalization(settings):
    a = settings.model_copy(
        update={
            "servicenow_instance_url": "https://TEST.service-now.com:443",
            "servicenow_oauth_scope": "b a",
        }
    )
    b = settings.model_copy(update={"servicenow_oauth_scope": "a b a"})
    assert CredentialCache(a).service == CredentialCache(b).service
    assert "test.service-now.com" not in CredentialCache(a).service


@pytest.mark.parametrize(
    "platform,module,class_name",
    [
        ("darwin", "keyring.backends.macOS", "Keyring"),
        ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
        ("linux", "keyring.backends.SecretService", "Keyring"),
    ],
)
def test_exact_native_backend_selection_ignores_plugins_and_properties(
    monkeypatch, platform, module, class_name
):
    import keyring

    class Native:
        priority = 5

        def __init__(self):
            raise AssertionError("Environment backend properties must not be applied")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{class_name: Native}))
    monkeypatch.setattr(
        keyring, "get_keyring", lambda: pytest.fail("No automatic backend discovery")
    )
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")
    monkeypatch.setenv("KEYRING_PROPERTY_GET_PASSWORD", "synthetic-sensitive-value")
    result = credential_cache._native_backend()
    if platform == "linux":
        assert isinstance(result, Native)
    else:
        assert type(result) is Native
    if platform == "win32":
        assert result.persist == "local machine"
    if platform == "darwin":
        assert result.keychain is None


@pytest.mark.parametrize("platform", ["freebsd", "darwin", "win32", "linux"])
def test_no_secure_backend_fails_without_fallback(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    for module in (
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
    ):
        monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(AuthError, match="No fallback storage"):
        credential_cache._native_backend()


@pytest.mark.parametrize("locked", ["collection", "item"])
def test_linux_locked_store_never_unlocks(monkeypatch, locked):
    class Native:
        priority = 5

    collection = SimpleNamespace(
        is_locked=lambda: locked == "collection",
        connection=SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(
        sys.modules, "keyring.backends.SecretService", SimpleNamespace(Keyring=Native)
    )
    monkeypatch.setitem(
        sys.modules,
        "secretstorage",
        SimpleNamespace(
            dbus_init=lambda: collection.connection, Collection=lambda _: collection
        ),
    )
    backend = credential_cache._native_backend()
    from keyring.errors import KeyringLocked

    with pytest.raises(KeyringLocked):
        if locked == "collection":
            backend.get_preferred_collection()
        else:
            backend.unlock(SimpleNamespace(is_locked=lambda: True))


def test_malformed_records_are_sanitized_and_not_exported(settings, native_store):
    for value in ["", "not-json", "[]", "{}", '{"version":1}', "x" * 4097]:
        cache = CredentialCache(settings)
        native_store.records[cache.service, cache.account] = value
        with pytest.raises(AuthError) as error:
            cache.load()
        assert (
            str(error.value)
            == "OS OAuth cache record is invalid. Stop cache-enabled processes and clear this entry."
        )
        assert error.value.__cause__ is None


def test_cache_record_contract(settings, native_store):
    for change in [
        {"version": True},
        {"expires_at": True},
        {"expires_at": "123"},
        {"refresh_token": None},
        {"refresh_token": "bad\r\nsecret"},
        {"extra": "synthetic-sensitive-value"},
    ]:
        cache = seed(settings, native_store)
        key = (cache.service, cache.account)
        native_store.records[key] = json.dumps(
            {**json.loads(native_store.records[key]), **change}
        )
        with pytest.raises(AuthError):
            cache.load()


@pytest.mark.parametrize("expiry", [0, -1, credential_cache.MAX_AGE_SECONDS * 2])
def test_expired_or_unreasonable_entries_are_removed(settings, native_store, expiry):
    cache = seed(settings, native_store, expires=int(time.time()) + expiry)
    assert cache.load() is None
    assert not native_store.records


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_storage_errors_do_not_reveal_values_or_traceback_chain(
    settings, native_store, operation, caplog
):
    cache = seed(settings, native_store)
    if operation != "read":
        cache.load()
    native_store.fail = operation
    with pytest.raises(AuthError) as error:
        if operation == "read":
            cache.load()
        elif operation == "write":
            cache.save(SecretStr("synthetic-refresh"))
        else:
            cache.clear()
    assert "synthetic-sensitive" not in "".join(traceback.format_exception(error.value))
    assert "synthetic-sensitive" not in caplog.text
    assert error.value.__context__ is None


async def test_fresh_process_refreshes_cached_grant_without_browser(
    settings, native_store, caplog
):
    settings = os_settings(settings)
    seed(settings, native_store)
    route = token_route()
    provider = OAuthPKCEProvider(settings)
    browser = AsyncMock(side_effect=AssertionError("No browser"))
    with patch.object(provider, "_authorize", browser):
        results = await asyncio.gather(*(provider.get_headers() for _ in range(5)))
    assert all(row["Authorization"] == "Bearer synthetic-access" for row in results)
    assert route.call_count == 1
    browser.assert_not_awaited()
    assert provider.authorization_generation == 1
    assert parse_qs(route.calls.last.request.content.decode())["refresh_token"] == [
        "synthetic-refresh"
    ]
    stored = next(iter(native_store.records.values()))
    assert "synthetic-rotated" in stored
    assert "synthetic-access" not in stored
    assert "expires_at" in stored
    assert "synthetic-refresh" not in caplog.text
    assert "synthetic-rotated" not in repr(provider._cache)


async def test_new_grant_persists_refresh_only_and_no_refresh_grant_clears_old(
    settings, native_store
):
    settings = os_settings(settings)
    token_route()
    provider = OAuthPKCEProvider(settings)
    with patch(
        "servicenow_mcp.auth.receive_authorization_code", return_value="synthetic-code"
    ):
        await provider.get_headers()
    stored = next(iter(native_store.records.values()))
    assert "synthetic-rotated" in stored
    assert "synthetic-access" not in stored
    assert (
        json.loads(stored)["expires_at"]
        <= time.time() + credential_cache.MAX_AGE_SECONDS
    )
    provider._token = None
    http_mock.post("https://test.service-now.com/oauth_token.do").respond(
        200,
        json={
            "access_token": "synthetic-new",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    with patch(
        "servicenow_mcp.auth.receive_authorization_code", return_value="synthetic-code"
    ):
        await provider.get_headers()
    assert not native_store.records


async def test_refresh_keeps_local_cache_lifetime_not_monotonic_clock(
    settings, native_store
):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    expiry = json.loads(native_store.records[cache.service, cache.account])[
        "expires_at"
    ]
    route = token_route()
    provider = OAuthPKCEProvider(settings)
    await provider.get_headers()
    assert provider._token is not None
    provider._token = provider._token.model_copy(update={"expires_at": 0})
    await provider.get_headers()
    assert route.call_count == 2
    assert (
        json.loads(native_store.records[cache.service, cache.account])["expires_at"]
        == expiry
    )


async def test_held_preview_cannot_load_or_substitute_cached_account(
    settings, native_store
):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    provider = OAuthPKCEProvider(settings)
    with (
        require_authorization_generation(0),
        pytest.raises(AuthError, match="Authorization changed"),
    ):
        await provider.get_headers()
    assert not http_mock.calls
    token_route()
    await provider.get_headers()
    generation = provider.authorization_generation
    newer = json.dumps(
        {
            "version": 1,
            "refresh_token": "synthetic-other-user",
            "expires_at": int(time.time()) + 1000,
        }
    )
    native_store.records[cache.service, cache.account] = newer
    with (
        require_authorization_generation(generation),
        pytest.raises(AuthError, match="another process"),
    ):
        await provider.get_headers()
    assert provider._token is None
    assert provider.authorization_generation > generation
    assert native_store.records[cache.service, cache.account] == newer


async def test_rejected_refresh_clears_cache_but_never_reauthorizes_a_preview(
    settings, native_store
):
    settings = os_settings(settings)
    seed(settings, native_store)
    route = token_route()
    provider = OAuthPKCEProvider(settings)
    await provider.get_headers()
    generation = provider.authorization_generation
    assert provider._token is not None
    provider._token = provider._token.model_copy(update={"expires_at": 0})
    route.respond(400, text="synthetic-sensitive-rejection")
    with (
        require_authorization_generation(generation),
        pytest.raises(AuthError, match="Authorization expired"),
    ):
        await provider.get_headers()
    assert not native_store.records
    assert provider._token is None


async def test_rejected_refresh_does_not_delete_another_process_new_entry(
    settings, native_store
):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    newer = None

    def rejected(request):
        nonlocal newer
        newer = json.dumps(
            {
                "version": 1,
                "refresh_token": "synthetic-newer-grant",
                "expires_at": int(time.time()) + 1000,
            }
        )
        native_store.records[cache.service, cache.account] = newer
        return httpx2.Response(400)

    http_mock.post("https://test.service-now.com/oauth_token.do").mock(
        side_effect=rejected
    )
    provider = OAuthPKCEProvider(settings)
    with (
        patch.object(provider, "_authorize", side_effect=AssertionError),
        pytest.raises(AuthError, match="another process"),
    ):
        await provider.get_headers()
    assert native_store.records[cache.service, cache.account] == newer


async def test_unrelated_failed_access_invalidation_does_not_clear_shared_cache(
    settings, native_store
):
    settings = os_settings(settings)
    seed(settings, native_store)
    token_route()
    provider = OAuthPKCEProvider(settings)
    await provider.get_headers()
    before = dict(native_store.records)
    provider.invalidate("Bearer old-access")
    assert provider._token is not None
    assert provider._token.expires_at > time.monotonic()
    provider.invalidate("Bearer synthetic-access")
    assert provider._token is not None
    assert provider._token.expires_at == 0
    assert native_store.records == before


async def test_cache_write_failure_never_releases_access_token(settings, native_store):
    settings = os_settings(settings)
    native_store.fail = "write"
    token_route()
    provider = OAuthPKCEProvider(settings)
    with (
        patch(
            "servicenow_mcp.auth.receive_authorization_code",
            return_value="synthetic-code",
        ),
        pytest.raises(AuthError),
    ):
        await provider.get_headers()
    assert provider._token is None


@pytest.mark.parametrize("raw", [pytest.param("bad-json", id="bad-json")])
def test_clear_works_for_malformed_entry_and_optout_does_not_clear(
    settings, native_store, raw
):
    cache = CredentialCache(settings)
    native_store.records[cache.service, cache.account] = raw
    OAuthPKCEProvider(settings)
    assert native_store.records
    cache.clear()
    assert not native_store.records
    cache.clear()


async def test_rejected_cached_grant_can_authorize_on_a_read(settings, native_store):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    route = http_mock.post("https://test.service-now.com/oauth_token.do").mock(
        side_effect=[
            httpx2.Response(400),
            httpx2.Response(
                200,
                json={
                    "access_token": "synthetic-fresh",
                    "refresh_token": "synthetic-new-grant",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            ),
        ]
    )
    provider = OAuthPKCEProvider(settings)
    with patch(
        "servicenow_mcp.auth.receive_authorization_code", return_value="synthetic-code"
    ) as browser:
        assert (await provider.get_headers())[
            "Authorization"
        ] == "Bearer synthetic-fresh"
    assert route.call_count == 2
    browser.assert_awaited_once()
    assert provider.authorization_generation == 2
    assert "synthetic-new-grant" in native_store.records[cache.service, cache.account]


async def test_unrotated_cached_refresh_is_preserved(settings, native_store):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    http_mock.post("https://test.service-now.com/oauth_token.do").respond(
        200,
        json={
            "access_token": "synthetic-access",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    await OAuthPKCEProvider(settings).get_headers()
    assert (
        json.loads(native_store.records[cache.service, cache.account])["refresh_token"]
        == "synthetic-refresh"
    )


@pytest.mark.parametrize("failure", [pytest.param("network", id="network")])
async def test_transient_refresh_failure_retains_cache_without_browser(
    settings, native_store, failure
):
    settings = os_settings(settings)
    seed(settings, native_store)
    before = dict(native_store.records)
    route = http_mock.post("https://test.service-now.com/oauth_token.do")
    if failure == "network":
        route.mock(side_effect=httpx2.ConnectError("synthetic-sensitive-error"))
    else:
        route.respond(
            500 if failure == "server" else 200, text="synthetic-sensitive-error"
        )
    provider = OAuthPKCEProvider(settings)
    with (
        patch.object(provider, "_authorize", side_effect=AssertionError),
        pytest.raises(AuthError) as error,
    ):
        await provider.get_headers()
    assert native_store.records == before
    assert "synthetic-sensitive" not in str(error.value)
    assert provider._token is not None
    assert not provider.current_token_secret()


async def test_observed_conflict_during_successful_refresh_blocks_header_and_save(
    settings, native_store
):
    settings = os_settings(settings)
    cache = seed(settings, native_store)
    newer = "synthetic-new-account-entry"

    def refresh(request):
        native_store.records[cache.service, cache.account] = newer
        return httpx2.Response(
            200,
            json={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-rotated",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    http_mock.post("https://test.service-now.com/oauth_token.do").mock(
        side_effect=refresh
    )
    provider = OAuthPKCEProvider(settings)
    with pytest.raises(AuthError, match="another process"):
        await provider.get_headers()
    assert provider._token is None
    assert native_store.records[cache.service, cache.account] == newer


def test_oversized_token_and_record_never_fallback(settings, native_store):
    cache = CredentialCache(settings)
    cache.load()
    with pytest.raises(AuthError, match="native cache limits"):
        cache.save(SecretStr("x" * 1001), new_grant=True)
    assert not native_store.records
    native_store.records[cache.service, cache.account] = "x" * 4097
    CredentialCache(settings).clear()
    assert not native_store.records


def test_clear_command_is_sanitized_and_does_not_authorize(
    settings, native_store, monkeypatch, capsys
):
    seed(settings, native_store)
    monkeypatch.setattr(sys, "argv", ["credential_cache", "clear"])
    monkeypatch.setattr("servicenow_mcp.config.Settings", lambda: settings)
    credential_cache.main()
    assert not native_store.records
    assert "cleared" in capsys.readouterr().out
    native_store.fail = "read"
    with pytest.raises(SystemExit) as error:
        credential_cache.main()
    assert "synthetic-sensitive" not in str(error.value)
    assert not http_mock.calls


async def test_cancelled_waiter_does_not_leave_a_native_store_writer(
    settings, native_store
):
    provider = OAuthPKCEProvider(os_settings(settings))
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    completed = []

    def native_write():
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(2):
            raise RuntimeError("Synthetic write was not released")
        completed.append(True)

    task = asyncio.create_task(provider._cache_io(native_write))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed == [True]
