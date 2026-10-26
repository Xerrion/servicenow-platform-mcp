"""Tests for the unified ``service_catalog`` tool (Phase 3a)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx2 import Response
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.mcp.tools.service_catalog import register_tools
from tests._mock_transport import http_mock
from tests.helpers import (
    decode_response,
    get_tool_functions,
    register_mutations,
)

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"
SC_BASE = f"{BASE_URL}/api/sn_sc/servicecatalog"

# Valid 32-char hex sys_ids used across this module. Real sys_ids are required
# because the tool calls ``validate_sys_id`` on these inputs.
ITEM_SYS_ID = "e" * 32
CATALOG_SYS_ID = "f" * 32


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """OAuthPKCEProvider for the unified service_catalog test scope."""
    return OAuthPKCEProvider(settings)


def _register_and_get_tools(
    settings: Settings, auth_provider: OAuthPKCEProvider
) -> dict[str, Any]:
    """Register the unified service_catalog tool on a fresh MCP and return callables."""
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    register_mutations(mcp, settings, auth_provider, frozenset({"catalog"}))
    http_mock.get(f"{SC_BASE}/items/{ITEM_SYS_ID}").respond(
        200, json={"result": {"sys_id": ITEM_SYS_ID}}
    )
    http_mock.get(f"{SC_BASE}/items/{ITEM_SYS_ID}/variables").respond(
        200, json={"result": []}
    )
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# catalogs_list
# ---------------------------------------------------------------------------


class TestCatalogsList:
    """Tests for action='catalogs_list'."""

    @pytest.mark.asyncio()
    async def test_list_with_text_filter(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """text parameter is forwarded to the API."""
        http_mock.get(f"{SC_BASE}/catalogs").mock(
            return_value=Response(200, json={"result": []})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](action="catalogs_list", text="hardware")

        assert "sysparm_text=hardware" in str(http_mock.calls.last.request.url)


# ---------------------------------------------------------------------------
# catalog_get
# ---------------------------------------------------------------------------


class TestCatalogGet:
    """Tests for action='catalog_get'."""

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_rejected(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """A malformed sys_id is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](action="catalog_get", sys_id="not-a-sys-id")
        )

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# categories_list
# ---------------------------------------------------------------------------


class TestCategoriesList:
    """Tests for action='categories_list'."""

    @pytest.mark.asyncio()
    async def test_top_level_only(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """top_level_only=True is forwarded."""
        http_mock.get(f"{SC_BASE}/catalogs/{CATALOG_SYS_ID}/categories").mock(
            return_value=Response(200, json={"result": []}),
        )

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](
            action="categories_list", catalog_sys_id=CATALOG_SYS_ID, top_level_only=True
        )

        assert "sysparm_top_level_only=true" in str(http_mock.calls.last.request.url)

    @pytest.mark.asyncio()
    async def test_invalid_catalog_sys_id_rejected(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """A catalog_sys_id that is not a 32-char hex string is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](
                action="categories_list", catalog_sys_id="not-a-sys-id"
            ),
        )

        assert result["status"] == "error"
        assert len(http_mock.calls) == 0


# ---------------------------------------------------------------------------
# items_list
# ---------------------------------------------------------------------------


class TestItemsList:
    """Tests for action='items_list'."""

    @pytest.mark.asyncio()
    async def test_list_with_filters(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """text / catalog / category / limit / offset are all forwarded."""
        http_mock.get(f"{SC_BASE}/items").mock(
            return_value=Response(200, json={"result": []})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](
            action="items_list",
            text="laptop",
            catalog="cat123",
            category="categ456",
            limit=10,
            offset=5,
        )

        url = str(http_mock.calls.last.request.url)
        assert "sysparm_text=laptop" in url
        assert "sysparm_catalog=cat123" in url
        assert "sysparm_category=categ456" in url
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url


# ---------------------------------------------------------------------------
# Action dispatch / argument validation
# ---------------------------------------------------------------------------


class TestActionDispatch:
    """Tests for action validation and missing-argument errors."""

    @pytest.mark.asyncio()
    async def test_missing_catalog_sys_id(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """categories_list errors when catalog_sys_id is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](action="categories_list")
        )

        assert result["status"] == "error"
        assert "catalog_sys_id" in result["error"]["message"]
