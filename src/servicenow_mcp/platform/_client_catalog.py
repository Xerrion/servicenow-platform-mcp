"""Service Catalog API operations."""

from typing import TYPE_CHECKING, Any

from servicenow_mcp._json import dump_request_body
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform._client_transport import ServiceNowRequestClient

if TYPE_CHECKING:
    import httpx2


class ServiceCatalogApiClient(ServiceNowRequestClient):
    """Implement Service Catalog browsing, cart, and order operations."""

    def _sc_url(self, *segments: str, api_version: str | None = None) -> str:
        """Build a Service Catalog API URL."""
        version = f"{api_version}/" if api_version else ""
        return f"{self._settings.servicenow_instance_url}/api/sn_sc/{version}servicecatalog/{'/'.join(segments)}"

    async def sc_get_catalogs(
        self,
        limit: int | None = None,
        text: str | None = None,
        *,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        """Retrieve catalogs available to the current user."""
        params: dict[str, str] = {}
        if text:
            params["sysparm_text"] = text
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        return await self._sc_get(
            "catalogs", params=params, view=view, api_version=api_version
        )

    async def sc_get_catalog(
        self, sys_id: str, *, view: str | None = None, api_version: str | None = None
    ) -> Any:
        """Retrieve a specific catalog."""
        return await self._sc_get(
            "catalogs", sys_id, view=view, api_version=api_version
        )

    async def sc_get_catalog_categories(
        self,
        catalog_sys_id: str,
        limit: int | None = None,
        offset: int | None = None,
        top_level_only: bool = False,
        *,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        """Retrieve categories for a catalog."""
        params: dict[str, str] = {}
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if top_level_only:
            params["sysparm_top_level_only"] = "true"
        return await self._sc_get(
            "catalogs",
            catalog_sys_id,
            "categories",
            params=params,
            view=view,
            api_version=api_version,
        )

    async def sc_get_category(
        self, sys_id: str, *, view: str | None = None, api_version: str | None = None
    ) -> Any:
        """Retrieve a specific category."""
        return await self._sc_get(
            "categories", sys_id, view=view, api_version=api_version
        )

    async def sc_get_items(
        self,
        limit: int | None = None,
        offset: int | None = None,
        text: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
        *,
        item_type: str | None = None,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        """Retrieve catalog items."""
        params: dict[str, str] = {}
        if text:
            params["sysparm_text"] = text
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if catalog:
            params["sysparm_catalog"] = catalog
        if category:
            params["sysparm_category"] = category
        if item_type:
            params["sysparm_type"] = item_type
        return await self._sc_get(
            "items", params=params, view=view, api_version=api_version
        )

    async def sc_get_item(
        self, sys_id: str, *, view: str | None = None, api_version: str | None = None
    ) -> Any:
        """Retrieve a specific catalog item."""
        return await self._sc_get("items", sys_id, view=view, api_version=api_version)

    async def sc_get_item_variables(
        self,
        sys_id: str,
        *,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        """Retrieve variables for a catalog item."""
        return await self._sc_get(
            "items", sys_id, "variables", view=view, api_version=api_version
        )

    async def sc_order_now(
        self, item_sys_id: str, variables: dict[str, Any] | None = None
    ) -> Any:
        """Order a catalog item without using the cart."""
        return await self._sc_post(
            "items", item_sys_id, "order_now", body=self._sc_item_body(variables)
        )

    async def sc_add_to_cart(
        self, item_sys_id: str, variables: dict[str, Any] | None = None
    ) -> Any:
        """Add a catalog item to the cart."""
        return await self._sc_post(
            "items", item_sys_id, "add_to_cart", body=self._sc_item_body(variables)
        )

    async def sc_get_cart(self, *, api_version: str | None = None) -> Any:
        """Retrieve the current user's cart."""
        return await self._sc_get("cart", api_version=api_version)

    async def sc_get_delivery_address(
        self, user_sys_id: str, *, api_version: str | None = None
    ) -> Any:
        return await self._sc_get(
            "cart", "delivery_address", user_sys_id, api_version=api_version
        )

    async def sc_get_delegation(
        self, item_sys_id: str, user_sys_id: str, *, api_version: str | None = None
    ) -> Any:
        return await self._sc_get(
            "items", item_sys_id, "delegation", user_sys_id, api_version=api_version
        )

    async def sc_get_wishlist(
        self, cart_item_id: str | None = None, *, api_version: str | None = None
    ) -> Any:
        segments = ("wishlist", cart_item_id) if cart_item_id else ("wishlist",)
        return await self._sc_get(*segments, api_version=api_version)

    async def sc_get_invalid_delegated_users(
        self,
        item_sys_id: str,
        users: list[str],
        *,
        api_version: str | None = None,
    ) -> Any:
        return await self._sc_post(
            "items",
            item_sys_id,
            "get_invalid_delegated_users",
            body={"sysparm_also_request_for": ",".join(users)},
            api_version=api_version,
        )

    async def sc_variable_display_value(
        self, sys_id: str, value: str, *, api_version: str | None = None
    ) -> Any:
        return await self._sc_post(
            "variables",
            sys_id,
            "display_value",
            body={"sysparm_value": value},
            api_version=api_version,
        )

    async def sc_mutate(
        self,
        method: str,
        *segments: str,
        body: dict[str, Any] | None = None,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        """Send one staged request. A DELETE succeeds only with the documented 204 response."""
        response = await self._ensure_client().request(
            method,
            self._sc_url(*segments, api_version=api_version),
            headers={**await self._headers(), "Content-Type": "application/json"},
            params={"sysparm_view": view} if view else None,
            content=dump_request_body(body) if body is not None else None,
        )
        self._sc_raise_for_status(response)
        if method == "DELETE":
            if response.status_code == 204:
                return {"deleted": True}
            raise ServiceNowMCPError(
                "Catalog deletion did not return HTTP 204. Remote outcome is unknown. Check ServiceNow before "
                "another preview.",
            )
        return self._extract_json_result(response)

    async def sc_submit_order(self) -> Any:
        """Submit the current cart as an order."""
        return await self._sc_post("cart", "submit_order", body={})

    async def sc_checkout(self) -> Any:
        """Check out the current cart."""
        return await self._sc_post("cart", "checkout", body={})

    @staticmethod
    def _sc_item_body(variables: dict[str, Any] | None) -> dict[str, Any]:
        if not variables:
            return {}
        return {"sysparm_quantity": "1", "variables": variables}

    async def _sc_get(
        self,
        *segments: str,
        params: dict[str, str] | None = None,
        view: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        params = dict(params or {})
        if view is not None:
            params["sysparm_view"] = view
        response = await self._ensure_client().get(
            self._sc_url(*segments, api_version=api_version),
            headers=await self._headers(),
            params=params,
        )
        self._sc_raise_for_status(response)
        return self._extract_json_result(response)

    async def _sc_post(
        self, *segments: str, body: dict[str, Any], api_version: str | None = None
    ) -> Any:
        return await self.sc_mutate(
            "POST", *segments, body=body, api_version=api_version
        )

    def _sc_raise_for_status(self, response: httpx2.Response) -> None:
        # Catalog errors can echo submitted variables, addresses, or producer script data.
        try:
            self._raise_for_status(response)
        except ServiceNowMCPError as error:
            error.args = (
                (
                    f"Service Catalog request failed (HTTP {response.status_code}). Check access and inputs. "
                    "For changes, the remote outcome is unknown. Check ServiceNow before another preview. The "
                    "request was not replayed."
                ),
            )
            raise
