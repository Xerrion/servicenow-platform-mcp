"""Tests for relationship tools (rel_references_to, rel_references_from)."""

import asyncio
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.policy import DENIED_TABLES

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register relationship tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.relationships import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestRelReferencesTo:
    """Tests for the rel_references_to tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_incoming_references(self, settings, auth_provider):
        """Finds records in other tables that reference the target record."""
        # Mock: query sys_dictionary for reference fields pointing to 'incident'
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "name": "task_sla",
                            "element": "task",
                            "column_label": "Task",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock: query the referencing table for records pointing to our sys_id
        respx.get(f"{BASE_URL}/api/now/table/task_sla").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "sla1", "task": "abc123"},
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["target"]["table"] == "incident"
        assert result["data"]["target"]["sys_id"] == "abc123"
        assert len(result["data"]["incoming_references"]) == 1
        assert result["data"]["incoming_references"][0]["table"] == "task_sla"

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_no_references(self, settings, auth_provider):
        """Returns empty incoming_references when no references exist."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["incoming_references"] == []

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error without making HTTP call."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table=denied, sys_id="abc")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestRelReferencesFrom:
    """Tests for the rel_references_from tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_outgoing_references(self, settings, auth_provider):
        """Finds what a record references via its reference fields."""
        # Mock: get the record itself
        respx.get(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "caller_id": "user1",
                        "assignment_group": "group1",
                        "state": "1",
                    }
                },
            )
        )
        # Mock: query sys_dictionary for reference fields on 'incident'
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "caller_id",
                            "reference": "sys_user",
                            "column_label": "Caller",
                        },
                        {
                            "element": "assignment_group",
                            "reference": "sys_user_group",
                            "column_label": "Assignment group",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table="incident", sys_id="abc123")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["source"]["table"] == "incident"
        outgoing = result["data"]["outgoing_references"]
        assert len(outgoing) == 2
        fields = [o["field"] for o in outgoing]
        assert "caller_id" in fields
        assert "assignment_group" in fields

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_no_reference_fields(self, settings, auth_provider):
        """Returns empty outgoing_references when record has no reference fields."""
        respx.get(f"{BASE_URL}/api/now/table/cmdb_ci/ci1").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "ci1", "name": "Server01"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table="cmdb_ci", sys_id="ci1")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["outgoing_references"] == []

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table=denied, sys_id="abc")
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestRelReferencesToBoundedConcurrency:
    """Tests that rel_references_to limits concurrency via asyncio.Semaphore."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_rel_references_to_bounded_concurrency(self, settings, auth_provider):
        """Verifies that rel_references_to uses a Semaphore to bound concurrent lookups."""
        # Mock sys_dictionary to return many reference fields
        ref_fields = [{"name": f"table_{i}", "element": "ref_field", "column_label": f"Ref {i}"} for i in range(15)]
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={"result": ref_fields},
                headers={"X-Total-Count": str(len(ref_fields))},
            )
        )
        # Mock each referencing table to return empty results
        for i in range(15):
            respx.get(f"{BASE_URL}/api/now/table/table_{i}").mock(
                return_value=httpx.Response(
                    200,
                    json={"result": []},
                    headers={"X-Total-Count": "0"},
                )
            )

        semaphore_instance = None

        class TrackingSemaphore(asyncio.Semaphore):
            """Semaphore subclass that tracks __aenter__ calls."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                nonlocal semaphore_instance
                semaphore_instance = self
                self.enter_count = 0

            async def __aenter__(self) -> None:
                self.enter_count += 1
                await super().__aenter__()

        with patch("servicenow_mcp.tools.relationships.asyncio.Semaphore", TrackingSemaphore):
            tools = _register_and_get_tools(settings, auth_provider)
            raw = await tools["rel_references_to"](table="incident", sys_id="abc123")
            result = json.loads(raw)

        assert result["status"] == "success"
        # Verify the semaphore was actually created and used
        assert semaphore_instance is not None
        assert semaphore_instance.enter_count == 15
