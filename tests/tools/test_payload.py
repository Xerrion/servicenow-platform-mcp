"""Native JSON payload boundaries preserve values and reject unsafe objects."""

from typing import TYPE_CHECKING

import pytest

from servicenow_mcp.capabilities.contracts import (
    MAX_JSON_DEPTH,
    MAX_JSON_PAYLOAD_BYTES,
    CapabilityError,
    validate_payload,
)

if TYPE_CHECKING:
    from pydantic import JsonValue


def nested(depth: int) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {}
    for _ in range(depth - 1):
        value = {"k": value}
    return value


def test_native_values_are_preserved() -> None:
    value = {"a": None, "b": False, "c": 0, "d": "", "e": [], "f": {}}
    assert validate_payload(value, field_name="data") == value


@pytest.mark.parametrize(
    "value",
    [
        '{"a":1}',
        "null",
        [],
        None,
        {"a": float("nan")},
        {"a": float("inf")},
        {"a": b"x"},
    ],
)
def test_invalid_native_values_fail(value) -> None:
    with pytest.raises(CapabilityError):
        validate_payload(value, field_name="data")


def test_depth_boundary() -> None:
    assert validate_payload(nested(MAX_JSON_DEPTH), field_name="data")
    with pytest.raises(CapabilityError, match="nesting depth"):
        validate_payload(nested(MAX_JSON_DEPTH + 1), field_name="data")
    with pytest.raises(CapabilityError, match="nesting depth"):
        validate_payload(nested(2000), field_name="data")


def test_utf8_size_boundary() -> None:
    value = {"a": "x" * (MAX_JSON_PAYLOAD_BYTES - len('{"a":""}'))}
    assert validate_payload(value, field_name="data")
    value["a"] += "x"
    with pytest.raises(CapabilityError, match="maximum size"):
        validate_payload(value, field_name="data")


def test_non_identifier_variable_keys_are_optional() -> None:
    with pytest.raises(ValueError, match="Invalid identifier"):
        validate_payload({"invalid-key": 1}, field_name="data")
    assert validate_payload(
        {"invalid-key": 1}, field_name="variables", validate_keys=False
    )
