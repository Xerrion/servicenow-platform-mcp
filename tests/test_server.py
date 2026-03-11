"""Tests for MCP server entry point."""

from unittest.mock import patch

from tests.helpers import get_tool_names


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

        assert mcp_server.name == "servicenow-dev-debug"

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
        assert "table_describe" in tool_names
        assert "record_get" in tool_names
        assert "table_query" in tool_names

    def test_readonly_includes_attachment_read_tools_but_not_write_tools(self) -> None:
        """readonly package includes attachment read tools and excludes write tools."""
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
        assert "attachment_list" in tool_names
        assert "attachment_get" in tool_names
        assert "attachment_download" in tool_names
        assert "attachment_download_by_name" in tool_names
        assert "attachment_upload" not in tool_names
        assert "attachment_delete" not in tool_names

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
        assert "attachment_list" in tool_names
        assert "attachment_get" in tool_names
        assert "attachment_download" in tool_names
        assert "attachment_download_by_name" in tool_names
        assert "attachment_upload" in tool_names
        assert "attachment_delete" in tool_names

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
