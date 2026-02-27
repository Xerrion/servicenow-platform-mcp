"""Tests for authentication module."""

import base64
from unittest.mock import patch

import pytest


class TestBasicAuthProvider:
    """Test Basic authentication provider."""

    def _make_settings(self, **overrides):
        from servicenow_mcp.config import Settings

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
        }
        env.update(overrides)
        with patch.dict("os.environ", env, clear=True):
            return Settings(_env_file=None)

    @pytest.mark.asyncio
    async def test_get_headers_returns_authorization(self):
        """get_headers includes an Authorization header."""
        from servicenow_mcp.auth import BasicAuthProvider

        settings = self._make_settings()
        provider = BasicAuthProvider(settings)
        headers = await provider.get_headers()

        assert "Authorization" in headers

    @pytest.mark.asyncio
    async def test_get_headers_basic_prefix(self):
        """Authorization header starts with 'Basic '."""
        from servicenow_mcp.auth import BasicAuthProvider

        settings = self._make_settings()
        provider = BasicAuthProvider(settings)
        headers = await provider.get_headers()

        assert headers["Authorization"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_get_headers_correct_encoding(self):
        """Authorization header contains correctly base64-encoded credentials."""
        from servicenow_mcp.auth import BasicAuthProvider

        settings = self._make_settings()
        provider = BasicAuthProvider(settings)
        headers = await provider.get_headers()

        expected = base64.b64encode(b"admin:s3cret").decode("ascii")
        assert headers["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_get_headers_with_different_credentials(self):
        """Encoding works for different username/password combinations."""
        from servicenow_mcp.auth import BasicAuthProvider

        settings = self._make_settings(
            SERVICENOW_USERNAME="user@company.com",
            SERVICENOW_PASSWORD="p@ss:w0rd!",
        )
        provider = BasicAuthProvider(settings)
        headers = await provider.get_headers()

        expected = base64.b64encode(b"user@company.com:p@ss:w0rd!").decode("ascii")
        assert headers["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_get_headers_includes_content_type(self):
        """Headers include JSON content type."""
        from servicenow_mcp.auth import BasicAuthProvider

        settings = self._make_settings()
        provider = BasicAuthProvider(settings)
        headers = await provider.get_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"


class TestCreateAuth:
    """Test auth factory function."""

    def _make_settings(self):
        from servicenow_mcp.config import Settings

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
        }
        with patch.dict("os.environ", env, clear=True):
            return Settings(_env_file=None)

    def test_create_auth_returns_basic_provider(self):
        """create_auth returns a BasicAuthProvider."""
        from servicenow_mcp.auth import BasicAuthProvider, create_auth

        settings = self._make_settings()
        provider = create_auth(settings)

        assert isinstance(provider, BasicAuthProvider)
