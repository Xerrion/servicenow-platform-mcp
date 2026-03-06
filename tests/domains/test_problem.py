"""Tests for Problem Management domain tools."""

from typing import Any

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper to register problem tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.problem import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


class TestProblemList:
    """Tests for problem_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_no_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should query all problems when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "id1",
                            "number": "PRB0010001",
                            "short_description": "Test 1",
                        },
                        {
                            "sys_id": "id2",
                            "number": "PRB0010002",
                            "short_description": "Test 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_list"]()
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "PRB0010001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_state_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should map state names to numeric values using PROBLEM_STATE_MAP."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["problem_list"](state="new")

        request = respx.calls.last.request
        # "new" maps to "1" via ChoiceRegistry defaults
        assert "state%3D1" in str(request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_multiple_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should combine multiple filters correctly."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["problem_list"](
            state="in_progress",
            priority="1",
            assigned_to="user123",
            assignment_group="group456",
        )

        request = respx.calls.last.request
        url_str = str(request.url)
        assert "state%3D2" in url_str
        assert "priority%3D1" in url_str
        assert "assigned_to%3Duser123" in url_str
        assert "assignment_group%3Dgroup456" in url_str

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_priority_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should filter by priority only."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["problem_list"](priority="1")

        request = respx.calls.last.request
        url_str = str(request.url)
        assert "priority%3D1" in url_str
        # No state filter should be present
        assert "state%3D" not in url_str

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_all_state(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should NOT add state filter when state='all'."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["problem_list"](state="all")

        request = respx.calls.last.request
        url_str = str(request.url)
        assert "state%3D" not in url_str


class TestProblemGet:
    """Tests for problem_get tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_valid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch problem by PRB number."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "PRB0010001",
                            "short_description": "Test problem",
                        }
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_get"](number="PRB0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "PRB0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio()
    async def test_get_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-PRB numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_get"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "PRB" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle problem not found."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_get"](number="PRB9999999")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_case_insensitive(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should uppercase the problem number before querying."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "PRB0010001",
                            "short_description": "Test",
                        }
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_get"](number="prb0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        # Verify the query used the uppercased number
        request = respx.calls.last.request
        assert "PRB0010001" in str(request.url)


class TestProblemCreate:
    """Tests for problem_create tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should create problem with required fields."""
        respx.post(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new123",
                        "number": "PRB0010002",
                        "short_description": "New problem",
                        "state": "1",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](
            short_description="New problem",
            urgency=2,
            impact=3,
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "PRB0010002"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_with_optional_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should include optional fields when provided."""
        respx.post(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "new456",
                        "number": "PRB0010003",
                        "short_description": "Full problem",
                        "description": "Detailed info",
                        "assigned_to": "user123",
                        "assignment_group": "group456",
                        "category": "software",
                        "subcategory": "os",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](
            short_description="Full problem",
            description="Detailed info",
            assigned_to="user123",
            assignment_group="group456",
            category="software",
            subcategory="os",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "PRB0010003"
        assert data["data"]["description"] == "Detailed info"
        assert data["data"]["assigned_to"] == "user123"

    @pytest.mark.asyncio()
    async def test_create_missing_short_description(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject empty short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_whitespace_short_description(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should reject whitespace-only short_description."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="   ")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_urgency_too_high(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject urgency=5 (max is 4)."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="Test", urgency=5)
        data = decode_response(result)

        assert data["status"] == "error"
        assert "urgency" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_urgency_too_low(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject urgency=0 (min is 1)."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="Test", urgency=0)
        data = decode_response(result)

        assert data["status"] == "error"
        assert "urgency" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_impact_too_high(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject impact=5 (max is 4)."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="Test", impact=5)
        data = decode_response(result)

        assert data["status"] == "error"
        assert "impact" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_invalid_impact_too_low(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject impact=0 (min is 1)."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_create"](short_description="Test", impact=0)
        data = decode_response(result)

        assert data["status"] == "error"
        assert "impact" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_create_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block creation in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["problem_create"](short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()


class TestProblemUpdate:
    """Tests for problem_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should update problem by PRB number."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "PRB0010001",
                            "short_description": "Old",
                        }
                    ]
                },
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "short_description": "Updated",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](
            number="PRB0010001",
            short_description="Updated",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated"

    @pytest.mark.asyncio()
    async def test_update_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-PRB numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](number="INC0010001", short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "PRB" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle problem not found."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](number="PRB9999999", short_description="Test")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_no_changes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should error when no fields to update are provided."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](number="PRB0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "no fields" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_with_state(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should map state name to numeric value using PROBLEM_STATE_MAP."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "state": "3",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](
            number="PRB0010001",
            state="known_error",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        # "known_error" maps to "3" via ChoiceRegistry defaults
        assert data["data"]["state"] == "3"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_with_all_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should update all supported fields."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "short_description": "Updated desc",
                        "urgency": "2",
                        "impact": "2",
                        "priority": "1",
                        "state": "5",
                        "description": "Full description",
                        "assigned_to": "user789",
                        "assignment_group": "group321",
                        "category": "hardware",
                        "subcategory": "disk",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_update"](
            number="PRB0010001",
            short_description="Updated desc",
            urgency=2,
            impact=2,
            priority=1,
            state="fix_in_progress",
            description="Full description",
            assigned_to="user789",
            assignment_group="group321",
            category="hardware",
            subcategory="disk",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated desc"
        assert data["data"]["urgency"] == "2"
        assert data["data"]["state"] == "5"

    @pytest.mark.asyncio()
    async def test_update_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block updates in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["problem_update"](
            number="PRB0010001",
            short_description="Test",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()


class TestProblemRootCause:
    """Tests for problem_root_cause tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_root_cause_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should document root cause with cause_notes."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "cause_notes": "Root cause identified",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="Root cause identified",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["cause_notes"] == "Root cause identified"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_root_cause_with_fix_notes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should include fix_notes when provided."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "cause_notes": "Memory leak in module X",
                        "fix_notes": "Patch applied to module X",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="Memory leak in module X",
            fix_notes="Patch applied to module X",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["cause_notes"] == "Memory leak in module X"
        assert data["data"]["fix_notes"] == "Patch applied to module X"

    @pytest.mark.asyncio()
    async def test_root_cause_invalid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-PRB numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="INC0010001",
            cause_notes="Some cause",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "PRB" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_root_cause_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle problem not found."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB9999999",
            cause_notes="Some cause",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_root_cause_empty_cause_notes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject empty cause_notes."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "cause_notes" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_root_cause_whitespace_cause_notes(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should reject whitespace-only cause_notes."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="   ",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "cause_notes" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_root_cause_blocked_in_prod(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        """Should block root cause updates in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="Root cause",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_root_cause_whitespace_fix_notes_ignored(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Whitespace-only fix_notes should not be included in the update."""
        respx.get(f"{BASE_URL}/api/now/table/problem").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "abc123", "number": "PRB0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/problem/abc123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123",
                        "number": "PRB0010001",
                        "cause_notes": "Memory leak in module X",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["problem_root_cause"](
            number="PRB0010001",
            cause_notes="Memory leak in module X",
            fix_notes="   ",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["cause_notes"] == "Memory leak in module X"
        assert "fix_notes" not in data["data"]
