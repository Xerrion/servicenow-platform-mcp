"""Tests for Incident Management domain tools."""

import json
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings

BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict:
    """Helper to register incident tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.domains.incident import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestIncidentList:
    """Tests for incident_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_no_filters(self, settings, auth_provider):
        """Should query all incidents when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "id1", "number": "INC0010001", "short_description": "Test 1"},
                        {"sys_id": "id2", "number": "INC0010002", "short_description": "Test 2"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_list"]()
        data = json.loads(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "INC0010001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_state_filter(self, settings, auth_provider):
        """Should map state names to numeric values."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["incident_list"](state="open")

        request = respx.calls.last.request
        assert "state%3D1" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_multiple_filters(self, settings, auth_provider):
        """Should combine multiple filters correctly."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["incident_list"](state="in_progress", priority="1", limit=50)

        request = respx.calls.last.request
        assert "state%3D2" in str(request.url)
        assert "priority%3D1" in str(request.url)
        assert "sysparm_limit=50" in str(request.url)


class TestIncidentGet:
    """Tests for incident_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_valid_number(self, settings, auth_provider):
        """Should fetch incident by INC number."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001", "short_description": "Test incident"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="INC0010001")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "INC0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_invalid_prefix(self, settings, auth_provider):
        """Should reject non-INC numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="CHG0010001")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings, auth_provider):
        """Should handle incident not found."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="INC9999999")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


class TestIncidentCreate:
    """Tests for incident_create tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_valid(self, settings, auth_provider):
        """Should create incident with required short_description."""
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "number": "INC0010123",
                        "short_description": "New incident",
                        "state": "1",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_create"](
            short_description="New incident",
            urgency=2,
            impact=3,
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "INC0010123"

    @pytest.mark.asyncio
    async def test_create_missing_short_description(self, settings, auth_provider):
        """Should reject empty short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_create"](short_description="")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_create_invalid_urgency(self, settings, auth_provider):
        """Should reject urgency outside 1-4 range."""
        tools = _register_and_get_tools(settings, auth_provider)

        # Test urgency=0
        result = await tools["incident_create"](short_description="Test", urgency=0)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "urgency" in data["error"].lower()

        # Test urgency=5
        result = await tools["incident_create"](short_description="Test", urgency=5)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "urgency" in data["error"].lower()


class TestIncidentUpdate:
    """Tests for incident_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_valid(self, settings, auth_provider):
        """Should update incident by INC number."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001", "short_description": "Old"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "INC0010001",
                        "short_description": "Updated",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_update"](
            number="INC0010001",
            short_description="Updated",
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated"

    @pytest.mark.asyncio
    async def test_update_invalid_number(self, settings, auth_provider):
        """Should reject non-INC numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_update"](number="CHG0010001", short_description="Test")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]

    @pytest.mark.asyncio
    async def test_update_blocked_in_prod(self):
        """Should block updates in production."""
        prod_env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "password",
            "MCP_TOOL_PACKAGE": "full",
            "SERVICENOW_ENV": "prod",
        }
        with patch.dict("os.environ", prod_env, clear=True):
            prod_settings = Settings(_env_file=None)
            prod_auth = BasicAuthProvider(prod_settings)

            tools = _register_and_get_tools(prod_settings, prod_auth)
            result = await tools["incident_update"](
                number="INC0010001",
                short_description="Test",
            )
            data = json.loads(result)

            assert data["status"] == "error"
            assert "production" in data["error"].lower()


class TestIncidentResolve:
    """Tests for incident_resolve tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_valid(self, settings, auth_provider):
        """Should resolve incident with close_code and close_notes."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001", "state": "2"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "INC0010001",
                        "state": "6",
                        "close_code": "Solved (Permanently)",
                        "close_notes": "Fixed the issue",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="Solved (Permanently)",
            close_notes="Fixed the issue",
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["state"] == "6"

    @pytest.mark.asyncio
    async def test_resolve_missing_close_code(self, settings, auth_provider):
        """Should require close_code."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="",
            close_notes="Fixed",
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "close_code" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_resolve_missing_close_notes(self, settings, auth_provider):
        """Should require close_notes."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="Solved (Permanently)",
            close_notes="",
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "close_notes" in data["error"].lower()


class TestIncidentAddComment:
    """Tests for incident_add_comment tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_comment_valid(self, settings, auth_provider):
        """Should add comment to incident."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "INC0010001",
                        "comments": "User comment added",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="INC0010001",
            comment="User comment added",
        )
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_work_note_valid(self, settings, auth_provider):
        """Should add work_note to incident."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "INC0010001",
                        "work_notes": "Internal work note",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="INC0010001",
            work_note="Internal work note",
        )
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_add_comment_both_empty(self, settings, auth_provider):
        """Should require at least one of comment or work_note."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="INC0010001",
            comment="",
            work_note="",
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "comment" in data["error"].lower() or "work_note" in data["error"].lower()
