"""Tests for Incident Management domain tools."""

from typing import Any

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper to register incident tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.incident import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


def _register_and_get_tools_no_choices(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper to register incident tools without a ChoiceRegistry."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.domains.incident import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=None)
    return get_tool_functions(mcp)


class TestIncidentList:
    """Tests for incident_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_no_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should query all incidents when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "id1",
                            "number": "INC0010001",
                            "short_description": "Test 1",
                        },
                        {
                            "sys_id": "id2",
                            "number": "INC0010002",
                            "short_description": "Test 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_list"]()
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "INC0010001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_state_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should map state names to numeric values."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["incident_list"](state="open")

        request = respx.calls.last.request
        assert "state%3D1" in str(request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_multiple_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_valid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch incident by INC number."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "INC0010001",
                            "short_description": "Test incident",
                        }
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "INC0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio()
    async def test_get_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-INC numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="CHG0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle incident not found."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_get"](number="INC9999999")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()


class TestIncidentCreate:
    """Tests for incident_create tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "INC0010123"

    @pytest.mark.asyncio()
    async def test_create_missing_short_description(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject empty short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_create"](short_description="")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_urgency(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject urgency outside 1-4 range."""
        tools = _register_and_get_tools(settings, auth_provider)

        # Test urgency=0
        result = await tools["incident_create"](short_description="Test", urgency=0)
        data = decode_response(result)
        assert data["status"] == "error"
        assert "urgency" in data["error"]["message"].lower()

        # Test urgency=5
        result = await tools["incident_create"](short_description="Test", urgency=5)
        data = decode_response(result)
        assert data["status"] == "error"
        assert "urgency" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_impact(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject impact outside 1-4 range."""
        tools = _register_and_get_tools(settings, auth_provider)

        result = await tools["incident_create"](short_description="Test", impact=0)
        data = decode_response(result)
        assert data["status"] == "error"
        assert "impact" in data["error"]["message"].lower()

        result = await tools["incident_create"](short_description="Test", impact=5)
        data = decode_response(result)
        assert data["status"] == "error"
        assert "impact" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block creation in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["incident_create"](short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_with_all_optional_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should include all optional fields in the create payload."""
        route = respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new002",
                        "number": "INC0010200",
                        "short_description": "Full incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_create"](
            short_description="Full incident",
            description="Detailed description text",
            caller_id="caller_sys_id",
            assignment_group="group_sys_id",
            assigned_to="user_sys_id",
            category="software",
            subcategory="os",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        # Verify the request body contained all optional fields
        import json

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["description"] == "Detailed description text"
        assert request_body["caller_id"] == "caller_sys_id"
        assert request_body["assignment_group"] == "group_sys_id"
        assert request_body["assigned_to"] == "user_sys_id"
        assert request_body["category"] == "software"
        assert request_body["subcategory"] == "os"


class TestIncidentUpdate:
    """Tests for incident_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should update incident by INC number."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "INC0010001",
                            "short_description": "Old",
                        }
                    ]
                },
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
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated"

    @pytest.mark.asyncio()
    async def test_update_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-INC numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_update"](number="CHG0010001", short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]["message"]

    @pytest.mark.asyncio()
    async def test_update_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block updates in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["incident_update"](
            number="INC0010001",
            short_description="Test",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle incident not found during update."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_update"](number="INC9999999", short_description="Updated")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_no_changes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject update when no fields are provided."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_update"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "no fields" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_with_all_optional_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should include all optional fields in the update payload."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "INC0010001"}]},
            )
        )
        route = respx.patch(f"{BASE_URL}/api/now/table/incident/abc123").mock(
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
            urgency=2,
            impact=1,
            priority=1,
            state="on_hold",
            description="Detailed update",
            assignment_group="group_sys_id",
            assigned_to="user_sys_id",
            category="hardware",
            subcategory="monitor",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        import json

        request_body = json.loads(route.calls.last.request.content)
        assert request_body["urgency"] == "2"
        assert request_body["impact"] == "1"
        assert request_body["priority"] == "1"
        assert request_body["state"] == "3"  # on_hold maps to 3
        assert request_body["description"] == "Detailed update"
        assert request_body["assignment_group"] == "group_sys_id"
        assert request_body["assigned_to"] == "user_sys_id"
        assert request_body["category"] == "hardware"
        assert request_body["subcategory"] == "monitor"


class TestIncidentResolve:
    """Tests for incident_resolve tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_resolve_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should resolve incident with close_code and close_notes."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "INC0010001",
                            "state": "2",
                        }
                    ]
                },
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
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["state"] == "6"

    @pytest.mark.asyncio()
    async def test_resolve_missing_close_code(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should require close_code."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="",
            close_notes="Fixed",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "close_code" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_resolve_missing_close_notes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should require close_notes."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="Solved (Permanently)",
            close_notes="",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "close_notes" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_resolve_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block resolving in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="Solved (Permanently)",
            close_notes="Fixed",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_resolve_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-INC numbers for resolve."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="CHG0010001",
            close_code="Solved (Permanently)",
            close_notes="Fixed",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_resolve_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle incident not found during resolve."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC9999999",
            close_code="Solved (Permanently)",
            close_notes="Fixed the issue",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_resolve_without_choices_uses_fallback(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Without ChoiceRegistry, resolved state falls back to '6'."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "INC0010001",
                            "state": "2",
                        }
                    ]
                },
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

        tools = _register_and_get_tools_no_choices(settings, auth_provider)
        result = await tools["incident_resolve"](
            number="INC0010001",
            close_code="Solved (Permanently)",
            close_notes="Fixed the issue",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["state"] == "6"


class TestIncidentAddComment:
    """Tests for incident_add_comment tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_comment_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_work_note_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        data = decode_response(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio()
    async def test_add_comment_both_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should require at least one of comment or work_note."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="INC0010001",
            comment="",
            work_note="",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "comment" in data["error"]["message"].lower() or "work_note" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_add_comment_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block adding comments in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["incident_add_comment"](
            number="INC0010001",
            comment="Test comment",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_add_comment_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-INC numbers for add_comment."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="CHG0010001",
            comment="Test",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "INC" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_comment_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle incident not found when adding comment."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["incident_add_comment"](
            number="INC9999999",
            comment="Test comment",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()
