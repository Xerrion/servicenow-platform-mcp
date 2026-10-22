"""Typed operation results and bounded native JSON payloads."""

import json
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, model_serializer

from servicenow_mcp.errors import ServiceNowMCPError


class CapabilityError(ServiceNowMCPError):
    """An anticipated operation failure with a safe public message."""

    def __init__(
        self,
        message: str,
        code: str = "INVALID_REQUEST",
        missing_fields: list[str] | None = None,
        *,
        missing_arguments: list[str] | None = None,
        outcome: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing_fields = missing_fields
        self.missing_arguments = missing_arguments
        self.outcome = outcome


class Pagination(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    offset: int | None = None
    limit: int | None = None
    total: int | None = None


class CapabilityResult(BaseModel):
    """A result with dynamic platform data and explicit completeness metadata."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: Literal["success"] = "success"
    data: JsonValue
    pagination: Pagination | None = None
    warnings: list[str] | None = None
    truncation: dict[str, JsonValue] | None = None

    @model_serializer(mode="wrap")
    def _omit_empty_metadata(self, handler: Any) -> dict[str, Any]:
        value = handler(self)
        for name in ("pagination", "warnings", "truncation"):
            if not value.get(name):
                value.pop(name, None)
        if "pagination" in value:
            value["pagination"] = {
                k: v for k, v in value["pagination"].items() if v is not None
            }
        return value

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def result(
    data: Any,
    pagination: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    truncation: dict[str, Any] | None = None,
) -> CapabilityResult:
    """Construct a successful result with explicit completeness metadata."""
    return CapabilityResult(
        data=data, pagination=pagination, warnings=warnings, truncation=truncation
    )


MAX_JSON_PAYLOAD_BYTES = 256 * 1024
MAX_JSON_DEPTH = 32


def validate_payload(
    raw: dict[str, Any],
    *,
    field_name: str,
    max_bytes: int = MAX_JSON_PAYLOAD_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    validate_keys: bool = True,
) -> dict[str, Any]:
    """Reject non-native, oversized, deeply nested, or non-JSON object inputs."""
    from servicenow_mcp.validation import validate_identifier

    if not isinstance(raw, dict):
        raise CapabilityError(f"{field_name} must be a JSON object")
    pending = [(raw, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > max_depth:
            raise CapabilityError(
                f"{field_name} exceeds maximum nesting depth of {max_depth}"
            )
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise CapabilityError(f"{field_name} must contain string keys")
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif not isinstance(value, str | bool | int | float | type(None)):
            raise CapabilityError(f"{field_name} contains a non-JSON value")
    try:
        encoded = json.dumps(
            raw, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except ValueError, UnicodeError:
        raise CapabilityError(f"{field_name} contains an invalid JSON value") from None
    if len(encoded) > max_bytes:
        raise CapabilityError(f"{field_name} exceeds maximum size of {max_bytes} bytes")
    if validate_keys:
        for key in raw:
            validate_identifier(key)
    return raw
