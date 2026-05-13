"""Shared security helpers for platform artifact write operations.

Single source of truth for the artifact-type catalog, the per-artifact script
field map, and the local-script-file resolution path. Imported by the unified
``record_write`` and ``record_read`` tools.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


# Mapping from human-friendly artifact type names to ServiceNow tables.
WRITABLE_ARTIFACT_TABLES: dict[str, str] = {
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "ui_policy": "sys_ui_policy",
    "ui_action": "sys_ui_action",
    "client_script": "sys_script_client",
    "scheduled_job": "sysauto_script",
    "fix_script": "sys_script_fix",
    "scripted_rest_resource": "sys_ws_operation",
    "ui_script": "sys_ui_script",
    "processor": "sys_processor",
    "widget": "sp_widget",
    "ui_page": "sys_ui_page",
    "ui_macro": "sys_ui_macro",
    "script_action": "sysevent_script_action",
    "mid_script_include": "ecc_agent_script_include",
    "notification_script": "sysevent_email_action",
    # Catalog expansion (v0.10.0+)
    "email_script": "sys_script_email",
    "catalog_client_script": "catalog_script_client",
    "catalog_ui_policy": "catalog_ui_policy",
    "transform_map_script": "sys_transform_script",
    "transform_entry_script": "sys_transform_entry",
    "acl": "sys_security_acl",
    "dynamic_filter": "sys_filter_option_dynamic",
    "decision_question": "sys_decision_question",
}

MAX_SCRIPT_FILE_BYTES: int = 1_048_576  # 1 MB

# Per-artifact script field list. Index 0 is the primary field (the default
# target when ``script_field`` is not specified on ``record_write``). Additional
# entries are alternate script-bearing fields that callers can target via the
# ``script_field`` parameter.
SCRIPT_FIELD_MAP: dict[str, list[str]] = {
    "business_rule": ["script", "condition"],
    "script_include": ["script"],
    "ui_action": ["script", "condition", "onclick"],
    "client_script": ["script"],
    "scheduled_job": ["script"],
    "fix_script": ["script"],
    "ui_script": ["script"],
    "processor": ["script"],
    "script_action": ["script"],
    "mid_script_include": ["script"],
    "ui_policy": ["script_true", "script_false"],
    "widget": ["client_script", "script", "template", "css", "link"],
    "ui_page": ["html", "client_script", "processing_script"],
    "scripted_rest_resource": ["operation_script"],
    "ui_macro": ["xml"],
    "notification_script": ["advanced_condition"],
    "email_script": ["script"],
    "catalog_client_script": ["script"],
    "catalog_ui_policy": ["script_true", "script_false"],
    "transform_map_script": ["script"],
    "transform_entry_script": ["script"],
    "acl": ["script"],
    "dynamic_filter": ["script"],
    "decision_question": ["condition_script"],
}

# Module-load invariant: every writable artifact must declare at least one
# script field. Catches catalog drift on import rather than at first call.
_missing_field_map = sorted(set(WRITABLE_ARTIFACT_TABLES) - set(SCRIPT_FIELD_MAP))
_empty_field_map = sorted(k for k, v in SCRIPT_FIELD_MAP.items() if not v)
if _missing_field_map or _empty_field_map:
    raise RuntimeError(
        f"SCRIPT_FIELD_MAP is incomplete: missing entries={_missing_field_map}; empty entries={_empty_field_map}"
    )
del _missing_field_map, _empty_field_map


def primary_script_field(artifact_type: str) -> str:
    """Return the primary (default) script field for an artifact type.

    Raises:
        KeyError: If artifact_type is not in SCRIPT_FIELD_MAP. Callers must
            validate the type via ``_resolve_writable_artifact_table`` first.
    """
    return SCRIPT_FIELD_MAP[artifact_type][0]


def _resolve_writable_artifact_table(artifact_type: str) -> str:
    """Resolve artifact_type to its ServiceNow table name.

    Raises:
        ValueError: If artifact_type is not in WRITABLE_ARTIFACT_TABLES.
    """
    table = WRITABLE_ARTIFACT_TABLES.get(artifact_type)
    if table is None:
        valid_types = ", ".join(sorted(WRITABLE_ARTIFACT_TABLES.keys()))
        raise ValueError(f"Unknown artifact_type '{artifact_type}'. Valid types: {valid_types}")
    return table


def _validate_xml_content(content: str) -> str | None:
    """Validate that ``content`` parses as well-formed XML.

    Returns:
        ``None`` on success, or a human-readable error message string on parse
        failure. Used by the ``ui_macro`` write path; Jelly macros use a single
        ``<j:jelly xmlns:j="jelly:core">`` root and should parse as-is.
    """
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        return f"ui_macro content is not well-formed XML: {exc}"
    return None


def _read_script_file(script_path: str, allowed_root: str) -> str:
    """Read a local script file and return its contents as a string.

    Args:
        script_path: Path to the script file.
        allowed_root: The resolved script path must be under this root directory.
            Must be non-empty - callers must configure ``script_allowed_root`` before using ``script_path``.

    Raises:
        ValueError: If the path is not absolute, allowed_root is empty or inaccessible,
            or the file exceeds MAX_SCRIPT_FILE_BYTES.
        PermissionError: If the resolved path is outside the allowed root.
        FileNotFoundError: If the file does not exist or is not a regular file.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    if not Path(script_path).is_absolute():
        raise ValueError(f"script_path must be an absolute path, got: {script_path!r}")

    if not allowed_root:
        raise ValueError("script_allowed_root must be configured when using script_path")

    try:
        resolved = Path(script_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(f"Script file not found or not accessible: {script_path!r}") from exc

    try:
        root = Path(allowed_root).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Configured script_allowed_root is not accessible: {allowed_root!r}") from exc
    if not resolved.is_relative_to(root):
        raise PermissionError(f"Script path {str(resolved)!r} is outside the allowed root {str(root)!r}")

    if not resolved.is_file():
        raise FileNotFoundError(f"Script path is not a regular file: {script_path!r}")

    file_size = resolved.stat().st_size
    if file_size > MAX_SCRIPT_FILE_BYTES:
        raise ValueError(
            f"Script file too large ({file_size} bytes). Maximum allowed size is {MAX_SCRIPT_FILE_BYTES} bytes (1 MB)."
        )

    return resolved.read_text(encoding="utf-8")
