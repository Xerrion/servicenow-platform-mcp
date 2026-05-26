"""Tests for the unified ``audit`` tool group and ``AuditRegistry``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.tools._audit import attribute_has_no_audit
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools.unified.audit import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SYS_DB_URL = f"{BASE_URL}/api/now/table/sys_db_object"
SYS_DICT_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
SYS_AUDIT_URL = f"{BASE_URL}/api/now/table/sys_audit"
SYS_AUDIT_STATS_URL = f"{BASE_URL}/api/now/stats/sys_audit"

SYS_ID_RECORD = "a" * 32


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the audit test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> dict[str, Any]:
    """Register the unified ``audit`` tool on a fresh MCP and return callables."""
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices, dictionary=dictionary)
    return get_tool_functions(mcp)


def _make_sys_db_handler(
    chain_parents: dict[str, str],
    table_audit: dict[str, str],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a side-effect handler for ``sys_db_object`` queries.

    ``chain_parents`` maps a table to its parent (``""`` for root). Used for
    chain-walk lookups (``name=<table>`` queries asking for ``super_class``).
    ``table_audit`` maps a table to its ``sys_audit`` flag (``"true"``,
    ``"false"``, or ``""``). Used for the chain-IN lookup that fetches the
    table-level audit row in one call.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        query = params.get("sysparm_query", "")
        fields = params.get("sysparm_fields", "")
        if "super_class" in fields:
            # Chain-walk: extract table name from ``name=<table>`` clause.
            target = query.split("name=", 1)[1].split("^", 1)[0]
            parent = chain_parents.get(target, "")
            return httpx.Response(200, json={"result": [{"super_class": parent}] if target in chain_parents else []})
        # Table-audit chain-IN lookup.
        result = [{"name": t, "sys_audit": table_audit.get(t, "")} for t in table_audit]
        return httpx.Response(200, json={"result": result})

    return handler


def _make_sys_dict_handler(
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a side-effect handler for ``sys_dictionary`` queries.

    Returns rows matching the ``nameIN<csv>`` clause; honours ``element=``
    when present so per-field queries collapse to the right subset.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        query = params.get("sysparm_query", "")
        # Parse nameIN clause.
        names: list[str] = []
        for part in query.split("^"):
            if part.startswith("nameIN"):
                names = part[len("nameIN") :].split(",")
                break
        element: str | None = None
        for part in query.split("^"):
            if part.startswith("element=") and not part.startswith("elementIS"):
                element = part.split("=", 1)[1]
                break

        result: list[dict[str, Any]] = []
        for table in names:
            for row in rows_by_table.get(table, []):
                if element is not None and row.get("element") != element:
                    continue
                merged = {"name": table, **row}
                result.append(merged)
        return httpx.Response(200, json={"result": result})

    return handler


def _stats_response(count: int) -> httpx.Response:
    """Build a sys_audit aggregate response with a single count value."""
    return httpx.Response(200, json={"result": {"stats": {"count": str(count)}}})


def _make_stats_handler(
    counts: dict[tuple[str, str | None], int],
) -> Callable[[httpx.Request], httpx.Response]:
    """Side-effect handler for the sys_audit aggregate endpoint.

    Keys are ``(table, field_or_None)``. Returns 0 when the lookup misses.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        query = params.get("sysparm_query", "")
        table = ""
        field: str | None = None
        for part in query.split("^"):
            if part.startswith("tablename="):
                table = part.split("=", 1)[1]
            elif part.startswith("fieldname="):
                field = part.split("=", 1)[1]
        return _stats_response(counts.get((table, field), 0))

    return handler


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_describe_returns_action_registry_with_no_io(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """``describe`` returns the action registry without any HTTP I/O."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    actions = result["data"]["actions"]
    assert set(actions.keys()) == {"check_field", "check_fields", "check_table", "history", "describe"}
    # No platform calls were issued.
    assert not respx.routes


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unknown_action_returns_error(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """Actions outside the enum are rejected up-front."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="nope")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "Unknown action" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_denied_table_rejected(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """check_table_access blocks deny-listed tables before any HTTP call."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="sys_credentials", field="password")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "denied by policy" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_invalid_identifier_rejected(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """validate_identifier rejects malformed field names."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="Bad Name!")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "Invalid identifier" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Verdict resolution: one test per enum value
# ---------------------------------------------------------------------------


def _wire_check_field_mocks(
    *,
    chain_parents: dict[str, str],
    table_audit: dict[str, str],
    field_rows: dict[str, list[dict[str, Any]]],
    field_count: int,
    table_count: int,
    table: str = "incident",
    field: str = "business_service",
) -> None:
    """Install respx handlers for one ``check_field`` scenario."""
    respx.get(SYS_DB_URL).mock(side_effect=_make_sys_db_handler(chain_parents, table_audit))
    respx.get(SYS_DICT_URL).mock(side_effect=_make_sys_dict_handler(field_rows))
    respx.get(SYS_AUDIT_STATS_URL).mock(
        side_effect=_make_stats_handler({(table, field): field_count, (table, None): table_count})
    )


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_audited(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Field is audited at parent table, activity confirms it."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={"task": [{"element": "business_service", "audit": "true", "attributes": ""}]},
        field_count=5,
        table_count=100,
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="business_service")
    result = decode_response(raw)
    assert result["status"] == "success"
    assert result["data"]["verdict"] == "audited"
    assert result["data"]["recent_activity"]["positive_control_passed"] is True
    assert result["data"]["recent_activity"]["window_days"] == 90
    assert "Default 90-day window used" in result["data"]["window_note"]


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_not_audited_field_flag(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Field audit=false (inherited from parent) wins over table audit=true."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={"task": [{"element": "business_service", "audit": "false", "attributes": ""}]},
        field_count=0,
        table_count=100,
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="business_service")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "not_audited_field_flag"
    assert result["data"]["reason"] == "audit_flag"
    assert result["data"]["inherited_from"] == "task"


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_not_audited_table_flag(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Table audit off short-circuits the verdict regardless of field config."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "false", "task": "false"},
        field_rows={"task": [{"element": "business_service", "audit": "true", "attributes": ""}]},
        field_count=0,
        table_count=0,
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="business_service")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "not_audited_table_flag"
    assert result["data"]["table_audit"] is False


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_audited_but_inactive(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Configured for audit, table has activity, field has none in window."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={"incident": [{"element": "description", "audit": "true", "attributes": ""}]},
        field_count=0,
        table_count=100,
        field="description",
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="description")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "audited_but_inactive"


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_inconclusive_zero_table(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Zero field rows AND zero table rows -> inconclusive (window uninformative)."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={"incident": [{"element": "description", "audit": "true", "attributes": ""}]},
        field_count=0,
        table_count=0,
        field="description",
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="description")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "inconclusive"
    assert result["data"]["recent_activity"]["positive_control_passed"] is False


@pytest.mark.asyncio()
@respx.mock
async def test_verdict_inconclusive_field_not_in_chain(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Field row absent everywhere -> inconclusive with the dedicated explanation."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={},  # no rows anywhere
        field_count=0,
        table_count=100,
        field="not_a_real_field",
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="not_a_real_field")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "inconclusive"
    assert "not found" in result["data"]["explanation"].lower()


# ---------------------------------------------------------------------------
# no_audit attribute veto
# ---------------------------------------------------------------------------


def test_attribute_has_no_audit_recognises_flag() -> None:
    """The veto regex matches at comma boundaries; substrings do not false-positive."""
    assert attribute_has_no_audit("no_audit=true")
    assert attribute_has_no_audit("foo=bar,no_audit=true,baz=qux")
    assert not attribute_has_no_audit("my_no_audit=true")
    assert not attribute_has_no_audit("no_audit=false")
    assert not attribute_has_no_audit("")


@pytest.mark.asyncio()
@respx.mock
async def test_no_audit_attribute_vetoes_audit_true(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A row with audit=true AND no_audit=true resolves to not_audited_field_flag."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={
            "incident": [
                {"element": "comments", "audit": "true", "attributes": "foo=bar,no_audit=true"},
            ]
        },
        field_count=0,
        table_count=100,
        field="comments",
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="comments")
    result = decode_response(raw)
    assert result["data"]["verdict"] == "not_audited_field_flag"
    assert result["data"]["reason"] == "no_audit_attribute"
    assert result["data"]["field_attributes"]["no_audit"] is True


# ---------------------------------------------------------------------------
# window_days override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_window_days_override_surfaces_in_response(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A non-default window_days value is echoed back and triggers the warning note."""
    _wire_check_field_mocks(
        chain_parents={"incident": "task", "task": ""},
        table_audit={"incident": "true", "task": "true"},
        field_rows={"task": [{"element": "business_service", "audit": "true", "attributes": ""}]},
        field_count=1,
        table_count=10,
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="business_service", window_days=365)
    result = decode_response(raw)
    assert result["data"]["recent_activity"]["window_days"] == 365
    assert "Non-default window" in result["data"]["window_note"]


# ---------------------------------------------------------------------------
# check_fields (batch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_check_fields_returns_per_field_verdicts(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``check_fields`` returns one verdict per field and a single shared table_change_count."""
    respx.get(SYS_DB_URL).mock(
        side_effect=_make_sys_db_handler(
            chain_parents={"incident": "task", "task": ""},
            table_audit={"incident": "true", "task": "true"},
        )
    )
    respx.get(SYS_DICT_URL).mock(
        side_effect=_make_sys_dict_handler(
            {
                "task": [{"element": "business_service", "audit": "true", "attributes": ""}],
                "incident": [{"element": "description", "audit": "false", "attributes": ""}],
            }
        )
    )
    respx.get(SYS_AUDIT_STATS_URL).mock(
        side_effect=_make_stats_handler(
            {
                ("incident", "business_service"): 3,
                ("incident", "description"): 0,
                ("incident", None): 42,
            }
        )
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_fields", table="incident", fields_csv="business_service,description")
    result = decode_response(raw)
    assert result["status"] == "success"
    data = result["data"]
    assert data["table_change_count"] == 42
    assert data["positive_control_passed"] is True
    verdicts = {entry["field"]: entry["verdict"] for entry in data["results"]}
    assert verdicts == {"business_service": "audited", "description": "not_audited_field_flag"}


@pytest.mark.asyncio()
async def test_check_fields_rejects_empty_list(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """An empty fields_csv list is rejected with a structured error."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_fields", table="incident", fields_csv="")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "at least one field" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_check_fields_rejects_over_max(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Requesting more than the configured max yields an error before any I/O."""
    tools = _register_and_get_tools(settings, auth_provider)
    fields_csv = ",".join(f"f{i}" for i in range(51))
    raw = await tools["audit"](action="check_fields", table="incident", fields_csv=fields_csv)
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "At most 50" in result["error"]["message"]


# ---------------------------------------------------------------------------
# check_table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_check_table_lists_field_overrides(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``check_table`` returns only fields whose flag differs from the table default."""
    respx.get(SYS_DB_URL).mock(
        side_effect=_make_sys_db_handler(
            chain_parents={"incident": "task", "task": ""},
            table_audit={"incident": "true", "task": "true"},
        )
    )
    respx.get(SYS_DICT_URL).mock(
        side_effect=_make_sys_dict_handler(
            {
                "task": [
                    {"element": "business_service", "audit": "false", "attributes": ""},
                    {"element": "state", "audit": "true", "attributes": ""},
                ],
                "incident": [
                    {"element": "description", "audit": "true", "attributes": "no_audit=true"},
                ],
            }
        )
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_table", table="incident")
    result = decode_response(raw)
    assert result["status"] == "success"
    data = result["data"]
    assert data["table_audit"] is True
    overrides = {entry["field"]: entry for entry in data["field_overrides"]}
    assert "state" not in overrides  # matches default
    assert overrides["business_service"]["reason"] == "audit_flag"
    assert overrides["business_service"]["inherited_from"] == "task"
    assert overrides["description"]["reason"] == "no_audit_attribute"
    assert overrides["description"]["inherited_from"] is None


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_history_masks_sensitive_field_values(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """sys_audit rows for sensitive fields have old/new values masked."""

    def _audit_handler(request: httpx.Request) -> httpx.Response:
        # Confirm the real sys_audit column name (`tablename`) is used; if the
        # tool ever regressed to `table=` the query string would not contain it.
        params = dict(request.url.params)
        query = params.get("sysparm_query", "")
        assert "tablename=incident" in query
        assert f"documentkey={SYS_ID_RECORD}" in query
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "sys_created_on": "2026-05-01 10:00:00",
                        "user": "admin",
                        "fieldname": "password",  # NOSONAR - field-name only
                        "oldvalue": "old-secret",  # NOSONAR
                        "newvalue": "new-secret",  # NOSONAR
                    },
                    {
                        "sys_created_on": "2026-05-02 12:00:00",
                        "user": "admin",
                        "fieldname": "state",
                        "oldvalue": "1",
                        "newvalue": "2",
                    },
                ]
            },
            headers={"X-Total-Count": "2"},
        )

    respx.get(SYS_AUDIT_URL).mock(side_effect=_audit_handler)
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="history", table="incident", sys_id=SYS_ID_RECORD)
    result = decode_response(raw)
    assert result["status"] == "success"
    entries = result["data"]["entries"]
    assert entries[0]["oldvalue"] == "***MASKED***"
    assert entries[0]["newvalue"] == "***MASKED***"
    assert entries[1]["oldvalue"] == "1"  # state is not sensitive
    assert "Default 90-day window used" in result["data"]["window_note"]


@pytest.mark.asyncio()
async def test_history_requires_sys_id(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``history`` rejects calls missing the record sys_id."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="history", table="incident")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "sys_id" in result["error"]["message"]


@pytest.mark.asyncio()
@respx.mock
async def test_history_explicit_since_overrides_window(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """An explicit ``since`` value is honoured and surfaced via window_note."""

    def _audit_handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert "sys_created_on>=2024-01-01" in params.get("sysparm_query", "")
        return httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})

    respx.get(SYS_AUDIT_URL).mock(side_effect=_audit_handler)
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="history", table="incident", sys_id=SYS_ID_RECORD, since="2024-01-01")
    result = decode_response(raw)
    assert result["status"] == "success"
    assert result["data"]["window"]["since"] == "2024-01-01"
    assert "Explicit since=2024-01-01" in result["data"]["window_note"]


# ---------------------------------------------------------------------------
# AuditRegistry.flush() contract (BLOCKER 1 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_flush_table_clears_field_row_cache(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``flush('incident')`` must invalidate ``_table_field_rows_cache`` entries.

    The row cache is keyed by ``"{table}:{field}"`` so a naive
    ``pop(table)`` would leave stale rows behind and the next
    ``get_field_audit`` call would return a stale verdict. This locks
    the flush contract: after flushing, a config change on the platform
    is visible immediately.
    """
    from servicenow_mcp.tools._audit import AuditRegistry

    respx.get(SYS_DB_URL).mock(
        side_effect=_make_sys_db_handler(
            chain_parents={"incident": "task", "task": ""},
            table_audit={"incident": "true", "task": "true"},
        )
    )

    audit_values = ["true", "false"]  # consumed in order across the two fetches

    def _dict_handler(request: httpx.Request) -> httpx.Response:
        current = audit_values.pop(0) if audit_values else "false"
        return httpx.Response(
            200,
            json={
                "result": [
                    {"name": "incident", "element": "business_service", "audit": current, "attributes": ""},
                ]
            },
        )

    respx.get(SYS_DICT_URL).mock(side_effect=_dict_handler)

    dictionary = DictionaryRegistry(settings, auth_provider)
    registry = AuditRegistry(settings, auth_provider, dictionary)

    first = await registry.get_field_audit("incident", "business_service")
    assert first.field_audit is True

    registry.flush("incident")

    second = await registry.get_field_audit("incident", "business_service")
    assert second.field_audit is False, "flush('incident') failed to invalidate _table_field_rows_cache"


# ---------------------------------------------------------------------------
# no_audit veto cross-action consistency (BLOCKER 2 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_no_audit_veto_field_audit_false_and_check_table_agrees(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """``no_audit=true`` forces ``field_audit=false`` and check_field agrees with check_table.

    For a row with ``audit=true`` and ``attributes`` containing
    ``no_audit=true``:
      * ``check_field`` returns ``field_audit=false`` (post-veto) AND
        ``verdict=not_audited_field_flag`` AND ``raw_field_audit=true``
        (so the raw row value remains observable).
      * ``check_table`` reports the same field as an override with the
        same post-veto ``field_audit=false`` and reason
        ``no_audit_attribute``.
    """
    respx.get(SYS_DB_URL).mock(
        side_effect=_make_sys_db_handler(
            chain_parents={"incident": "task", "task": ""},
            table_audit={"incident": "true", "task": "true"},
        )
    )
    respx.get(SYS_DICT_URL).mock(
        side_effect=_make_sys_dict_handler(
            {
                "incident": [
                    {"element": "comments", "audit": "true", "attributes": "foo=bar,no_audit=true"},
                ]
            }
        )
    )
    respx.get(SYS_AUDIT_STATS_URL).mock(
        side_effect=_make_stats_handler({("incident", "comments"): 0, ("incident", None): 100})
    )

    tools = _register_and_get_tools(settings, auth_provider)

    field_raw = await tools["audit"](action="check_field", table="incident", field="comments")
    field_result = decode_response(field_raw)
    assert field_result["data"]["verdict"] == "not_audited_field_flag"
    assert field_result["data"]["reason"] == "no_audit_attribute"
    assert field_result["data"]["field_audit"] is False, "no_audit veto must force field_audit=False"
    assert field_result["data"]["raw_field_audit"] is True, "raw audit column value must remain observable"

    table_raw = await tools["audit"](action="check_table", table="incident")
    table_result = decode_response(table_raw)
    overrides = {entry["field"]: entry for entry in table_result["data"]["field_overrides"]}
    assert "comments" in overrides, "check_table must surface the vetoed field as an override"
    assert overrides["comments"]["field_audit"] is False, "check_table must agree with check_field post-veto"
    assert overrides["comments"]["reason"] == "no_audit_attribute"


# ---------------------------------------------------------------------------
# Regression: input validation and malformed-response handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_history_rejects_invalid_sys_id_without_io(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """Malformed sys_id on action='history' returns a structured error WITHOUT any HTTP I/O."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="history", table="incident", sys_id="not-a-sys-id")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "Invalid sys_id" in result["error"]["message"]
    # No respx routes were registered; if the tool reached httpx, respx would raise.
    assert respx.calls.call_count == 0, "history must validate sys_id BEFORE opening the HTTP client"


@pytest.mark.asyncio()
@respx.mock
async def test_check_field_malformed_stats_count_returns_error(
    settings: Settings,
    auth_provider: BasicAuthProvider,
) -> None:
    """A non-integer stats.count must surface as an error envelope, not be silently coerced to 0."""
    respx.get(SYS_DB_URL).mock(
        side_effect=_make_sys_db_handler(
            {"incident": "task", "task": ""},
            {"incident": "true", "task": "true"},
        )
    )
    respx.get(SYS_DICT_URL).mock(
        side_effect=_make_sys_dict_handler(
            {"task": [{"element": "business_service", "audit": "true", "attributes": ""}]}
        )
    )
    # Malformed: stats.count is a non-numeric string.
    respx.get(SYS_AUDIT_STATS_URL).mock(return_value=httpx.Response(200, json={"result": {"stats": {"count": "abc"}}}))

    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["audit"](action="check_field", table="incident", field="business_service")
    result = decode_response(raw)

    assert result["status"] == "error", "malformed stats.count must not be silently coerced to 0"
    message = result["error"]["message"]
    assert "stats" in message.lower() or "count" in message.lower()
