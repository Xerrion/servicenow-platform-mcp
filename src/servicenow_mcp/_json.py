"""Pydantic JSON adapters shared by every application-owned JSON boundary."""

from typing import Any

from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError


# Inbound parsing and tool output keep NaN/Infinity as JSON constants, matching the prior stdlib contract.
JSON: TypeAdapter[Any] = TypeAdapter(Any, config=ConfigDict(ser_json_inf_nan="constants"))

# Outbound request bodies must be strict JSON: ServiceNow cannot parse NaN, Infinity or Python-only types.
_OUTBOUND: TypeAdapter[JsonValue] = TypeAdapter(JsonValue, config=ConfigDict(allow_inf_nan=False))


def dump_request_body(data: Any) -> bytes:
    """Serialize a request body once as strict UTF-8 JSON.

    Raises:
        ValueError: When *data* holds NaN, Infinity or a value with no JSON form.
    """
    try:
        return _OUTBOUND.dump_json(_OUTBOUND.validate_python(data))
    except ValidationError:
        raise ValueError(
            "Request body is not JSON compliant: NaN, Infinity and non-JSON values are not allowed."
        ) from None
