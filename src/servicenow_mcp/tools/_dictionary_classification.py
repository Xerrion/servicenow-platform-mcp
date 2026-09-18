"""Classify ServiceNow dictionary fields that can hold executable content."""

from typing import Final

from servicenow_mcp.tools._dictionary_models import DictionaryField, ScriptField


UNAMBIGUOUS_SCRIPT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "script",
        "script_plain",
        "script_server",
        "script_client",
        "email_script",
        "html_script",
        "html_template",
        "css",
    }
)
EXCLUDED_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "translated_html",
        "template_value",
        "glide_var",
        "json",
        "conditions",
        "condition_string",
        "glide_action_list",
        "variable_conditions",
        "snapshot_template_value",
        "variable_template_value",
    }
)
_HEURISTIC_TYPES: Final[frozenset[str]] = frozenset({"html", "xml"})
_HEURISTIC_ATTR_FLAGS: Final[tuple[tuple[str, str], ...]] = (
    ("tinymce_allow_all", "true"),
    ("html_sanitize", "false"),
)


def classify_script_field(field: DictionaryField) -> ScriptField | None:
    """Return the script-field projection when the dictionary field qualifies."""
    if field.name in EXCLUDED_ELEMENTS:
        return None
    if field.internal_type in UNAMBIGUOUS_SCRIPT_TYPES:
        return ScriptField(field.name, field.internal_type, field.inherited_from, False)
    if field.internal_type in _HEURISTIC_TYPES and attributes_admit_heuristic(field.attributes):
        return ScriptField(field.name, field.internal_type, field.inherited_from, True)
    return None


def attributes_admit_heuristic(attributes: str) -> bool:
    """Return whether html/xml attributes mark a field as executable content."""
    parsed: dict[str, str] = {}
    for raw_token in attributes.split(","):
        token = raw_token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        parsed[key.strip().lower()] = value.strip().lower()
    return any(parsed.get(key) == value for key, value in _HEURISTIC_ATTR_FLAGS)
