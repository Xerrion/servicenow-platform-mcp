"""Tests for artifact write tools (preview/apply flow)."""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import MASK_VALUE
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
SYS_ID_ART001 = "a" * 32


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
        """Returns (dict, warnings, script_field) for valid JSON object with no script_path."""
        result = _parse_and_validate_payload('{"name": "Test"}', "data", "script_include", "", "", "corr-1")
        assert isinstance(result, tuple)
        payload, warnings, script_field = result
        assert payload == {"name": "Test"}
        assert warnings == []
        assert script_field is None

    def test_non_dict_returns_error_string(self) -> None:
        """Returns formatted error string for non-dict JSON."""
        result = _parse_and_validate_payload('["a", "b"]', "data", "script_include", "", "", "corr-1")
        assert isinstance(result, str)

    def test_invalid_key_raises(self) -> None:
        """Raises ValueError for keys that fail validate_identifier."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            _parse_and_validate_payload('{"INVALID-KEY!": "v"}', "data", "script_include", "", "", "corr-1")

    def test_script_path_injects_content(self, tmp_path: Any) -> None:
        """Injects script file content into payload via SCRIPT_FIELD_MAP."""
        script = tmp_path / "inject.xml"
        script.write_text("<hello/>", encoding="utf-8")

        result = _parse_and_validate_payload('{"name": "M"}', "data", "ui_macro", str(script), str(tmp_path), "corr-1")
        assert isinstance(result, tuple)
        payload, warnings, script_field = result
        assert payload["xml"] == "<hello/>"
        assert warnings == []
        assert script_field == "xml"

    def test_script_path_warns_on_override(self, tmp_path: Any) -> None:
        """Emits warning when script_path overrides existing field."""
        script = tmp_path / "override.js"
        script.write_text("new", encoding="utf-8")

        result = _parse_and_validate_payload(
            '{"script": "old"}', "changes", "script_include", str(script), str(tmp_path), "corr-1"
        )
        assert isinstance(result, tuple)
        payload, warnings, script_field = result
        assert payload["script"] == "new"
        assert len(warnings) == 1
        assert "overridden" in warnings[0].lower()
        assert script_field == "script"


# -- artifact_create_preview ---------------------------------------------------


class TestArtifactCreatePreview:
    """Tests for artifact_create_preview."""

    @pytest.mark.asyncio()
    async def test_returns_token_and_summary(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Preview validates input and returns a token plus safe summary."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "TestInclude", "script": "var x = 1;", "api_key": "leaked"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["action"] == "create"
        assert data["table"] == "sys_script_include"
        assert data["artifact_type"] == "script_include"
        assert isinstance(data["token"], str)
        assert data["token"]
        assert set(data["fields"]) == {"name", "script", "api_key"}
        # Script body is summarised, not echoed
        assert data["summary"]["script"]["size_bytes"] == len("var x = 1;")
        assert data["summary"]["script"]["head"] == "var x = 1;"
        # Sensitive field value never appears in the envelope
        assert data["summary"]["api_key"] == MASK_VALUE
        assert data["ttl_seconds"] > 0

    @pytest.mark.asyncio()
    async def test_script_body_head_truncated(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Script body 'head' is truncated to the configured character budget."""
        tools = _register_and_get_tools(settings, auth_provider)
        long_body = "a" * 500
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "Long", "script": long_body}),
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        head = result["data"]["summary"]["script"]["head"]
        assert len(head) <= 80
        assert result["data"]["summary"]["script"]["size_bytes"] == 500

    @pytest.mark.asyncio()
    async def test_script_path_size_summarised(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path content is summarised by size, not echoed verbatim."""
        script_file = tmp_path / "inc.js"
        script_file.write_text("function test() { return 42; }", encoding="utf-8")

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "FromFile"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["script_field"] == "script"
        assert data["summary"]["script"]["size_bytes"] == len("function test() { return 42; }")
        assert data["summary"]["script"]["head"].startswith("function test()")

    @pytest.mark.asyncio()
    async def test_script_path_size_limit_enforced_at_preview(
        self, script_settings: Settings, script_auth_provider: BasicAuthProvider, tmp_path: Any
    ) -> None:
        """script_path exceeding 1 MB is rejected before any token is issued."""
        oversize = tmp_path / "huge.js"
        oversize.write_bytes(b"x" * (MAX_SCRIPT_FILE_BYTES + 1))

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "Huge"}),
            script_path=str(oversize),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "too large" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_key_blocked_at_preview(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Invalid payload keys fail at preview time, before any token is issued."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"INVALID-KEY!": "value"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Preview is blocked in production environments."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "Test"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_unknown_artifact_type(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unknown artifact_type fails at preview time."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_create_preview"](
            artifact_type="nonexistent_type",
            data=json.dumps({"name": "Test"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "unknown" in result["error"]["message"].lower()


# -- artifact_update_preview ---------------------------------------------------


class TestArtifactUpdatePreview:
    """Tests for artifact_update_preview."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_diff_with_masked_sensitive_fields(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Update preview fetches current record and masks sensitive fields in the diff."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_ART001,
                        "name": "OldName",
                        "script": "var old = 1;",
                        "api_key": "OLD_SECRET",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "NewName", "api_key": "NEW_SECRET", "script": "var n = 2;"}),  # NOSONAR
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["action"] == "update"
        assert data["sys_id"] == SYS_ID_ART001
        # Non-sensitive, non-script field echoes both sides
        assert data["diff"]["name"] == {"old": "OldName", "new": "NewName"}
        # Sensitive field masked on both sides
        assert data["diff"]["api_key"] == {"old": MASK_VALUE, "new": MASK_VALUE}
        # Script body summarised on both sides
        assert data["diff"]["script"]["new"]["size_bytes"] == len("var n = 2;")
        assert data["diff"]["script"]["old"]["size_bytes"] == len("var old = 1;")
        # Full current record is also masked for 'before' context
        assert data["before"]["api_key"] == MASK_VALUE
        # sys_script_include has 'script' in TABLE_SCRIPT_FIELDS -> masked in before
        assert data["before"]["script"] != "var old = 1;"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_content_never_echoed(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        """A full script file read via script_path is summarised, not echoed."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "Inc", "script": ""}},
            )
        )

        script_file = tmp_path / "body.js"
        full_body = "function leaked() { return 'super secret body content that must not appear verbatim'; }"
        script_file.write_text(full_body, encoding="utf-8")

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "Inc"}),
            script_path=str(script_file),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        # The full body must not appear anywhere in the serialized envelope
        assert "super secret body content that must not appear verbatim" not in raw
        assert result["data"]["diff"]["script"]["new"]["size_bytes"] == len(full_body)

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """sys_id validation runs before any current-record fetch."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id="not-a-valid-hex-id",
            changes=json.dumps({"name": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_invalid_key_blocked_at_preview(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Invalid change keys are rejected at preview time."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"INVALID-KEY!": "value"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Update preview blocked in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()


# -- artifact_apply ------------------------------------------------------------


class TestArtifactApply:
    """Tests for artifact_apply."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_create(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """artifact_apply consumes a create-preview token and writes the record."""
        tools = _register_and_get_tools(settings, auth_provider)

        preview_raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "TestInc", "script": "var x = 1;"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        route = respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "name": "TestInc",
                        "script": "var x = 1;",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        raw = await tools["artifact_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["action"] == "create"
        assert result["data"]["sys_id"] == "new001"
        assert result["data"]["record"]["password"] == MASK_VALUE
        assert route.called
        # Outbound body must carry the full script even though the preview
        # envelope never did.
        body = json.loads(route.calls[0].request.content)
        assert body["script"] == "var x = 1;"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_update(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """artifact_apply consumes an update-preview token and patches the record."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID_ART001, "name": "Old"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["artifact_update_preview"](
            artifact_type="script_include",
            sys_id=SYS_ID_ART001,
            changes=json.dumps({"name": "New"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        route = respx.patch(f"{BASE_URL}/api/now/table/sys_script_include/{SYS_ID_ART001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "New"}},
            )
        )

        raw = await tools["artifact_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["action"] == "update"
        assert result["data"]["sys_id"] == SYS_ID_ART001
        assert route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_token_is_single_use(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A preview token cannot be applied twice."""
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "Once"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        respx.post(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": "n1", "name": "Once"}})
        )

        first = decode_response(await tools["artifact_apply"](preview_token=token))
        assert first["status"] == "success"

        second = decode_response(await tools["artifact_apply"](preview_token=token))
        assert second["status"] == "error"
        assert "invalid" in second["error"]["message"].lower() or "expired" in second["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_token_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An unknown token fails with a clear error envelope."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["artifact_apply"](preview_token="not-a-real-token")
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_write_gate_recheck_on_apply(
        self, settings: Settings, auth_provider: BasicAuthProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful preview still fails at apply time if prod mode flips on in between."""
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["artifact_create_preview"](
            artifact_type="script_include",
            data=json.dumps({"name": "Block"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # Flip the production gate between preview and apply - writes must stop.
        monkeypatch.setattr(type(settings), "is_production", property(lambda _self: True))

        raw = await tools["artifact_apply"](preview_token=token)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()


# -- Tool registration ---------------------------------------------------------


class TestToolRegistration:
    """Tests for tool registration and TOOL_NAMES constant."""

    def test_tool_names_constant(self) -> None:
        """TOOL_NAMES contains exactly the preview/apply trio - no direct-write names."""
        assert TOOL_NAMES == [
            "artifact_create_preview",
            "artifact_update_preview",
            "artifact_apply",
        ]

    def test_all_tools_registered(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """All TOOL_NAMES are registered in the MCP tool map."""
        tools = _register_and_get_tools(settings, auth_provider)
        for name in TOOL_NAMES:
            assert name in tools, f"Tool '{name}' not registered"

    def test_legacy_direct_write_tools_not_registered(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The CRIT-1 direct-write tools must be gone from the registry."""
        tools = _register_and_get_tools(settings, auth_provider)
        assert "artifact_create" not in tools
        assert "artifact_update" not in tools
