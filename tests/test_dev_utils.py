"""Tests for developer utility tools (dev_toggle, dev_set_property)."""

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register dev_utils tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.dev_utils import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# -- dev_toggle ----------------------------------------------------------------


class TestDevToggle:
    """Tests for the dev_toggle tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_toggles_active_field(self, settings, auth_provider):
        """Toggles the active field and returns old/new values."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "My Business Rule",
                        "active": "true",
                    }
                },
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "My Business Rule",
                        "active": "false",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["old_active"] == "true"
        assert result["data"]["new_active"] == "false"

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings, auth_provider):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.dev_utils import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = toon_decode(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_unknown_artifact_type(self, settings, auth_provider):
        """Returns error for unknown artifact type."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_toggle"](artifact_type="unknown_type", sys_id="br001", active=False)
        result = toon_decode(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_denied_table_returns_error(self, settings, auth_provider):
        """Returns error when resolved table is denied by policy."""
        from unittest.mock import patch as mock_patch

        from servicenow_mcp.errors import ForbiddenError

        tools = _register_and_get_tools(settings, auth_provider)

        with mock_patch(
            "servicenow_mcp.tools.dev_utils.check_table_access",
            side_effect=ForbiddenError("Access forbidden"),
        ):
            raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)

        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "acl" in result["error"]["message"].lower() or "forbidden" in result["error"]["message"].lower()


# -- dev_set_property ----------------------------------------------------------


class TestDevSetProperty:
    """Tests for the dev_set_property tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_updates_property_value(self, settings, auth_provider):
        """Updates a system property and returns old value."""
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "prop001",
                            "name": "glide.ui.session_timeout",
                            "value": "30",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sys_properties/prop001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "prop001",
                        "name": "glide.ui.session_timeout",
                        "value": "60",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["old_value"] == "30"
        assert result["data"]["new_value"] == "60"

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.dev_utils import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = toon_decode(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_property_not_found(self, settings, auth_provider):
        """Returns error when property doesn't exist."""
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="nonexistent.property", value="x")
        result = toon_decode(raw)

        assert result["status"] == "error"


# -- Sensitive field masking ---------------------------------------------------


class TestDevUtilsSensitiveFieldMasking:
    """Tests for sensitive field masking in dev_utils tool responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_dev_set_property_masks_sensitive_value(self, settings, auth_provider):
        """Setting a property with a sensitive name masks old/new values in response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "prop002",
                            "name": "my.api_key_token",
                            "value": "old_secret_key",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.patch(f"{BASE_URL}/api/now/table/sys_properties/prop002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "prop002",
                        "name": "my.api_key_token",
                        "value": "new_secret_key",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="my.api_key_token", value="new_secret_key")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["old_value"] == "***MASKED***"
        assert result["data"]["new_value"] == "***MASKED***"
        assert result["data"]["name"] == "my.api_key_token"


# -- Generic exception handlers ------------------------------------------------


class TestDevToggleGenericException:
    """Tests for dev_toggle generic exception handler."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_exception(self, settings, auth_provider):
        """dev_toggle returns error envelope when an unexpected exception occurs."""
        from unittest.mock import AsyncMock, patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.dev_utils.ServiceNowClient.__aenter__",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "connection failed" in result["error"]["message"]


class TestDevSetPropertyGenericException:
    """Tests for dev_set_property generic exception handler."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_exception(self, settings, auth_provider):
        """dev_set_property returns error envelope when an unexpected exception occurs."""
        from unittest.mock import AsyncMock, patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.dev_utils.ServiceNowClient.__aenter__",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network failure"),
        ):
            raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = toon_decode(raw)
        assert result["status"] == "error"
        assert "network failure" in result["error"]["message"]
