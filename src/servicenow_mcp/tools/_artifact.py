"""Platform-agnostic helpers for the script-file write path.

After the v0.10.0 dictionary-driven refactor, ``_artifact.py`` no longer owns
any platform catalog. The two helpers here are independent of which table or
field is being written:

- ``_read_script_file``: resolves a local script path against ``script_allowed_root``
  and reads it with the 1 MB size cap.
- ``validate_ui_macro_xml``: well-formed XML check, used when the resolved
  script field has ``internal_type == 'xml'`` (per ``DictionaryRegistry``).

Script-field discovery lives in ``servicenow_mcp.tools._dictionary``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


MAX_SCRIPT_FILE_BYTES: int = 1_048_576  # 1 MB


def validate_ui_macro_xml(content: str) -> str | None:
    """Validate that ``content`` parses as well-formed XML.

    Returns:
        ``None`` on success, or a human-readable error message string on parse
        failure. Used by the ``record_write`` script path when the resolved
        target field has ``internal_type == 'xml'``; Jelly macros use a single
        ``<j:jelly xmlns:j="jelly:core">`` root and should parse as-is.
    """
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        return f"XML content is not well-formed: {exc}"
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
