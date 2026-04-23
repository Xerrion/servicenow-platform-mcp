"""Tests for Request Management domain tools."""

from typing import Any
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper to register request tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.request import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


class TestRequestList:
    """Tests for request_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_no_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should query all requests when no filters provided."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "4e89d81a2e6fb4be2578d245fd8511c1",
                            "number": "REQ0010001",
                            "short_description": "Request 1",
                        },
                        {
                            "sys_id": "867d5f8110f8aa79dd63d7440f217242",
                            "number": "REQ0010002",
                            "short_description": "Request 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_list"]()
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "REQ0010001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_state_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should apply state filter to query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](state="1")

        request = respx.calls.last.request
        assert "state%3D1" in str(request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_requested_for(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should apply requested_for filter to query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](requested_for="95c946bf622ef93b0a211cd0fd028dfd")

        request = respx.calls.last.request
        assert "requested_for%3D95c946bf622ef93b0a211cd0fd028dfd" in str(request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_multiple_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should combine multiple filters correctly."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["request_list"](
            state="1",
            requested_for="95c946bf622ef93b0a211cd0fd028dfd",
            assignment_group="948e04007eb5c3b60182c0a3ed3b6e7e",
        )

        request = respx.calls.last.request
        url = str(request.url)
        assert "state%3D1" in url
        assert "requested_for%3D95c946bf622ef93b0a211cd0fd028dfd" in url
        assert "assignment_group%3D948e04007eb5c3b60182c0a3ed3b6e7e" in url


class TestRequestGet:
    """Tests for request_get tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_valid_number(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch request by REQ number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "6367c48dd193d56ea7b0baad25b19455",
                            "number": "REQ0010001",
                            "short_description": "Test request",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="REQ0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"
        assert data["data"]["sys_id"] == "6367c48dd193d56ea7b0baad25b19455"

    @pytest.mark.asyncio()
    async def test_get_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-REQ numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "REQ" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle request not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="REQ9999999")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_case_insensitive(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should uppercase the number before querying."""
        respx.get(f"{BASE_URL}/api/now/table/sc_request").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "6367c48dd193d56ea7b0baad25b19455",
                            "number": "REQ0010001",
                            "short_description": "Test request",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_get"](number="req0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"

        request = respx.calls.last.request
        assert "REQ0010001" in str(request.url)


class TestRequestItems:
    """Tests for request_items tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_items_valid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch request items by REQ number using dot-walk query."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "4e702d8dacb758a70499bd7a1cd42590",
                            "number": "RITM0010001",
                            "short_description": "Item 1",
                        },
                        {
                            "sys_id": "cbc7e024367097309a6aba1dc0023de5",
                            "number": "RITM0010002",
                            "short_description": "Item 2",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="REQ0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["number"] == "RITM0010001"

        request = respx.calls.last.request
        assert "request.number%3DREQ0010001" in str(request.url)

    @pytest.mark.asyncio()
    async def test_items_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-REQ numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="INC0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "REQ" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_items_empty_result(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should succeed with empty list when no items found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_items"](number="REQ0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"] == []


class TestRequestItemGet:
    """Tests for request_item_get tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_valid_ritm(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should fetch request item by RITM number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "41857183a56ba0402914ad20df39a464",
                            "number": "RITM0010001",
                            "short_description": "Test item",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="RITM0010001")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "RITM0010001"
        assert data["data"]["sys_id"] == "41857183a56ba0402914ad20df39a464"

    @pytest.mark.asyncio()
    async def test_get_invalid_prefix(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should reject non-RITM numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="REQ0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "RITM" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Should handle request item not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_get"](number="RITM9999999")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()


class TestRequestItemUpdate:
    """Tests for request_item_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_valid(
        self, _mock_write_gate: Any, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should update request item state by RITM number."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "41857183a56ba0402914ad20df39a464", "number": "RITM0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sc_req_item/41857183a56ba0402914ad20df39a464").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "41857183a56ba0402914ad20df39a464",
                        "number": "RITM0010001",
                        "state": "3",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM0010001", state="3")
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["state"] == "3"

    @pytest.mark.asyncio()
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_with_assignment(
        self, _mock_write_gate: Any, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should update assignment_group and assigned_to."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "41857183a56ba0402914ad20df39a464", "number": "RITM0010001"}]},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sc_req_item/41857183a56ba0402914ad20df39a464").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "41857183a56ba0402914ad20df39a464",
                        "number": "RITM0010001",
                        "assignment_group": "948e04007eb5c3b60182c0a3ed3b6e7e",
                        "assigned_to": "da469dabaacf11c033fabc4a90cd8895",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](
            number="RITM0010001",
            assignment_group="948e04007eb5c3b60182c0a3ed3b6e7e",
            assigned_to="da469dabaacf11c033fabc4a90cd8895",
        )
        data = decode_response(result)

        assert data["status"] == "success"
        assert data["data"]["assignment_group"] == "948e04007eb5c3b60182c0a3ed3b6e7e"
        assert data["data"]["assigned_to"] == "da469dabaacf11c033fabc4a90cd8895"

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_invalid_number(
        self, _mock_write_gate: Any, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should reject non-RITM numbers."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="REQ0010001", state="3")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "RITM" in data["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_not_found(
        self, _mock_write_gate: Any, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should handle request item not found."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM9999999", state="3")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "not found" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_update_no_changes(
        self, _mock_write_gate: Any, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Should error when no fields to update are provided."""
        respx.get(f"{BASE_URL}/api/now/table/sc_req_item").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "41857183a56ba0402914ad20df39a464", "number": "RITM0010001"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["request_item_update"](number="RITM0010001")
        data = decode_response(result)

        assert data["status"] == "error"
        assert "no fields" in data["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_update_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Should block updates in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = await tools["request_item_update"](
            number="RITM0010001",
            state="3",
        )
        data = decode_response(result)

        assert data["status"] == "error"
        assert "production" in data["error"]["message"].lower()
