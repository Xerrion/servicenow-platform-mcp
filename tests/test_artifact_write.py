"""Tests for artifact write tools (artifact_create, artifact_update)."""

import json
from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.artifact_write import (
    MAX_SCRIPT_FILE_BYTES,
    TOOL_NAMES,
    WRITABLE_ARTIFACT_TABLES,
    _read_script_file,
    _resolve_writable_artifact_table,
)
from servicenow_mcp.tools.metadata import ARTIFACT_TABLES
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"

# Valid 32-char hex sys_ids for tests (validate_sys_id requires this format)
SYS_ID_ART001 = "a" * 32  # aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register artifact_write tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.artifact_write import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# -- WRITABLE_ARTIFACT_TABLES --------------------------------------------------


class TestWritableArtifactTables:
    """Tests for the WRITABLE_ARTIFACT_TABLES constant and resolver."""

    def test_contains_all_original_artifact_tables(self) -> None:
        """All 7 artifact types from metadata.py are present in the writable superset."""
        for artifact_type, table in ARTIFACT_TABLES.items():
            assert artifact_type in WRITABLE_ARTIFACT_TABLES
            assert WRITABLE_ARTIFACT_TABLES[artifact_type] == table

    def test_has_expected_count(self) -> None:
        """WRITABLE_ARTIFACT_TABLES contains exactly 17 entries."""
        assert len(WRITABLE_ARTIFACT_TABLES) == 17

    def test_resolve_valid_type(self) -> None:
        """Resolves a known artifact type to the correct table name."""
        assert _resolve_writable_artifact_table("script_include") == "sys_script_include"
        assert _resolve_writable_artifact_table("widget") == "sp_widget"

    def test_resolve_invalid_type_raises(self) -> None:
        """Raises ValueError for an unknown artifact type with valid types listed."""
        with pytest.raises(ValueError, match="Unknown artifact_type"):
            _resolve_writable_artifact_table("nonexistent_type")


# -- _read_script_file ---------------------------------------------------------


class TestReadScriptFile:
    """Tests for the _read_script_file helper."""

    def test_reads_absolute_path(self, tmp_path: Any) -> None:
        """Reads a UTF-8 file from an absolute path and returns its content."""
        script = tmp_path / "test_script.js"
        script.write_text("var x = 1;", encoding="utf-8")

        result = _read_script_file(str(script))
        assert result == "var x = 1;"

    def test_rejects_relative_path(self) -> None:
        """Raises ValueError when given a relative path."""
        with pytest.raises(ValueError, match="absolute"):
            _read_script_file("relative/path/script.js")

    def test_file_not_found(self, tmp_path: Any) -> None:
        """Raises FileNotFoundError for a non-existent file."""
        nonexistent = tmp_path / "does_not_exist.js"
        with pytest.raises(FileNotFoundError):
            _read_script_file(str(nonexistent))

    def test_file_too_large(self, tmp_path: Any) -> None:
        """Raises ValueError when file exceeds MAX_SCRIPT_FILE_BYTES."""
        large_file = tmp_path / "large_script.js"
        large_file.write_bytes(b"x" * (MAX_SCRIPT_FILE_BYTES + 1))

        with pytest.raises(ValueError, match="too large"):
            _read_script_file(str(large_file))

    def test_non_utf8_raises(self, tmp_path: Any) -> None:
        """Raises UnicodeDecodeError for a file with invalid UTF-8 bytes."""
        bad_file = tmp_path / "bad_encoding.js"
        bad_file.write_bytes(b"\xff\xfe\x80\x81")

        with pytest.raises(UnicodeDecodeError):
            _read_script_file(str(bad_file))


# -- artifact_create -----------------------------------------------------------


class TestArtifactCreate:
    """Tests for the artifact_create tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_creates_artifact_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Creates an artifact and returns the masked record."""
        respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "name": "TestScriptInclude",
                        "script": "var x = 1;",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"name": "TestScriptInclude", "script": "var x = 1;"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "sys_script_include"
        assert result["data"]["artifact_type"] == "script_include"
        assert result["data"]["sys_id"] == "new001"
        assert result["data"]["record"]["name"] == "TestScriptInclude"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    @respx.mock
    async def test_creates_artifact_with_script_path(
        self, settings: Settings, auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Reads script content from a local file and sets it as the script field."""
        script_file = tmp_path / "my_script.js"
        script_file.write_text("function test() { return true; }", encoding="utf-8")

        respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new002",
                        "name": "FileScript",
                        "script": "function test() { return true; }",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"name": "FileScript"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new002"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_warns_on_override(
        self, settings: Settings, auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Warns when script_path overrides an existing 'script' key in data."""
        script_file = tmp_path / "override.js"
        script_file.write_text("new content", encoding="utf-8")

        respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new003",
                        "name": "Override",
                        "script": "new content",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"name": "Override", "script": "old content"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result.get("warnings") is not None
        assert any("overridden" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"name": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when data is not valid JSON."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](artifact_type="script_include", data="not valid json")
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_unknown_artifact_type(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for an unknown artifact type."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="nonexistent_type",
            data=json.dumps({"name": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "unknown" in result["error"]["message"].lower()


# -- artifact_update -----------------------------------------------------------


class TestArtifactUpdate:
    """Tests for the artifact_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_updates_artifact_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Updates an artifact and returns the masked record."""
        respx.patch(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_ART001,
                        "name": "UpdatedScript",
                        "active": "true",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"active": "true"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "sys_script_include"
        assert result["data"]["artifact_type"] == "script_include"
        assert result["data"]["sys_id"] == SYS_ID_ART001
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    @respx.mock
    async def test_updates_artifact_with_script_path(
        self, settings: Settings, auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Reads script content from a local file when updating."""
        script_file = tmp_path / "update_script.js"
        script_file.write_text("var updated = true;", encoding="utf-8")

        respx.patch(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_ART001,
                        "name": "FileUpdate",
                        "script": "var updated = true;",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "FileUpdate"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == SYS_ID_ART001

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_warns_on_override(
        self, settings: Settings, auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Warns when script_path overrides an existing 'script' key in changes."""
        script_file = tmp_path / "override_update.js"
        script_file.write_text("new update content", encoding="utf-8")

        respx.patch(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_ART001,
                        "script": "new update content",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"script": "old content"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result.get("warnings") is not None
        assert any("overridden" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"active": "false"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_sys_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for an invalid sys_id format."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id="not-a-valid-hex-id",
            changes=json.dumps({"active": "false"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_unknown_artifact_type(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for an unknown artifact type."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="nonexistent_type",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "unknown" in result["error"]["message"].lower()


# -- Tool registration ---------------------------------------------------------


class TestToolRegistration:
    """Tests for tool registration and TOOL_NAMES constant."""

    def test_tool_names_constant(self) -> None:
        """TOOL_NAMES contains the expected tool names."""
        assert TOOL_NAMES == ["artifact_create", "artifact_update"]

    def test_all_tools_registered(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """All TOOL_NAMES are registered in the MCP tool map."""
        tools = _register_and_get_tools(settings, auth_provider)
        for name in TOOL_NAMES:
            assert name in tools, f"Tool '{name}' not registered"
