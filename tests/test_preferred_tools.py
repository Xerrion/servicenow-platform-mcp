"""Tests for the preferred-tool steering map and warning formatter."""

import pytest

from servicenow_mcp.preferred_tools import (
    TABLE_TO_PREFERRED_TOOL,
    PreferredTool,
    format_preference_warning,
    preferred_tool_for,
)


class TestPreferredToolFor:
    """Tests for the preferred_tool_for lookup."""

    def test_exact_match_incident(self) -> None:
        """Exact-match lookup returns the incident entry."""
        entry = preferred_tool_for("incident")
        assert entry is not None
        assert entry.get("list") == "incident_list"
        assert entry.get("get") == "incident_get"

    def test_unknown_table_returns_none(self) -> None:
        """Unmapped tables return None."""
        assert preferred_tool_for("definitely_not_a_real_table") is None

    def test_cmdb_subclass_falls_back_to_cmdb_ci(self) -> None:
        """CMDB subclasses (cmdb_ci_*) fall back to the cmdb_ci entry."""
        sub = preferred_tool_for("cmdb_ci_server")
        base = preferred_tool_for("cmdb_ci")
        assert sub is not None
        assert base is not None
        assert sub is base

    def test_cmdb_ci_itself_returns_cmdb_ci_entry(self) -> None:
        """cmdb_ci itself is an exact match (not via fallback)."""
        entry = preferred_tool_for("cmdb_ci")
        assert entry is not None
        assert entry.get("list") == "cmdb_list"

    def test_cmdb_rel_ci_does_not_fall_back(self) -> None:
        """cmdb_rel_ci returns its own entry, not cmdb_ci's.

        ``"cmdb_rel_ci".startswith("cmdb_ci")`` is False (the third char is
        'r', not 'c'), so the fallback in ``preferred_tool_for`` does not fire
        and the exact-match cmdb_rel_ci entry is returned.
        """
        assert "cmdb_rel_ci".startswith("cmdb_ci") is False
        entry = preferred_tool_for("cmdb_rel_ci")
        cmdb_ci_entry = preferred_tool_for("cmdb_ci")
        assert entry is not None
        assert entry is not cmdb_ci_entry
        assert entry.get("get") == "cmdb_relationships"
        assert "list" not in entry

    def test_meta_artifact_tables_share_entry(self) -> None:
        """All metadata-artifact tables share the same dict object reference."""
        a = preferred_tool_for("sys_script")
        b = preferred_tool_for("sys_script_include")
        c = preferred_tool_for("sys_ui_policy")
        assert a is not None
        assert b is not None
        assert c is not None
        assert a is b
        assert b is c
        assert a.get("list") == "meta_list_artifacts"


class TestFormatPreferenceWarning:
    """Tests for format_preference_warning."""

    def test_both_list_and_get(self) -> None:
        """Entries with both list and get include both names joined by ' / '."""
        entry = TABLE_TO_PREFERRED_TOOL["incident"]
        msg = format_preference_warning("incident", entry)
        assert "`incident_list`" in msg
        assert "`incident_get`" in msg
        assert "`incident_list` / `incident_get`" in msg
        assert entry["value_add"] in msg

    def test_list_only(self) -> None:
        """List-only entries (e.g. sys_audit) emit only the list tool, no extra tool joined by ' / '."""
        entry = TABLE_TO_PREFERRED_TOOL["sys_audit"]
        assert "list" in entry
        assert "get" not in entry
        msg = format_preference_warning("sys_audit", entry)
        assert "`changes_last_touched`" in msg
        # No second tool separator: there must not be a "` / `" sequence joining two tool names.
        assert "` / `" not in msg

    def test_get_only(self) -> None:
        """Get-only entries (cmdb_rel_ci) emit only the get tool."""
        entry = TABLE_TO_PREFERRED_TOOL["cmdb_rel_ci"]
        assert "get" in entry
        assert "list" not in entry
        msg = format_preference_warning("cmdb_rel_ci", entry)
        assert "`cmdb_relationships`" in msg
        assert "` / `" not in msg

    def test_warning_uses_ascii_hyphen(self) -> None:
        """Warning string uses regular ASCII hyphens, not em/en-dashes (per AGENTS.md)."""
        entry = TABLE_TO_PREFERRED_TOOL["incident"]
        msg = format_preference_warning("incident", entry)
        assert "\u2014" not in msg  # em-dash
        assert "\u2013" not in msg  # en-dash

    def test_malformed_entry_raises_value_error(self) -> None:
        """An entry with neither 'list' nor 'get' raises ValueError mentioning the table."""
        bogus: PreferredTool = {"value_add": "x"}
        with pytest.raises(ValueError, match="some_table"):
            format_preference_warning("some_table", bogus)
