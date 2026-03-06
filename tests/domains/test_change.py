"""Tests for Change Management domain tools."""

from typing import Any

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper to register change tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.change import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


class TestChangeList:
    """Tests for change_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_no_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should query all change requests when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "id1",
                            "number": "CHG0010001",
                            "short_description": "Test change 1",
                        },
                        {
                            "sys_id": "id2",
                            "number": "CHG0010002",
                            "short_description": "Test change 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_list"]()
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "CHG0010001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_state_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should map state names to numeric values."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["change_list"](state="new")

        request = respx.calls.last.request
        assert "state%3D-5" in str(request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_multiple_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_valid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch change request by CHG number."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "CHG0010001",
                            "short_description": "Test change",
                        }
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="CHG0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "CHG0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio()
    async def test_get_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle change request not found."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_get"](number="CHG9999999")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()


class TestChangeCreate:
    """Tests for change_create tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "CHG0010123"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_with_all_optional_params(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should include all optional fields in the created record."""
        route = respx.post(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new002",
                        "number": "CHG0010124",
                        "short_description": "Full change",
                        "type": "emergency",
                        "description": "Detailed desc",
                        "risk": "high",
                        "assignment_group": "grp001",
                        "start_date": "2026-04-01 08:00:00",
                        "end_date": "2026-04-01 12:00:00",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](
            short_description="Full change",
            description="Detailed desc",
            type="emergency",
            risk="high",
            assignment_group="grp001",
            start_date="2026-04-01 08:00:00",
            end_date="2026-04-01 12:00:00",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "CHG0010124"

        import json

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["description"] == "Detailed desc"
        assert request_body["risk"] == "high"
        assert request_body["assignment_group"] == "grp001"
        assert request_body["start_date"] == "2026-04-01 08:00:00"
        assert request_body["end_date"] == "2026-04-01 12:00:00"

    @pytest.mark.asyncio()
    async def test_create_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block creation in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["change_create"](
            short_description="Test change",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_missing_short_description(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject empty short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](short_description="")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_type(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject invalid type value."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_create"](short_description="Test", type="invalid_type")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "type" in data["error"]["message"].lower()


class TestChangeUpdate:
    """Tests for change_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should update change request by CHG number."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "CHG0010001",
                            "short_description": "Old",
                        }
                    ]
                },
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
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated"

    @pytest.mark.asyncio()
    async def test_update_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_update"](number="INC0010001", short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]["message"]

    @pytest.mark.asyncio()
    async def test_update_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block updates in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["change_update"](
            number="CHG0010001",
            short_description="Test",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle change request not found during update."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_update"](
            number="CHG9999999",
            short_description="Updated",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_with_all_optional_params(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should pass all optional fields including state mapping."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001"}]},
            )
        )
        route = respx.patch(f"{BASE_URL}/api/now/table/change_request/abc123").mock(
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
            description="New detailed desc",
            type="emergency",
            risk="high",
            assignment_group="grp001",
            state="implement",
        )
        data = decode_response(result)

        assert data["status"] == "success"

        import json

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["description"] == "New detailed desc"
        assert request_body["type"] == "emergency"
        assert request_body["risk"] == "high"
        assert request_body["assignment_group"] == "grp001"
        assert request_body["state"] == "-1"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_no_changes_provided(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should error when no update fields are provided."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "CHG0010001"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_update"](number="CHG0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "no fields" in data["error"]["message"].lower()


class TestChangeTasks:
    """Tests for change_tasks tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_tasks_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch change tasks by change request number."""
        respx.get(f"{BASE_URL}/api/now/table/change_task").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "task1",
                            "number": "CTASK0010001",
                            "short_description": "Task 1",
                        },
                        {
                            "sys_id": "task2",
                            "number": "CTASK0010002",
                            "short_description": "Task 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="CHG0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "CTASK0010001"

    @pytest.mark.asyncio()
    async def test_tasks_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_tasks_empty_result(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle no tasks found."""
        respx.get(f"{BASE_URL}/api/now/table/change_task").mock(
            return_value=Response(
                200,
                json={"result": []},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_tasks"](number="CHG0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 0


class TestChangeAddComment:
    """Tests for change_add_comment tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_comment_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_work_note_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio()
    async def test_add_comment_both_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should require at least one of comment or work_note."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="CHG0010001",
            comment="",
            work_note="",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "comment" in data["error"]["message"].lower() or "work_note" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_add_comment_blocked_in_prod(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        """Should block adding comments in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["change_add_comment"](
            number="CHG0010001",
            comment="Test comment",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_add_comment_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-CHG numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="INC0010001",
            comment="Test comment",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "CHG" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_comment_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle change request not found when adding comment."""
        respx.get(f"{BASE_URL}/api/now/table/change_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["change_add_comment"](
            number="CHG9999999",
            comment="Test comment",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()
