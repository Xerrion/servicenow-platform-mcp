"""Keep nullable string tool inputs intact during MCP argument validation."""

import inspect
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import ValidatorFunctionWrapHandler, WrapValidator


def _validate_nullable_string(value: Any, handler: ValidatorFunctionWrapHandler) -> str | None:
    """Accept explicit null; let Pydantic validate every non-null value as text."""
    if value is None:
        return None
    return handler(value)


_NullableString = Annotated[
    str,
    WrapValidator(_validate_nullable_string, json_schema_input_type=str | None),
]


def nullable_string_signature(fn: Callable[..., Any]) -> inspect.Signature | None:
    """Return an MCP-compatible signature when the function has nullable strings.

    The MCP SDK pre-parses JSON-looking strings unless the Pydantic field's
    annotation is exactly ``str``. A plain ``str | None`` therefore turns JSON
    payload text into an object, and even the literal text ``null`` into None.
    WrapValidator keeps the field's base annotation as str while accepting null
    and advertising the correct string-or-null input schema. Only introspection
    changes; the original callable, defaults, and non-string inputs are untouched.
    """
    signature = inspect.signature(fn, eval_str=True)
    parameters = [
        parameter.replace(annotation=_NullableString) if parameter.annotation == str | None else parameter
        for parameter in signature.parameters.values()
    ]
    if all(before is after for before, after in zip(signature.parameters.values(), parameters, strict=True)):
        return None
    return signature.replace(parameters=parameters)
