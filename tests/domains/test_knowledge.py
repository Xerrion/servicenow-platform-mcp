"""Tests for Knowledge Management domain tools."""

import json
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings

BASE_URL = "https://test.service-now.com"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, object]:
    """Helper to register knowledge tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.domains.knowledge import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestKnowledgeSearch:
    """Tests for knowledge_search tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_default_published(self, settings, auth_provider):
        """Should search published articles by default."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "kb1", "number": "KB0010001", "short_description": "How to reset password"},
                        {"sys_id": "kb2", "number": "KB0010002", "short_description": "Password policy guide"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_search"](query="password")
        data = json.loads(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        request = respx.calls.last.request
        assert "short_descriptionLIKEpassword" in str(request.url)
        assert "textLIKEpassword" in str(request.url)
        assert "workflow_state%3Dpublished" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_custom_workflow_state(self, settings, auth_provider):
        """Should filter by custom workflow_state."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["knowledge_search"](query="test", workflow_state="draft")

        request = respx.calls.last.request
        assert "workflow_state%3Ddraft" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_custom_limit(self, settings, auth_provider):
        """Should respect custom limit parameter."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["knowledge_search"](query="test", limit=50)

        request = respx.calls.last.request
        assert "sysparm_limit=50" in str(request.url)


class TestKnowledgeGet:
    """Tests for knowledge_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_by_kb_number(self, settings, auth_provider):
        """Should fetch knowledge article by KB number."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(
                200,
                json={"result": [{"sys_id": "kb123", "number": "KB0010001", "short_description": "Test article"}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_get"](number_or_sys_id="KB0010001")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "KB0010001"
        request = respx.calls.last.request
        assert "number%3DKB0010001" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_by_sys_id(self, settings, auth_provider):
        """Should fetch knowledge article by sys_id if 32-char hex."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            side_effect=[
                Response(200, json={"result": []}),  # First query by number fails
                Response(
                    200,
                    json={"result": [{"sys_id": "abc123def456abc123def456abc12345", "number": "KB0010001"}]},
                ),  # Second query by sys_id succeeds
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_get"](number_or_sys_id="abc123def456abc123def456abc12345")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "KB0010001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings, auth_provider):
        """Should return error when knowledge article not found."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            side_effect=[
                Response(200, json={"result": []}),  # Query by number
                Response(200, json={"result": []}),  # Query by sys_id
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_get"](number_or_sys_id="abc123def456abc123def456abc12345")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


class TestKnowledgeCreate:
    """Tests for knowledge_create tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_minimal(self, settings, auth_provider):
        """Should create knowledge article with minimal required fields."""
        respx.post(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(
                201,
                json={
                    "result": {
                        "sys_id": "kb_new",
                        "number": "KB0010003",
                        "short_description": "New article",
                        "text": "Article content",
                        "workflow_state": "draft",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_create"](short_description="New article", text="Article content")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "KB0010003"
        assert data["data"]["workflow_state"] == "draft"

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_missing_short_description(self, settings, auth_provider):
        """Should return error if short_description is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_create"](short_description="", text="Article content")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "short_description" in data["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_missing_text(self, settings, auth_provider):
        """Should return error if text is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_create"](short_description="New article", text="")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "text" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_create_production_blocked(self):
        """Should block write in production environment."""
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
            result = await tools["knowledge_create"](short_description="Test", text="Content")
            data = json.loads(result)

            assert data["status"] == "error"
            assert "production" in data["error"].lower()


class TestKnowledgeUpdate:
    """Tests for knowledge_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_by_number(self, settings, auth_provider):
        """Should update knowledge article by KB number."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(200, json={"result": [{"sys_id": "kb123", "number": "KB0010001"}]})
        )
        respx.patch(f"{BASE_URL}/api/now/table/kb_knowledge/kb123").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "kb123", "number": "KB0010001", "short_description": "Updated title"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_update"](number_or_sys_id="KB0010001", short_description="Updated title")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["short_description"] == "Updated title"

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_by_sys_id(self, settings, auth_provider):
        """Should update knowledge article by sys_id."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            side_effect=[
                Response(200, json={"result": []}),  # Query by number fails
                Response(200, json={"result": [{"sys_id": "abc123def456abc123def456abc12345", "number": "KB0010001"}]}),
            ]
        )
        respx.patch(f"{BASE_URL}/api/now/table/kb_knowledge/abc123def456abc123def456abc12345").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "abc123def456abc123def456abc12345",
                        "number": "KB0010001",
                        "text": "Updated content",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_update"](
            number_or_sys_id="abc123def456abc123def456abc12345", text="Updated content"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["data"]["text"] == "Updated content"

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_not_found(self, settings, auth_provider):
        """Should return error if article not found."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            side_effect=[
                Response(200, json={"result": []}),
                Response(200, json={"result": []}),
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_update"](number_or_sys_id="KB9999999", short_description="Test")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


class TestKnowledgeFeedback:
    """Tests for knowledge_feedback tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_with_rating(self, settings, auth_provider):
        """Should submit rating feedback."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(200, json={"result": [{"sys_id": "kb123", "number": "KB0010001"}]})
        )
        respx.patch(f"{BASE_URL}/api/now/table/kb_knowledge/kb123").mock(
            return_value=Response(200, json={"result": {"sys_id": "kb123", "number": "KB0010001", "rating": "5"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001", rating=5)
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_with_comment(self, settings, auth_provider):
        """Should submit comment feedback."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(200, json={"result": [{"sys_id": "kb123", "number": "KB0010001"}]})
        )
        respx.patch(f"{BASE_URL}/api/now/table/kb_knowledge/kb123").mock(
            return_value=Response(
                200, json={"result": {"sys_id": "kb123", "number": "KB0010001", "feedback_comments": "Very helpful"}}
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001", comment="Very helpful")
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_both_rating_and_comment(self, settings, auth_provider):
        """Should submit both rating and comment."""
        respx.get(f"{BASE_URL}/api/now/table/kb_knowledge").mock(
            return_value=Response(200, json={"result": [{"sys_id": "kb123", "number": "KB0010001"}]})
        )
        respx.patch(f"{BASE_URL}/api/now/table/kb_knowledge/kb123").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "sys_id": "kb123",
                        "number": "KB0010001",
                        "rating": "4",
                        "feedback_comments": "Good article",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001", rating=4, comment="Good article")
        data = json.loads(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_missing_both(self, settings, auth_provider):
        """Should return error if neither rating nor comment provided."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "rating or comment" in data["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_invalid_rating_low(self, settings, auth_provider):
        """Should return error if rating below 1."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001", rating=0)
        data = json.loads(result)

        assert data["status"] == "error"
        assert "between 1 and 5" in data["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_feedback_invalid_rating_high(self, settings, auth_provider):
        """Should return error if rating above 5."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["knowledge_feedback"](number_or_sys_id="KB0010001", rating=6)
        data = json.loads(result)

        assert data["status"] == "error"
        assert "between 1 and 5" in data["error"].lower()
