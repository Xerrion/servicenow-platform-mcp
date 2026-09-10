"""Shared test fixtures and helpers."""

import time
from collections.abc import Generator
from unittest.mock import patch

import pytest

from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider
from servicenow_mcp.config import Settings


@pytest.fixture(autouse=True)
def _disable_sentry_capture() -> Generator[None, None, None]:
    """Prevent Sentry from capturing exceptions during tests.

    Resets the module-level ``_initialized`` flag so that
    ``capture_exception()`` short-circuits before reaching the real SDK.
    """
    import servicenow_mcp.sentry as _sentry_mod

    _sentry_mod._initialized = False
    yield
    _sentry_mod._initialized = False


@pytest.fixture()
def settings() -> Settings:
    """Create test settings with valid defaults."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        "SERVICENOW_OAUTH_SCOPE": "useraccount",
        "SERVICENOW_ENV": "dev",
        "MCP_TOOL_PACKAGE": "full",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def prod_settings() -> Settings:
    """Create test settings for production environment."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://prod.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        "SERVICENOW_OAUTH_SCOPE": "useraccount",
        "SERVICENOW_ENV": "prod",
        "MCP_TOOL_PACKAGE": "full",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def prod_auth_provider(prod_settings: Settings) -> OAuthPKCEProvider:
    """Create a OAuthPKCEProvider from production test settings."""
    return OAuthPKCEProvider(prod_settings)


@pytest.fixture(autouse=True)
def _isolate_unit_settings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not load developer credentials or telemetry settings in unit tests."""
    if "integration" not in request.node.path.parts:
        monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _stub_user_authorization(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Keep unit tests offline; authentication and integration tests use the real provider."""
    if request.node.path.name == "test_auth.py" or "integration" in request.node.path.parts:
        yield
        return
    with patch.object(
        OAuthPKCEProvider, "_authorize", return_value=AccessToken("test-only-token", time.monotonic() + 3600)
    ):
        yield
