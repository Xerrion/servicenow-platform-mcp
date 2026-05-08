"""Tests for MCP server entry point."""

import importlib
import json
from types import ModuleType
from typing import Any
from unittest.mock import patch

from tests.helpers import get_tool_functions, get_tool_names


class TestCreateMcpServer:
    """Test MCP server creation."""

    def test_creates_server_with_name(self) -> None:
        """Server has the expected name."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",  # NOSONAR - intentional test-only fixture credential
            "MCP_TOOL_PACKAGE": "none",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        assert mcp_server.name == "servicenow-platform-mcp"

    def test_server_has_list_tool_packages_tool(self) -> None:
        """Server always registers the list_tool_packages tool."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",  # NOSONAR - intentional test-only fixture credential
            "MCP_TOOL_PACKAGE": "none",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        # The tool manager should have the list_tool_packages tool
        tool_names = get_tool_names(mcp_server)
        assert "list_tool_packages" in tool_names

    def test_server_loads_core_readonly_tools(self) -> None:
        """When using core_readonly package, core read-only tools are registered."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
            "MCP_TOOL_PACKAGE": "core_readonly",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        tool_names = get_tool_names(mcp_server)
        assert "describe" in tool_names
        assert "query" in tool_names
        assert "attachment" in tool_names

    def test_readonly_includes_attachment_read_tools_but_not_write_tools(self) -> None:
        """readonly package includes the attachment read tool and excludes record_write.

        Note: the unified ``attachment`` group registers both read and write
        attachment tools in one module (write paths are blocked at runtime by
        ``write_gate`` in production), so ``attachment_write`` is present even
        in the ``readonly`` preset. ``record_write``/``record_apply`` are not.
        """
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
            "MCP_TOOL_PACKAGE": "readonly",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        tool_names = get_tool_names(mcp_server)
        assert "attachment" in tool_names
        assert "record_write" not in tool_names
        assert "record_apply" not in tool_names

    def test_full_includes_attachment_read_and_write_tools(self) -> None:
        """full package includes both attachment read and write tools."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
            "MCP_TOOL_PACKAGE": "full",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        tool_names = get_tool_names(mcp_server)
        assert "attachment" in tool_names
        assert "attachment_write" in tool_names

    def test_none_package_has_only_list_packages(self) -> None:
        """'none' package only has the list_tool_packages tool."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",
            "MCP_TOOL_PACKAGE": "none",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        tool_names = get_tool_names(mcp_server)
        assert tool_names == ["list_tool_packages"]

    def test_list_tool_packages_tool_returns_package_data(self) -> None:
        """Calling list_tool_packages returns serialized package registry."""
        from servicenow_mcp.server import create_mcp_server

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",  # NOSONAR - intentional test-only fixture credential
            "MCP_TOOL_PACKAGE": "none",
        }
        with patch.dict("os.environ", env, clear=True):
            mcp_server = create_mcp_server()

        tools = get_tool_functions(mcp_server)
        raw = tools["list_tool_packages"]()
        result = json.loads(raw)

        assert isinstance(result, dict)
        assert "full" in result
        assert "none" in result
        assert "core_readonly" in result
        assert result["none"] == []
        assert "query" in result["core_readonly"]

    def test_import_error_during_tool_loading_is_handled(self) -> None:
        """Server still starts when a tool group module fails to import."""
        from servicenow_mcp.server import create_mcp_server

        original_import = importlib.import_module

        def mock_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
            if name == "servicenow_mcp.tools.unified.query":
                raise ImportError("fake import error")
            return original_import(name, *args, **kwargs)

        env = {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_USERNAME": "admin",
            "SERVICENOW_PASSWORD": "s3cret",  # NOSONAR - intentional test-only fixture credential
            "MCP_TOOL_PACKAGE": "core_readonly",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch("servicenow_mcp.server.importlib.import_module", side_effect=mock_import),
        ):
            mcp_server = create_mcp_server()

        tool_names = get_tool_names(mcp_server)
        # The query tool should not be registered due to the import failure.
        assert "query" not in tool_names
        # Other tool groups should still load successfully.
        assert "list_tool_packages" in tool_names
        assert "describe" in tool_names
