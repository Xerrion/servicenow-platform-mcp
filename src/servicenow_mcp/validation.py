"""ServiceNow value normalization and validation."""

import re
from typing import Any


_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_SYS_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{32}$")


def resolve_ref_value(val: Any) -> str:
    """Coerce a ServiceNow field value to a plain string.

    ServiceNow may return reference fields as dicts (e.g.
    ``{"display_value": "...", "link": "https://..."}``) instead of plain
    strings when ``display_value=true`` is used. This helper normalizes
    any such value to a string so downstream code can safely use it.

    Args:
        val: The raw field value. It may be a string, dict, None, or
            another primitive.

    Returns:
        A plain string representation of the value.
    """
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return str(val.get("value") or val.get("display_value") or "")
    if val is None:
        return ""
    return str(val)


def validate_identifier(name: str | dict[str, Any] | None) -> None:
    """Raise ValueError if *name* is not a valid ServiceNow identifier.

    ServiceNow field names consist of lowercase alphanumerics and
    underscores. Dot-walked references are also accepted.
    """
    if not isinstance(name, str):
        name = resolve_ref_value(name)
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid identifier: {name!r}. "
            "Only lowercase alphanumeric characters, underscores, and dot-walked segments are allowed."
        )


def validate_sys_id(value: str) -> None:
    """Raise ValueError if *value* is not a valid 32-character hexadecimal ServiceNow sys_id."""
    if not isinstance(value, str):
        value = resolve_ref_value(value)
    if not _SYS_ID_RE.match(value):
        raise ValueError(f"Invalid sys_id: {value!r}. Expected a 32-character lowercase hexadecimal string.")


def sanitize_query_value(value: str | dict[str, Any] | None) -> str:
    """Escape encoded-query delimiters in a user-supplied value.

    ServiceNow uses ``^`` as the condition separator. A literal caret in a
    value is represented as ``^^``.
    """
    if not isinstance(value, str):
        value = resolve_ref_value(value)
    return value.replace("^", "^^")
