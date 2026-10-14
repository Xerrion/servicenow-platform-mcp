"""Native preview and apply tools for every mutating capability."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import JsonValue

from servicenow_mcp.capabilities.attachments.common import (
    MAX_ATTACHMENT_BYTES,
    decode_content_base64,
    ensure_attachment_size_within_limit,
)
from servicenow_mcp.capabilities.catalog.contracts import CatalogMutationAction, CatalogView
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator
from servicenow_mcp.capabilities.mutations.models import AttachmentCommand, CatalogCommand, RecordCommand
from servicenow_mcp.mcp.handlers import tool_handler


PREVIEW_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)


def register_tools(mcp: MCPServer, coordinator: MutationCoordinator) -> None:
    if "records" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def record_preview(
            action: Literal["create", "update", "delete"],
            table: str,
            sys_id: str | None = None,
            data: dict[str, JsonValue] | None = None,
        ) -> CapabilityResult:
            """Preview a record mutation without changing ServiceNow.

            create requires data and forbids sys_id. update requires both. delete requires
            sys_id and forbids data. data is a native field/value object, limited to 256 KiB
            and depth 32. It can contain complete script or XML strings.
            Dictionary metadata checks inherited mandatory and XML fields.
            The result contains a masked summary, expiry, and a single-use preview_token.
            Use mutation_apply to execute. This token does not prove human approval.
            """
            return await coordinator.preview(RecordCommand(action=action, table=table, sys_id=sys_id, data=data))

    if "attachments" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def attachment_preview(
            action: Literal["upload", "delete"],
            table: str | None = None,
            table_sys_id: str | None = None,
            file_name: str | None = None,
            content_base64: str | None = None,
            content_type: str | None = "application/octet-stream",
            sys_id: str | None = None,
        ) -> CapabilityResult:
            """Preview an attachment upload or deletion without changing ServiceNow.

            upload requires table, table_sys_id, file_name, and content_base64.
            Decoded content is limited to 10 MiB. content_type defaults to application/octet-stream.
            delete requires only sys_id. Ownership comes from current attachment metadata.
            The result describes metadata, size, and digest, never file contents.
            Apply its single-use preview_token with mutation_apply before expiry.
            """
            if content_base64 is not None and len(content_base64) > 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3):
                raise ValueError("Attachment exceeds the maximum supported size of 10 MiB.")
            content = decode_content_base64(content_base64) if content_base64 is not None else None
            if content is not None:
                ensure_attachment_size_within_limit(content, operation="upload")
            command = AttachmentCommand(
                action=action,
                table=table,
                table_sys_id=table_sys_id,
                file_name=file_name,
                content=content,
                content_type=content_type or "application/octet-stream",
                sys_id=sys_id,
            )
            return await coordinator.preview(command)

    if "catalog" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def service_catalog_preview(
            action: CatalogMutationAction,
            item_sys_id: str | None = None,
            variables: dict[str, JsonValue] | None = None,
            quantity: int | None = None,
            requested_for: str | None = None,
            also_request_for: list[str] | None = None,
            cart_item_id: str | None = None,
            cart_sys_id: str | None = None,
            items: list[dict[str, JsonValue]] | None = None,
            view: CatalogView | None = None,
            api_version: str | None = None,
        ) -> CapabilityResult:
            """Preview a catalog or cart mutation using read endpoints only.

            order_now and add_to_cart require item_sys_id. They accept variables, quantity,
            requested_for, and also_request_for (1-50 user sys_ids).
            submit_producer requires item_sys_id and accepts variables and view. It creates a record
            through the producer endpoint during apply. Preview never executes producer scripts.
            add_to_wishlist requires item_sys_id and accepts quantity.
            submit_guide requires item_sys_id and accepts variables.
            checkout_guide requires item_sys_id and items: [{sys_id, sysparm_quantity, variables}].
            Each guide quantity is a positive integer string. variables is a native object.
            cart_item_update requires cart_item_id and quantity. It accepts variables.
            cart_item_delete requires cart_item_id. cart_empty requires cart_sys_id.
            cart_empty can delete the cart and its contents, subject to ServiceNow roles.
            cart_submit and cart_checkout accept no item or variables.
            quantity is a non-negative integer. api_version optionally selects a version such as v1.
            Preview cannot simulate fulfillment, producer routing, or guarantee prices.
            Apply the single-use preview_token with mutation_apply. Detectable state changes require a new preview.
            """
            return await coordinator.preview(
                CatalogCommand(
                    action=action,
                    item_sys_id=item_sys_id,
                    variables=variables,
                    quantity=quantity,
                    requested_for=requested_for,
                    also_request_for=also_request_for,
                    cart_item_id=cart_item_id,
                    cart_sys_id=cart_sys_id,
                    items=items,
                    view=view,
                    api_version=api_version,
                )
            )

    @mcp.tool(annotations=APPLY_ANNOTATIONS)
    @tool_handler
    async def mutation_apply(preview_token: str) -> CapabilityResult:
        """Apply one previously reviewed record, attachment, or catalog preview.

        preview_token is single-use and expires after 300 seconds. It is consumed on success,
        rejection, cancellation, or failure. Apply rechecks policy, connection, authorization,
        and detectable state changes. No mutation is replayed automatically.
        If the remote outcome is unknown, check ServiceNow before creating another preview.
        """
        return await coordinator.apply(preview_token)
