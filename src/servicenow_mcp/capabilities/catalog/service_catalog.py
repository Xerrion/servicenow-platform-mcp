"""Read-only Service Catalog operations."""

from typing import TYPE_CHECKING

from servicenow_mcp.capabilities.catalog.contracts import (
    CatalogReadAction,
    CatalogView,
    validate_api_version,
    validate_users,
)
from servicenow_mcp.capabilities.catalog.masking import mask_catalog
from servicenow_mcp.capabilities.contracts import (
    CapabilityError,
    CapabilityResult,
    validate_payload,
)
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import MASK_VALUE
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


class ServiceCatalogCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        self.clients = client_factory or (
            lambda: ServiceNowClient(settings, auth_provider)
        )

    async def service_catalog(
        self,
        action: CatalogReadAction,
        sys_id: str | None = None,
        catalog_sys_id: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
        text: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
        top_level_only: bool | None = False,
        view: CatalogView | None = None,
        item_type: str | None = None,
        user_sys_id: str | None = None,
        item_sys_id: str | None = None,
        cart_item_id: str | None = None,
        also_request_for: list[str] | None = None,
        value: str | None = None,
        api_version: str | None = None,
    ) -> CapabilityResult:
        limit = 20 if limit is None else limit
        offset = 0 if offset is None else offset
        if limit <= 0 or offset < 0:
            raise CapabilityError(
                "limit must be positive and offset must be non-negative."
            )
        validate_api_version(api_version)
        if view is not None and action not in {
            "catalogs_list",
            "catalog_get",
            "categories_list",
            "category_get",
            "items_list",
            "item_get",
            "item_variables",
        }:
            raise CapabilityError("view is not accepted by this catalog action.")
        for name, argument, actions in (
            ("item_type", item_type, {"items_list"}),
            ("user_sys_id", user_sys_id, {"delivery_address", "delegation"}),
            ("item_sys_id", item_sys_id, {"delegation", "get_invalid_delegated_users"}),
            ("cart_item_id", cart_item_id, {"wishlist_item_get"}),
            ("also_request_for", also_request_for, {"get_invalid_delegated_users"}),
            ("value", value, {"variable_display_value"}),
        ):
            if argument is not None and action not in actions:
                raise CapabilityError(f"{name} is not accepted by this catalog action.")
        if action in {
            "catalog_get",
            "category_get",
            "item_get",
            "item_variables",
            "variable_display_value",
        }:
            if not sys_id:
                raise CapabilityError("This action requires sys_id.")
            validate_sys_id(sys_id)
        if action == "categories_list":
            if not catalog_sys_id:
                raise CapabilityError("categories_list requires catalog_sys_id.")
            validate_sys_id(catalog_sys_id)
        required = {
            "delivery_address": {"user_sys_id": user_sys_id},
            "delegation": {"item_sys_id": item_sys_id, "user_sys_id": user_sys_id},
            "wishlist_item_get": {"cart_item_id": cart_item_id},
            "get_invalid_delegated_users": {
                "item_sys_id": item_sys_id,
                "also_request_for": also_request_for,
            },
            "variable_display_value": {"value": value},
        }
        for name, argument in required.get(action, {}).items():
            if argument is None:
                raise CapabilityError(f"This action requires {name}.")
        for identifier in (user_sys_id, item_sys_id, cart_item_id):
            if identifier is not None:
                validate_sys_id(identifier)
        validate_users(also_request_for)
        if also_request_for is not None:
            validate_payload({"users": also_request_for}, field_name="also_request_for")
        if action == "variable_display_value" and value is not None:
            validate_sys_id(value)
        async with self.clients() as client:
            match action:
                case "catalogs_list":
                    data = await client.sc_get_catalogs(
                        limit=limit, text=text, view=view, api_version=api_version
                    )
                case "catalog_get":
                    data = await client.sc_get_catalog(
                        sys_id or "", view=view, api_version=api_version
                    )
                case "categories_list":
                    data = await client.sc_get_catalog_categories(
                        catalog_sys_id or "",
                        limit,
                        offset,
                        top_level_only or False,
                        view=view,
                        api_version=api_version,
                    )
                case "category_get":
                    data = await client.sc_get_category(
                        sys_id or "", view=view, api_version=api_version
                    )
                case "items_list":
                    data = await client.sc_get_items(
                        limit,
                        offset,
                        text,
                        catalog,
                        category,
                        item_type=item_type,
                        view=view,
                        api_version=api_version,
                    )
                case "item_get":
                    data = await client.sc_get_item(
                        sys_id or "", view=view, api_version=api_version
                    )
                case "item_variables":
                    data = await client.sc_get_item_variables(
                        sys_id or "", view=view, api_version=api_version
                    )
                case "cart_get":
                    data = await client.sc_get_cart(api_version=api_version)
                case "delivery_address":
                    data = await client.sc_get_delivery_address(
                        user_sys_id or "", api_version=api_version
                    )
                case "delegation":
                    data = await client.sc_get_delegation(
                        item_sys_id or "", user_sys_id or "", api_version=api_version
                    )
                case "wishlist_get" | "wishlist_item_get":
                    data = await client.sc_get_wishlist(
                        cart_item_id if action == "wishlist_item_get" else None,
                        api_version=api_version,
                    )
                case "get_invalid_delegated_users":
                    data = await client.sc_get_invalid_delegated_users(
                        item_sys_id or "",
                        also_request_for or [],
                        api_version=api_version,
                    )
                case "variable_display_value":
                    data = await client.sc_variable_display_value(
                        sys_id or "", value or "", api_version=api_version
                    )
                case _:
                    raise CapabilityError("Unsupported catalog read action.")
        if isinstance(data, dict) and ("errCode" in data or "error" in data):
            raise CapabilityError(
                "Service Catalog rejected the request. Check access and inputs.",
                "CATALOG_REJECTED",
            )
        if action in {"delivery_address", "variable_display_value"}:
            # These endpoints return personal data as unlabelled strings.
            data = MASK_VALUE
        return CapabilityResult(data=mask_catalog(data))
