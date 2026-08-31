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

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import gate_write
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.utils import format_response, validate_sys_id


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


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


# ---------------------------------------------------------------------------
# Argument validation (parse-don't-validate; early exit)
# ---------------------------------------------------------------------------


def _validate_args(
    action: str,
    sys_id: str,
    item_sys_id: str,
    catalog_sys_id: str,
    correlation_id: str,
) -> str | None:
    """Return error envelope if ``action`` / argument combination is invalid."""
    if action not in _VALID_ACTIONS:
        return _err(
            correlation_id,
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
        )

    if action in _SYS_ID_ACTIONS and not sys_id:
        return _err(correlation_id, f"sys_id is required for action={action!r}.")

    if action == "categories_list" and not catalog_sys_id:
        return _err(correlation_id, "catalog_sys_id is required for action='categories_list'.")

    if action in {"order_now", "add_to_cart"} and not item_sys_id:
        return _err(correlation_id, f"item_sys_id is required for action={action!r}.")

    return None


# ---------------------------------------------------------------------------
# Per-action execution helpers
# ---------------------------------------------------------------------------


async def _run_catalogs_list(client: ServiceNowClient, limit: int, text: str, correlation_id: str) -> str:
    """Execute ``catalogs_list``: list catalogs visible to the caller."""
    result = await client.sc_get_catalogs(limit=limit, text=text)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_catalog_get(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute ``catalog_get``: fetch one catalog by sys_id."""
    result = await client.sc_get_catalog(sys_id)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_categories_list(
    client: ServiceNowClient,
    catalog_sys_id: str,
    limit: int,
    offset: int,
    top_level_only: bool,
    correlation_id: str,
) -> str:
    """Execute ``categories_list``: list categories of a catalog."""
    result = await client.sc_get_catalog_categories(
        catalog_sys_id=catalog_sys_id,
        limit=limit,
        offset=offset,
        top_level_only=top_level_only,
    )
    return format_response(data=result, correlation_id=correlation_id)


async def _run_category_get(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute ``category_get``: fetch one category by sys_id."""
    result = await client.sc_get_category(sys_id)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_items_list(
    client: ServiceNowClient,
    limit: int,
    offset: int,
    text: str,
    catalog: str,
    category: str,
    correlation_id: str,
) -> str:
    """Execute ``items_list``: list catalog items with optional filters."""
    result = await client.sc_get_items(
        limit=limit,
        offset=offset,
        text=text,
        catalog=catalog,
        category=category,
    )
    return format_response(data=result, correlation_id=correlation_id)


async def _run_item_get(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute ``item_get``: fetch one catalog item by sys_id."""
    result = await client.sc_get_item(sys_id)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_item_variables(client: ServiceNowClient, sys_id: str, correlation_id: str) -> str:
    """Execute ``item_variables``: fetch the form variables for a catalog item."""
    result = await client.sc_get_item_variables(sys_id)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_order_now(
    client: ServiceNowClient,
    item_sys_id: str,
    parsed_vars: dict[str, Any] | None,
    correlation_id: str,
) -> str:
    """Execute ``order_now``: order a catalog item directly, bypassing the cart."""
    result = await client.sc_order_now(item_sys_id, variables=parsed_vars)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_add_to_cart(
    client: ServiceNowClient,
    item_sys_id: str,
    parsed_vars: dict[str, Any] | None,
    correlation_id: str,
) -> str:
    """Execute ``add_to_cart``: add a catalog item to the caller's cart."""
    result = await client.sc_add_to_cart(item_sys_id, variables=parsed_vars)
    return format_response(data=result, correlation_id=correlation_id)


async def _run_cart_get(client: ServiceNowClient, correlation_id: str) -> str:
    """Execute ``cart_get``: retrieve the caller's current cart."""
    result = await client.sc_get_cart()
    return format_response(data=result, correlation_id=correlation_id)


async def _run_cart_submit(client: ServiceNowClient, correlation_id: str) -> str:
    """Execute ``cart_submit``: submit the caller's cart as an order."""
    result = await client.sc_submit_order()
    return format_response(data=result, correlation_id=correlation_id)


async def _run_cart_checkout(client: ServiceNowClient, correlation_id: str) -> str:
    """Execute ``cart_checkout``: two-step checkout for the caller's cart."""
    result = await client.sc_checkout()
    return format_response(data=result, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``service_catalog`` tool.

    ``choices`` is unused here but accepted for unified-loader contract parity.
    """
    del choices, dictionary  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def service_catalog(
        action: str,
        sys_id: str = "",
        item_sys_id: str = "",
        catalog_sys_id: str = "",
        catalog: str = "",
        category: str = "",
        text: str = "",
        variables: str = "",
        limit: int = 20,
        offset: int = 0,
        top_level_only: bool = False,
        *,
        correlation_id: str = "",
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
        err = _validate_args(action, sys_id, item_sys_id, catalog_sys_id, correlation_id)
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
            blocked = gate_write("sc_req_item", settings, correlation_id)
            if blocked:
                return blocked
            if variables:
                parsed = parse_payload_json(
                    variables,
                    field_name="variables",
                    correlation_id=correlation_id,
                    validate_keys=False,
                )
                if isinstance(parsed, str):
                    return parsed
                parsed_vars = parsed

        elif action == "add_to_cart":
            validate_sys_id(item_sys_id)
            blocked = gate_write("sc_cart_item", settings, correlation_id)
            if blocked:
                return blocked
            if variables:
                parsed = parse_payload_json(
                    variables,
                    field_name="variables",
                    correlation_id=correlation_id,
                    validate_keys=False,
                )
                if isinstance(parsed, str):
                    return parsed
                parsed_vars = parsed

        elif action in {"cart_submit", "cart_checkout"}:
            blocked = gate_write("sc_request", settings, correlation_id)
            if blocked:
                return blocked

        elif action == "categories_list":
            validate_sys_id(catalog_sys_id)

        # --- 4. Dispatch -------------------------------------------------
        async with client_factory() as client:
            if action == "catalogs_list":
                return await _run_catalogs_list(client, limit, text, correlation_id)
            if action == "catalog_get":
                return await _run_catalog_get(client, sys_id, correlation_id)
            if action == "categories_list":
                return await _run_categories_list(client, catalog_sys_id, limit, offset, top_level_only, correlation_id)
            if action == "category_get":
                return await _run_category_get(client, sys_id, correlation_id)
            if action == "items_list":
                return await _run_items_list(client, limit, offset, text, catalog, category, correlation_id)
            if action == "item_get":
                return await _run_item_get(client, sys_id, correlation_id)
            if action == "item_variables":
                return await _run_item_variables(client, sys_id, correlation_id)
            if action == "order_now":
                return await _run_order_now(client, item_sys_id, parsed_vars, correlation_id)
            if action == "add_to_cart":
                return await _run_add_to_cart(client, item_sys_id, parsed_vars, correlation_id)
            if action == "cart_get":
                return await _run_cart_get(client, correlation_id)
            if action == "cart_submit":
                return await _run_cart_submit(client, correlation_id)
            # cart_checkout (only remaining action; validated at top)
            return await _run_cart_checkout(client, correlation_id)
