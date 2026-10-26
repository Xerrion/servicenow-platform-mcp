"""Documented Service Catalog action inputs shared by tools and commands."""

import re
from typing import Literal

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.validation import validate_sys_id

CatalogView = Literal["desktop", "mobile", "both"]
CatalogReadAction = Literal[
    "catalogs_list",
    "catalog_get",
    "categories_list",
    "category_get",
    "items_list",
    "item_get",
    "item_variables",
    "cart_get",
    "delivery_address",
    "delegation",
    "wishlist_get",
    "wishlist_item_get",
    "get_invalid_delegated_users",
    "variable_display_value",
]
CatalogChangeAction = Literal[
    "order_now",
    "add_to_cart",
    "cart_submit",
    "cart_checkout",
    "submit_producer",
    "add_to_wishlist",
    "checkout_guide",
    "submit_guide",
    "cart_item_update",
    "cart_item_delete",
    "cart_empty",
]


def validate_api_version(api_version: str | None) -> None:
    if api_version is not None and not re.fullmatch(r"v[1-9][0-9]*", api_version):
        raise CapabilityError("api_version must be v followed by a positive integer.")


def validate_users(users: list[str] | None, *, max_users: int | None = None) -> None:
    if users is not None:
        if not users:
            raise CapabilityError("also_request_for requires at least one user sys_id.")
        if max_users is not None and len(users) > max_users:
            raise CapabilityError("also_request_for requires 1-50 user sys_ids.")
        for user in users:
            validate_sys_id(user)
