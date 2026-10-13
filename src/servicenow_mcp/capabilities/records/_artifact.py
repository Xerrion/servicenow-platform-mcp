"""Platform-agnostic XML validation for inline field writes."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def validate_ui_macro_xml(content: str) -> str | None:
    """Return an error for malformed XML, or None for well-formed XML.

    Jelly macros with a single namespace-qualified root are accepted.
    The caller selects XML fields through dictionary metadata.
    """
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        return f"XML content is not well-formed: {exc}"
    return None
