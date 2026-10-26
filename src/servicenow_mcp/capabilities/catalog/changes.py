"""Catalog previews use GETs only. Apply sends exactly one staged change."""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.catalog.masking import mask_catalog
from servicenow_mcp.capabilities.changes.models import CatalogCommand, fingerprint
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import (
    resolve_ref_value,
    validate_identifier,
    validate_sys_id,
)

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


def target_table(command: CatalogCommand) -> str:
    if command.action == "order_now":
        return "sc_req_item"
    if command.action == "submit_producer":
        return "sc_cat_item_producer"
    if command.action == "cart_empty":
        return "sc_cart"
    if command.action in {
        "add_to_cart",
        "add_to_wishlist",
        "cart_item_update",
        "cart_item_delete",
    }:
        return "sc_cart_item"
    return "sc_request"


async def producer_state(
    command: CatalogCommand, client: ServiceNowClient
) -> dict[str, Any]:
    metadata = await client.get_record(
        "sc_cat_item_producer",
        command.item_sys_id or "",
        fields=[
            "sys_id",
            "table_name",
            "sys_updated_on",
            "script",
            "post_insert_script",
            "template",
        ],
    )
    table = metadata.get("table_name")
    if not isinstance(table, str) or not table:
        raise CapabilityError(
            "Cannot establish the producer target table. Check access to producer metadata."
        )
    validate_identifier(table)
    return metadata


async def preview(
    command: CatalogCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str]:
    try:
        return await _preview(command, client)
    except ServiceNowMCPError as error:
        # Metadata Table API errors can echo producer content too.
        error.args = ("Cannot read catalog state. Check ServiceNow access and inputs.",)
        raise


async def _preview(
    command: CatalogCommand, client: ServiceNowClient
) -> tuple[dict[str, Any], str]:
    observed: dict[str, Any] = {}
    if command.item_sys_id:
        observed["item"] = await client.sc_get_item(
            command.item_sys_id, api_version=command.api_version
        )
        observed["definitions"] = await client.sc_get_item_variables(
            command.item_sys_id,
            api_version=command.api_version,
        )
    if command.action == "submit_producer":
        observed["producer"] = await producer_state(command, client)
    if command.action in {
        "cart_submit",
        "cart_checkout",
        "cart_item_update",
        "cart_item_delete",
        "cart_empty",
        "checkout_guide",
    }:
        observed["cart"] = await client.sc_get_cart(api_version=command.api_version)
    if command.action == "add_to_wishlist":
        observed["wishlist"] = await client.sc_get_wishlist(
            api_version=command.api_version
        )
    if command.action in {"cart_item_update", "cart_item_delete"}:
        observed["cart_item"] = await client.get_record(
            "sc_cart_item",
            command.cart_item_id or "",
            fields=["sys_id", "sys_updated_on", "quantity", "cat_item", "cart"],
        )
        if command.variables is not None:
            item_id = resolve_ref_value(observed["cart_item"].get("cat_item"))
            if item_id:
                validate_sys_id(item_id)
                observed["definitions"] = await client.sc_get_item_variables(
                    item_id, api_version=command.api_version
                )
    if command.action == "cart_empty":
        # The REST API can delete another user's cart. Observe that exact cart as well.
        observed["target_cart"] = await client.get_record(
            "sc_cart",
            command.cart_sys_id or "",
            fields=["sys_id", "sys_updated_on"],
        )
        observed["target_items"] = await client.query_records(
            "sc_cart_item",
            query=ServiceNowQuery().equals("cart", command.cart_sys_id or "").build(),
            fields=["sys_id", "sys_updated_on"],
            limit=1000,
            order_by="sys_id",
        )
        if len(observed["target_items"]["records"]) >= 1000 or observed["target_items"][
            "count"
        ] > len(
            observed["target_items"]["records"],
        ):
            raise CapabilityError(
                "Cannot fully observe this cart. Reduce its contents before preview."
            )
    if command.action == "checkout_guide":
        observed["guide_items"] = [
            {
                "item": await client.sc_get_item(
                    str(item["sys_id"]), api_version=command.api_version
                ),
                "definitions": await client.sc_get_item_variables(
                    str(item["sys_id"]), api_version=command.api_version
                ),
            }
            for item in command.items or []
        ]
    summary = command.model_dump(exclude={"family", "action"}, exclude_none=True)
    if (
        command.action in {"order_now", "add_to_cart", "add_to_wishlist"}
        and command.quantity is None
    ):
        _, _, body = request(command)
        if body and "sysparm_quantity" in body:
            summary["quantity"] = 1
    if command.action in {"order_now", "add_to_cart"}:
        summary["variables"] = command.variables
    summary.update({key: value for key, value in observed.items() if key != "producer"})
    if "producer" in observed:
        summary["target_table"] = observed["producer"]["table_name"]
    return _mask_output(summary, command, observed), fingerprint(observed)


def _mask_output(value: Any, command: CatalogCommand, observed: dict[str, Any]) -> Any:
    definitions_by_item = {}
    definitions = [
        observed.get("definitions"),
        observed.get("item", {}).get("variables"),
    ]
    if command.item_sys_id:
        definitions_by_item[command.item_sys_id] = definitions
    cart_item_id = resolve_ref_value(observed.get("cart_item", {}).get("cat_item"))
    if cart_item_id:
        definitions_by_item[cart_item_id] = definitions
    for item, snapshot in zip(
        command.items or [], observed.get("guide_items", []), strict=True
    ):
        item_id = str(item["sys_id"])
        definitions_by_item[item_id] = [
            definitions_by_item.get(item_id),
            snapshot["definitions"],
            snapshot["item"].get("variables"),
        ]
    return mask_catalog(
        value, definitions=definitions, definitions_by_item=definitions_by_item
    )


def request(
    command: CatalogCommand,
) -> tuple[str, tuple[str, ...], dict[str, Any] | None]:
    action = command.action
    if action in {"cart_submit", "cart_checkout"}:
        return (
            "POST",
            ("cart", "submit_order" if action == "cart_submit" else "checkout"),
            {},
        )
    if action in {"cart_item_delete", "cart_empty"}:
        segments = (
            ("cart", command.cart_item_id or "")
            if action == "cart_item_delete"
            else ("cart", command.cart_sys_id or "", "empty")
        )
        return "DELETE", segments, None
    if action == "checkout_guide":
        body: dict[str, Any] = {"items": command.items}
    elif action in {"submit_producer", "submit_guide"}:
        body = {"variables": command.variables or {}}
    else:
        body = {}
        # Keep the legacy empty order payload, but support explicit quantity without variables.
        if (
            command.quantity is not None
            or command.variables
            or command.requested_for
            or command.also_request_for
            or action in {"add_to_wishlist", "cart_item_update"}
        ):
            body["sysparm_quantity"] = str(
                command.quantity if command.quantity is not None else 1
            )
        if command.variables or (
            command.variables is not None
            and (command.quantity is not None or action == "cart_item_update")
        ):
            body["variables"] = command.variables
        if command.requested_for is not None:
            body["sysparm_requested_for"] = command.requested_for
        if command.also_request_for is not None:
            body["sysparm_also_request_for"] = ",".join(command.also_request_for)
    if action == "cart_item_update":
        return "PUT", ("cart", command.cart_item_id or ""), body
    return (
        "PUT" if action == "submit_guide" else "POST",
        ("items", command.item_sys_id or "", action),
        body,
    )


async def apply(
    command: CatalogCommand, expected: str | None, client: ServiceNowClient
):
    summary, current = await preview(command, client)
    if current != expected:
        raise CapabilityError(
            "Catalog or cart state changed since preview. Create a new preview.",
            "STALE_PREVIEW",
        )
    method, segments, body = request(command)
    value = await client.sc_mutate(
        method, *segments, body=body, view=command.view, api_version=command.api_version
    )
    if isinstance(value, dict) and ("errCode" in value or "error" in value):
        raise CapabilityError(
            "Service Catalog rejected the request. Check access and inputs before another preview.",
            "CATALOG_REJECTED",
        )
    if command.action == "submit_producer" and (
        not isinstance(value, dict)
        or not isinstance(value.get("sys_id"), str)
        or not value["sys_id"]
        or not isinstance(value.get("table"), str)
        or not value["table"]
    ):
        raise CapabilityError(
            "Producer response did not identify the created record. Remote outcome is unknown. Check ServiceNow "
            "before another preview.",
            "UNKNOWN_OUTCOME",
        )
    return _mask_output(value, command, summary)
