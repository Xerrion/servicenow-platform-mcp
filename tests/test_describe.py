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

    from servicenow_mcp.tools.describe import register_tools

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


class TestListScriptFields:
    """``describe(action='list_script_fields')`` returns dictionary-driven script fields."""

    @staticmethod
    def _mock_super_class(parent: str = "") -> None:
        """Mock the sys_db_object lookup to return ``parent`` as super_class display value."""
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(200, json={"result": [{"super_class": parent}]}),
        )

    @staticmethod
    def _mock_dictionary_rows(rows: list[dict[str, str]]) -> None:
        """Mock the sys_dictionary fetch to return ``rows``."""
        respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(200, json={"result": rows}),
        )

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_script_fields_for_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        self._mock_super_class("")
        self._mock_dictionary_rows(
            [
                {"element": "script", "internal_type": "script", "attributes": ""},
                {"element": "condition", "internal_type": "script_plain", "attributes": ""},
                {"element": "name", "internal_type": "string", "attributes": ""},
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_script_fields", table="sys_script")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["table"] == "sys_script"
        assert data["chain"] == ["sys_script"]
        assert data["count"] == 2
        names = [f["name"] for f in data["script_fields"]]
        assert names == ["script", "condition"]
        for entry in data["script_fields"]:
            assert set(entry.keys()) == {"name", "internal_type", "inherited_from", "via_heuristic"}
            assert entry["via_heuristic"] is False
            assert entry["inherited_from"] is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_script_fields")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]

    @respx.mock
    @pytest.mark.asyncio()
    async def test_unknown_action_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="not_a_real_action")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown describe action" in result["error"]["message"]

    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_table_without_action_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"]()
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]


class TestListTables:
    """``describe(action='list_tables')`` lists tables from sys_db_object."""

    @staticmethod
    def _mock_tables(rows: list[dict[str, str]], total: int | None = None) -> None:
        """Mock the sys_db_object query to return ``rows`` with a total-count header."""
        respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
            return_value=httpx.Response(
                200,
                json={"result": rows},
                headers={"X-Total-Count": str(total if total is not None else len(rows))},
            ),
        )

    @respx.mock
    @pytest.mark.asyncio()
    async def test_lists_filtered_tables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        self._mock_tables(
            [
                {"name": "incident", "label": "Incident", "super_class": "task", "sys_scope": "Global"},
                {"name": "incident_alert", "label": "Incident Alert", "super_class": "task", "sys_scope": "Global"},
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_tables", name_filter="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["count"] == 2
        assert [t["name"] for t in data["tables"]] == ["incident", "incident_alert"]
        assert data["tables"][0] == {
            "name": "incident",
            "label": "Incident",
            "super_class": "task",
            "sys_scope": "Global",
        }

    @respx.mock
    @pytest.mark.asyncio()
    async def test_coerces_display_value_dicts(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Reference fields returned as display-value dicts are flattened to strings."""
        self._mock_tables(
            [
                {
                    "name": "incident",
                    "label": "Incident",
                    "super_class": {"display_value": "Task", "value": "abc"},
                    "sys_scope": {"display_value": "Global", "value": "global"},
                },
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_tables", name_filter="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        row = result["data"]["tables"][0]
        assert row["super_class"] == "Task"
        assert row["sys_scope"] == "Global"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_no_filter_lists_all(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty name_filter still returns rows (no filter clause)."""
        self._mock_tables([{"name": "task", "label": "Task", "super_class": "", "sys_scope": "Global"}])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_tables")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["count"] == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_truncation_warning(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Hitting the result cap emits a truncation warning."""
        from servicenow_mcp.tools.describe import _LIST_TABLES_LIMIT

        rows = [
            {"name": f"t{i:04d}", "label": f"T{i}", "super_class": "", "sys_scope": "Global"}
            for i in range(_LIST_TABLES_LIMIT)
        ]
        self._mock_tables(rows, total=_LIST_TABLES_LIMIT * 2)

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["describe"](action="list_tables")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["count"] == _LIST_TABLES_LIMIT
        assert any("truncated" in w for w in result.get("warnings", []))
