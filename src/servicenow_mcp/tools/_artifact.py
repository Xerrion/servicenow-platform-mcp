"""Shared security helpers for platform artifact write operations.

Single source of truth for the artifact-type catalog, the per-artifact script
field map, and the local-script-file resolution path. Imported by both the
legacy ``artifact_write`` tools and the unified ``record_write`` tool.
"""

from __future__ import annotations

from pathlib import Path


# Mapping from human-friendly artifact type names to ServiceNow tables.
# Superset of metadata.py's 7-type ARTIFACT_TABLES.
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
    "scripted_rest_api": "sys_web_service",
    "notification_script": "sysevent_email_action",
}

DEFAULT_SCRIPT_FIELD: str = "script"
MAX_SCRIPT_FILE_BYTES: int = 1_048_576  # 1 MB

# Per-artifact override for the script field name.
# Types not listed here default to DEFAULT_SCRIPT_FIELD ("script").
SCRIPT_FIELD_MAP: dict[str, str] = {
    "ui_policy": "script_true",
    "scripted_rest_resource": "operation_script",
    "widget": "client_script",
    "ui_page": "html",
    "ui_macro": "xml",
    "notification_script": "advanced_condition",
}


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
