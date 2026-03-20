"""Tests for the sentry module (Sentry error tracking)."""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import servicenow_mcp.sentry as sentry_mod
from servicenow_mcp.sentry import (
    capture_exception,
    set_sentry_context,
    set_sentry_tag,
    setup_sentry,
    shutdown_sentry,
)


@pytest.fixture(autouse=True)
def _reset_sentry_state() -> Generator[None, None, None]:
    """Reset sentry module state between tests."""
    # Root conftest._disable_sentry_capture also resets _initialized;
    # this fixture provides an additional explicit reset for sentry-specific tests.
    yield
    sentry_mod._initialized = False


def _make_settings(**overrides: Any) -> Any:
    """Build a minimal Settings for Sentry tests."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "s3cret",
        "SERVICENOW_ENV": "dev",
        "MCP_TOOL_PACKAGE": "full",
    }
    for key, value in overrides.items():
        env[key.upper()] = str(value)
    with patch.dict("os.environ", env, clear=True):
        from servicenow_mcp.config import Settings

        return Settings(_env_file=None)


# ---------------------------------------------------------------------------
# setup_sentry
# ---------------------------------------------------------------------------


class TestSetupSentry:
    """Tests for setup_sentry()."""

    def test_disabled_when_no_dsn(self) -> None:
        """When sentry_dsn is empty, setup completes but does not call init."""
        settings = _make_settings()
        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            setup_sentry(settings)
            mock_sdk.init.assert_not_called()

        assert sentry_mod._initialized is True

    def test_disabled_when_sdk_not_installed(self) -> None:
        """When sentry-sdk is not installed, setup completes as no-op."""
        settings = _make_settings(sentry_dsn="https://key@sentry.io/123")
        with patch.object(sentry_mod, "HAS_SENTRY", False):
            setup_sentry(settings)

        assert sentry_mod._initialized is True

    def test_calls_init_with_correct_args(self) -> None:
        """When DSN is set and SDK is available, sentry_sdk.init is called with correct args."""
        settings = _make_settings(sentry_dsn="https://key@sentry.io/123", sentry_environment="staging")
        mock_init = MagicMock()

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "_HAS_MCP_INTEGRATION", False),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.init = mock_init
            setup_sentry(settings)

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args[1]
        assert call_kwargs["dsn"] == "https://key@sentry.io/123"
        assert call_kwargs["environment"] == "staging"
        assert call_kwargs["send_default_pii"] is True
        assert call_kwargs["integrations"] == []
        assert call_kwargs["traces_sample_rate"] == 1.0
        assert call_kwargs["profiles_sample_rate"] is None

    def test_falls_back_to_servicenow_env(self) -> None:
        """When sentry_environment is empty, falls back to servicenow_env."""
        settings = _make_settings(sentry_dsn="https://key@sentry.io/123", servicenow_env="production")
        mock_init = MagicMock()

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.init = mock_init
            setup_sentry(settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs["environment"] == "production"

    def test_idempotency(self) -> None:
        """Calling setup_sentry twice only initializes once."""
        settings = _make_settings(sentry_dsn="https://key@sentry.io/123")
        mock_init = MagicMock()

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.init = mock_init
            setup_sentry(settings)
            setup_sentry(settings)

        assert mock_init.call_count == 1

    def test_dsn_whitespace_only_treated_as_empty(self) -> None:
        """A DSN that is only whitespace is treated as empty (disabled)."""
        settings = _make_settings(sentry_dsn="   ")

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.init = MagicMock()
            setup_sentry(settings)

        mock_sdk.init.assert_not_called()
        assert sentry_mod._initialized is True

    def test_includes_mcp_integration_when_available(self) -> None:
        """When MCPIntegration is available, it is included in integrations list."""
        settings = _make_settings(sentry_dsn="https://key@sentry.io/123")
        mock_init = MagicMock()

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "_HAS_MCP_INTEGRATION", True),
            patch.object(sentry_mod, "MCPIntegration", create=True) as mock_mcp_cls,
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.init = mock_init
            setup_sentry(settings)

        call_kwargs = mock_init.call_args[1]
        assert len(call_kwargs["integrations"]) == 1
        mock_mcp_cls.assert_called_once()


# ---------------------------------------------------------------------------
# capture_exception
# ---------------------------------------------------------------------------


class TestCaptureException:
    """Tests for capture_exception()."""

    def test_noop_when_not_initialized(self) -> None:
        """capture_exception does nothing when not initialized."""
        assert sentry_mod._initialized is False
        # Should not raise
        capture_exception(ValueError("test"))

    def test_noop_when_sdk_not_installed(self) -> None:
        """capture_exception does nothing when HAS_SENTRY is False."""
        sentry_mod._initialized = True
        with patch.object(sentry_mod, "HAS_SENTRY", False):
            capture_exception(ValueError("test"))

    def test_delegates_to_sdk_when_initialized(self) -> None:
        """capture_exception calls sentry_sdk.capture_exception when initialized."""
        sentry_mod._initialized = True
        exc = ValueError("test error")

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            capture_exception(exc)
            mock_sdk.capture_exception.assert_called_once_with(exc)

    def test_passes_none_for_current_exception(self) -> None:
        """capture_exception(None) passes None to sentry_sdk (uses sys.exc_info)."""
        sentry_mod._initialized = True

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            capture_exception(None)
            mock_sdk.capture_exception.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# set_sentry_tag
# ---------------------------------------------------------------------------


class TestSetSentryTag:
    """Tests for set_sentry_tag()."""

    def test_noop_when_not_initialized(self) -> None:
        """set_sentry_tag does nothing when not initialized."""
        set_sentry_tag("key", "value")  # Should not raise

    def test_noop_when_sdk_not_installed(self) -> None:
        """set_sentry_tag does nothing when HAS_SENTRY is False."""
        sentry_mod._initialized = True
        with patch.object(sentry_mod, "HAS_SENTRY", False):
            set_sentry_tag("key", "value")

    def test_delegates_when_initialized(self) -> None:
        """set_sentry_tag calls sentry_sdk.set_tag when initialized."""
        sentry_mod._initialized = True

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            set_sentry_tag("tool.name", "incident_list")
            mock_sdk.set_tag.assert_called_once_with("tool.name", "incident_list")


# ---------------------------------------------------------------------------
# set_sentry_context
# ---------------------------------------------------------------------------


class TestSetSentryContext:
    """Tests for set_sentry_context()."""

    def test_noop_when_not_initialized(self) -> None:
        """set_sentry_context does nothing when not initialized."""
        set_sentry_context("tool", {"name": "test"})  # Should not raise

    def test_noop_when_sdk_not_installed(self) -> None:
        """set_sentry_context does nothing when HAS_SENTRY is False."""
        sentry_mod._initialized = True
        with patch.object(sentry_mod, "HAS_SENTRY", False):
            set_sentry_context("tool", {"name": "test"})

    def test_delegates_when_initialized(self) -> None:
        """set_sentry_context calls sentry_sdk.set_context when initialized."""
        sentry_mod._initialized = True
        data = {"name": "incident_list", "correlation_id": "abc-123"}

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            set_sentry_context("tool", data)
            mock_sdk.set_context.assert_called_once_with("tool", data)


# ---------------------------------------------------------------------------
# shutdown_sentry
# ---------------------------------------------------------------------------


class TestShutdownSentry:
    """Tests for shutdown_sentry()."""

    def test_resets_initialized(self) -> None:
        """After shutdown, _initialized is False."""
        sentry_mod._initialized = True
        mock_client = MagicMock()
        mock_client.is_active.return_value = True

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.get_client.return_value = mock_client
            shutdown_sentry()

        assert sentry_mod._initialized is False

    def test_flushes_and_closes_client(self) -> None:
        """shutdown calls flush and close on the active Sentry client."""
        sentry_mod._initialized = True
        mock_client = MagicMock()
        mock_client.is_active.return_value = True

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.get_client.return_value = mock_client
            shutdown_sentry()

        mock_client.flush.assert_called_once_with(timeout=2.0)
        mock_client.close.assert_called_once_with(timeout=2.0)

    def test_skips_inactive_client(self) -> None:
        """When client.is_active() is False, flush and close are not called."""
        sentry_mod._initialized = True
        mock_client = MagicMock()
        mock_client.is_active.return_value = False

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.get_client.return_value = mock_client
            shutdown_sentry()

        mock_client.flush.assert_not_called()
        mock_client.close.assert_not_called()
        assert sentry_mod._initialized is False

    def test_multiple_shutdowns_are_safe(self) -> None:
        """Calling shutdown_sentry multiple times does not raise."""
        shutdown_sentry()
        shutdown_sentry()
        shutdown_sentry()
        assert sentry_mod._initialized is False

    def test_shutdown_without_setup_is_safe(self) -> None:
        """Shutdown before any setup does not raise."""
        assert sentry_mod._initialized is False
        shutdown_sentry()
        assert sentry_mod._initialized is False

    def test_shutdown_handles_client_error_gracefully(self) -> None:
        """If client.flush raises, shutdown still resets state."""
        sentry_mod._initialized = True
        mock_client = MagicMock()
        mock_client.is_active.return_value = True
        mock_client.flush.side_effect = RuntimeError("flush failed")

        with (
            patch.object(sentry_mod, "HAS_SENTRY", True),
            patch.object(sentry_mod, "sentry_sdk", create=True) as mock_sdk,
        ):
            mock_sdk.get_client.return_value = mock_client
            shutdown_sentry()  # Should not raise

        assert sentry_mod._initialized is False

    def test_shutdown_noop_when_sdk_not_installed(self) -> None:
        """When HAS_SENTRY is False, shutdown just resets _initialized."""
        sentry_mod._initialized = True
        with patch.object(sentry_mod, "HAS_SENTRY", False):
            shutdown_sentry()

        assert sentry_mod._initialized is False


# ---------------------------------------------------------------------------
# Integration: set_sentry_context call sites
# ---------------------------------------------------------------------------


class TestSetSentryContextIntegration:
    """Tests that set_sentry_context is called at strategic points."""

    def test_server_sets_server_context(self) -> None:
        """create_mcp_server sets server context after setup_sentry."""
        with (
            patch.dict(
                "os.environ",
                {
                    "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
                    "SERVICENOW_USERNAME": "admin",
                    "SERVICENOW_PASSWORD": "secret",
                    "SERVICENOW_ENV": "dev",
                    "MCP_TOOL_PACKAGE": "none",
                },
                clear=True,
            ),
            patch("servicenow_mcp.server.setup_sentry"),
            patch("servicenow_mcp.server.set_sentry_context") as mock_ctx,
        ):
            from servicenow_mcp.server import create_mcp_server

            create_mcp_server()
            mock_ctx.assert_called_once_with(
                "server",
                {
                    "instance_url": "test.service-now.com",
                    "environment": "dev",
                    "is_production": False,
                    "tool_package": "none",
                },
            )

    async def test_tool_handler_sets_tool_context(self) -> None:
        """tool_handler sets tool context with name, correlation_id, and args."""
        from servicenow_mcp.decorators import tool_handler

        @tool_handler
        async def my_tool(table: str, *, correlation_id: str = "") -> str:
            return '{"status": "success"}'

        with patch("servicenow_mcp.decorators.set_sentry_context") as mock_ctx:
            await my_tool(table="incident")
            mock_ctx.assert_called_once()
            call_args = mock_ctx.call_args
            assert call_args[0][0] == "tool"
            context_data = call_args[0][1]
            assert context_data["name"] == "my_tool"
            assert "correlation_id" in context_data
            assert context_data["args"] == {"table": "incident"}

    async def test_raise_for_status_sets_http_context(self) -> None:
        """_raise_for_status sets HTTP context before raising."""
        import httpx

        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import ServerError

        mock_request = httpx.Request("GET", "https://test.service-now.com/api/now/table/incident?limit=10")
        mock_response = httpx.Response(500, request=mock_request, json={"error": {"message": "Server error"}})

        settings = _make_settings()
        auth = BasicAuthProvider(settings)
        client = ServiceNowClient(settings, auth)

        with patch("servicenow_mcp.client.set_sentry_context") as mock_ctx:
            with pytest.raises(ServerError):
                client._raise_for_status(mock_response)
            mock_ctx.assert_called_once_with(
                "http",
                {
                    "status_code": 500,
                    "method": "GET",
                    "url": "https://test.service-now.com/api/now/table/incident",  # query stripped
                },
            )
