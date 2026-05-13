"""Tests for the unified ``record_write`` and ``record_apply`` tools (Phase 3a)."""

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
from servicenow_mcp.tools._artifact import (
    SCRIPT_FIELD_MAP,
    WRITABLE_ARTIFACT_TABLES,
    _resolve_writable_artifact_table,
    _validate_xml_content,
    primary_script_field,
)
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
METADATA_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
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

    from servicenow_mcp.tools.unified.record_write import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


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
    async def test_missing_table_and_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](action="create", data=json.dumps({"x": 1}))
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_script_path_without_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"x": 1}),
            script_path="/tmp/foo.js",
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "script_path requires artifact_type" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Standard record write (no artifact_type)
# ---------------------------------------------------------------------------


class TestStandardRecordWrite:
    """Plain record writes without artifact_type."""

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
# Artifact write (artifact_type set)
# ---------------------------------------------------------------------------


class TestArtifactWrite:
    """Artifact mode: artifact_type maps to a writable table; SCRIPT_FIELD_MAP applies."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_artifact_create_resolves_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        respx.post(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "BR1"}},
            ),
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="business_rule",
            data=json.dumps({"name": "BR1", "script": "// noop"}),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["table"] == "sys_script"
        assert result["data"]["artifact_type"] == "business_rule"
        assert result["data"]["sys_id"] == SYS_ID_ART001

    @pytest.mark.asyncio()
    async def test_artifact_unknown_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="unknown_kind",
            data=json.dumps({"name": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown artifact_type" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_artifact_script_path_injects_into_default_script_field(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "br.js"
        script_file.write_text("gs.info('hello');\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": SYS_ID_ART001, "name": "BR1"}},
            ),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="business_rule",
            data=json.dumps({"name": "BR1"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "success"

        # Inspect the request body that was actually sent.
        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["script"] == "gs.info('hello');\n"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_artifact_script_path_uses_script_field_map_override(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # widget -> client_script
        script_file = tmp_path / "widget.js"
        script_file.write_text("api.controller = function() {};\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sp_widget").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "W1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="widget",
            data=json.dumps({"name": "W1"}),
            script_path=str(script_file),
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["client_script"] == "api.controller = function() {};\n"
        assert "script" not in sent

    @pytest.mark.asyncio()
    @respx.mock
    async def test_artifact_script_path_overrides_data_field_with_warning(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "br.js"
        script_file.write_text("FILE_WINS\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "BR1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="business_rule",
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
    async def test_artifact_script_path_traversal_blocked(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # File exists outside script_allowed_root -> PermissionError -> error envelope.
        outside = tmp_path.parent / "outside.js"
        outside.write_text("nope")
        try:
            tools = _register_and_get_tools(script_settings, script_auth_provider)
            raw = await tools["record_write"](
                action="create",
                artifact_type="business_rule",
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
    async def test_artifact_script_path_too_large_returns_error(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        from servicenow_mcp.tools._artifact import MAX_SCRIPT_FILE_BYTES

        big = tmp_path / "big.js"
        big.write_bytes(b"a" * (MAX_SCRIPT_FILE_BYTES + 1))

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="business_rule",
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
# Artifact catalog structural test
# ---------------------------------------------------------------------------


class TestArtifactCatalog:
    """Structural invariants for ``WRITABLE_ARTIFACT_TABLES`` and ``SCRIPT_FIELD_MAP``.

    Locks the catalog: any entry added to ``WRITABLE_ARTIFACT_TABLES`` must
    have a non-empty list in ``SCRIPT_FIELD_MAP``, and ``primary_script_field``
    must return the first entry of that list.
    """

    @pytest.mark.parametrize("artifact_type", sorted(WRITABLE_ARTIFACT_TABLES))
    def test_artifact_table_resolves(self, artifact_type: str) -> None:
        expected = WRITABLE_ARTIFACT_TABLES[artifact_type]
        assert _resolve_writable_artifact_table(artifact_type) == expected

    @pytest.mark.parametrize("artifact_type", sorted(WRITABLE_ARTIFACT_TABLES))
    def test_script_field_map_entry_exists_and_is_non_empty(self, artifact_type: str) -> None:
        fields = SCRIPT_FIELD_MAP[artifact_type]
        assert isinstance(fields, list)
        assert fields, f"SCRIPT_FIELD_MAP[{artifact_type!r}] must be non-empty"

    @pytest.mark.parametrize("artifact_type", sorted(WRITABLE_ARTIFACT_TABLES))
    def test_primary_script_field_returns_index_zero(self, artifact_type: str) -> None:
        assert primary_script_field(artifact_type) == SCRIPT_FIELD_MAP[artifact_type][0]

    def test_catalog_size_matches_expected(self) -> None:
        # Lock the count so an accidental drop/duplicate is loud.
        assert len(WRITABLE_ARTIFACT_TABLES) == 24
        assert set(WRITABLE_ARTIFACT_TABLES.keys()) == set(SCRIPT_FIELD_MAP.keys())

    def test_dropped_scripted_rest_api_entry_stays_dropped(self) -> None:
        # sys_web_service has no script field; the children (sys_ws_operation)
        # are already covered as ``scripted_rest_resource``.
        assert "scripted_rest_api" not in WRITABLE_ARTIFACT_TABLES


# ---------------------------------------------------------------------------
# script_field parameter
# ---------------------------------------------------------------------------


class TestScriptFieldParameter:
    """``script_field`` overrides the destination field within the allowed list."""

    @pytest.mark.asyncio()
    async def test_script_field_without_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_write"](
            action="create",
            table="incident",
            data=json.dumps({"x": 1}),
            script_field="client_script",
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "script_field requires artifact_type" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_field_invalid_for_artifact_type_returns_error(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # business_rule allows ["script", "condition"]; "client_script" is not allowed
        script_file = tmp_path / "br.js"
        script_file.write_text("// noop\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="business_rule",
            data=json.dumps({"name": "BR1"}),
            script_path=str(script_file),
            script_field="client_script",
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Invalid script_field" in result["error"]["message"]
        assert "business_rule" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_field_default_writes_to_primary(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # widget primary field is "client_script"
        script_file = tmp_path / "widget.js"
        script_file.write_text("api.controller = function() {};\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sp_widget").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "W1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="widget",
            data=json.dumps({"name": "W1"}),
            script_path=str(script_file),
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["client_script"] == "api.controller = function() {};\n"
        assert "template" not in sent

    @pytest.mark.asyncio()
    @respx.mock
    async def test_script_field_override_writes_to_secondary(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        # widget alt field "template" (HTML)
        script_file = tmp_path / "widget.html"
        script_file.write_text("<div>{{c.data.msg}}</div>\n")

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sp_widget").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "W1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="widget",
            data=json.dumps({"name": "W1"}),
            script_path=str(script_file),
            script_field="template",
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"

        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["template"] == "<div>{{c.data.msg}}</div>\n"
        assert "client_script" not in sent


# ---------------------------------------------------------------------------
# ui_macro XML validation
# ---------------------------------------------------------------------------


class TestUIMacroXMLValidation:
    """``ui_macro`` content must parse as well-formed XML before write."""

    def test_validate_xml_content_accepts_jelly_root(self) -> None:
        jelly = '<j:jelly xmlns:j="jelly:core" xmlns:g="glide" xmlns:g2="null"><g:evaluate>1</g:evaluate></j:jelly>'
        assert _validate_xml_content(jelly) is None

    def test_validate_xml_content_rejects_malformed(self) -> None:
        bad = "<unclosed>"
        error = _validate_xml_content(bad)
        assert error is not None
        assert "ui_macro content is not well-formed XML" in error

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

        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        post_mock = respx.post(f"{BASE_URL}/api/now/table/sys_ui_macro").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": SYS_ID_ART001, "name": "M1"}}),
        )
        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="ui_macro",
            data=json.dumps({"name": "M1"}),
            script_path=str(script_file),
            preview=False,
        )
        assert decode_response(raw)["status"] == "success"
        sent = json.loads(post_mock.calls[0].request.content)
        assert sent["xml"].startswith("<j:jelly")

    @pytest.mark.asyncio()
    async def test_ui_macro_write_rejects_malformed_xml(
        self,
        script_settings: Settings,
        script_auth_provider: BasicAuthProvider,
        tmp_path: Any,
    ) -> None:
        script_file = tmp_path / "macro.xml"
        script_file.write_text("<not-well-formed>\n")

        tools = _register_and_get_tools(script_settings, script_auth_provider)
        raw = await tools["record_write"](
            action="create",
            artifact_type="ui_macro",
            data=json.dumps({"name": "M1"}),
            script_path=str(script_file),
            preview=False,
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "not well-formed XML" in result["error"]["message"]
