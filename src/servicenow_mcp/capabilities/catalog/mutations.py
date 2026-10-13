"""Catalog previews observe existing state without changing an order or cart."""

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import CatalogCommand, fingerprint
from servicenow_mcp.platform.client import ServiceNowClient
from servicenow_mcp.policy import mask_sensitive_fields


def target_table(command: CatalogCommand) -> str:
    if command.action == "order_now":
        return "sc_req_item"
    return "sc_cart_item" if command.action == "add_to_cart" else "sc_request"


async def preview(command: CatalogCommand, client: ServiceNowClient) -> tuple[dict, str]:
    if command.action in {"order_now", "add_to_cart"}:
        item = await client.sc_get_item(command.item_sys_id or "")
        definitions = await client.sc_get_item_variables(command.item_sys_id or "")
        observed = {"item": item, "definitions": definitions}
        summary = {"item_sys_id": command.item_sys_id, **observed, "variables": command.variables}
    else:
        observed = await client.sc_get_cart()
        summary = {"cart": observed}
    return mask_sensitive_fields(summary), fingerprint(observed)


async def apply(command: CatalogCommand, expected: str | None, client: ServiceNowClient):
    _, current = await preview(command, client)
    if current != expected:
        raise CapabilityError("Catalog or cart state changed since preview. Create a new preview.", "STALE_PREVIEW")
    if command.action == "order_now":
        value = await client.sc_order_now(command.item_sys_id or "", variables=command.variables)
    elif command.action == "add_to_cart":
        value = await client.sc_add_to_cart(command.item_sys_id or "", variables=command.variables)
    elif command.action == "cart_submit":
        value = await client.sc_submit_order()
    else:
        value = await client.sc_checkout()
    return mask_sensitive_fields({"value": value})["value"]
