"""Integration tests for package loading - verifies real MCP server creation across all presets."""

import os
from unittest.mock import patch

import pytest

from servicenow_mcp.packages import PACKAGE_REGISTRY
from servicenow_mcp.server import create_mcp_server
from tests.helpers import get_registered_tools, get_tool_names


pytestmark = pytest.mark.integration

# Counts include discovery and one shared apply tool for selected mutation groups.
EXPECTED_TOOL_COUNTS: dict[str, int] = {
    "full": 17,
    "readonly": 13,
    # core_readonly preset: 1 always-on plus query, describe, and attachment.
    "core_readonly": 4,
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
    async def test_package_loads_correct_tool_count(
        self, package_name: str, expected_count: int
    ) -> None:  # pragma: no cover
        """Verify each preset loads exactly the expected number of tools."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_count = len(await get_registered_tools(mcp))
        assert tool_count == expected_count, (
            f"Package '{package_name}' loaded {tool_count} tools, expected {expected_count}"
        )

    @pytest.mark.parametrize(
        "package_name",
        list(EXPECTED_TOOL_COUNTS.keys()),
        ids=list(EXPECTED_TOOL_COUNTS.keys()),
    )
    async def test_package_tool_names_are_unique(self, package_name: str) -> None:  # pragma: no cover
        """No duplicate tool names within a preset."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_names = await get_tool_names(mcp)
        assert len(tool_names) == len(set(tool_names)), (
            f"Package '{package_name}' has duplicate tool names: {[n for n in tool_names if tool_names.count(n) > 1]}"
        )

    async def test_all_registry_packages_have_expected_counts(self) -> None:  # pragma: no cover
        """``EXPECTED_TOOL_COUNTS`` covers exactly the registry presets."""
        registry_names = set(PACKAGE_REGISTRY.keys())
        expected_names = set(EXPECTED_TOOL_COUNTS.keys())
        assert registry_names == expected_names, (
            f"Mismatch between registry and expected counts. "
            f"Missing from expected: {registry_names - expected_names}. "
            f"Extra in expected: {expected_names - registry_names}"
        )

    async def test_list_tool_packages_always_present(self) -> None:  # pragma: no cover
        """``list_tool_packages`` is registered in every preset including ``none``."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "none"}):
            mcp = create_mcp_server()
            tool_names = await get_tool_names(mcp)
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
    async def test_comma_separated_groups_load(self, groups_csv: str) -> None:  # pragma: no cover
        """Comma-separated group syntax creates a working server."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": groups_csv}):
            mcp = create_mcp_server()
            tool_count = len(await get_registered_tools(mcp))
        # At minimum: list_tool_packages + at least one tool per group.
        assert tool_count > 1, f"Comma-separated groups '{groups_csv}' loaded only {tool_count} tools"

    async def test_full_package_includes_all_unified_tools(self) -> None:  # pragma: no cover
        """``full`` registers every unified tool by name."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": "full"}):
            mcp = create_mcp_server()
            tool_names = set(await get_tool_names(mcp))

        expected = {
            "list_tool_packages",
            "query",
            "describe",
            "record_preview",
            "mutation_apply",
            "record_read",
            "attachment",
            "attachment_preview",
            "investigate",
            "resolve_choice",
            "service_catalog",
            "service_catalog_preview",
            "analysis",
            "audit",
            "flow",
            "code_search",
            "cmdb",
        }
        assert tool_names == expected

    @pytest.mark.parametrize(
        ("package_name", "expected_tools"),
        [
            ("attachment", {"list_tool_packages", "attachment"}),
            ("attachment_write", {"list_tool_packages", "attachment_preview", "mutation_apply"}),
        ],
    )
    async def test_attachment_groups_load_independently(
        self, package_name: str, expected_tools: set[str]
    ) -> None:  # pragma: no cover
        """Attachment read and write groups do not register each other."""
        with patch.dict(os.environ, {"MCP_TOOL_PACKAGE": package_name}):
            mcp = create_mcp_server()
            tool_names = set(await get_tool_names(mcp))

        assert tool_names == expected_tools
