"""Native preview and apply tools for every mutating capability."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations
from pydantic import JsonValue

from servicenow_mcp.capabilities.attachments.common import (
    MAX_ATTACHMENT_BYTES,
    decode_content_base64,
    ensure_attachment_size_within_limit,
)
from servicenow_mcp.capabilities.catalog.contracts import (
    CatalogMutationAction,
    CatalogView,
)
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.mutations.models import (
    AttachmentCommand,
    CatalogCommand,
    RecordCommand,
)
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator


PREVIEW_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_tools(mcp: MCPServer, coordinator: MutationCoordinator) -> None:
    if "records" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @describe_tool_parameters(
            {
                "action": "Choose create, update, or delete.",
                "table": "Target ServiceNow table.",
                "sys_id": "Record sys_id for update/delete; omit for create.",
                "data": "Native field/value object for create/update; include mandatory fields.",
            }
        )
        @tool_handler
        async def record_preview(
            action: Literal["create", "update", "delete"],
            table: str,
            sys_id: str | None = None,
            data: dict[str, JsonValue] | None = None,
        ) -> CapabilityResult:
            """Preview a record create, update, or delete without writing it. Review the summary and call mutation_apply with the returned preview_token to perform the change."""
            return await coordinator.preview(
                RecordCommand(action=action, table=table, sys_id=sys_id, data=data)
            )

    if "attachments" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @describe_tool_parameters(
            {
                "action": "Choose upload or delete.",
                "table": "Parent table for upload.",
                "table_sys_id": "Parent record sys_id for upload.",
                "file_name": "Attachment file name for upload.",
                "content_base64": "Actual file bytes encoded as base64; upload only, maximum 10 MiB decoded.",
                "content_type": "Upload MIME type; defaults to application/octet-stream.",
                "sys_id": "Attachment sys_id for delete.",
            }
        )
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
            """Preview an attachment upload or deletion without changing ServiceNow. Review its metadata and call mutation_apply with the returned preview_token to execute."""
            if content_base64 is not None and len(content_base64) > 4 * (
                (MAX_ATTACHMENT_BYTES + 2) // 3
            ):
                raise ValueError(
                    "Attachment exceeds the maximum supported size of 10 MiB."
                )
            content = (
                decode_content_base64(content_base64)
                if content_base64 is not None
                else None
            )
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
        @describe_tool_parameters(
            {
                "action": "Choose an order, producer, guide, wishlist, or cart operation.",
                "item_sys_id": "Catalog item sys_id for item and guide operations.",
                "variables": "Native object mapping catalog variable names to values.",
                "quantity": "Non-negative item quantity.",
                "requested_for": "User sys_id for whom to place the request.",
                "also_request_for": "Additional user sys_id array; 1-50 entries.",
                "cart_item_id": "Cart item sys_id for update or delete.",
                "cart_sys_id": "Cart sys_id for cart_empty.",
                "items": "Guide checkout items; each has sys_id, sysparm_quantity, and variables.",
                "view": "Optional catalog view: desktop, mobile, or both.",
                "api_version": {
                    "description": "Optional endpoint version, such as v1.",
                    "pattern": r"^v[1-9][0-9]*$",
                },
            }
        )
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
            """Preview a catalog or cart change without submitting it. Read item variables or cart state first, then call mutation_apply with the reviewed preview_token."""
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
                ),
            )

    @mcp.tool(annotations=APPLY_ANNOTATIONS)
    @describe_tool_parameters(
        {
            "preview_token": "Exact token returned by a preview tool; expires after 300 seconds and is single-use.",
        }
    )
    @tool_handler
    async def mutation_apply(preview_token: str) -> CapabilityResult:
        """Apply a reviewed record, attachment, or catalog change using its exact preview_token. Tokens expire after 300 seconds and are single-use, including after failure."""
        return await coordinator.apply(preview_token)
