"""Immutable commands and reviewable mutation summaries."""

import hashlib
import json
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from servicenow_mcp.capabilities.contracts import CapabilityError, validate_payload
from servicenow_mcp.validation import validate_identifier, validate_sys_id


class Command(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


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
                raise CapabilityError("create requires data and does not accept sys_id.")
        elif self.sys_id is None:
            raise CapabilityError("update and delete require sys_id.")
        else:
            try:
                validate_sys_id(self.sys_id)
            except ValueError:
                raise CapabilityError("sys_id must contain 32 hexadecimal characters.") from None
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
            if not self.table or not self.table_sys_id or not self.file_name or self.content is None:
                raise CapabilityError("upload requires table, table_sys_id, file_name, and content.")
            if self.sys_id is not None:
                raise CapabilityError("upload does not accept sys_id.")
            validate_identifier(self.table)
            validate_sys_id(self.table_sys_id)
        else:
            if not self.sys_id:
                raise CapabilityError("delete requires sys_id.")
            validate_sys_id(self.sys_id)
            if any(value is not None for value in (self.table, self.table_sys_id, self.file_name, self.content)):
                raise CapabilityError("delete accepts only sys_id.")
        return self


class CatalogCommand(Command):
    family: Literal["catalog"] = "catalog"
    action: Literal["order_now", "add_to_cart", "cart_submit", "cart_checkout"]
    item_sys_id: str | None = None
    variables: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.action in {"order_now", "add_to_cart"}:
            if not self.item_sys_id:
                raise CapabilityError("This action requires item_sys_id.")
            validate_sys_id(self.item_sys_id)
            if self.variables is not None:
                validate_payload(self.variables, field_name="variables", validate_keys=False)
        elif self.item_sys_id is not None or self.variables is not None:
            raise CapabilityError("Cart submit and checkout accept no item or variables.")
        return self


MutationCommand = Annotated[RecordCommand | AttachmentCommand | CatalogCommand, Field(discriminator="family")]


class StagedMutation(Command):
    command: MutationCommand = Field(repr=False)
    instance: str
    authorization_generation: int
    fingerprint: str | None = None
    owning_table: str | None = None


class MutationPreview(BaseModel):
    family: Literal["records", "attachments", "catalog"]
    action: str
    preview_token: str
    expires_in_seconds: int
    summary: dict[str, JsonValue]
    limitations: list[str]


def fingerprint(value: JsonValue) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError):
        raise CapabilityError("Cannot establish the current state for this mutation.") from None
    return hashlib.sha256(encoded).hexdigest()


def retained_bytes(staged: StagedMutation) -> int:
    command = staged.command
    payload = staged.model_dump(mode="python", exclude={"command"})
    payload["command"] = command.model_dump(mode="python", exclude={"content"})
    return len(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) + (
        len(command.content or b"") if isinstance(command, AttachmentCommand) else 0
    )
