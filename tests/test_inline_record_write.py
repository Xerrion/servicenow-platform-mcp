"""Inline field-map writes preserve validation, previews, and field selection."""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.tools._payload import MAX_JSON_PAYLOAD_BYTES
from servicenow_mcp.tools.record_write import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com/api/now/table"
SYS_ID = "c" * 32


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """Create authentication from the inline write test settings."""
    return OAuthPKCEProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: OAuthPKCEProvider) -> dict[str, Any]:
    """Register write tools on a fresh server and return their callables."""
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


@pytest.fixture()
def server(settings: Settings) -> MCPServer:
    """Register write tools without reading local credentials."""
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    return mcp


def mock_metadata(fields: dict[str, str]) -> None:
    """Serve dictionary types and a root table through mocked HTTP only."""
    respx.get(f"{BASE_URL}/sys_db_object").respond(200, json={"result": []})
    respx.get(f"{BASE_URL}/sys_dictionary").respond(
        200,
        json={"result": [{"element": key, "internal_type.name": value} for key, value in fields.items()]},
    )


class TestInlineRecordWrite:
    """Inline writes preserve payload validation and preview semantics."""

    async def test_write_schema_has_only_inline_input(self, server: MCPServer) -> None:
        schemas = {tool.name: tool for tool in await server.list_tools()}
        assert set(schemas["record_write"].input_schema["properties"]) == {
            "action",
            "table",
            "sys_id",
            "data",
            "preview",
        }

    @pytest.mark.parametrize("action", ["create", "update"])
    @pytest.mark.parametrize("preview", [True, False])
    @pytest.mark.parametrize("content", ["<unclosed>", "", None, 1, {"value": "<root/>"}])
    @respx.mock
    async def test_inline_xml_rejected_before_token_or_mutation(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, action: str, preview: bool, content: Any
    ) -> None:
        mock_metadata({"u_markup": "xml"})
        tools = _register_and_get_tools(settings, auth_provider)
        with patch.object(PreviewTokenStore, "create", new_callable=AsyncMock) as create_token:
            result = decode_response(
                await tools["record_write"](
                    action=action,
                    table="u_custom",
                    sys_id=SYS_ID if action == "update" else "",
                    data=json.dumps({"u_markup": content}),
                    preview=preview,
                )
            )
        assert result["status"] == "error"
        assert "u_markup" in result["error"]["message"]
        assert "XML" in result["error"]["message"]
        create_token.assert_not_awaited()
        assert all(call.request.method == "GET" for call in respx.calls)
        assert all(
            call.request.url.path.rsplit("/", 1)[-1] in {"sys_db_object", "sys_dictionary"} for call in respx.calls
        )

    @pytest.mark.parametrize("action", ["create", "update"])
    @pytest.mark.parametrize("preview", [True, False])
    @respx.mock
    async def test_multiple_inline_fields_round_trip(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, action: str, preview: bool
    ) -> None:
        payload = {
            "name": "Widget",
            "script": "data.message = 'hello';\n",
            "client_script": "function() { this.ready = true; }",
            "template": "<div>{{c.data.message}}</div>",
            "css": ".message { color: red; }",
            "u_markup": '<j:jelly xmlns:j="jelly:core"/>',
            "password": "test-only-secret",
        }
        mock_metadata(
            {
                "name": "string",
                "script": "script",
                "client_script": "script_client",
                "template": "html",
                "css": "css",
                "u_markup": "xml",
                "password": "string",
            }
        )
        url = f"{BASE_URL}/u_widget" + (f"/{SYS_ID}" if action == "update" else "")
        if action == "update" and preview:
            respx.get(url).respond(200, json={"result": {"sys_id": SYS_ID, "script": "old", "password": "old-secret"}})
        mutation = respx.route(method="POST" if action == "create" else "PATCH", url=url).respond(
            200, json={"result": {"sys_id": SYS_ID, **payload}}
        )
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["record_write"](
                action=action,
                table="u_widget",
                sys_id=SYS_ID if action == "update" else "",
                data=json.dumps(payload),
                preview=preview,
            )
        )
        assert result["status"] == "success"
        assert "test-only-secret" not in json.dumps(result)
        if preview:
            assert not mutation.called
            section = result["data"]["preview"]["data" if action == "create" else "diff"]
            for key, value in payload.items():
                actual = section[key] if action == "create" else section[key]["new"]
                assert actual == ("***MASKED***" if key == "password" else value)
            token = result["data"]["preview_token"]
            result = decode_response(await tools["record_apply"](preview_token=token))
            assert result["status"] == "success"
            assert "test-only-secret" not in json.dumps(result)
            assert decode_response(await tools["record_apply"](preview_token=token))["status"] == "error"
        assert mutation.call_count == 1
        assert json.loads(mutation.calls[0].request.content) == payload
        metadata_calls = [call for call in respx.calls if call.request.url.path.endswith("/sys_dictionary")]
        for call in metadata_calls:
            assert call.request.url.params["sysparm_fields"] in {"element,internal_type.name", "element,mandatory"}
        expected_metadata_calls = (3 if preview else 2) if action == "create" else 1
        assert len(metadata_calls) == expected_metadata_calls

    @respx.mock
    async def test_metadata_only_update_does_not_send_script(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        mock_metadata({"active": "boolean"})
        url = f"{BASE_URL}/sys_script/{SYS_ID}"
        current = {"sys_id": SYS_ID, "script": "doNotChange();", "active": True}
        respx.get(url).respond(200, json={"result": current})
        mutation = respx.patch(url).respond(200, json={"result": {**current, "active": False}})
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["record_write"](
                action="update",
                table="sys_script",
                sys_id=SYS_ID,
                data='{"active": false}',
            )
        )
        assert result["status"] == "success"
        assert set(result["data"]["preview"]["diff"]) == {"active"}
        applied = decode_response(await tools["record_apply"](preview_token=result["data"]["preview_token"]))
        assert applied["data"]["record"]["script"] == current["script"]
        assert json.loads(mutation.calls[0].request.content) == {"active": False}
        metadata_calls = [call for call in respx.calls if call.request.url.path.endswith("/sys_dictionary")]
        assert len(metadata_calls) == 1
        assert metadata_calls[0].request.url.params["sysparm_query"] == "name=sys_script^elementINactive^active=true"
        assert metadata_calls[0].request.url.params["sysparm_fields"] == "element,internal_type.name"

    @pytest.mark.parametrize("action", ["create", "update"])
    @pytest.mark.parametrize("preview", [True, False])
    @respx.mock
    async def test_inline_size_limit_precedes_metadata(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, action: str, preview: bool
    ) -> None:
        payload = json.dumps({"script": "é" * (MAX_JSON_PAYLOAD_BYTES // 2)}, ensure_ascii=False)
        assert len(payload) < MAX_JSON_PAYLOAD_BYTES < len(payload.encode("utf-8"))
        with patch.object(PreviewTokenStore, "create", new_callable=AsyncMock) as create_token:
            result = decode_response(
                await _register_and_get_tools(settings, auth_provider)["record_write"](
                    action=action,
                    table="sys_script",
                    sys_id=SYS_ID if action == "update" else "",
                    data=payload,
                    preview=preview,
                )
            )
        assert result["status"] == "error"
        assert str(MAX_JSON_PAYLOAD_BYTES) in result["error"]["message"]
        create_token.assert_not_awaited()
        assert not respx.calls

    @respx.mock
    async def test_inline_size_boundary_is_accepted(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        overhead = len(json.dumps({"script": ""}).encode("utf-8"))
        payload = json.dumps({"script": "x" * (MAX_JSON_PAYLOAD_BYTES - overhead)})
        assert len(payload.encode("utf-8")) == MAX_JSON_PAYLOAD_BYTES
        mock_metadata({"script": "script"})
        result = decode_response(
            await _register_and_get_tools(settings, auth_provider)["record_write"](
                action="create",
                table="sys_script",
                data=payload,
            )
        )
        assert result["status"] == "success"

    @pytest.mark.parametrize("payload", ["[]", "not json", '{"bad^key":"x"}'])
    @respx.mock
    async def test_invalid_payload_precedes_metadata(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, payload: str
    ) -> None:
        result = decode_response(
            await _register_and_get_tools(settings, auth_provider)["record_write"](
                action="create",
                table="sys_script",
                data=payload,
            )
        )
        assert result["status"] == "error"
        assert not respx.calls

    @pytest.mark.parametrize("status_code", [403, 500])
    @respx.mock
    async def test_metadata_error_blocks_write(
        self, settings: Settings, auth_provider: OAuthPKCEProvider, status_code: int
    ) -> None:
        respx.get(f"{BASE_URL}/sys_db_object").respond(200, json={"result": []})
        respx.get(f"{BASE_URL}/sys_dictionary").respond(status_code, json={"error": {"message": "Unavailable"}})
        with patch.object(PreviewTokenStore, "create", new_callable=AsyncMock) as create_token:
            result = decode_response(
                await _register_and_get_tools(settings, auth_provider)["record_write"](
                    action="create",
                    table="u_custom",
                    data='{"u_markup":"<root/>"}',
                )
            )
        assert result["status"] == "error"
        create_token.assert_not_awaited()
        assert all(call.request.method == "GET" for call in respx.calls)

    @respx.mock
    async def test_inherited_xml_is_validated(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        def objects(request: httpx.Request) -> httpx.Response:
            parent = "u_parent" if request.url.params["sysparm_query"] == "name=u_child" else ""
            return httpx.Response(200, json={"result": [{"super_class.name": parent}]})

        def fields(request: httpx.Request) -> httpx.Response:
            rows = (
                []
                if "name=u_child^" in request.url.params["sysparm_query"]
                else [{"element": "u_markup", "internal_type.name": "xml"}]
            )
            return httpx.Response(200, json={"result": rows})

        respx.get(f"{BASE_URL}/sys_db_object").mock(side_effect=objects)
        respx.get(f"{BASE_URL}/sys_dictionary").mock(side_effect=fields)
        result = decode_response(
            await _register_and_get_tools(settings, auth_provider)["record_write"](
                action="update",
                table="u_child",
                sys_id=SYS_ID,
                data='{"u_markup":"<unclosed>"}',
                preview=False,
            )
        )
        assert "not well-formed" in result["error"]["message"]
        assert all(call.request.method == "GET" for call in respx.calls)

    @respx.mock
    async def test_apply_rechecks_production_gate(self, settings: Settings, auth_provider: OAuthPKCEProvider) -> None:
        mock_metadata({"script": "script"})
        tools = _register_and_get_tools(settings, auth_provider)
        preview = decode_response(
            await tools["record_write"](
                action="create",
                table="sys_script",
                data='{"script":"run();"}',
            )
        )
        settings.servicenow_env = "prod"
        result = decode_response(await tools["record_apply"](preview_token=preview["data"]["preview_token"]))
        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()
        assert all(call.request.method == "GET" for call in respx.calls)
        assert (
            decode_response(await tools["record_apply"](preview_token=preview["data"]["preview_token"]))["status"]
            == "error"
        )

    @respx.mock
    async def test_missing_mandatory_field_blocks_create(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        respx.get(f"{BASE_URL}/sys_db_object").respond(200, json={"result": []})

        def fields(request: httpx.Request) -> httpx.Response:
            if request.url.params["sysparm_fields"] == "element,mandatory":
                assert request.url.params["sysparm_query"] == "name=sys_script^elementISNOTEMPTY^active=true"
                rows = [{"element": "name", "mandatory": "true"}]
            else:
                rows = [{"element": "script", "internal_type.name": "script"}]
            return httpx.Response(200, json={"result": rows})

        respx.get(f"{BASE_URL}/sys_dictionary").mock(side_effect=fields)
        with patch.object(PreviewTokenStore, "create", new_callable=AsyncMock) as create_token:
            result = decode_response(
                await _register_and_get_tools(settings, auth_provider)["record_write"](
                    action="create",
                    table="sys_script",
                    data='{"script":"run();"}',
                )
            )
        assert result["status"] == "error"
        assert result["data"]["missing_fields"] == ["name"]
        create_token.assert_not_awaited()
        assert all(call.request.method == "GET" for call in respx.calls)
