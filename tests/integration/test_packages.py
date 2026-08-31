"""Integration tests for package loading - verifies real MCP server creation across all presets."""

import os
from unittest.mock import patch

import pytest

from servicenow_mcp.packages import PACKAGE_REGISTRY
from servicenow_mcp.server import create_mcp_server
from tests.helpers import get_registered_tools, get_tool_names


pytestmark = pytest.mark.integration

# Expected tool counts per preset under the unified tool surface (Phase 3b).
# Counts include the always-registered ``list_tool_packages`` tool.
#
# Group -> tool count:
#   query (1), describe (1), record_write (2: record_write + record_apply),
#   record_read (1), attachment (2: attachment + attachment_write),
#   investigate (1), resolve_choice (1), service_catalog (1), audit (1),
#   flow (1), code_search (1).
#
# Note: ``readonly`` and ``core_readonly`` both load the ``attachment`` group,
# which registers both read AND write attachment tools (write tools are
# blocked at runtime by ``write_gate`` in production). This is documented in
# ``packages.py``.
EXPECTED_TOOL_COUNTS: dict[str, int] = {
    # full preset: 1 always-on plus 13 package tools.
    "full": 14,
    # readonly preset: 1 always-on plus 10 package tools.
    "readonly": 11,
    # core_readonly preset: 1 always-on plus query, describe, two attachment
    # tools = 5.
    "core_readonly": 5,
    # none preset: only list_tool_packages.
    "none": 1,
}


class TestPackageLoading:
    """Each preset creates an MCP server with the expected tool surface."""

    @pytest.mark.parametrize(
        ("package_name", "expected_count"),
        list(EXPECTED_TOOL_COUNTS.items()),
        ids=list(EXPECTED_TOOL_COUNTS.keys()),
    )
    def test_package_loads_correct_tool_count(self, package_name: str, expected_count: int) -> None:  # pragma: no cover
        """Verify each preset loads exactly the expected number of tools."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_count = len(get_registered_tools(mcp))
        assert tool_count == expected_count, (
            f"Package '{package_name}' loaded {tool_count} tools, expected {expected_count}"
        )

    @pytest.mark.parametrize(
        "package_name",
        list(EXPECTED_TOOL_COUNTS.keys()),
        ids=list(EXPECTED_TOOL_COUNTS.keys()),
    )
    def test_package_tool_names_are_unique(self, package_name: str) -> None:  # pragma: no cover
        """No duplicate tool names within a preset."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_names = get_tool_names(mcp)
        assert len(tool_names) == len(set(tool_names)), (
            f"Package '{package_name}' has duplicate tool names: {[n for n in tool_names if tool_names.count(n) > 1]}"
        )

    def test_all_registry_packages_have_expected_counts(self) -> None:  # pragma: no cover
        """``EXPECTED_TOOL_COUNTS`` covers exactly the registry presets."""
        registry_names = set(PACKAGE_REGISTRY.keys())
        expected_names = set(EXPECTED_TOOL_COUNTS.keys())
        assert registry_names == expected_names, (
            f"Mismatch between registry and expected counts. "
            f"Missing from expected: {registry_names - expected_names}. "
            f"Extra in expected: {expected_names - registry_names}"
        )

    def test_list_tool_packages_always_present(self) -> None:  # pragma: no cover
        """``list_tool_packages`` is registered in every preset including ``none``."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "none"}):
            mcp = create_mcp_server()
            tool_names = get_tool_names(mcp)
        assert "list_tool_packages" in tool_names

    @pytest.mark.parametrize(
        "groups_csv",
        [
            "query,describe",
            "query,attachment",
            "describe,investigate,resolve_choice",
        ],
        ids=[
            "query+describe",
            "query+attachment",
            "describe+investigate+resolve_choice",
        ],
    )
    def test_comma_separated_groups_load(self, groups_csv: str) -> None:  # pragma: no cover
        """Comma-separated group syntax creates a working server."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": groups_csv}):
            mcp = create_mcp_server()
            tool_count = len(get_registered_tools(mcp))
        # At minimum: list_tool_packages + at least one tool per group.
        assert tool_count > 1, f"Comma-separated groups '{groups_csv}' loaded only {tool_count} tools"

    def test_full_package_includes_all_unified_tools(self) -> None:  # pragma: no cover
        """``full`` registers every unified tool by name."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "full"}):
            mcp = create_mcp_server()
            tool_names = set(get_tool_names(mcp))

        expected = {
            "list_tool_packages",
            "query",
            "describe",
            "record_write",
            "record_apply",
            "record_read",
            "attachment",
            "attachment_write",
            "investigate",
            "resolve_choice",
            "service_catalog",
            "audit",
            "flow",
            "code_search",
        }
        assert tool_names == expected
