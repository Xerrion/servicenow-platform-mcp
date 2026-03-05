"""Integration tests for package loading - verifies real MCP server creation with all package presets."""

import os
from unittest.mock import patch

import pytest

from servicenow_mcp.packages import PACKAGE_REGISTRY
from servicenow_mcp.server import create_mcp_server

pytestmark = pytest.mark.integration

# Expected tool counts per package (verified in Wave FINAL F3).
# +1 for the always-registered list_tool_packages tool.
EXPECTED_TOOL_COUNTS: dict[str, int] = {
    "full": 76,
    "introspection_only": 12,
    "none": 1,
    "itil": 48,
    "developer": 37,
    "readonly": 28,
    "analyst": 18,
    "incident_management": 18,
    "change_management": 16,
    "cmdb": 13,
    "problem_management": 17,
    "request_management": 11,
    "knowledge_management": 11,
}


class TestPackageLoading:
    """Test that each package preset creates an MCP server with the correct tool count."""

    @pytest.mark.parametrize(
        ("package_name", "expected_count"),
        list(EXPECTED_TOOL_COUNTS.items()),
        ids=list(EXPECTED_TOOL_COUNTS.keys()),
    )
    def test_package_loads_correct_tool_count(self, package_name: str, expected_count: int) -> None:
        """Verify each package loads the expected number of tools."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_count = len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
        assert tool_count == expected_count, (
            f"Package '{package_name}' loaded {tool_count} tools, expected {expected_count}"
        )

    @pytest.mark.parametrize(
        "package_name",
        list(EXPECTED_TOOL_COUNTS.keys()),
        ids=list(EXPECTED_TOOL_COUNTS.keys()),
    )
    def test_package_tool_names_are_unique(self, package_name: str) -> None:
        """Verify no duplicate tool names within a package."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_names = list(mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]
        assert len(tool_names) == len(set(tool_names)), (
            f"Package '{package_name}' has duplicate tool names: {[n for n in tool_names if tool_names.count(n) > 1]}"
        )

    def test_all_registry_packages_have_expected_counts(self) -> None:
        """Verify EXPECTED_TOOL_COUNTS covers every package in the registry."""
        registry_names = set(PACKAGE_REGISTRY.keys())
        expected_names = set(EXPECTED_TOOL_COUNTS.keys())
        assert registry_names == expected_names, (
            f"Mismatch between registry and expected counts. "
            f"Missing from expected: {registry_names - expected_names}. "
            f"Extra in expected: {expected_names - registry_names}"
        )

    def test_list_tool_packages_always_present(self) -> None:
        """Verify that list_tool_packages is registered in every package including 'none'."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "none"}):
            mcp = create_mcp_server()
            tool_names = list(mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]
        assert "list_tool_packages" in tool_names

    @pytest.mark.parametrize(
        "groups_csv",
        [
            "introspection,relationships",
            "domain_incident,domain_change",
            "metadata,documentation,utility",
        ],
        ids=[
            "introspection+relationships",
            "incident+change",
            "metadata+docs+utility",
        ],
    )
    def test_comma_separated_groups_load(self, groups_csv: str) -> None:
        """Verify comma-separated group syntax creates a working server."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": groups_csv}):
            mcp = create_mcp_server()
            tool_count = len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
        # At minimum: list_tool_packages + at least one tool from each group
        assert tool_count > 1, f"Comma-separated groups '{groups_csv}' loaded only {tool_count} tools"

    def test_full_package_includes_all_domain_tools(self) -> None:
        """Verify the 'full' package includes all 32 domain tools."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "full"}):
            mcp = create_mcp_server()
            tool_names = set(mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]

        domain_tools = {
            # Incident domain (6)
            "incident_list",
            "incident_get",
            "incident_create",
            "incident_update",
            "incident_resolve",
            "incident_add_comment",
            # Change domain (6)
            "change_list",
            "change_get",
            "change_create",
            "change_update",
            "change_tasks",
            "change_add_comment",
            # CMDB domain (5)
            "cmdb_list",
            "cmdb_get",
            "cmdb_relationships",
            "cmdb_classes",
            "cmdb_health",
            # Problem domain (5)
            "problem_list",
            "problem_get",
            "problem_create",
            "problem_update",
            "problem_root_cause",
            # Request domain (5)
            "request_list",
            "request_get",
            "request_items",
            "request_item_get",
            "request_item_update",
            # Knowledge domain (5)
            "knowledge_search",
            "knowledge_get",
            "knowledge_create",
            "knowledge_update",
            "knowledge_feedback",
        }
        missing = domain_tools - tool_names
        assert not missing, f"Full package missing domain tools: {missing}"
