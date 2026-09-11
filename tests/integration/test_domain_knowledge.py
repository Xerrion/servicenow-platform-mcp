"""Integration tests for Knowledge Management domain tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestDomainKnowledge:
    """Test Knowledge Management domain API operations on a live instance."""

    async def test_knowledge_search_returns_records(
        self, live_settings: Settings, live_auth: OAuthPKCEProvider
    ) -> None:
        """Search knowledge articles (simulates knowledge_search tool)."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "kb_knowledge",
                "workflow_state=published",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_knowledge_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        kb_article_sys_id: str | None,
    ) -> None:
        """Fetch a knowledge article by sys_id."""
        if not kb_article_sys_id:
            pytest.skip("No knowledge article found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "kb_knowledge",
                f"sys_id={kb_article_sys_id}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1
        assert result["records"][0]["sys_id"] == kb_article_sys_id

    async def test_knowledge_get_by_number(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        kb_article_sys_id: str | None,
    ) -> None:
        """Fetch a knowledge article by KB number (simulates knowledge_get tool)."""
        if not kb_article_sys_id:
            pytest.skip("No knowledge article found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("kb_knowledge", kb_article_sys_id, fields=["number"])
            number = record.get("number", "")
            if not number:
                pytest.skip("Knowledge article has no number field")

            result = await client.query_records(
                "kb_knowledge",
                f"number={number}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1

    async def test_knowledge_search_with_text_filter(
        self, live_settings: Settings, live_auth: OAuthPKCEProvider
    ) -> None:
        """Search knowledge articles with text LIKE query (simulates knowledge_search tool)."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "kb_knowledge",
                "short_descriptionLIKEpassword^ORtextLIKEpassword^workflow_state=published",
                display_values=True,
                limit=5,
            )
        # May return 0 results - just verify API call succeeds
        assert isinstance(result["records"], list)

    async def test_knowledge_table_accessible(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Verify kb_knowledge table is queryable without filters."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "kb_knowledge",
                "",
                display_values=True,
                limit=1,
            )
        assert isinstance(result["records"], list)
