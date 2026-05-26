"""Tests for the unified ``service_catalog`` tool (Phase 3a)."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.service_catalog import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SC_BASE = f"{BASE_URL}/api/sn_sc/servicecatalog"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified service_catalog test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified service_catalog tool on a fresh MCP and return callables."""
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# catalogs_list
# ---------------------------------------------------------------------------


class TestCatalogsList:
    """Tests for action='catalogs_list'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_defaults(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """List catalogs with default parameters."""
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
        result = decode_response(await tools["service_catalog"](action="catalogs_list"))

        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["data"][0]["title"] == "Service Catalog"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_text_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """text parameter is forwarded to the API."""
        respx.get(f"{SC_BASE}/catalogs").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](action="catalogs_list", text="hardware")

        assert "sysparm_text=hardware" in str(respx.calls.last.request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_limit(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """limit parameter is forwarded."""
        respx.get(f"{SC_BASE}/catalogs").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](action="catalogs_list", limit=5)

        assert "sysparm_limit=5" in str(respx.calls.last.request.url)


# ---------------------------------------------------------------------------
# catalog_get
# ---------------------------------------------------------------------------


class TestCatalogGet:
    """Tests for action='catalog_get'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_catalog(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetch one catalog by sys_id."""
        sys_id = "a" * 32
        respx.get(f"{SC_BASE}/catalogs/{sys_id}").mock(
            return_value=Response(200, json={"result": {"sys_id": sys_id, "title": "Service Catalog"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="catalog_get", sys_id=sys_id))

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == sys_id

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A malformed sys_id is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="catalog_get", sys_id="not-a-sys-id"))

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# categories_list
# ---------------------------------------------------------------------------


class TestCategoriesList:
    """Tests for action='categories_list'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_categories(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """List categories for a catalog."""
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
        result = decode_response(await tools["service_catalog"](action="categories_list", catalog_sys_id="cat123"))

        assert result["status"] == "success"
        assert len(result["data"]) == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_pagination(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """limit / offset are forwarded."""
        respx.get(f"{SC_BASE}/catalogs/cat123/categories").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](action="categories_list", catalog_sys_id="cat123", limit=10, offset=5)

        url = str(respx.calls.last.request.url)
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_top_level_only(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """top_level_only=True is forwarded."""
        respx.get(f"{SC_BASE}/catalogs/cat123/categories").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](action="categories_list", catalog_sys_id="cat123", top_level_only=True)

        assert "sysparm_top_level_only=true" in str(respx.calls.last.request.url)


# ---------------------------------------------------------------------------
# category_get
# ---------------------------------------------------------------------------


class TestCategoryGet:
    """Tests for action='category_get'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_category(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetch one category by sys_id."""
        sys_id = "b" * 32
        respx.get(f"{SC_BASE}/categories/{sys_id}").mock(
            return_value=Response(200, json={"result": {"sys_id": sys_id, "title": "Hardware"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="category_get", sys_id=sys_id))

        assert result["status"] == "success"
        assert result["data"]["title"] == "Hardware"


# ---------------------------------------------------------------------------
# items_list
# ---------------------------------------------------------------------------


class TestItemsList:
    """Tests for action='items_list'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_defaults(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """List items with defaults."""
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
        result = decode_response(await tools["service_catalog"](action="items_list"))

        assert result["status"] == "success"
        assert len(result["data"]) == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_with_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """text / catalog / category / limit / offset are all forwarded."""
        respx.get(f"{SC_BASE}/items").mock(return_value=Response(200, json={"result": []}))

        tools = _register_and_get_tools(settings, auth_provider)
        await tools["service_catalog"](
            action="items_list",
            text="laptop",
            catalog="cat123",
            category="categ456",
            limit=10,
            offset=5,
        )

        url = str(respx.calls.last.request.url)
        assert "sysparm_text=laptop" in url
        assert "sysparm_catalog=cat123" in url
        assert "sysparm_category=categ456" in url
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url


# ---------------------------------------------------------------------------
# item_get
# ---------------------------------------------------------------------------


class TestItemGet:
    """Tests for action='item_get'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_item(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetch one catalog item by sys_id."""
        sys_id = "c" * 32
        respx.get(f"{SC_BASE}/items/{sys_id}").mock(
            return_value=Response(200, json={"result": {"sys_id": sys_id, "name": "Laptop", "price": "$1200"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="item_get", sys_id=sys_id))

        assert result["status"] == "success"
        assert result["data"]["name"] == "Laptop"


# ---------------------------------------------------------------------------
# item_variables
# ---------------------------------------------------------------------------


class TestItemVariables:
    """Tests for action='item_variables'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetch the form variables of a catalog item."""
        sys_id = "d" * 32
        respx.get(f"{SC_BASE}/items/{sys_id}/variables").mock(
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
        result = decode_response(await tools["service_catalog"](action="item_variables", sys_id=sys_id))

        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "urgency"


# ---------------------------------------------------------------------------
# order_now
# ---------------------------------------------------------------------------


class TestOrderNow:
    """Tests for action='order_now'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_order_no_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Order an item without variables (writes succeed in dev)."""
        respx.post(f"{SC_BASE}/items/item123/order_now").mock(
            return_value=Response(200, json={"result": {"sys_id": "req123", "number": "REQ0010001"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="order_now", item_sys_id="item123"))

        assert result["status"] == "success"
        assert result["data"]["number"] == "REQ0010001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_order_with_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Order an item with variables JSON (validate_keys=False allows arbitrary names)."""
        respx.post(f"{SC_BASE}/items/item123/order_now").mock(
            return_value=Response(200, json={"result": {"sys_id": "req123", "number": "REQ0010001"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](action="order_now", item_sys_id="item123", variables='{"urgency": "1"}')
        )

        assert result["status"] == "success"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Production blocks the write before HTTP."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = decode_response(await tools["service_catalog"](action="order_now", item_sys_id="item123"))

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_invalid_item_sys_id_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An item_sys_id with disallowed characters is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](action="order_now", item_sys_id="not valid; DROP TABLE")
        )

        assert result["status"] == "error"
        assert len(respx.calls) == 0


# ---------------------------------------------------------------------------
# add_to_cart
# ---------------------------------------------------------------------------


class TestAddToCart:
    """Tests for action='add_to_cart'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_no_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Add an item to cart (writes succeed in dev)."""
        respx.post(f"{SC_BASE}/items/item123/add_to_cart").mock(
            return_value=Response(200, json={"result": {"cart_item_id": "ci123", "item_id": "item123"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="add_to_cart", item_sys_id="item123"))

        assert result["status"] == "success"
        assert result["data"]["cart_item_id"] == "ci123"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_add_with_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Variables JSON with arbitrary keys is accepted (validate_keys=False)."""
        respx.post(f"{SC_BASE}/items/item123/add_to_cart").mock(
            return_value=Response(200, json={"result": {"cart_item_id": "ci123"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["service_catalog"](
                action="add_to_cart",
                item_sys_id="item123",
                variables='{"quantity": "2"}',
            )
        )

        assert result["status"] == "success"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Production blocks add-to-cart."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = decode_response(await tools["service_catalog"](action="add_to_cart", item_sys_id="item123"))

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_invalid_item_sys_id_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An item_sys_id with disallowed characters is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="add_to_cart", item_sys_id="bad; DROP"))

        assert result["status"] == "error"
        assert len(respx.calls) == 0


# ---------------------------------------------------------------------------
# cart_get
# ---------------------------------------------------------------------------


class TestCartGet:
    """Tests for action='cart_get'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cart(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Retrieve the caller's cart."""
        respx.get(f"{SC_BASE}/cart").mock(
            return_value=Response(
                200,
                json={
                    "result": {
                        "items": [{"cart_item_id": "ci1", "name": "Laptop"}],
                        "subtotal": "$1200",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="cart_get"))

        assert result["status"] == "success"
        assert result["data"]["subtotal"] == "$1200"


# ---------------------------------------------------------------------------
# cart_submit
# ---------------------------------------------------------------------------


class TestCartSubmit:
    """Tests for action='cart_submit'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_submit_cart(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Submit the cart (writes succeed in dev)."""
        respx.post(f"{SC_BASE}/cart/submit_order").mock(
            return_value=Response(
                200,
                json={"result": {"request_number": "REQ0010001", "request_id": "req123"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="cart_submit"))

        assert result["status"] == "success"
        assert result["data"]["request_number"] == "REQ0010001"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Production blocks cart submission."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = decode_response(await tools["service_catalog"](action="cart_submit"))

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# cart_checkout
# ---------------------------------------------------------------------------


class TestCartCheckout:
    """Tests for action='cart_checkout'."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_checkout(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Two-step checkout (writes succeed in dev)."""
        respx.post(f"{SC_BASE}/cart/checkout").mock(
            return_value=Response(
                200,
                json={"result": {"request_number": "REQ0010002", "request_id": "req456"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="cart_checkout"))

        assert result["status"] == "success"
        assert result["data"]["request_number"] == "REQ0010002"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Production blocks checkout."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = decode_response(await tools["service_catalog"](action="cart_checkout"))

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Action dispatch / argument validation
# ---------------------------------------------------------------------------


class TestActionDispatch:
    """Tests for action validation and missing-argument errors."""

    @pytest.mark.asyncio()
    async def test_unknown_action(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An unrecognized action yields an error envelope listing all valid actions."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="bogus"))

        assert result["status"] == "error"
        message = result["error"]["message"].lower()
        assert "unknown action" in message
        # Sample a few valid action names that should be enumerated in the error.
        for action_name in ("catalogs_list", "order_now", "cart_checkout"):
            assert action_name in result["error"]["message"]

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "action",
        ["catalog_get", "category_get", "item_get", "item_variables"],
    )
    async def test_missing_sys_id(self, action: str, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """sys_id-required actions error when sys_id is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action=action))

        assert result["status"] == "error"
        assert "sys_id" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_missing_catalog_sys_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """categories_list errors when catalog_sys_id is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action="categories_list"))

        assert result["status"] == "error"
        assert "catalog_sys_id" in result["error"]["message"]

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("action", ["order_now", "add_to_cart"])
    async def test_missing_item_sys_id(self, action: str, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """order_now / add_to_cart error when item_sys_id is empty."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["service_catalog"](action=action))

        assert result["status"] == "error"
        assert "item_sys_id" in result["error"]["message"]
