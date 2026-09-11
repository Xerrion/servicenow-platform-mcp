"""Integration test: basic connectivity to the live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestConnectivity:
    """Verify we can reach the ServiceNow instance."""

    async def test_can_connect_and_fetch_metadata(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Basic connectivity: fetch sys_dictionary for the incident table."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            metadata = await client.get_metadata("incident")

        assert len(metadata) > 0, "No metadata returned — connectivity issue"
