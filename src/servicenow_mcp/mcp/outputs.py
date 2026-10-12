"""Native error results. Capabilities do not depend on MCP types."""

import json
from typing import Any, ClassVar, Literal

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    code: str
    message: str
    phase: str | None = None
    operation: str | None = None
    trace_id: str | None = None
    missing_fields: list[str] | None = None


class ErrorResult(BaseModel):
    status: Literal["error"] = "error"
    data: None = None
    error: ErrorDetail


def error_result(message: str, code: str = "INVALID_REQUEST", **details: Any) -> CallToolResult:
    """Return the same safe information in text and structured content."""
    payload = ErrorResult(error=ErrorDetail(code=code, message=message, **details)).model_dump(exclude_none=True)
    payload["data"] = None
    return CallToolResult(
        is_error=True,
        structured_content=payload,
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, allow_nan=False))],
    )
