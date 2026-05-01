"""Tests for artifact write tools (artifact_create, artifact_update)."""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.artifact_write import (
    MAX_SCRIPT_FILE_BYTES,
    SCRIPT_FIELD_MAP,
    TOOL_NAMES,
    WRITABLE_ARTIFACT_TABLES,
    _parse_and_validate_payload,
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


@pytest.fixture()
def script_settings(tmp_path: Any) -> Settings:
    """Create test settings with script_allowed_root set to tmp_path."""
    env = {
        "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "s3cret",  # NOSONAR - test-only dummy credential
        "SERVICENOW_ENV": "dev",
        "MCP_TOOL_PACKAGE": "full",
        "SCRIPT_ALLOWED_ROOT": str(tmp_path),
    }
    with patch.dict("os.environ", env, clear=True):
        return Settings(_env_file=None)


@pytest.fixture()
def script_auth_provider(script_settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from script_settings."""
    return BasicAuthProvider(script_settings)


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

    def test_script_field_map_types_in_writable_tables(self) -> None:
        """All types in SCRIPT_FIELD_MAP are also in WRITABLE_ARTIFACT_TABLES."""
        for artifact_type in SCRIPT_FIELD_MAP:
            assert artifact_type in WRITABLE_ARTIFACT_TABLES


# -- _read_script_file ---------------------------------------------------------


class TestReadScriptFile:
    """Tests for the _read_script_file helper."""

    def test_reads_absolute_path(self, tmp_path: Any) -> None:
        """Reads a UTF-8 file from an absolute path and returns its content."""
        script = tmp_path / "test_script.js"
        script.write_text("var x = 1;", encoding="utf-8")

        result = _read_script_file(str(script), allowed_root=str(tmp_path))
        assert result == "var x = 1;"

    def test_rejects_relative_path(self) -> None:
        """Raises ValueError when given a relative path."""
        with pytest.raises(ValueError, match="absolute path"):
            _read_script_file("relative/path/script.js", allowed_root="/nonexistent/root")

    def test_rejects_empty_allowed_root(self, tmp_path: Any) -> None:
        """Raises ValueError when allowed_root is empty."""
        script = tmp_path / "script.js"
        script.write_text("var x = 1;", encoding="utf-8")

        with pytest.raises(ValueError, match="script_allowed_root must be configured"):
            _read_script_file(str(script), allowed_root="")

    def test_file_not_found(self, tmp_path: Any) -> None:
        """Raises FileNotFoundError for a non-existent file."""
        nonexistent = tmp_path / "does_not_exist.js"
        with pytest.raises(FileNotFoundError):
            _read_script_file(str(nonexistent), allowed_root=str(tmp_path))

    def test_file_too_large(self, tmp_path: Any) -> None:
        """Raises ValueError when file exceeds MAX_SCRIPT_FILE_BYTES."""
        large_file = tmp_path / "large_script.js"
        large_file.write_bytes(b"x" * (MAX_SCRIPT_FILE_BYTES + 1))

        with pytest.raises(ValueError, match="too large"):
            _read_script_file(str(large_file), allowed_root=str(tmp_path))

    def test_non_utf8_raises(self, tmp_path: Any) -> None:
        """Raises UnicodeDecodeError for a file with invalid UTF-8 bytes."""
        bad_file = tmp_path / "bad_encoding.js"
        bad_file.write_bytes(b"\xff\xfe\x80\x81")

        with pytest.raises(UnicodeDecodeError):
            _read_script_file(str(bad_file), allowed_root=str(tmp_path))

    def test_path_outside_allowed_root(self, tmp_path: Any) -> None:
        """Raises PermissionError when the resolved path is outside allowed_root."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        script = outside_dir / "evil.js"
        script.write_text("alert(1);", encoding="utf-8")

        with pytest.raises(PermissionError, match="outside the allowed root"):
            _read_script_file(str(script), allowed_root=str(allowed_dir))

    def test_path_within_allowed_root(self, tmp_path: Any) -> None:
        """Succeeds when the resolved path is within allowed_root."""
        script = tmp_path / "allowed_script.js"
        script.write_text("var ok = true;", encoding="utf-8")

        result = _read_script_file(str(script), allowed_root=str(tmp_path))
        assert result == "var ok = true;"

    def test_symlink_outside_allowed_root(self, tmp_path: Any) -> None:
        """Raises PermissionError when a symlink resolves outside allowed_root."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        real_file = outside_dir / "secret.js"
        real_file.write_text("secret", encoding="utf-8")

        symlink = allowed_dir / "link.js"
        symlink.symlink_to(real_file)

        with pytest.raises(PermissionError, match="outside the allowed root"):
            _read_script_file(str(symlink), allowed_root=str(allowed_dir))

    def test_inaccessible_allowed_root(self, tmp_path: Any) -> None:
        """Raises ValueError when allowed_root does not exist."""
        script = tmp_path / "script.js"
        script.write_text("var x = 1;", encoding="utf-8")
        bogus_root = str(tmp_path / "nonexistent_root")

        with pytest.raises(ValueError, match="not accessible"):
            _read_script_file(str(script), allowed_root=bogus_root)

    def test_directory_path_rejected(self, tmp_path: Any) -> None:
        """Raises FileNotFoundError when the path is a directory, not a file."""
        a_dir = tmp_path / "subdir"
        a_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="not a regular file"):
            _read_script_file(str(a_dir), allowed_root=str(tmp_path))


# -- _parse_and_validate_payload -----------------------------------------------


class TestParseAndValidatePayload:
    """Tests for the _parse_and_validate_payload helper."""

    def test_valid_json_object(self) -> None:
        """Returns (dict, warnings) for valid JSON object with no script_path."""
        result = _parse_and_validate_payload('{"name": "Test"}', "data", "script_include", "", "", "corr-1")
        assert isinstance(result, tuple)
        payload, warnings = result
        assert payload == {"name": "Test"}
        assert warnings == []

    def test_non_dict_returns_error_string(self) -> None:
        """Returns formatted error string for non-dict JSON."""
        result = _parse_and_validate_payload('["a", "b"]', "data", "script_include", "", "", "corr-1")
        assert isinstance(result, str)

    def test_invalid_key_returns_error_string(self) -> None:
        """Returns a formatted error envelope for keys that fail validate_identifier."""
        result = _parse_and_validate_payload('{"INVALID-KEY!": "v"}', "data", "script_include", "", "", "corr-1")
        assert isinstance(result, str)
        decoded = decode_response(result)
        assert decoded["status"] == "error"
        assert "invalid key" in decoded["error"]["message"].lower()

    def test_script_path_injects_content(self, tmp_path: Any) -> None:
        """Injects script file content into payload via SCRIPT_FIELD_MAP."""
        script = tmp_path / "inject.xml"
        script.write_text("<hello/>", encoding="utf-8")

        result = _parse_and_validate_payload('{"name": "M"}', "data", "ui_macro", str(script), str(tmp_path), "corr-1")
        assert isinstance(result, tuple)
        payload, warnings = result
        assert payload["xml"] == "<hello/>"
        assert warnings == []

    def test_script_path_warns_on_override(self, tmp_path: Any) -> None:
        """Emits warning when script_path overrides existing field."""
        script = tmp_path / "override.js"
        script.write_text("new", encoding="utf-8")

        result = _parse_and_validate_payload(
            '{"script": "old"}', "changes", "script_include", str(script), str(tmp_path), "corr-1"
        )
        assert isinstance(result, tuple)
        payload, warnings = result
        assert payload["script"] == "new"
        assert len(warnings) == 1
        assert "overridden" in warnings[0].lower()


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
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Reads script content from a local file and sets it as the script field."""
        script_file = tmp_path / "my_script.js"
        script_file.write_text("function test() { return true; }", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
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

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"name": "FileScript"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new002"

        # Verify outbound request body contains the script content
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["script"] == "function test() { return true; }"
        assert request_body["name"] == "FileScript"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_warns_on_override(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
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

        tools = _register_and_get_tools(script_settings, script_auth_provider)
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

    @pytest.mark.asyncio()
    async def test_non_dict_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when data is a JSON array instead of object."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps(["not", "an", "object"]),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "object" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_key_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when data contains an invalid field name."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="script_include",
            data=json.dumps({"INVALID-KEY!": "value"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"


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
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """Reads script content from a local file when updating."""
        script_file = tmp_path / "update_script.js"
        script_file.write_text("var updated = true;", encoding="utf-8")

        route = respx.patch(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
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

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "FileUpdate"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == SYS_ID_ART001

        # Verify outbound request body contains the script content
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["script"] == "var updated = true;"
        assert request_body["name"] == "FileUpdate"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_warns_on_override(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
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

        tools = _register_and_get_tools(script_settings, script_auth_provider)
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

    @pytest.mark.asyncio()
    async def test_non_dict_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when changes is a JSON array instead of object."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps(["not", "an", "object"]),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "object" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_key_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when changes contains an invalid field name."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"INVALID-KEY!": "value"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"


# -- SCRIPT_FIELD_MAP ----------------------------------------------------------


class TestScriptFieldMap:
    """Tests for SCRIPT_FIELD_MAP per-artifact script field routing."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_macro_writes_to_xml_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for ui_macro is written to the 'xml' field."""
        script_file = tmp_path / "macro.xml"
        script_file.write_text("<j:jelly><p>Hello</p></j:jelly>", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sys_ui_macro").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "macro001", "name": "TestMacro", "xml": "<j:jelly><p>Hello</p></j:jelly>"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="ui_macro",
            data=json.dumps({"name": "TestMacro"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["xml"] == "<j:jelly><p>Hello</p></j:jelly>"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_page_writes_to_html_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for ui_page is written to the 'html' field."""
        script_file = tmp_path / "page.html"
        script_file.write_text("<div>Hello</div>", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sys_ui_page").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "page001", "name": "TestPage", "html": "<div>Hello</div>"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="ui_page",
            data=json.dumps({"name": "TestPage"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["html"] == "<div>Hello</div>"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_with_field_map(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """artifact_update also uses SCRIPT_FIELD_MAP for the correct field."""
        script_file = tmp_path / "macro_update.xml"
        script_file.write_text("<updated/>", encoding="utf-8")

        route = respx.patch(f"{BASE_URL}/api/now/table/sys_ui_macro/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_ART001, "xml": "<updated/>"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="ui_macro",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "UpdatedMacro"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["xml"] == "<updated/>"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_policy_writes_to_script_true_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for ui_policy is written to the 'script_true' field."""
        script_file = tmp_path / "policy.js"
        script_file.write_text("g_form.setVisible('field', true);", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "pol001",
                        "name": "TestPolicy",
                        "script_true": "g_form.setVisible('field', true);",
                    }
                },
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="ui_policy",
            data=json.dumps({"name": "TestPolicy"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["script_true"] == "g_form.setVisible('field', true);"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_widget_writes_to_client_script_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for widget is written to the 'client_script' field."""
        script_file = tmp_path / "widget.js"
        script_file.write_text("function($scope) { $scope.data.ready = true; }", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sp_widget").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "wid001", "name": "TestWidget"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="widget",
            data=json.dumps({"name": "TestWidget"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["client_script"] == "function($scope) { $scope.data.ready = true; }"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_scripted_rest_resource_writes_to_operation_script_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for scripted_rest_resource is written to the 'operation_script' field."""
        script_file = tmp_path / "rest_op.js"
        script_file.write_text("(function process(request, response) {})(request, response);", encoding="utf-8")

        route = respx.post(f"{BASE_URL}/api/now/table/sys_ws_operation").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "rest001", "name": "TestRestOp"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create"](
            artifact_type="scripted_rest_resource",
            data=json.dumps({"name": "TestRestOp"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["operation_script"] == "(function process(request, response) {})(request, response);"
        assert "script" not in request_body

    @pytest.mark.asyncio()
    @respx.mock
    async def test_notification_script_writes_to_advanced_condition_field(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content for notification_script is written to the 'advanced_condition' field."""
        script_file = tmp_path / "notif.js"
        script_file.write_text("current.priority == 1", encoding="utf-8")

        route = respx.patch(f"{BASE_URL}/api/now/table/sysevent_email_action/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "TestNotif"}},
            )
        )

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_update"](
            artifact_type="notification_script",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "TestNotif"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_body = json.loads(route.calls[0].request.content)
        assert request_body["advanced_condition"] == "current.priority == 1"
        assert "script" not in request_body


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
