"""Opt-in refresh-token storage in explicitly selected native OS credential stores."""

import hashlib
import json
import re
import sys
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import SecretStr

from servicenow_mcp.errors import AuthError

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings

MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MAX_TOKEN_CHARACTERS = (
    1000  # Leave room for JSON within Windows' credential blob limit.
)
_STORAGE_ERROR = (
    "OS OAuth cache is unavailable or locked. Unlock the native credential store, "
    "or set SERVICENOW_OAUTH_CACHE=memory. No fallback storage was used."
)
_CHANGED_ERROR = (
    "OS OAuth cache changed in another process. No cached account was substituted. "
    "Close this process and create new previews in a new session. Use cache-enabled processes sequentially."
)


def _native_backend() -> Any:
    """Bypass keyring discovery, configured third-party backends, and chainers."""
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            # KeyringBackend.__init__ only applies KEYRING_PROPERTY_* overrides.
            # Skip it so environment properties cannot replace methods or stores.
            backend = object.__new__(Keyring)
            backend.keychain = None  # Use the OS user's default, never KEYCHAIN_PATH.
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            backend = object.__new__(WinVaultKeyring)
            backend.persist = "local machine"
        elif sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring
            from keyring.errors import KeyringLocked

            # The native backend otherwise tries to unlock collections/items.
            # Opt-in cache access must fail rather than open an unlock workflow.
            class UnlockedSecretService(Keyring):
                def get_preferred_collection(self) -> Any:
                    import secretstorage

                    connection = secretstorage.dbus_init()
                    try:
                        collection = secretstorage.Collection(connection)
                        if collection.is_locked():
                            raise KeyringLocked("Credential collection is locked")
                        return collection
                    except Exception:
                        connection.close()
                        raise

                def unlock(self, item: Any) -> None:
                    if item.is_locked():
                        raise KeyringLocked("Credential item is locked")

            backend = object.__new__(UnlockedSecretService)
            backend.scheme = "default"
        else:
            raise RuntimeError
        if backend.priority > 0:
            return backend
    except Exception:  # noqa: BLE001, S110 - sanitize without logging backend exception values
        pass
    raise AuthError(_STORAGE_ERROR) from None


class CredentialCache:
    """One OS-user/configuration entry, with optimistic stale-writer detection.

    Native keyring APIs have no compare-and-swap. The read-before-write checks
    reject observed conflicts, but callers must use separate processes sequentially.
    Access tokens and monotonic expiry are never persisted.
    """

    def __init__(self, settings: Settings) -> None:
        url = urlsplit(settings.servicenow_instance_url)
        origin = f"https://{url.hostname}" + (
            f":{url.port}" if url.port not in {None, 443} else ""
        )
        identity = [
            origin,
            settings.servicenow_oauth_client_id,
            sorted(set(settings.servicenow_oauth_scope.split())),
            settings.servicenow_oauth_redirect_uri,
        ]
        digest = hashlib.sha256(
            json.dumps(identity, separators=(",", ":")).encode()
        ).hexdigest()
        self.service = f"servicenow-platform-mcp.oauth.v1.{digest}"
        self.account = "refresh"
        self._backend: Any = None
        self._observed: SecretStr | None = None
        self._loaded = False
        self._expires_at = 0

    def _call(self, operation: str, *args: str) -> Any:
        try:
            if self._backend is None:
                self._backend = _native_backend()
            return getattr(self._backend, operation)(self.service, self.account, *args)
        except Exception:  # noqa: BLE001, S110 - never log backend exceptions or their values
            pass
        raise AuthError(_STORAGE_ERROR) from None

    def _read(self) -> str | None:
        value = self._call("get_password")
        if value is not None and not isinstance(value, str):
            raise AuthError(
                "OS OAuth cache record is invalid. Stop cache-enabled processes and clear this entry."
            )
        return value

    def check_current(self) -> None:
        if not self._loaded:
            raise AuthError("OS OAuth cache must be loaded before use.")
        current = self._read()
        observed = (
            self._observed.get_secret_value() if self._observed is not None else None
        )
        if current != observed:
            raise AuthError(_CHANGED_ERROR)

    def load(self) -> SecretStr | None:
        raw = self._read()
        self._observed = SecretStr(raw) if raw is not None else None
        self._loaded = True
        if raw is None:
            return None
        if len(raw) > 4096:
            raise AuthError(
                "OS OAuth cache record is invalid. Stop cache-enabled processes and clear this entry."
            )
        valid = False
        record: Any = None
        try:
            record = json.loads(raw)
            valid = (
                isinstance(record, dict)
                and set(record) == {"version", "refresh_token", "expires_at"}
                and type(record["version"]) is int
                and record["version"] == 1
                and type(record["expires_at"]) is int
                and isinstance(record["refresh_token"], str)
                and 0 < len(record["refresh_token"]) <= MAX_TOKEN_CHARACTERS
                and re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", record["refresh_token"])
                is not None
            )
        except ValueError, TypeError, RecursionError:
            pass
        if not valid:
            raise AuthError(
                "OS OAuth cache record is invalid. Stop cache-enabled processes and clear this entry."
            ) from None
        now = int(time.time())
        if not now < record["expires_at"] <= now + MAX_AGE_SECONDS:
            self.clear()
            return None
        self._expires_at = record["expires_at"]
        return SecretStr(record["refresh_token"])

    def save(self, token: SecretStr | None, *, new_grant: bool = False) -> None:
        self.check_current()
        if token is None:
            self.clear()
            return
        value = token.get_secret_value()
        if not 0 < len(value) <= MAX_TOKEN_CHARACTERS or not re.fullmatch(
            r"[A-Za-z0-9._~+/-]+=*", value
        ):
            raise AuthError(
                "OAuth refresh token cannot be stored within the native cache limits."
            )
        if new_grant:
            self._expires_at = int(time.time()) + MAX_AGE_SECONDS
        if self._expires_at <= time.time():
            raise AuthError(
                "OS OAuth cache expired. Clear it and authorize on a read before creating new previews."
            )
        raw = json.dumps(
            {"version": 1, "refresh_token": value, "expires_at": self._expires_at},
            separators=(",", ":"),
        )
        self._call("set_password", raw)
        self._observed = SecretStr(raw)

    def clear(self) -> None:
        if not self._loaded:
            raw = self._read()
            self._observed = SecretStr(raw) if raw is not None else None
            self._loaded = True
        self.check_current()
        if self._observed is not None:
            self._call("delete_password")
        self._observed = None
        self._expires_at = 0


def main() -> None:
    """Clear only the current configured cache identity. Never authorize or export tokens."""
    import argparse

    from servicenow_mcp.config import Settings

    parser = argparse.ArgumentParser(
        description="Clear the configured native OAuth cache entry."
    )
    parser.add_argument("action", choices=["clear"])
    parser.parse_args()
    try:
        cache = CredentialCache(Settings())
        cache.clear()
    except AuthError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:  # noqa: BLE001 - configuration errors must not echo secret inputs
        raise SystemExit(
            "Cannot clear OAuth cache. Check the required instance and OAuth settings."
        ) from None
    print(
        "Configured OS OAuth cache entry cleared. Existing processes must be closed separately."
    )


if __name__ == "__main__":
    main()
