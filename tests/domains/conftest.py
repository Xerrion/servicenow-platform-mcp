"""Shared fixtures for domain tool tests."""

from unittest.mock import patch

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Test settings fixture (no env file loading)."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "password",
        "MCP_TOOL_PACKAGE": "full",
        "SERVICENOW_ENV": "dev",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Test auth provider fixture."""
    return BasicAuthProvider(settings)
