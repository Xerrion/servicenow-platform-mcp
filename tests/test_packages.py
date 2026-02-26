"""Tests for tool package system."""

import pytest


class TestPackageRegistry:
    """Test package registry and loading."""

    def test_registry_contains_full(self):
        """full package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "full" in PACKAGE_REGISTRY

    def test_registry_contains_none(self):
        """'none' package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "none" in PACKAGE_REGISTRY

    def test_registry_contains_introspection_only(self):
        """introspection_only package is defined."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "introspection_only" in PACKAGE_REGISTRY

    def test_full_includes_introspection(self):
        """full package includes introspection tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "introspection" in PACKAGE_REGISTRY["full"]

    def test_full_includes_metadata(self):
        """full package includes metadata tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "metadata" in PACKAGE_REGISTRY["full"]

    def test_full_includes_relationships(self):
        """full package includes relationship tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "relationships" in PACKAGE_REGISTRY["full"]

    def test_none_package_is_empty(self):
        """'none' package has no tool groups."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert PACKAGE_REGISTRY["none"] == []

    def test_get_package_valid(self):
        """get_package returns tool groups for a valid package."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert isinstance(groups, list)
        assert len(groups) > 0

    def test_get_package_invalid_raises(self):
        """get_package raises ValueError for unknown package."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown"):
            get_package("nonexistent_package")

    def test_full_includes_changes(self):
        """full package includes change intelligence tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "changes" in PACKAGE_REGISTRY["full"]

    def test_full_includes_debug(self):
        """full package includes debug/trace tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "debug" in PACKAGE_REGISTRY["full"]

    def test_list_packages_returns_all(self):
        """list_packages returns all registered packages."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "none" in packages
        assert "full" in packages
        assert "introspection_only" in packages

    def test_dev_debug_not_in_registry(self):
        """dev_debug package has been removed from the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "dev_debug" not in PACKAGE_REGISTRY

    def test_get_package_returns_copy(self):
        """get_package returns a copy — mutating it does not affect the registry."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        groups.append("should_not_persist")
        fresh = get_package("full")
        assert "should_not_persist" not in fresh

    def test_list_packages_returns_copies(self):
        """list_packages returns deep copies of value lists."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        packages["full"].append("should_not_persist")
        fresh = list_packages()
        assert "should_not_persist" not in fresh["full"]
