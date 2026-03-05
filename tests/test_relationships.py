"""Tests for relationship tools (rel_references_to, rel_references_from)."""

import asyncio
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

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
        result = toon_decode(raw)

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
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["incoming_references"] == []

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error without making HTTP call."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table=denied, sys_id="abc")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_paginates_all_dictionary_entries(self, settings, auth_provider):
        """Paginated sys_dictionary fetches collect fields across all pages."""
        page_size = 1000

        # Build two pages: first page has exactly page_size entries, second has 2
        page1_fields = [
            {
                "name": f"table_p1_{i}",
                "element": "ref_field",
                "reference": "sys_user",
                "column_label": f"Ref {i}",
            }
            for i in range(page_size)
        ]
        page2_fields = [
            {
                "name": "task",
                "element": "assigned_to",
                "reference": "sys_user",
                "column_label": "Assigned to",
            },
            {
                "name": "task",
                "element": "opened_by",
                "reference": "sys_user",
                "column_label": "Opened by",
            },
        ]

        dict_route = respx.get(f"{BASE_URL}/api/now/table/sys_dictionary")
        dict_route.side_effect = [
            # First page: full page_size records -> triggers next page fetch
            httpx.Response(
                200,
                json={"result": page1_fields},
                headers={"X-Total-Count": str(page_size + 2)},
            ),
            # Second page: fewer than page_size -> pagination stops
            httpx.Response(
                200,
                json={"result": page2_fields},
                headers={"X-Total-Count": str(page_size + 2)},
            ),
        ]

        # Mock all referencing tables to return empty results (page 1 tables)
        for i in range(page_size):
            respx.get(f"{BASE_URL}/api/now/table/table_p1_{i}").mock(
                return_value=httpx.Response(
                    200,
                    json={"result": []},
                    headers={"X-Total-Count": "0"},
                )
            )

        # Mock the page 2 table (task) - returns a matching record for assigned_to
        respx.get(f"{BASE_URL}/api/now/table/task").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "task1", "assigned_to": "user123"}]},
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table="sys_user", sys_id="user123")
        result = toon_decode(raw)

        assert result["status"] == "success"
        refs = result["data"]["incoming_references"]
        # The task table entries from page 2 should be present in results
        task_refs = [r for r in refs if r["table"] == "task"]
        assert len(task_refs) >= 1, "Fields from page 2 should be processed"
        task_fields = {r["field"] for r in task_refs}
        assert "assigned_to" in task_fields or "opened_by" in task_fields

        # Verify that two sys_dictionary pages were fetched
        assert dict_route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_denied_and_internal_tables(self, settings, auth_provider):
        """Denied tables and system-internal var__m_ entries are skipped."""
        denied = next(iter(DENIED_TABLES))
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        # Denied table entry - should be skipped
                        {
                            "name": denied,
                            "element": "user",
                            "reference": "sys_user",
                            "column_label": "User",
                        },
                        # var__m_ internal entry - should be skipped
                        {
                            "name": "var__m_some_table",
                            "element": "ref",
                            "reference": "sys_user",
                            "column_label": "Ref",
                        },
                        # sys_variable_value entry - should be skipped
                        {
                            "name": "sys_variable_value",
                            "element": "ref",
                            "reference": "sys_user",
                            "column_label": "Ref",
                        },
                        # Valid entry - should be processed
                        {
                            "name": "incident",
                            "element": "caller_id",
                            "reference": "sys_user",
                            "column_label": "Caller",
                        },
                    ]
                },
                headers={"X-Total-Count": "4"},
            )
        )

        # Only incident should be queried (the rest are filtered out)
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "inc1", "caller_id": "user1"}]},
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_to"](table="sys_user", sys_id="user1")
        result = toon_decode(raw)

        assert result["status"] == "success"
        refs = result["data"]["incoming_references"]
        # Only the incident entry should survive filtering
        assert len(refs) == 1
        assert refs[0]["table"] == "incident"
        assert refs[0]["field"] == "caller_id"


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
        # Mock: hierarchy lookup -- incident has no parent
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"super_class": ""}]},
                headers={"X-Total-Count": "1"},
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["source"]["table"] == "incident"
        outgoing = result["data"]["outgoing_references"]
        assert len(outgoing) == 2
        fields = [o["field"] for o in outgoing]
        assert "caller_id" in fields
        assert "assignment_group" in fields

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_inherited_reference_fields(self, settings, auth_provider):
        """Inherited reference fields from parent tables are included."""
        # Mock: get the incident record -- has fields from both incident and task
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "caller_id": "user1",
                        "assigned_to": "user2",
                        "opened_by": "user3",
                        "state": "1",
                    }
                },
            )
        )

        # Mock: hierarchy -- incident -> task (then task has no parent)
        hierarchy_route = respx.get(f"{BASE_URL}/api/now/table/sys_db_object")
        hierarchy_route.side_effect = [
            # First call: lookup incident -> returns super_class pointing to task
            httpx.Response(
                200,
                json={"result": [{"super_class": {"value": "task_sys_id", "link": ""}}]},
                headers={"X-Total-Count": "1"},
            ),
            # Third call: lookup task -> no super_class
            httpx.Response(
                200,
                json={"result": [{"super_class": ""}]},
                headers={"X-Total-Count": "1"},
            ),
        ]

        # Mock: resolve task_sys_id -> "task"
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object/task_sys_id").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"name": "task"}},
            )
        )

        # Mock: sys_dictionary returns fields from both incident and task
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        # incident's own field
                        {
                            "element": "caller_id",
                            "reference": "sys_user",
                            "column_label": "Caller",
                        },
                        # inherited from task
                        {
                            "element": "assigned_to",
                            "reference": "sys_user",
                            "column_label": "Assigned to",
                        },
                        {
                            "element": "opened_by",
                            "reference": "sys_user",
                            "column_label": "Opened by",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table="incident", sys_id="inc001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        outgoing = result["data"]["outgoing_references"]
        fields = [o["field"] for o in outgoing]
        # All three reference fields should be found (including inherited ones)
        assert "caller_id" in fields
        assert "assigned_to" in fields
        assert "opened_by" in fields
        assert len(outgoing) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_hierarchy_stops_at_root(self, settings, auth_provider):
        """Hierarchy walk terminates when there is no super_class."""
        # Mock: get the record
        respx.get(f"{BASE_URL}/api/now/table/task/t1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "t1",
                        "assigned_to": "user1",
                    }
                },
            )
        )
        # Mock: hierarchy -- task has no parent (empty super_class)
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"super_class": ""}]},
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock: sys_dictionary returns one field
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "assigned_to",
                            "reference": "sys_user",
                            "column_label": "Assigned to",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table="task", sys_id="t1")
        result = toon_decode(raw)

        assert result["status"] == "success"
        outgoing = result["data"]["outgoing_references"]
        assert len(outgoing) == 1
        assert outgoing[0]["field"] == "assigned_to"
        # Only one sys_db_object query should have been made (for "task" itself)
        # and it stopped immediately since super_class was empty

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
        # Mock: hierarchy -- cmdb_ci has no parent
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"super_class": ""}]},
                headers={"X-Total-Count": "1"},
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["outgoing_references"] == []

    @pytest.mark.asyncio
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Denied table returns error."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["rel_references_from"](table=denied, sys_id="abc")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


class TestRelReferencesToBoundedConcurrency:
    """Tests that rel_references_to limits concurrency via asyncio.Semaphore."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_rel_references_to_bounded_concurrency(self, settings, auth_provider):
        """Verifies that rel_references_to uses a Semaphore to bound concurrent lookups."""
        # Mock sys_dictionary to return many reference fields
        ref_fields = [
            {
                "name": f"table_{i}",
                "element": "ref_field",
                "column_label": f"Ref {i}",
            }
            for i in range(15)
        ]
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

        with patch(
            "servicenow_mcp.tools.relationships.asyncio.Semaphore",
            TrackingSemaphore,
        ):
            tools = _register_and_get_tools(settings, auth_provider)
            raw = await tools["rel_references_to"](table="incident", sys_id="abc123")
            result = toon_decode(raw)

        assert result["status"] == "success"
        # Verify the semaphore was actually created and used
        assert semaphore_instance is not None
        assert semaphore_instance.enter_count == 15
