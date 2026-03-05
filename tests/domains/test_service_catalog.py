"""Tests for Service Catalog domain tools."""

from unittest.mock import patch

import pytest
import respx
from httpx import Response
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings

BASE_URL = "https://test.service-now.com"
SC_BASE = f"{BASE_URL}/api/sn_sc/servicecatalog"


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict:
    """Helper to register service catalog tools and extract callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.choices import ChoiceRegistry
    from servicenow_mcp.tools.domains.service_catalog import register_tools

    mcp = FastMCP("test")
    choices = ChoiceRegistry(settings, auth_provider)
    choices._fetched = True
    choices._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ── Catalog List ─────────────────────────────────────────────────────────


class TestScCatalogsList:
    """Tests for sc_catalogs_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_defaults(self, settings, auth_provider):
        """Should list catalogs with default parameters."""
        respx.get(f"{SC_BASE}/catalogs").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "cat1", "title": "Service Catalog"},
                        {"sys_id": "cat2", "title": "Technical Catalog"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_catalogs_list"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["title"] == "Service Catalog"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_text_filter(self, settings, auth_provider):
        """Should pass text search parameter."""
        respx.get(f"{SC_BASE}/catalogs").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["sc_catalogs_list"](text="hardware")

        request = respx.calls.last.request
        assert "sysparm_text=hardware" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_with_limit(self, settings, auth_provider):
        """Should pass limit parameter."""
        respx.get(f"{SC_BASE}/catalogs").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["sc_catalogs_list"](limit=5)

        request = respx.calls.last.request
        assert "sysparm_limit=5" in str(request.url)


# ── Catalog Get ──────────────────────────────────────────────────────────


class TestScCatalogGet:
    """Tests for sc_catalog_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_catalog(self, settings, auth_provider):
        """Should fetch a specific catalog by sys_id."""
        respx.get(f"{SC_BASE}/catalogs/cat123").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "cat123", "title": "Service Catalog"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_catalog_get"](sys_id="cat123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["sys_id"] == "cat123"
        assert data["data"]["title"] == "Service Catalog"


# ── Categories List ──────────────────────────────────────────────────────


class TestScCategoriesList:
    """Tests for sc_categories_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_categories(self, settings, auth_provider):
        """Should list categories for a catalog."""
        respx.get(f"{SC_BASE}/catalogs/cat123/categories").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "categ1", "title": "Hardware"},
                        {"sys_id": "categ2", "title": "Software"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_categories_list"](catalog_sys_id="cat123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["title"] == "Hardware"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_categories_with_pagination(self, settings, auth_provider):
        """Should pass limit and offset parameters."""
        respx.get(f"{SC_BASE}/catalogs/cat123/categories").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["sc_categories_list"](catalog_sys_id="cat123", limit=10, offset=5)

        request = respx.calls.last.request
        url = str(request.url)
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_categories_top_level_only(self, settings, auth_provider):
        """Should pass top_level_only parameter."""
        respx.get(f"{SC_BASE}/catalogs/cat123/categories").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["sc_categories_list"](catalog_sys_id="cat123", top_level_only=True)

        request = respx.calls.last.request
        assert "sysparm_top_level_only=true" in str(request.url)


# ── Category Get ─────────────────────────────────────────────────────────


class TestScCategoryGet:
    """Tests for sc_category_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_category(self, settings, auth_provider):
        """Should fetch a specific category by sys_id."""
        respx.get(f"{SC_BASE}/categories/categ123").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "categ123", "title": "Hardware"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_category_get"](sys_id="categ123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["sys_id"] == "categ123"
        assert data["data"]["title"] == "Hardware"


# ── Items List ───────────────────────────────────────────────────────────


class TestScItemsList:
    """Tests for sc_items_list tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_items_defaults(self, settings, auth_provider):
        """Should list catalog items with default parameters."""
        respx.get(f"{SC_BASE}/items").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"sys_id": "item1", "name": "Laptop"},
                        {"sys_id": "item2", "name": "Monitor"},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_items_list"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["name"] == "Laptop"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_items_with_filters(self, settings, auth_provider):
        """Should pass text, catalog, and category filters."""
        respx.get(f"{SC_BASE}/items").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["sc_items_list"](
            text="laptop",
            catalog="cat123",
            category="categ456",
            limit=10,
            offset=5,
        )

        request = respx.calls.last.request
        url = str(request.url)
        assert "sysparm_text=laptop" in url
        assert "sysparm_catalog=cat123" in url
        assert "sysparm_category=categ456" in url
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url


# ── Item Get ─────────────────────────────────────────────────────────────


class TestScItemGet:
    """Tests for sc_item_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_item(self, settings, auth_provider):
        """Should fetch a specific catalog item by sys_id."""
        respx.get(f"{SC_BASE}/items/item123").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "item123", "name": "Laptop", "price": "$1200"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_item_get"](sys_id="item123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["sys_id"] == "item123"
        assert data["data"]["name"] == "Laptop"


# ── Item Variables ───────────────────────────────────────────────────────


class TestScItemVariables:
    """Tests for sc_item_variables tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_variables(self, settings, auth_provider):
        """Should fetch variables for a catalog item."""
        respx.get(f"{SC_BASE}/items/item123/variables").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {"name": "urgency", "type": "choice", "mandatory": True},
                        {"name": "description", "type": "text", "mandatory": False},
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_item_variables"](sys_id="item123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert len(data["data"]) == 2
        assert data["data"][0]["name"] == "urgency"


# ── Order Now ────────────────────────────────────────────────────────────


class TestScOrderNow:
    """Tests for sc_order_now tool."""

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_order_now_no_variables(self, mock_write_gate, settings, auth_provider):
        """Should order an item without variables."""
        respx.post(f"{SC_BASE}/items/item123/order_now").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "req123", "number": "REQ0010001"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_order_now"](item_sys_id="item123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_order_now_with_variables(self, mock_write_gate, settings, auth_provider):
        """Should order an item with variables JSON."""
        respx.post(f"{SC_BASE}/items/item123/order_now").mock(
            return_value=Response(
                200,
                json={"result": {"sys_id": "req123", "number": "REQ0010001"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_order_now"](
            item_sys_id="item123",
            variables='{"urgency": "1"}',
        )
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["number"] == "REQ0010001"

    @pytest.mark.asyncio
    async def test_order_now_blocked_in_prod(self):
        """Should block ordering in production."""
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
            result = await tools["sc_order_now"](item_sys_id="item123")
            data = toon_decode(result)

            assert data["status"] == "error"
            assert "production" in data["error"]["message"].lower()


# ── Add to Cart ──────────────────────────────────────────────────────────


class TestScAddToCart:
    """Tests for sc_add_to_cart tool."""

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_add_to_cart_no_variables(self, mock_write_gate, settings, auth_provider):
        """Should add item to cart without variables."""
        respx.post(f"{SC_BASE}/items/item123/add_to_cart").mock(
            return_value=Response(
                200,
                json={"result": {"cart_item_id": "ci123", "item_id": "item123"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_add_to_cart"](item_sys_id="item123")
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["cart_item_id"] == "ci123"

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_add_to_cart_with_variables(self, mock_write_gate, settings, auth_provider):
        """Should add item to cart with variables JSON."""
        respx.post(f"{SC_BASE}/items/item123/add_to_cart").mock(
            return_value=Response(
                200,
                json={"result": {"cart_item_id": "ci123", "item_id": "item123"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_add_to_cart"](
            item_sys_id="item123",
            variables='{"quantity": "2"}',
        )
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["cart_item_id"] == "ci123"

    @pytest.mark.asyncio
    async def test_add_to_cart_blocked_in_prod(self):
        """Should block add-to-cart in production."""
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
            result = await tools["sc_add_to_cart"](item_sys_id="item123")
            data = toon_decode(result)

            assert data["status"] == "error"
            assert "production" in data["error"]["message"].lower()


# ── Cart Get ─────────────────────────────────────────────────────────────


class TestScCartGet:
    """Tests for sc_cart_get tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_cart(self, settings, auth_provider):
        """Should retrieve the current user's cart."""
        respx.get(f"{SC_BASE}/cart").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "items": [
                            {"cart_item_id": "ci1", "name": "Laptop"},
                        ],
                        "subtotal": "$1200",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_cart_get"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["subtotal"] == "$1200"


# ── Cart Submit ──────────────────────────────────────────────────────────


class TestScCartSubmit:
    """Tests for sc_cart_submit tool."""

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_submit_cart(self, mock_write_gate, settings, auth_provider):
        """Should submit the cart as an order."""
        respx.post(f"{SC_BASE}/cart/submit_order").mock(
            return_value=Response(
                200,
                json={"result": {"request_number": "REQ0010001", "request_id": "req123"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_cart_submit"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["request_number"] == "REQ0010001"

    @pytest.mark.asyncio
    async def test_submit_cart_blocked_in_prod(self):
        """Should block cart submission in production."""
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
            result = await tools["sc_cart_submit"]()
            data = toon_decode(result)

            assert data["status"] == "error"
            assert "production" in data["error"]["message"].lower()


# ── Cart Checkout ────────────────────────────────────────────────────────


class TestScCartCheckout:
    """Tests for sc_cart_checkout tool."""

    @pytest.mark.asyncio
    @respx.mock
    @patch("servicenow_mcp.policy.write_gate", return_value=None)
    async def test_checkout_cart(self, mock_write_gate, settings, auth_provider):
        """Should checkout the cart."""
        respx.post(f"{SC_BASE}/cart/checkout").mock(
            return_value=Response(
                200,
                json={"result": {"request_number": "REQ0010002", "request_id": "req456"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = await tools["sc_cart_checkout"]()
        data = toon_decode(result)

        assert data["status"] == "success"
        assert data["data"]["request_number"] == "REQ0010002"

    @pytest.mark.asyncio
    async def test_checkout_blocked_in_prod(self):
        """Should block checkout in production."""
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
            result = await tools["sc_cart_checkout"]()
            data = toon_decode(result)

            assert data["status"] == "error"
            assert "production" in data["error"]["message"].lower()
