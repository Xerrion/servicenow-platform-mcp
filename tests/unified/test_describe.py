"""Tests for the unified ``describe`` tool (Phase 3a)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified-tool test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> dict[str, Any]:
    """Register the unified ``describe`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.unified.describe import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


class TestDescribe:
    """Tests for the unified describe tool (mirrors the legacy table_describe coverage)."""

    @staticmethod
    def _mock_dictionary() -> None:
        """Default sys_dictionary mock with two fields: number, state."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "number",
                            "internal_type": "string",
                            "max_length": "40",
                            "mandatory": "true",
                            "read_only": "false",
                            "reference": "",
                            "column_label": "Number",
                            "default_value": "",
                            "sys_id": "noise-1",
                            "sys_scope": "global",
                            "attributes": "edge_encryption_enabled=true",
                        },
                        {
                            "element": "state",
                            "internal_type": "integer",
                            "max_length": "40",
                            "mandatory": "false",
                            "read_only": "false",
                            "reference": "",
                            "column_label": "State",
                            "default_value": "1",
                            "sys_id": "noise-2",
                            "sys_scope": "global",
                            "attributes": "",
                        },
                    ]
                },
            )
        )

    @staticmethod
    def _mock_db_object() -> None:
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "name": "incident",
                            "label": "Incident",
                            "super_class": "",
                            "is_extendable": "true",
                            "number_ref": "",
                            "sys_id": "abc123",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

    @staticmethod
    def _mock_choices(records: list[dict[str, Any]] | None = None) -> None:
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=httpx.Response(
                200,
                json={"result": records or []},
                headers={"X-Total-Count": str(len(records or []))},
            )
        )

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_slim_field_metadata(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Default shape returns the 8-key slim per-field metadata."""
        self._mock_dictionary()
        self._mock_db_object()
        self._mock_choices()

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["field_count"] == 2
        assert "documentation" not in data

        first, second = data["fields"]
        assert first == {
            "name": "number",
            "label": "Number",
            "type": "string",
            "max_length": 40,
            "mandatory": True,
            "read_only": False,
            "reference_table": "",
            "choice_count": 0,
        }
        assert second["name"] == "state"
        assert second["type"] == "integer"
        assert second["mandatory"] is False
        assert second["choice_count"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_verbose_returns_full_dictionary_row(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """verbose=True returns full sys_dictionary rows minus the deny-list, plus choice_count."""
        self._mock_dictionary()
        self._mock_db_object()
        self._mock_choices()

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident", verbose=True)
        result = decode_response(raw)

        assert result["status"] == "success"
        first = result["data"]["fields"][0]
        assert first["element"] == "number"
        assert first["column_label"] == "Number"
        assert first["internal_type"] == "string"
        assert first["choice_count"] == 0
        # Deny-list keys must be stripped
        assert "sys_id" not in first
        assert "sys_scope" not in first
        assert "attributes" not in first
        assert "default_value" not in first

    @pytest.mark.asyncio()
    @respx.mock
    async def test_fields_filter_narrows_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """fields= filter restricts the response and warns on unknown names."""
        self._mock_dictionary()
        self._mock_db_object()
        self._mock_choices()

        tools = _register_and_get_tools(settings, auth_provider)

        raw = await tools["describe"](table="incident", fields="state, priority")
        result = decode_response(raw)
        assert result["status"] == "success"
        names = [f["name"] for f in result["data"]["fields"]]
        assert names == ["state"]
        assert any("priority" in w for w in result.get("warnings", []))

    @pytest.mark.asyncio()
    @respx.mock
    async def test_include_docs_attaches_documentation(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """include_docs=True triggers a sys_documentation fetch and attaches it."""
        self._mock_dictionary()
        self._mock_db_object()
        self._mock_choices()
        respx.get(f"{BASE_URL}/api/now/table/sys_documentation").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "state",
                            "label": "State",
                            "help": "Lifecycle state of the incident.",
                            "hint": "",
                            "url": "",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident", include_docs=True)
        result = decode_response(raw)

        assert result["status"] == "success"
        docs = result["data"]["documentation"]
        assert "state" in docs
        assert docs["state"]["help"] == "Lifecycle state of the incident."

    @pytest.mark.asyncio()
    @respx.mock
    async def test_choice_count_populated(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """choice_count reflects the batched sys_choice tally per element."""
        self._mock_dictionary()
        self._mock_db_object()
        self._mock_choices(
            [
                {"element": "state"},
                {"element": "state"},
                {"element": "state"},
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        by_name = {f["name"]: f for f in result["data"]["fields"]}
        assert by_name["state"]["choice_count"] == 3
        assert by_name["number"]["choice_count"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_choice_fetch_failure_does_not_break_describe(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A failing sys_choice query degrades gracefully to choice_count=0 with a warning."""
        self._mock_dictionary()
        self._mock_db_object()
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=httpx.Response(500, json={"error": {"message": "boom"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        for field in result["data"]["fields"]:
            assert field["choice_count"] == 0
        assert any("sys_choice" in w for w in result.get("warnings", []))

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Blocked tables return an error response (no HTTP call made)."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table=denied)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_includes_correlation_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Response always contains a correlation_id."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        self._mock_choices()

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](table="incident")
        result = decode_response(raw)

        assert "correlation_id" in result
        assert len(result["correlation_id"]) > 0


class TestListArtifactTypes:
    """``describe(action='list_artifact_types')`` returns the writable artifact catalog."""

    @pytest.mark.asyncio()
    async def test_returns_all_24_entries_sorted(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_artifact_types")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["count"] == 24
        assert len(data["artifact_types"]) == 24

        names = [entry["artifact_type"] for entry in data["artifact_types"]]
        assert names == sorted(names), "Entries must be alphabetically sorted"

    @pytest.mark.asyncio()
    async def test_each_entry_has_required_shape(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_artifact_types")
        result = decode_response(raw)

        for entry in result["data"]["artifact_types"]:
            assert set(entry.keys()) == {
                "artifact_type",
                "table",
                "script_fields",
                "primary_field",
            }
            assert isinstance(entry["script_fields"], list)
            assert entry["script_fields"], "script_fields must be non-empty"
            assert entry["primary_field"] == entry["script_fields"][0]

    @pytest.mark.asyncio()
    async def test_known_entries_are_present(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_artifact_types")
        result = decode_response(raw)
        by_name = {e["artifact_type"]: e for e in result["data"]["artifact_types"]}

        assert by_name["business_rule"]["table"] == "sys_script"
        assert by_name["business_rule"]["script_fields"] == ["script", "condition"]
        assert by_name["widget"]["primary_field"] == "client_script"
        assert by_name["acl"]["table"] == "sys_security_acl"
        assert "scripted_rest_api" not in by_name

    @pytest.mark.asyncio()
    async def test_table_not_required_when_action_set(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_artifact_types")
        assert decode_response(raw)["status"] == "success"

    @pytest.mark.asyncio()
    async def test_unknown_action_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="not_a_real_action")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown describe action" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_missing_table_without_action_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"]()
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]
