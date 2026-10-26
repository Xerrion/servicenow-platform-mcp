"""Immutable commands and reviewable change summaries."""

import hashlib
import json
import re
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from servicenow_mcp.capabilities.catalog.contracts import (
    CatalogChangeAction,
    CatalogView,
    validate_api_version,
    validate_users,
)
from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.validation import validate_identifier, validate_sys_id


class Command(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )


class RecordCommand(Command):
    family: Literal["records"] = "records"
    action: Literal["create", "update", "delete"]
    table: str
    sys_id: str | None = None
    data: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_action(self) -> Self:
        validate_identifier(self.table)
        if self.action == "create":
            if self.sys_id is not None or self.data is None:
                raise CapabilityError(
                    "create requires data and does not accept sys_id."
                )
        elif self.sys_id is None:
            raise CapabilityError("update and delete require sys_id.")
        else:
            try:
                validate_sys_id(self.sys_id)
            except ValueError:
                raise CapabilityError(
                    "sys_id must contain 32 hexadecimal characters."
                ) from None
        if self.action == "update" and self.data is None:
            raise CapabilityError("update requires data.")
        if self.action == "delete" and self.data is not None:
            raise CapabilityError("delete does not accept data.")
        if self.data is not None:
            validate_payload(self.data, field_name="data")
        return self


class AttachmentCommand(Command):
    family: Literal["attachments"] = "attachments"
    action: Literal["upload", "delete"]
    table: str | None = None
    table_sys_id: str | None = None
    sys_id: str | None = None
    file_name: str | None = None
    content_type: str = "application/octet-stream"
    content: bytes | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.action == "upload":
            if (
                not self.table
                or not self.table_sys_id
                or not self.file_name
                or self.content is None
            ):
                raise CapabilityError(
                    "upload requires table, table_sys_id, file_name, and content."
                )
            if self.sys_id is not None:
                raise CapabilityError("upload does not accept sys_id.")
            validate_identifier(self.table)
            validate_sys_id(self.table_sys_id)
        else:
            if not self.sys_id:
                raise CapabilityError("delete requires sys_id.")
            validate_sys_id(self.sys_id)
            if any(
                value is not None
                for value in (
                    self.table,
                    self.table_sys_id,
                    self.file_name,
                    self.content,
                )
            ):
                raise CapabilityError("delete accepts only sys_id.")
        return self


class CatalogCommand(Command):
    family: Literal["catalog"] = "catalog"
    action: CatalogChangeAction
    item_sys_id: str | None = None
    variables: dict[str, JsonValue] | None = Field(default=None, repr=False)
    quantity: int | None = Field(default=None, strict=True, ge=0)
    requested_for: str | None = None
    also_request_for: list[str] | None = Field(default=None, repr=False)
    cart_item_id: str | None = None
    cart_sys_id: str | None = None
    items: list[dict[str, JsonValue]] | None = Field(default=None, repr=False)
    view: CatalogView | None = None
    api_version: str | None = None

    @model_validator(mode="after")
    def check_action(self) -> Self:
        validate_api_version(self.api_version)
        item_actions = {
            "order_now",
            "add_to_cart",
            "submit_producer",
            "add_to_wishlist",
            "checkout_guide",
            "submit_guide",
        }
        allowed = {"api_version"}
        if self.action in item_actions:
            allowed.add("item_sys_id")
            if not self.item_sys_id:
                raise CapabilityError("This action requires item_sys_id.")
        if self.action in {"order_now", "add_to_cart", "cart_item_update"}:
            allowed.update({"variables", "quantity"})
        if self.action in {"order_now", "add_to_cart"}:
            allowed.update({"requested_for", "also_request_for"})
        if self.action in {"submit_producer", "submit_guide"}:
            allowed.add("variables")
        if self.action == "submit_producer":
            allowed.add("view")
        if self.action == "add_to_wishlist":
            allowed.add("quantity")
        if self.action == "checkout_guide":
            allowed.add("items")
            if self.items is None:
                raise CapabilityError("checkout_guide requires items.")
        if self.action in {"cart_item_update", "cart_item_delete"}:
            allowed.add("cart_item_id")
            if self.cart_item_id is None:
                raise CapabilityError("This action requires cart_item_id.")
        if self.action == "cart_item_update" and self.quantity is None:
            raise CapabilityError("cart_item_update requires quantity.")
        if self.action == "cart_empty":
            allowed.add("cart_sys_id")
            if self.cart_sys_id is None:
                raise CapabilityError("cart_empty requires cart_sys_id.")
        for name, value in self.model_dump(exclude={"family", "action"}).items():
            if value is not None and name not in allowed:
                raise CapabilityError(f"{name} is not accepted by this catalog action.")
        for value in (
            self.item_sys_id,
            self.cart_item_id,
            self.cart_sys_id,
            self.requested_for,
        ):
            if value is not None:
                validate_sys_id(value)
        validate_users(self.also_request_for, max_users=50)
        if self.variables is not None:
            validate_payload(
                self.variables, field_name="variables", validate_keys=False
            )
        if self.items is not None:
            validate_payload(
                {"items": self.items}, field_name="items", validate_keys=False
            )
            for item in self.items:
                item_id = item.get("sys_id")
                if set(item) - {
                    "sys_id",
                    "sysparm_quantity",
                    "variables",
                } or not isinstance(item_id, str):
                    raise CapabilityError(
                        "Each guide item requires sys_id and accepts only sysparm_quantity and variables.",
                    )
                validate_sys_id(item_id)
                quantity = item.get("sysparm_quantity", "1")
                if (
                    not isinstance(quantity, str)
                    or not re.fullmatch(r"[0-9]+", quantity)
                    or int(quantity) <= 0
                ):
                    raise CapabilityError(
                        "Guide item sysparm_quantity must be a positive integer string."
                    )
                if "variables" in item and not isinstance(item["variables"], dict):
                    raise CapabilityError("Guide item variables must be an object.")
        return self


ChangeCommand = Annotated[
    RecordCommand | AttachmentCommand | CatalogCommand,
    Field(discriminator="family"),
]


class StagedChange(Command):
    command: ChangeCommand = Field(repr=False)
    instance: str
    authorization_generation: int
    fingerprint: str | None = None
    owning_table: str | None = None


class ChangePreview(BaseModel):
    family: Literal["records", "attachments", "catalog"]
    action: str
    preview_token: str
    expires_in_seconds: int
    summary: dict[str, JsonValue]
    limitations: list[str]


def fingerprint(value: JsonValue) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    except TypeError, ValueError, UnicodeError:
        raise CapabilityError(
            "Cannot establish the current state for this change."
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def retained_bytes(staged: StagedChange) -> int:
    command = staged.command
    payload = staged.model_dump(mode="python", exclude={"command"})
    payload["command"] = command.model_dump(mode="python", exclude={"content"})
    return len(
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ) + (len(command.content or b"") if isinstance(command, AttachmentCommand) else 0)
