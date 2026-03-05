"""Integration tests for Problem Management domain tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings

pytestmark = pytest.mark.integration


class TestDomainProblem:
    """Test Problem Management domain API operations on a live instance."""

    async def test_problem_list_returns_records(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query problems without filters."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "problem",
                "",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_problem_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        problem_sys_id: str | None,
    ) -> None:
        """Fetch a single problem by sys_id."""
        if not problem_sys_id:
            pytest.skip("No problem found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record(
                "problem",
                problem_sys_id,
                fields=[
                    "sys_id",
                    "number",
                    "short_description",
                    "state",
                    "priority",
                ],
            )
        assert record["sys_id"] == problem_sys_id

    async def test_problem_get_by_number(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        problem_sys_id: str | None,
    ) -> None:
        """Fetch a problem by PRB number (simulates problem_get tool)."""
        if not problem_sys_id:
            pytest.skip("No problem found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("problem", problem_sys_id, fields=["number"])
            number = record.get("number", "")
            assert number.startswith("PRB"), f"Unexpected problem number format: {number}"

            result = await client.query_records(
                "problem",
                f"number={number}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1

    async def test_problem_list_with_state_filter(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query problems filtered by state (new)."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "problem",
                "state=1",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_problem_list_with_priority_filter(
        self, live_settings: Settings, live_auth: BasicAuthProvider
    ) -> None:
        """Query problems filtered by priority."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "problem",
                "priority=3",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)
