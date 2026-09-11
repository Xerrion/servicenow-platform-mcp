"""Unified ``service_catalog`` action-dispatching tool.

Folds the twelve legacy ``sc_*`` tools (catalog/category/item read endpoints,
order/cart write endpoints) into a single action-dispatching surface. Old
tools remain registered alongside until Phase 3b retires them.

Actions: ``catalogs_list``, ``catalog_get``, ``categories_list``,
``category_get``, ``items_list``, ``item_get``, ``item_variables``,
``order_now``, ``add_to_cart``, ``cart_get``, ``cart_submit``,
``cart_checkout``.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import gate_write
from servicenow_mcp.response import format_response
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.validation import validate_sys_id


TOOL_NAMES: list[str] = ["service_catalog"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "catalogs_list",
        "catalog_get",
        "categories_list",
        "category_get",
        "items_list",
        "item_get",
        "item_variables",
        "order_now",
        "add_to_cart",
        "cart_get",
        "cart_submit",
        "cart_checkout",
    }
)

# Actions whose single required argument is ``sys_id`` (and must pass validate_sys_id).
_SYS_ID_ACTIONS: Final[frozenset[str]] = frozenset({"catalog_get", "category_get", "item_get", "item_variables"})


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _err(message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, status="error", error=message)


# ---------------------------------------------------------------------------
# Argument validation (parse-don't-validate; early exit)
# ---------------------------------------------------------------------------


def _validate_args(
    action: str,
    sys_id: str,
    item_sys_id: str,
    catalog_sys_id: str,
) -> str | None:
    """Return error envelope if ``action`` / argument combination is invalid."""
    if action not in _VALID_ACTIONS:
        return _err(
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
        )

    if action in _SYS_ID_ACTIONS and not sys_id:
        return _err(f"sys_id is required for action={action!r}.")

    if action == "categories_list" and not catalog_sys_id:
        return _err("catalog_sys_id is required for action='categories_list'.")

    if action in {"order_now", "add_to_cart"} and not item_sys_id:
        return _err(f"item_sys_id is required for action={action!r}.")

    return None


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``service_catalog`` tool."""
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def service_catalog(
        action: str,
        sys_id: str = "",
        item_sys_id: str = "",
        catalog_sys_id: str = "",
        catalog: str | None = None,
        category: str | None = None,
        text: str | None = None,
        variables: str = "",
        limit: int = 20,
        offset: int = 0,
        top_level_only: bool = False,
    ) -> str:
        """Service Catalog operations. Dispatch on ``action``.

        Args:
            action: One of: catalogs_list, catalog_get, categories_list, category_get,
                items_list, item_get, item_variables, order_now, add_to_cart,
                cart_get, cart_submit, cart_checkout.
            sys_id: Record sys_id (catalog_get, category_get, item_get, item_variables).
            item_sys_id: Catalog item sys_id (order_now, add_to_cart).
            catalog_sys_id: Catalog sys_id (categories_list).
            catalog: Filter by catalog sys_id (items_list).
            category: Filter by category sys_id (items_list).
            text: Search text (catalogs_list, items_list).
            variables: JSON object of variable name/value pairs (order_now, add_to_cart).
            limit: Max results (catalogs_list, categories_list, items_list). Default 20.
            offset: Pagination offset (categories_list, items_list). Default 0.
            top_level_only: Return only top-level categories (categories_list).
        """
        # --- 1. Argument validation (early exit) -------------------------
        err = _validate_args(action, sys_id, item_sys_id, catalog_sys_id)
        if err:
            return err

        # --- 2. Identifier-shape validation for sys_id-only actions ------
        # (validate_sys_id raises ValueError → @tool_handler converts to envelope)
        if action in _SYS_ID_ACTIONS:
            validate_sys_id(sys_id)

        # --- 3. Per-action policy gating + payload parsing ---------------
        parsed_vars: dict[str, Any] | None = None

        if action == "order_now":
            validate_sys_id(item_sys_id)
            blocked = gate_write("sc_req_item", settings)
            if blocked:
                return blocked
            if variables:
                parsed = parse_payload_json(
                    variables,
                    field_name="variables",
                    validate_keys=False,
                )
                if isinstance(parsed, str):
                    return parsed
                parsed_vars = parsed

        elif action == "add_to_cart":
            validate_sys_id(item_sys_id)
            blocked = gate_write("sc_cart_item", settings)
            if blocked:
                return blocked
            if variables:
                parsed = parse_payload_json(
                    variables,
                    field_name="variables",
                    validate_keys=False,
                )
                if isinstance(parsed, str):
                    return parsed
                parsed_vars = parsed

        elif action in {"cart_submit", "cart_checkout"}:
            blocked = gate_write("sc_request", settings)
            if blocked:
                return blocked

        elif action == "categories_list":
            validate_sys_id(catalog_sys_id)

        # --- 4. Dispatch -------------------------------------------------
        async with client_factory() as client:
            if action == "catalogs_list":
                result = await client.sc_get_catalogs(limit=limit, text=text)
            elif action == "catalog_get":
                result = await client.sc_get_catalog(sys_id)
            elif action == "categories_list":
                result = await client.sc_get_catalog_categories(
                    catalog_sys_id=catalog_sys_id,
                    limit=limit,
                    offset=offset,
                    top_level_only=top_level_only,
                )
            elif action == "category_get":
                result = await client.sc_get_category(sys_id)
            elif action == "items_list":
                result = await client.sc_get_items(
                    limit=limit, offset=offset, text=text, catalog=catalog, category=category
                )
            elif action == "item_get":
                result = await client.sc_get_item(sys_id)
            elif action == "item_variables":
                result = await client.sc_get_item_variables(sys_id)
            elif action == "order_now":
                result = await client.sc_order_now(item_sys_id, variables=parsed_vars)
            elif action == "add_to_cart":
                result = await client.sc_add_to_cart(item_sys_id, variables=parsed_vars)
            elif action == "cart_get":
                result = await client.sc_get_cart()
            elif action == "cart_submit":
                result = await client.sc_submit_order()
            else:
                # cart_checkout is the only remaining validated action.
                result = await client.sc_checkout()
            return format_response(data=result)
