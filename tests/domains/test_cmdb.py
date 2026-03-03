"""Tests for CMDB domain tools."""

import pytest
import respx
from httpx import Response
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.domains import cmdb


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict:
    """Helper to register tools and return tool callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    cmdb.register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestCmdbList:
    """Tests for cmdb_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_default_cmdb_ci(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test listing CIs from default cmdb_ci table."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_list = tools["cmdb_list"]

        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "ci1", "name": "server-01", "operational_status": "1"},
                        {"sys_id": "ci2", "name": "server-02", "operational_status": "1"},
                    ]
                },
            )
        )

        result = await cmdb_list()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["name"] == "server-01"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_ci_class_param(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test listing CIs from specific CI class table."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_list = tools["cmdb_list"]

        respx.get("https://test.service-now.com/api/now/table/cmdb_ci_server").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "srv1", "name": "web-server-01", "operational_status": "1"},
                    ]
                },
            )
        )

        result = await cmdb_list(ci_class="cmdb_ci_server")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["name"] == "web-server-01"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_operational_status_filter(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test filtering by operational status."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_list = tools["cmdb_list"]

        # Mock the query with operational_status=1
        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "ci1", "name": "server-01", "operational_status": "1"},
                    ]
                },
            )
        )

        result = await cmdb_list(operational_status="operational")
        data = toon_decode(result)

        assert data["status"] == "success"
        request = respx.calls.last.request
        assert "operational_status%3D1" in str(request.url) or "operational_status=1" in str(request.url)


class TestCmdbGet:
    """Tests for cmdb_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_by_sys_id(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching CI by sys_id."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_get = tools["cmdb_get"]

        sys_id = "a" * 32  # 32-char hex string
        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": sys_id, "name": "server-01", "operational_status": "1"},
                    ]
                },
            )
        )

        result = await cmdb_get(name_or_sys_id=sys_id)
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["name"] == "server-01"
        request = respx.calls.last.request
        assert "sys_id%3D" in str(request.url) or f"sys_id={sys_id}" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_by_name(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching CI by name."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_get = tools["cmdb_get"]

        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "ci1", "name": "server-01", "operational_status": "1"},
                    ]
                },
            )
        )

        result = await cmdb_get(name_or_sys_id="server-01")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["name"] == "server-01"
        # Verify query used name (URL encoded)
        request = respx.calls.last.request
        assert "name%3Dserver-01" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching non-existent CI."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_get = tools["cmdb_get"]

        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={"result": []},
            )
        )

        result = await cmdb_get(name_or_sys_id="nonexistent")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


class TestCmdbRelationships:
    """Tests for cmdb_relationships tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_both_directions(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching relationships in both directions."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        sys_id = "a" * 32
        respx.get("https://test.service-now.com/api/now/table/cmdb_rel_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "rel1", "parent": {"value": "parent1"}, "child": {"value": sys_id}},
                        {"sys_id": "rel2", "parent": {"value": sys_id}, "child": {"value": "child1"}},
                    ]
                },
            )
        )

        result = await cmdb_relationships(name_or_sys_id=sys_id, direction="both")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_parent_only(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching parent relationships only."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        sys_id = "a" * 32
        respx.get("https://test.service-now.com/api/now/table/cmdb_rel_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "rel1", "parent": {"value": "parent1"}, "child": {"value": sys_id}},
                    ]
                },
            )
        )

        result = await cmdb_relationships(name_or_sys_id=sys_id, direction="parent")
        data = toon_decode(result)

        assert data["status"] == "success"
        # Verify query used child.sys_id
        request = respx.calls.last.request
        assert f"child.sys_id%3D{sys_id}" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_by_name_lookup(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching relationships by CI name (requires sys_id lookup first)."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        # First query: resolve name to sys_id
        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "resolved_sys_id", "name": "server-01"},
                    ]
                },
            )
        )

        # Second query: fetch relationships
        respx.get("https://test.service-now.com/api/now/table/cmdb_rel_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "rel1", "parent": {"value": "parent1"}, "child": {"value": "resolved_sys_id"}},
                    ]
                },
            )
        )

        result = await cmdb_relationships(name_or_sys_id="server-01", direction="parent")
        data = toon_decode(result)

        assert data["status"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_name_not_found(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test that name lookup returning no records produces an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        # Name lookup returns empty result
        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(
                200,
                json={"result": []},
            )
        )

        result = await cmdb_relationships(name_or_sys_id="nonexistent-ci", direction="both")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_child_only(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test fetching child relationships only."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        sys_id = "b" * 32
        respx.get("https://test.service-now.com/api/now/table/cmdb_rel_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "rel1", "parent": {"value": sys_id}, "child": {"value": "child1"}},
                    ]
                },
            )
        )

        result = await cmdb_relationships(name_or_sys_id=sys_id, direction="child")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 1
        # Verify query used parent.sys_id
        request = respx.calls.last.request
        assert f"parent.sys_id%3D{sys_id}" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_relationships_invalid_direction(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test that an invalid direction produces an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_relationships = tools["cmdb_relationships"]

        sys_id = "c" * 32
        result = await cmdb_relationships(name_or_sys_id=sys_id, direction="sideways")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "invalid direction" in data["error"].lower()


class TestCmdbClasses:
    """Tests for cmdb_classes tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_classes_aggregate(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test listing unique CI classes via aggregate API."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_classes = tools["cmdb_classes"]

        respx.get("https://test.service-now.com/api/now/stats/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"groupby_fields": [{"value": "cmdb_ci_server"}], "stats": {"count": "42"}},
                        {"groupby_fields": [{"value": "cmdb_ci_network_adapter"}], "stats": {"count": "15"}},
                    ]
                },
            )
        )

        result = await cmdb_classes(limit=50)
        data = toon_decode(result)

        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_classes_respects_limit(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test that cmdb_classes respects limit parameter."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_classes = tools["cmdb_classes"]

        respx.get("https://test.service-now.com/api/now/stats/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"groupby_fields": [{"value": "cmdb_ci_server"}], "stats": {"count": "42"}},
                    ]
                },
            )
        )

        result = await cmdb_classes(limit=10)
        data = toon_decode(result)

        assert data["status"] == "success"


class TestCmdbHealth:
    """Tests for cmdb_health tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_default_table(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test CMDB health check on default cmdb_ci table."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_health = tools["cmdb_health"]

        respx.get("https://test.service-now.com/api/now/stats/cmdb_ci").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"groupby_fields": [{"value": "1"}], "stats": {"count": "100"}},
                        {"groupby_fields": [{"value": "2"}], "stats": {"count": "5"}},
                        {"groupby_fields": [{"value": "6"}], "stats": {"count": "10"}},
                    ]
                },
            )
        )

        result = await cmdb_health()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_specific_ci_class(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test CMDB health check on specific CI class."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_health = tools["cmdb_health"]

        respx.get("https://test.service-now.com/api/now/stats/cmdb_ci_server").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"groupby_fields": [{"value": "1"}], "stats": {"count": "50"}},
                    ]
                },
            )
        )

        result = await cmdb_health(ci_class="cmdb_ci_server")
        data = toon_decode(result)

        assert data["status"] == "success"
        # Verify the correct table was queried
        request = respx.calls.last.request
        assert "cmdb_ci_server" in str(request.url)


class TestErrorHandling:
    """Tests for error handling across CMDB tools."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_denied_table_access(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test that denied tables are blocked."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_list = tools["cmdb_list"]

        # Try to access a denied table
        result = await cmdb_list(ci_class="sys_user_has_password")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "denied" in data["error"].lower() or "forbidden" in data["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_safe_tool_call_exception_handling(self, settings: Settings, auth_provider: BasicAuthProvider):
        """Test that exceptions are caught and returned as error responses."""
        tools = _register_and_get_tools(settings, auth_provider)
        cmdb_get = tools["cmdb_get"]

        # Mock an HTTP error
        respx.get("https://test.service-now.com/api/now/table/cmdb_ci").mock(
            return_value=Response(500, json={"error": "Internal Server Error"})
        )

        result = await cmdb_get(name_or_sys_id="test")
        data = toon_decode(result)

        assert data["status"] == "error"
        assert "correlation_id" in data
