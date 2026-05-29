"""Tests for the unified ``record_write`` and ``record_apply`` tools."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from servicenow_mcp.tools._artifact import validate_ui_macro_xml
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
METADATA_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
SYS_DB_OBJECT_URL = f"{BASE_URL}/api/now/table/sys_db_object"
NO_MANDATORY_RESPONSE = httpx.Response(200, json={"result": []})

SYS_ID_INC001 = "a" * 32
SYS_ID_ART001 = "c" * 32


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified record_write test scope."""
    return BasicAuthProvider(settings)


@pytest.fixture()
def script_settings(tmp_path: Any) -> Settings:
    """Test settings with script_allowed_root pointed at tmp_path."""
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
    """BasicAuthProvider matching script_settings."""
    return BasicAuthProvider(script_settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified record_write tools on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.record_write import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


def _mock_dictionary(table: str, fields: list[dict[str, str]]) -> None:
    """Mock the sys_db_object + sys_dictionary fetch for ``table`` with ``fields``."""
    # sys_db_object lookup returns no super_class (root table) - the chain stops here.
    respx.get(SYS_DB_OBJECT_URL).mock(
        return_value=httpx.Response(200, json={"result": [{"super_class": ""}]}),
    )
    # sys_dictionary returns the supplied fields for the requested table name.
    respx.get(METADATA_URL).mock(
        return_value=httpx.Response(200, json={"result": fields}),
    )
    del table  # respx routes by URL; table is encoded into the query string


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------


class TestActionDispatch:
    """Cross-argument validation that runs before any HTTP call."""

    @pytest.mark.asyncio()
    async def test_unknown_action_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](action="frobnicate", table="incident")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "frobnicate" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_create_action_requires_data(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](action="create", table="incident")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "data is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_create_with_sys_id_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            sys_id=SYS_ID_INC001,
            data=json.dumps({"short_description": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "sys_id must be empty" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_update_requires_sys_id_and_data(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)

        raw = await tools["record_write"](action="update", table="incident", data=json.dumps({"state": "2"}))
        assert decode_response(raw)["error"]["message"].startswith("sys_id is required")

        raw = await tools["record_write"](action="update", table="incident", sys_id=SYS_ID_INC001)
        assert decode_response(raw)["error"]["message"].startswith("data is required")

    @pytest.mark.asyncio()
    async def test_delete_with_data_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="delete",
            table="incident",
            sys_id=SYS_ID_INC001,
            data=json.dumps({"x": 1}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "data must be empty" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_missing_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](action="create", data=json.dumps({"x": 1}))
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_script_field_without_script_path_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_script",
            data=json.dumps({"name": "BR1"}),
            script_field="script",
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "script_field requires script_path" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_oversized_data_payload_rejected_before_token_creation(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """``data`` >1 MiB returns a structured error and creates no preview token."""
        from servicenow_mcp.tools import record_write as record_write_module

        # Patch PreviewTokenStore.create to detect any token-allocation attempt.
        # If the cap fires correctly, this never runs.
        created_tokens: list[Any] = []

        original_create = record_write_module.PreviewTokenStore.create

        async def _spy_create(self: Any, payload: dict[str, Any]) -> str:
            created_tokens.append(payload)
            return await original_create(self, payload)

        with patch.object(record_write_module.PreviewTokenStore, "create", _spy_create):
            tools = _register_and_get_tools(settings, auth_provider)
            # 1 MiB + 1 byte payload as a JSON object: pad a single field.
            oversized_value = "x" * (1 * 1024 * 1024 + 1)
            payload = json.dumps({"short_description": oversized_value})
            assert len(payload.encode("utf-8")) > 1 * 1024 * 1024
            raw = await tools["record_write"](
                action="create",
                table="incident",
                data=payload,
            )

        result = decode_response(raw)
        assert result["status"] == "error"
        assert "1 MiB" in result["error"]["message"]
        assert created_tokens == [], "no preview token should be allocated for oversized payload"


# ---------------------------------------------------------------------------
# Standard record write (no script_path)
# ---------------------------------------------------------------------------


class TestStandardRecordWrite:
    """Plain record writes against a non-script-bearing table."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_preview_returns_token(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"short_description": "Test", "state": "1"}),
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["action"] == "create"
        assert result["data"]["table"] == "incident"
        assert "preview_token" in result["data"]
        assert result["data"]["preview"]["data"]["short_description"] == "Test"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_direct_commits_immediately(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "new001", "short_description": "Test"}},
            )
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"short_description": "Test"}),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new001"
        assert "preview_token" not in result["data"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_preview_includes_diff(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID_INC001, "state": "1"}}),
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="update",
            table="incident",
            sys_id=SYS_ID_INC001,
            data=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        diff = result["data"]["preview"]["diff"]
        assert diff["state"] == {"old": "1", "new": "2"}
        assert "preview_token" in result["data"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_delete_preview_stores_snapshot(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "short_description": "doomed",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            ),
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](action="delete", table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)
        assert result["status"] == "success"
        snap = result["data"]["preview"]["record_snapshot"]
        assert snap["short_description"] == "doomed"
        assert snap["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_apply_consumes_token(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        # Phase 1: preview create
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"short_description": "Apply me"}),
        )
        token = decode_response(preview_raw)["data"]["preview_token"]

        # Phase 2: apply
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "new042", "short_description": "Apply me"}},
            ),
        )
        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new042"

        # Token is single-use - second call must fail.
        raw2 = await tools["record_apply"](preview_token=token)
        assert decode_response(raw2)["status"] == "error"

    @pytest.mark.asyncio()
    async def test_record_apply_unknown_token_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_apply"](preview_token="not-a-real-token")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Invalid or expired" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Script-path writes (dictionary-driven field detection)
# ---------------------------------------------------------------------------


class TestScriptPathWrite:
    """``script_path`` writes a file into the resolved script-bearing field."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_writes_to_first_detected_field(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "br.js"
        script_file.write_text("gs.info('hello');\n")

        _mock_dictionary(
            "sys_script",
            [{"element": "script", "internal_type": "script", "attributes": ""}],
        )
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "BR1"}},
            ),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_script",
            data=json.dumps({"name": "BR1"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "success"

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["script"] == "gs.info('hello');\n"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_with_override_writes_to_named_field(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # sp_widget has multiple script fields - override to 'template'
        script_file = tmp_path / "widget.html"
        script_file.write_text("<div>{{c.data.msg}}</div>\n")

        _mock_dictionary(
            "sp_widget",
            [
                {"element": "client_script", "internal_type": "script_client", "attributes": ""},
                {"element": "script", "internal_type": "script", "attributes": ""},
                {
                    "element": "template",
                    "internal_type": "html",
                    "attributes": "tinymce_allow_all=true",
                },
            ],
        )
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sp_widget").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "W1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sp_widget",
            data=json.dumps({"name": "W1"}),
            script_path=str(script_file),
            script_field="template",
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["template"] == "<div>{{c.data.msg}}</div>\n"
        assert "client_script" not in sent

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_invalid_field_returns_error(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "br.js"
        script_file.write_text("// noop\n")

        _mock_dictionary(
            "sys_script",
            [{"element": "script", "internal_type": "script", "attributes": ""}],
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_script",
            data=json.dumps({"name": "BR1"}),
            script_path=str(script_file),
            script_field="client_script",
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Invalid script_field" in result["error"]["message"]
        assert "sys_script" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_no_script_fields_detected_returns_error(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # Table with no script-bearing fields - file write should fail cleanly.
        script_file = tmp_path / "x.js"
        script_file.write_text("// noop\n")

        _mock_dictionary(
            "incident",
            [{"element": "short_description", "internal_type": "string", "attributes": ""}],
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"short_description": "x"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "no script-bearing fields" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_overrides_data_field_with_warning(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "br.js"
        script_file.write_text("FILE_WINS\n")

        _mock_dictionary(
            "sys_script",
            [{"element": "script", "internal_type": "script", "attributes": ""}],
        )
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "BR1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_script",
            data=json.dumps({"name": "BR1", "script": "DATA_LOSES"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        warnings = result.get("warnings") or []
        assert any("overridden by script_path" in w for w in warnings)

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["script"] == "FILE_WINS\n"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_traversal_blocked(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # File exists outside script_allowed_root -> PermissionError -> error envelope.
        _mock_dictionary(
            "sys_script",
            [{"element": "script", "internal_type": "script", "attributes": ""}],
        )
        outside = tmp_path.parent / "outside.js"
        outside.write_text("nope")
        try:
            tools = _register_and_get_tools(script_settings, script_auth_provider)
            raw = await tools["record_write"](
                action="create",
                table="sys_script",
                data=json.dumps({"name": "BR1"}),
                script_path=str(outside),
                preview=False,
            )
            result = decode_response(raw)
            assert result["status"] == "error"
            assert "outside the allowed root" in result["error"]["message"]
        finally:
            outside.unlink(missing_ok=True)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_path_too_large_returns_error(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        from servicenow_mcp.tools._artifact import MAX_SCRIPT_FILE_BYTES

        _mock_dictionary(
            "sys_script",
            [{"element": "script", "internal_type": "script", "attributes": ""}],
        )
        big = tmp_path / "big.js"
        big.write_bytes(b"a" * (MAX_SCRIPT_FILE_BYTES + 1))

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_script",
            data=json.dumps({"name": "BR1"}),
            script_path=str(big),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "too large" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Policy gates
# ---------------------------------------------------------------------------


class TestPolicyGates:
    """Defense-in-depth checks: denied tables, prod env, sys_id format."""

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table=denied,
            data=json.dumps({"x": 1}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_production_blocks_writes_with_proper_settings(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"short_description": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_format_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="update",
            table="incident",
            sys_id="not-a-real-sys-id",
            data=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "sys_id" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# ui_macro / XML validation
# ---------------------------------------------------------------------------


class TestUIMacroXMLValidation:
    """XML validation fires when the detected field has ``internal_type == 'xml'``."""

    def test_validate_ui_macro_xml_accepts_jelly_root(self) -> None:
        jelly = '<j:jelly xmlns:j="jelly:core" xmlns:g="glide" xmlns:g2="null"><g:evaluate>1</g:evaluate></j:jelly>'
        assert validate_ui_macro_xml(jelly) is None

    def test_validate_ui_macro_xml_rejects_malformed(self) -> None:
        bad = "<unclosed>"
        error = validate_ui_macro_xml(bad)
        assert error is not None
        assert "XML content is not well-formed" in error

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_macro_write_accepts_well_formed_xml(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "macro.xml"
        script_file.write_text(
            '<j:jelly xmlns:j="jelly:core" xmlns:g="glide" xmlns:g2="null"><g:evaluate>1</g:evaluate></j:jelly>\n'
        )

        _mock_dictionary(
            "sys_ui_macro",
            [
                {
                    "element": "xml",
                    "internal_type": "xml",
                    "attributes": "tinymce_allow_all=true",
                }
            ],
        )
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_ui_macro").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "M1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_ui_macro",
            data=json.dumps({"name": "M1"}),
            script_path=str(script_file),
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"
        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["xml"].startswith("<j:jelly")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_macro_write_rejects_malformed_xml(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "macro.xml"
        script_file.write_text("<not-well-formed>\n")

        _mock_dictionary(
            "sys_ui_macro",
            [
                {
                    "element": "xml",
                    "internal_type": "xml",
                    "attributes": "tinymce_allow_all=true",
                }
            ],
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="sys_ui_macro",
            data=json.dumps({"name": "M1"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "not well-formed" in result["error"]["message"]
