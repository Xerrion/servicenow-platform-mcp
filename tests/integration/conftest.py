"""Integration test fixtures for live ServiceNow instance testing.

These fixtures load real credentials from .env.local and discover
test data (incidents, update sets, business rules) on the live instance.

All integration tests are marked with @pytest.mark.integration and
skipped by default. Run with: uv run pytest -m integration
"""

import pytest
import pytest_asyncio

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


# Apply the integration marker to every test in this directory
pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """Load settings from .env.local with real credentials."""
    return Settings()


@pytest.fixture(scope="session")
def live_auth(live_settings: Settings) -> OAuthPKCEProvider:
    """Create auth provider from live settings."""
    return OAuthPKCEProvider(live_settings)


@pytest_asyncio.fixture(scope="session")
async def incident_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real active incident sys_id for tests that need one."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "incident",
            "active=true",
            fields=["sys_id", "number"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def update_set_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real update set sys_id for change intelligence tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "sys_update_set",
            "stateINin progress,complete",
            fields=["sys_id", "name"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def business_rule_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real business rule sys_id for metadata tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "sys_script",
            "",
            fields=["sys_id", "name"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def change_request_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real change request sys_id for domain tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "change_request",
            "",
            fields=["sys_id", "number"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def cmdb_ci_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real CMDB configuration item sys_id for domain tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "cmdb_ci",
            "",
            fields=["sys_id", "name"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def problem_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real problem sys_id for domain tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "problem",
            "",
            fields=["sys_id", "number"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def sc_request_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real service catalog request sys_id for domain tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "sc_request",
            "",
            fields=["sys_id", "number"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")


@pytest_asyncio.fixture(scope="session")
async def kb_article_sys_id(live_settings: Settings, live_auth: OAuthPKCEProvider) -> str | None:
    """Discover a real knowledge article sys_id for domain tests."""
    async with ServiceNowClient(live_settings, live_auth) as client:
        result = await client.query_records(
            "kb_knowledge",
            "",
            fields=["sys_id", "number"],
            limit=1,
        )
    records = result["records"]
    if not records:
        return None
    return records[0].get("sys_id", "")
