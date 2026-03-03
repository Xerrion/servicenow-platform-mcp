"""Tests for Change Management domain tools."""

import json
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings

BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict:
    """Helper to register change tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.domains.change import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestChangeList:
    """Tests for change_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_no_filters(self, settings, auth_provider):
        """Should query all change requests when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "id1", "number": "CHG0010001", "short_description": "Test change 1"},
                        {"sys_id": "id2", "number": "CHG0010002", "short_description": "Test change 2"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_list"]()
        data = json.loads(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "CHG0010001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_state_filter(self, settings, auth_provider):
        """Should map state names to numeric values."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["change_list"](state="new")

        request = respx.calls.last.request
        assert "state%3D-5" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_multiple_filters(self, settings, auth_provider):
        """Should combine multiple filters correctly."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["change_list"](state="scheduled", type="emergency", limit=50)

        request = respx.calls.last.request
        assert "state%3D-2" in str(request.url)
        assert "type%3Demergency" in str(request.url)
        assert "sysparm_limit=50" in str(request.url)


class TestChangeGet:
    """Tests for change_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_valid_number(self, settings, auth_provider):
        """Should fetch change request by CHG number."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001", "short_description": "Test change"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="CHG0010001")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "CHG0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_invalid_prefix(self, settings, auth_provider):
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="INC0010001")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings, auth_provider):
        """Should handle change request not found."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="CHG9999999")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


class TestChangeCreate:
    """Tests for change_create tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_valid(self, settings, auth_provider):
        """Should create change request with required short_description."""
        respx.post(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "number": "CHG0010123",
                        "short_description": "New change",
                        "type": "normal",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](
            short_description="New change",
            type="normal",
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "CHG0010123"

    @pytest.mark.asyncio
    async def test_create_missing_short_description(self, settings, auth_provider):
        """Should reject empty short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](short_description="")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_create_invalid_type(self, settings, auth_provider):
        """Should reject invalid type value."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](short_description="Test", type="invalid_type")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "type" in data["error"].lower()


class TestChangeUpdate:
    """Tests for change_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_valid(self, settings, auth_provider):
        """Should update change request by CHG number."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001", "short_description": "Old"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/change_request/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "CHG0010001",
                        "short_description": "Updated",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_update"](
            number="CHG0010001",
            short_description="Updated",
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated"

    @pytest.mark.asyncio
    async def test_update_invalid_number(self, settings, auth_provider):
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_update"](number="INC0010001", short_description="Test")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]

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
            result = await tools["change_update"](
                number="CHG0010001",
                short_description="Test",
            )
            data = json.loads(result)

            assert data["status"] == "error"
            assert "production" in data["error"].lower()


class TestChangeTasks:
    """Tests for change_tasks tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_tasks_valid(self, settings, auth_provider):
        """Should fetch change tasks by change request number."""
        respx.get(f"{BASE_URL}/api/now/table/change_task").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "task1", "number": "CTASK0010001", "short_description": "Task 1"},
                        {"sys_id": "task2", "number": "CTASK0010002", "short_description": "Task 2"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="CHG0010001")
        data = json.loads(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "CTASK0010001"

    @pytest.mark.asyncio
    async def test_tasks_invalid_prefix(self, settings, auth_provider):
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="INC0010001")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_tasks_empty_result(self, settings, auth_provider):
        """Should handle no tasks found."""
        respx.get(f"{BASE_URL}/api/now/table/change_task").mock(
            return_value=Response(
                200,
                json={"result": []},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="CHG0010001")
        data = json.loads(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 0


class TestChangeAddComment:
    """Tests for change_add_comment tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_comment_valid(self, settings, auth_provider):
        """Should add comment to change request."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/change_request/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "CHG0010001",
                        "comments": "User comment added",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="CHG0010001",
            comment="User comment added",
        )
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_work_note_valid(self, settings, auth_provider):
        """Should add work_note to change request."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/change_request/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "CHG0010001",
                        "work_notes": "Internal work note",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="CHG0010001",
            work_note="Internal work note",
        )
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_add_comment_both_empty(self, settings, auth_provider):
        """Should require at least one of comment or work_note."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="CHG0010001",
            comment="",
            work_note="",
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "comment" in data["error"].lower() or "work_note" in data["error"].lower()
