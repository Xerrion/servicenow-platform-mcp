"""Tests for Request Management domain tools."""

from unittest.mock import patch

import pytest
import respx
from httpx import Response
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings

BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict:
    """Helper to register request tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.request import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestRequestList:
    """Tests for request_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_no_filters(self, settings, auth_provider):
        """Should query all requests when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "id1",
                            "number": "REQ0010001",
                            "short_description": "Request 1",
                        },
                        {
                            "sys_id": "id2",
                            "number": "REQ0010002",
                            "short_description": "Request 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_list"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "REQ0010001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_state_filter(self, settings, auth_provider):
        """Should apply state filter to query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](state="1")

        request = respx.calls.last.request
        assert "state%3D1" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_requested_for(self, settings, auth_provider):
        """Should apply requested_for filter to query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](requested_for="user123")

        request = respx.calls.last.request
        assert "requested_for%3Duser123" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_multiple_filters(self, settings, auth_provider):
        """Should combine multiple filters correctly."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](state="1", requested_for="user123", assignment_group="group456")

        request = respx.calls.last.request
        url = str(request.url)
        assert "state%3D1" in url
        assert "requested_for%3Duser123" in url
        assert "assignment_group%3Dgroup456" in url


class TestRequestGet:
    """Tests for request_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_valid_number(self, settings, auth_provider):
        """Should fetch request by REQ number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "REQ0010001",
                            "short_description": "Test request",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="REQ0010001")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"
        assert data["data"]["sys_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_invalid_prefix(self, settings, auth_provider):
        """Should reject non-REQ numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="INC0010001")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "REQ" in data["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings, auth_provider):
        """Should handle request not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="REQ9999999")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_case_insensitive(self, settings, auth_provider):
        """Should uppercase the number before querying."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc123",
                            "number": "REQ0010001",
                            "short_description": "Test request",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="req0010001")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"

        request = respx.calls.last.request
        assert "REQ0010001" in str(request.url)


class TestRequestItems:
    """Tests for request_items tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_items_valid(self, settings, auth_provider):
        """Should fetch request items by REQ number using dot-walk query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "item1",
                            "number": "RITM0010001",
                            "short_description": "Item 1",
                        },
                        {
                            "sys_id": "item2",
                            "number": "RITM0010002",
                            "short_description": "Item 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="REQ0010001")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "RITM0010001"

        request = respx.calls.last.request
        assert "request.number%3DREQ0010001" in str(request.url)

    @pytest.mark.asyncio
    async def test_items_invalid_prefix(self, settings, auth_provider):
        """Should reject non-REQ numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="INC0010001")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "REQ" in data["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_items_empty_result(self, settings, auth_provider):
        """Should succeed with empty list when no items found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="REQ0010001")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"] == []


class TestRequestItemGet:
    """Tests for request_item_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_valid_ritm(self, settings, auth_provider):
        """Should fetch request item by RITM number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "item123",
                            "number": "RITM0010001",
                            "short_description": "Test item",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="RITM0010001")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "RITM0010001"
        assert data["data"]["sys_id"] == "item123"

    @pytest.mark.asyncio
    async def test_get_invalid_prefix(self, settings, auth_provider):
        """Should reject non-RITM numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="REQ0010001")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "RITM" in data["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings, auth_provider):
        """Should handle request item not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="RITM9999999")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()


class TestRequestItemUpdate:
    """Tests for request_item_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_valid(self, mock_write_gate, settings, auth_provider):
        """Should update request item state by RITM number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "item123", "number": "RITM0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sc_req_item/item123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "item123",
                        "number": "RITM0010001",
                        "state": "3",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM0010001", state="3")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["state"] == "3"

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_with_assignment(self, mock_write_gate, settings, auth_provider):
        """Should update assignment_group and assigned_to."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "item123", "number": "RITM0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sc_req_item/item123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "item123",
                        "number": "RITM0010001",
                        "assignment_group": "group456",
                        "assigned_to": "user789",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](
            number="RITM0010001",
            assignment_group="group456",
            assigned_to="user789",
        )
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["assignment_group"] == "group456"
        assert data["data"]["assigned_to"] == "user789"

    @pytest.mark.asyncio
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_invalid_number(self, mock_write_gate, settings, auth_provider):
        """Should reject non-RITM numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="REQ0010001", state="3")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "RITM" in data["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_not_found(self, mock_write_gate, settings, auth_provider):
        """Should handle request item not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM9999999", state="3")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_no_changes(self, mock_write_gate, settings, auth_provider):
        """Should error when no fields to update are provided."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "item123", "number": "RITM0010001"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM0010001")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "no fields" in data["error"]["message"].lower()

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
            result = await tools["request_item_update"](
                number="RITM0010001",
                state="3",
            )
            data = toon_decode(result)

            assert data["status"] == "error"
            assert "production" in data["error"]["message"].lower()
