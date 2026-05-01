"""Tests for configuration module."""

from unittest.mock import patch

import pytest


class TestSettings:
    """Test ServiceNow MCP settings loading and validation."""

    def _make_env(self, **overrides: str) -> dict[str, str]:
        """Create a minimal valid environment dict."""
        base = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "password123",
        }
        base.update(overrides)
        return base

    def test_load_valid_config(self) -> None:
        """Settings loads correctly from valid environment variables."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.servicenow_instance_url == "https://test.service-now.com"
        assert settings.servicenow_username == "admin"
        assert settings.servicenow_password.get_secret_value() == "password123"

    def test_missing_instance_url_raises(self) -> None:
        """Missing SERVICENOW_INSTANCE_URL raises validation error."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        del env["SERVICENOW_INSTANCE_URL"]
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises((ValueError, TypeError)),
        ):
            Settings(_env_file=None)

    def test_missing_username_raises(self) -> None:
        """Missing SERVICENOW_USERNAME raises validation error."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        del env["SERVICENOW_USERNAME"]
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises((ValueError, TypeError)),
        ):
            Settings(_env_file=None)

    def test_missing_password_raises(self) -> None:
        """Missing SERVICENOW_PASSWORD raises validation error."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        del env["SERVICENOW_PASSWORD"]
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises((ValueError, TypeError)),
        ):
            Settings(_env_file=None)

    def test_default_mcp_tool_package(self) -> None:
        """MCP_TOOL_PACKAGE defaults to 'full'."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.mcp_tool_package == "full"

    def test_custom_mcp_tool_package(self) -> None:
        """MCP_TOOL_PACKAGE can be overridden."""
        from servicenow_mcp.config import Settings

        env = self._make_env(MCP_TOOL_PACKAGE="full")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.mcp_tool_package == "full"

    def test_default_env(self) -> None:
        """SERVICENOW_ENV defaults to 'dev'."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.servicenow_env == "dev"

    def test_default_max_row_limit(self) -> None:
        """MAX_ROW_LIMIT defaults to 100."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.max_row_limit == 100

    def test_custom_max_row_limit(self) -> None:
        """MAX_ROW_LIMIT can be overridden."""
        from servicenow_mcp.config import Settings

        env = self._make_env(MAX_ROW_LIMIT="50")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.max_row_limit == 50

    def test_large_table_names_default(self) -> None:
        """LARGE_TABLE_NAMES_CSV has sensible defaults."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert "syslog" in settings.large_table_names
        assert "sys_audit" in settings.large_table_names

    def test_large_table_names_from_csv(self) -> None:
        """LARGE_TABLE_NAMES_CSV parses comma-separated string."""
        from servicenow_mcp.config import Settings

        env = self._make_env(LARGE_TABLE_NAMES_CSV="table_a,table_b,table_c")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.large_table_names == frozenset({"table_a", "table_b", "table_c"})

    def test_instance_url_trailing_slash_stripped(self) -> None:
        """Trailing slash is stripped from instance URL."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_INSTANCE_URL="https://test.service-now.com/")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.servicenow_instance_url == "https://test.service-now.com"

    def test_is_production_true(self) -> None:
        """is_production returns True when env is 'prod'."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_ENV="prod")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.is_production is True

    def test_is_production_false(self) -> None:
        """is_production returns False when env is not 'prod'."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_ENV="dev")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.is_production is False

    def test_is_production_with_production_string(self) -> None:
        """is_production returns True when env is 'production'."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_ENV="production")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.is_production is True

    def test_is_production_case_insensitive(self) -> None:
        """is_production returns True for case-insensitive 'PROD'."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_ENV="PROD")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.is_production is True

    def test_invalid_url_scheme_rejected(self) -> None:
        """Instance URL without https:// scheme is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(SERVICENOW_INSTANCE_URL="http://test.service-now.com")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match="https://"),
        ):
            Settings(_env_file=None)

    def test_max_row_limit_too_low_rejected(self) -> None:
        """max_row_limit below 1 is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(MAX_ROW_LIMIT="0")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match="between 1 and 10000"),
        ):
            Settings(_env_file=None)

    def test_max_row_limit_too_high_rejected(self) -> None:
        """max_row_limit above 10000 is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(MAX_ROW_LIMIT="99999")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match="between 1 and 10000"),
        ):
            Settings(_env_file=None)

    def test_invalid_tool_package_rejected(self) -> None:
        """Unknown mcp_tool_package is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(MCP_TOOL_PACKAGE="foo")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match="Unknown group names"),
        ):
            Settings(_env_file=None)

    def test_large_table_names_is_frozenset(self) -> None:
        """large_table_names returns a frozenset."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert isinstance(settings.large_table_names, frozenset)

    def test_large_table_names_cached(self) -> None:
        """large_table_names returns the same cached object on repeated access."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        first = settings.large_table_names
        second = settings.large_table_names
        assert first is second

    def test_default_httpx_timeout(self) -> None:
        """httpx_timeout_seconds defaults to 30 seconds."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.httpx_timeout_seconds == pytest.approx(30.0)

    def test_custom_httpx_timeout(self) -> None:
        """httpx_timeout_seconds can be overridden."""
        from servicenow_mcp.config import Settings

        env = self._make_env(HTTPX_TIMEOUT_SECONDS="120")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.httpx_timeout_seconds == pytest.approx(120.0)

    def test_httpx_timeout_too_low_rejected(self) -> None:
        """httpx_timeout_seconds below 1.0 is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(HTTPX_TIMEOUT_SECONDS="0.5")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match=r"between 1\.0 and 600\.0"),
        ):
            Settings(_env_file=None)

    def test_httpx_timeout_too_high_rejected(self) -> None:
        """httpx_timeout_seconds above 600.0 is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env(HTTPX_TIMEOUT_SECONDS="601")
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match=r"between 1\.0 and 600\.0"),
        ):
            Settings(_env_file=None)

    def test_httpx_timeout_lower_bound_accepted(self) -> None:
        """httpx_timeout_seconds at 1.0 is accepted."""
        from servicenow_mcp.config import Settings

        env = self._make_env(HTTPX_TIMEOUT_SECONDS="1")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.httpx_timeout_seconds == pytest.approx(1.0)

    def test_httpx_timeout_upper_bound_accepted(self) -> None:
        """httpx_timeout_seconds at 600.0 is accepted."""
        from servicenow_mcp.config import Settings

        env = self._make_env(HTTPX_TIMEOUT_SECONDS="600")
        with patch.dict("os.environ", env, clear=True):
            settings = Settings(_env_file=None)

        assert settings.httpx_timeout_seconds == pytest.approx(600.0)

    def test_httpx_timeout_nan_rejected(self) -> None:
        """httpx_timeout_seconds set to NaN is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match=r"between 1\.0 and 600\.0"),
        ):
            Settings(_env_file=None, httpx_timeout_seconds=float("nan"))

    def test_httpx_timeout_infinity_rejected(self) -> None:
        """httpx_timeout_seconds set to +Infinity is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match=r"between 1\.0 and 600\.0"),
        ):
            Settings(_env_file=None, httpx_timeout_seconds=float("inf"))

    def test_httpx_timeout_negative_infinity_rejected(self) -> None:
        """httpx_timeout_seconds set to -Infinity is rejected."""
        from servicenow_mcp.config import Settings

        env = self._make_env()
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(ValueError, match=r"between 1\.0 and 600\.0"),
        ):
            Settings(_env_file=None, httpx_timeout_seconds=float("-inf"))
