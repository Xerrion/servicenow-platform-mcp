"""Tests for the unified ``query`` tool (Phase 3a)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

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
    dictionary: Any = None,
) -> dict[str, Any]:
    """Register the unified ``query`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.query import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices, dictionary=dictionary)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# query mode (default)
# ---------------------------------------------------------------------------


class TestQueryMode:
    """Default mode: paginated record query."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_mode_returns_records(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns matching records with pagination, sensitive fields masked."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "1",
                            "number": "INC0001",
                            "password": "leak",  # NOSONAR(S2068) test fixture, masked at line 74
                        },
                        {"sys_id": "2", "number": "INC0002"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", encoded_query="active=true")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["data"][0]["password"] == "***MASKED***"
        assert result["pagination"] == {"offset": 0, "limit": 20, "total": 2}

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Denied table is rejected with a policy error envelope."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table=denied)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_large_table_without_date_filter_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """enforce_query_safety still gates large tables in unified query mode."""
        settings.large_table_names_csv = "syslog,sys_audit"
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="syslog", encoded_query="level=error")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "date" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sys_audit_query_masks_audit_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """sys_audit rows have oldvalue/newvalue masked when fieldname is sensitive."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc",
                            "tablename": "sys_user",
                            "fieldname": "password",
                            "oldvalue": "old_pass",
                            "newvalue": "new_pass",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        # sys_audit is in default large_table_names; supply a real date filter.
        raw = await tools["query"](
            table="sys_audit",
            encoded_query="sys_created_on>=javascript:gs.daysAgoStart(7)",
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        row = result["data"][0]
        assert row["oldvalue"] == "***MASKED***"
        assert row["newvalue"] == "***MASKED***"
        # The meta-field itself stays visible; only the value triple is masked.
        assert row["fieldname"] == "password"


# ---------------------------------------------------------------------------
# sys_id mode
# ---------------------------------------------------------------------------


class TestSysIdMode:
    """Single-record fetch mode."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sys_id_mode_returns_single_record(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """sys_id branch returns one record and no pagination key."""
        sys_id = "a" * 32
        respx.get(f"{BASE_URL}/api/now/table/incident/{sys_id}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": sys_id, "number": "INC0001", "secret": "shh"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", sys_id=sys_id)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["number"] == "INC0001"
        assert result["data"]["secret"] == "***MASKED***"
        assert "pagination" not in result

    @pytest.mark.asyncio()
    async def test_sys_id_mode_with_invalid_sys_id_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Non-32-char-hex sys_id is rejected before any HTTP call."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", sys_id="not-a-real-id")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "sys_id" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# aggregate mode
# ---------------------------------------------------------------------------


class TestAggregateMode:
    """Stats API mode."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_mode_count(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """`aggregate='count'` calls the Stats endpoint and returns the result dict."""
        route = respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "42"}}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", aggregate="count")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["stats"]["count"] == "42"
        assert "pagination" not in result
        assert route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_mode_avg_with_group_by(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """`aggregate='avg:priority'` + `group_by='state'` is forwarded to Stats."""
        route = respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"groupby_fields": [{"field": "state", "value": "1"}]}]},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", aggregate="avg:priority", group_by="state")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request_url = str(route.calls.last.request.url)
        assert "sysparm_group_by=state" in request_url
        assert "sysparm_avg_fields=priority" in request_url

    @pytest.mark.asyncio()
    async def test_aggregate_mode_unknown_op_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """`aggregate='median:foo'` rejects with valid-op list."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", aggregate="median:foo")
        result = decode_response(raw)

        assert result["status"] == "error"
        message = result["error"]["message"].lower()
        assert "median" in message
        assert "avg" in message
        assert "sum" in message

    @pytest.mark.asyncio()
    async def test_group_by_without_aggregate_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """`group_by` set without `aggregate` is rejected upfront."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", group_by="state")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "group_by" in result["error"]["message"]


# ---------------------------------------------------------------------------
# resolve_labels
# ---------------------------------------------------------------------------


class TestResolveLabels:
    """ChoiceRegistry-backed label resolution against `encoded_query`."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_resolve_labels_appends_resolved_value(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Each resolved label is ANDed into encoded_query as `field=value`."""
        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        choices = ChoiceRegistry(settings, auth_provider)
        choices._fetched = True
        choices.resolve = AsyncMock(side_effect=lambda _t, _f, label: {"open": "1", "high": "2"}[label])  # type: ignore[method-assign]

        tools = _register_and_get_tools(settings, auth_provider, choices=choices)
        raw = await tools["query"](
            table="incident", encoded_query="active=true", resolve_labels="state=open,priority=high"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        request_url = str(route.calls.last.request.url)
        assert "active%3Dtrue%5Estate%3D1%5Epriority%3D2" in request_url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_resolve_labels_passthrough_emits_warning(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A non-numeric label that resolves to itself triggers a warning."""
        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        choices = ChoiceRegistry(settings, auth_provider)
        choices._fetched = True
        choices.resolve = AsyncMock(side_effect=lambda _t, _f, label: label)  # type: ignore[method-assign]

        tools = _register_and_get_tools(settings, auth_provider, choices=choices)
        raw = await tools["query"](table="incident", resolve_labels="state=mystery")
        result = decode_response(raw)

        assert result["status"] == "success"
        warnings = result.get("warnings", [])
        assert any("mystery" in w and "resolve_labels" in w for w in warnings)


# ---------------------------------------------------------------------------
# Mode conflicts
# ---------------------------------------------------------------------------


class TestModeConflicts:
    """Mutually exclusive mode arguments."""

    @pytest.mark.asyncio()
    async def test_conflicting_sys_id_and_aggregate_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """sys_id + aggregate together is a usage error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["query"](table="incident", sys_id="a" * 32, aggregate="count")
        result = decode_response(raw)

        assert result["status"] == "error"
        message = result["error"]["message"].lower()
        assert "sys_id" in message
        assert "aggregate" in message


# ---------------------------------------------------------------------------
# Encoded-query field extraction (pure)
# ---------------------------------------------------------------------------


class TestExtractQueryFields:
    """Best-effort parse of field references out of an encoded query."""

    @pytest.mark.parametrize(
        ("encoded_query", "expected"),
        [
            ("", []),
            ("name=Vinklubben", ["name"]),
            ("state=1^priority=2", ["state", "priority"]),
            ("active=true^ORstate=2", ["active", "state"]),
            ("short_descriptionLIKEfoo", ["short_description"]),
            ("descriptionISEMPTY", ["description"]),
            ("assigned_to.name=Bob", ["assigned_to"]),
            ("state=1^ORDERBYDESCsys_created_on", ["state", "sys_created_on"]),
            ("priority=1^NQstate=2", ["priority", "state"]),
            ("state=1^priority=2^state=3", ["state", "priority"]),
        ],
    )
    def test_extracts_root_fields(self, encoded_query: str, expected: list[str]) -> None:
        """Root field names are pulled out, deduped, and order-preserved."""
        from servicenow_mcp.tools.query import _extract_query_fields

        assert _extract_query_fields(encoded_query) == expected


# ---------------------------------------------------------------------------
# Advisory field validation through the query tool
# ---------------------------------------------------------------------------


class TestFieldValidation:
    """Unknown fields in encoded_query produce a warning, not a hard error."""

    def _stub_dictionary(
        self,
        settings: Settings,
        auth_provider: BasicAuthProvider,
        field_names: list[str],
    ) -> Any:
        from servicenow_mcp.tools._dictionary import DictionaryField, DictionaryRegistry

        dictionary = DictionaryRegistry(settings, auth_provider)
        fields = [
            DictionaryField(name=name, internal_type="string", attributes="", inherited_from=None)
            for name in field_names
        ]
        dictionary.get_all_fields = AsyncMock(return_value=fields)  # type: ignore[method-assign]
        return dictionary

    @pytest.mark.asyncio()
    @respx.mock
    async def test_unknown_field_warns(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A filter on a non-existent column warns that results are unfiltered."""
        respx.get(f"{BASE_URL}/api/now/table/u_custom").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        dictionary = self._stub_dictionary(settings, auth_provider, ["u_samaccountname", "u_member"])

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["query"](table="u_custom", encoded_query="name=Vinklubben")
        result = decode_response(raw)

        assert result["status"] == "success"
        warnings = result.get("warnings", [])
        assert any("name" in w and "not found" in w for w in warnings)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_known_field_no_warning(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A filter on a real column produces no field-validation warning."""
        respx.get(f"{BASE_URL}/api/now/table/u_custom").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        dictionary = self._stub_dictionary(settings, auth_provider, ["u_samaccountname", "u_member"])

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["query"](table="u_custom", encoded_query="u_samaccountname=ACL_X")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert not any("not found" in w for w in result.get("warnings", []))

    @pytest.mark.asyncio()
    @respx.mock
    async def test_lookup_failure_skips_validation(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A dictionary lookup error is swallowed; the query still succeeds."""
        respx.get(f"{BASE_URL}/api/now/table/u_custom").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        from servicenow_mcp.tools._dictionary import DictionaryRegistry

        dictionary = DictionaryRegistry(settings, auth_provider)
        dictionary.get_all_fields = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["query"](table="u_custom", encoded_query="name=Vinklubben")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert not any("not found" in w for w in result.get("warnings", []))
