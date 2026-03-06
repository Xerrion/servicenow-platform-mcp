"""Shared test fixtures and helpers."""

from unittest.mock import patch

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Create test settings with valid defaults."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "s3cret",
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
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "s3cret",
        "SERVICENOW_ENV": "prod",
        "MCP_TOOL_PACKAGE": "full",
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def prod_auth_provider(prod_settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from production test settings."""
    return BasicAuthProvider(prod_settings)
