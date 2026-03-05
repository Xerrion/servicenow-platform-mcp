"""Tests for investigation tools (investigate_run, investigate_explain) and 7 investigation modules."""

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register investigation tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.investigations import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ── Dispatcher: investigate_run ───────────────────────────────────────────


class TestInvestigateRun:
    """Tests for the investigate_run dispatcher tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatches_to_stale_automations(self, settings, auth_provider):
        """Dispatches to stale_automations and returns findings."""
        # Stuck flow context
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "fc001",
                            "name": "Approval Flow",
                            "state": "IN_PROGRESS",
                            "sys_created_on": "2026-01-01 00:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Disabled BRs — empty
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )
        # Disabled script includes — empty
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )
        # Stale scheduled jobs — empty
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="stale_automations")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_rejects_unknown_investigation(self, settings, auth_provider):
        """Returns error for unknown investigation name."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="nonexistent")
        result = toon_decode(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_investigate_explain_returns_context(self, settings, auth_provider):
        """investigate_explain returns contextual explanation for a finding."""
        # Mock fetching the flow_context record
        respx.get(f"{BASE_URL}/api/now/table/flow_context/fc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "fc001",
                        "name": "Approval Flow",
                        "state": "IN_PROGRESS",
                        "sys_created_on": "2026-01-01 00:00:00",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="flow_context:fc001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "explanation" in result["data"]
        assert "element" in result["data"]


# ── stale_automations ─────────────────────────────────────────────────────


class TestStaleAutomations:
    """Tests for the stale_automations investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_stuck_flow(self, settings, auth_provider):
        """Finds a stuck Flow Designer context."""
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "fc001",
                            "name": "Stuck Flow",
                            "state": "IN_PROGRESS",
                            "sys_created_on": "2026-01-01 00:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="stale_automations")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 1
        assert result["data"]["findings"][0]["category"] == "stuck_flow"

    @pytest.mark.asyncio
    @respx.mock
    async def test_clean_instance_no_findings(self, settings, auth_provider):
        """Clean instance returns no findings."""
        for table in [
            "flow_context",
            "sys_script",
            "sys_script_include",
            "sysauto_script",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="stale_automations")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_uses_gs_days_ago(self, settings, auth_provider):
        """Queries use gs.daysAgoEnd instead of Python datetime strings."""
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="stale_automations",
            params='{"stale_days": 30}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["stale_days"] == 30


# ── deprecated_apis ───────────────────────────────────────────────────────


class TestDeprecatedApis:
    """Tests for the deprecated_apis investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_deprecated_pattern(self, settings, auth_provider):
        """Finds scripts using deprecated Packages. API."""
        # Code Search returns a match for "Packages."
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "search_results": (
                            [
                                {
                                    "sys_id": "script001",
                                    "className": "sys_script_include",
                                    "name": "OldHelper",
                                }
                            ]
                            if "Packages." in str(request.url)
                            else []
                        )
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="deprecated_apis")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1
        # At least one finding should reference the Packages. pattern
        patterns_found = [f["pattern"] for f in result["data"]["findings"]]
        assert "Packages." in patterns_found

    @pytest.mark.asyncio
    @respx.mock
    async def test_clean_code_no_findings(self, settings, auth_provider):
        """Clean code returns no findings."""
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"search_results": []}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="deprecated_apis")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 0


# ── table_health ──────────────────────────────────────────────────────────


class TestTableHealth:
    """Tests for the table_health investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_health_report(self, settings, auth_provider):
        """Returns a complete health report for a table."""
        # Aggregate count
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "500"}}},
            )
        )
        # Business rules
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "br1", "name": "BR One", "when": "before"},
                        {"sys_id": "br2", "name": "BR Two", "when": "after"},
                        {"sys_id": "br3", "name": "BR Three", "when": "async"},
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )
        # Client scripts
        respx.get(f"{BASE_URL}/api/now/table/sys_script_client").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "cs1", "name": "CS One"}]},
                headers={"X-Total-Count": "1"},
            )
        )
        # ACLs — query now includes exact name match OR field-level prefix
        respx.get(
            f"{BASE_URL}/api/now/table/sys_security_acl",
            params__contains={"sysparm_query": "name=incident^ORnameSTARTSWITHincident."},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "acl1", "name": "incident.*.read"},
                        {"sys_id": "acl2", "name": "incident.*.write"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        # UI policies
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # Syslog errors
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="table_health", params='{"table": "incident"}')
        result = toon_decode(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["table"] == "incident"
        assert data["record_count"] == 500
        assert data["automation"]["business_rules"]["count"] == 3
        assert data["automation"]["client_scripts"]["count"] == 1
        assert data["automation"]["acl_count"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_hours(self, settings, auth_provider):
        """All queries include time filter when hours is specified."""
        # Aggregate
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(200, json={"result": {"stats": {"count": "100"}}})
        )
        for table in [
            "sys_script",
            "sys_script_client",
            "sys_security_acl",
            "sys_ui_policy",
            "syslog",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="table_health",
            params='{"table": "incident", "hours": 24}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["hours"] == 24

    @pytest.mark.asyncio
    @respx.mock
    async def test_rejects_invalid_table_identifier(self, settings, auth_provider):
        """Rejects a table name containing injection characters."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="table_health",
            params='{"table": "incident^active=true"}',
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]


# ── acl_conflicts ─────────────────────────────────────────────────────────


class TestAclConflicts:
    """Tests for the acl_conflicts investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_detects_overlapping_acls(self, settings, auth_provider):
        """Detects two ACLs with the same name but different conditions."""
        respx.get(
            f"{BASE_URL}/api/now/table/sys_security_acl",
            params__contains={"sysparm_query": "name=incident^ORnameSTARTSWITHincident."},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "acl1",
                            "name": "incident.*.read",
                            "operation": "read",
                            "condition": "active=true",
                            "script": "",
                            "active": "true",
                        },
                        {
                            "sys_id": "acl2",
                            "name": "incident.*.read",
                            "operation": "read",
                            "condition": "priority=1",
                            "script": "",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="acl_conflicts", params='{"table": "incident"}')
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_conflicts(self, settings, auth_provider):
        """Unique ACL names produce no conflicts."""
        respx.get(
            f"{BASE_URL}/api/now/table/sys_security_acl",
            params__contains={"sysparm_query": "name=incident^ORnameSTARTSWITHincident."},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "acl1",
                            "name": "incident.*.read",
                            "operation": "read",
                            "condition": "",
                            "script": "",
                            "active": "true",
                        },
                        {
                            "sys_id": "acl2",
                            "name": "incident.*.write",
                            "operation": "write",
                            "condition": "",
                            "script": "",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="acl_conflicts", params='{"table": "incident"}')
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 0


# ── error_analysis ────────────────────────────────────────────────────────


class TestErrorAnalysis:
    """Tests for the error_analysis investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_clusters_errors_by_source(self, settings, auth_provider):
        """Clusters syslog errors by source field."""
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "log1",
                            "message": "Error evaluating script",
                            "source": "sys_script.My BR",
                            "level": "0",
                            "sys_created_on": "2026-02-20 10:00:00",
                        },
                        {
                            "sys_id": "log2",
                            "message": "Error evaluating script again",
                            "source": "sys_script.My BR",
                            "level": "0",
                            "sys_created_on": "2026-02-20 10:05:00",
                        },
                        {
                            "sys_id": "log3",
                            "message": "Null pointer",
                            "source": "sys_script_include.Helper",
                            "level": "0",
                            "sys_created_on": "2026-02-20 11:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="error_analysis")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 2  # 2 clusters
        # Top cluster should have frequency 2
        top = result["data"]["findings"][0]
        assert top["frequency"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_errors_clean_report(self, settings, auth_provider):
        """No syslog errors returns clean report."""
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="error_analysis")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_hours(self, settings, auth_provider):
        """syslog query includes time filter."""
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="error_analysis", params='{"hours": 6}')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] == 6


# ── slow_transactions ─────────────────────────────────────────────────────


class TestSlowTransactions:
    """Tests for the slow_transactions investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_slow_query_pattern(self, settings, auth_provider):
        """Finds a slow query pattern from sys_query_pattern."""
        # sys_query_pattern returns a hit
        respx.get(f"{BASE_URL}/api/now/table/sys_query_pattern").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "qp001",
                            "name": "incident - complex query",
                            "count": "450",
                            "sys_created_on": "2026-02-20 08:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # All other pattern tables empty
        for table in [
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="slow_transactions")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1
        assert result["data"]["findings"][0]["category"] == "slow_query"

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_hours(self, settings, auth_provider):
        """Queries include gs.hoursAgoStart time filter."""
        # Mock all 7 pattern tables
        for table in [
            "sys_query_pattern",
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="slow_transactions", params='{"hours": 12}')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] == 12

    @pytest.mark.asyncio
    @respx.mock
    async def test_default_hours_is_24(self, settings, auth_provider):
        """Default hours is 24 when not specified."""
        for table in [
            "sys_query_pattern",
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="slow_transactions")
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] == 24


# ── performance_bottlenecks ───────────────────────────────────────────────


class TestPerformanceBottlenecks:
    """Tests for the performance_bottlenecks investigation module."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_finds_heavy_automation_table(self, settings, auth_provider):
        """Finds a table with excessive active business rules."""
        # Query sys_script for active BRs — returns many for 'incident'
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"br{i}",
                            "name": f"BR {i}",
                            "collection": "incident",
                            "active": "true",
                        }
                        for i in range(15)
                    ]
                },
                headers={"X-Total-Count": "15"},
            )
        )
        # Scheduled jobs — empty
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # Flow contexts — empty
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="performance_bottlenecks")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1
        assert result["data"]["findings"][0]["category"] == "heavy_automation"

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_hours(self, settings, auth_provider):
        """Queries include time filter when hours is specified."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="performance_bottlenecks", params='{"hours": 12}')
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] == 12

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_hours_defaults_to_none(self, settings, auth_provider):
        """Hours defaults to None when not specified."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="performance_bottlenecks")
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] is None


# ── explain() tests for all 7 investigation modules ──────────────────────


class TestExplainStaleAutomations:
    """Tests for stale_automations explain() — all four table branches."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_flow_context(self, settings, auth_provider):
        """explain() returns context for a stuck flow_context record."""
        respx.get(f"{BASE_URL}/api/now/table/flow_context/fc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "fc001",
                        "name": "Approval Flow",
                        "state": "IN_PROGRESS",
                        "sys_created_on": "2026-01-01 00:00:00",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="flow_context:fc001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "explanation" in result["data"]
        assert "element" in result["data"]
        assert "Approval Flow" in result["data"]["explanation"]
        assert result["data"]["record"]["sys_id"] == "fc001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_sys_script(self, settings, auth_provider):
        """explain() returns context for a disabled business rule."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "Old BR",
                        "active": "false",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="sys_script:br001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "disabled" in result["data"]["explanation"].lower()
        assert "Old BR" in result["data"]["explanation"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_sys_script_include(self, settings, auth_provider):
        """explain() returns context for a disabled script include."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include/si001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "si001",
                        "name": "LegacyHelper",
                        "active": "false",
                        "api_name": "global.LegacyHelper",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="sys_script_include:si001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "disabled" in result["data"]["explanation"].lower()
        assert "LegacyHelper" in result["data"]["explanation"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_sysauto_script(self, settings, auth_provider):
        """explain() returns context for a stale scheduled job."""
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script/sj001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "sj001",
                        "name": "Nightly Cleanup",
                        "last_run": "2025-06-01 00:00:00",
                        "run_type": "daily",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="sysauto_script:sj001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "Nightly Cleanup" in result["data"]["explanation"]
        assert "last run" in result["data"]["explanation"].lower()


class TestExplainDeprecatedApis:
    """Tests for deprecated_apis explain()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_deprecated_script(self, settings, auth_provider):
        """explain() returns context for a script using deprecated APIs."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include/script001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "script001",
                        "name": "OldHelper",
                        "api_name": "global.OldHelper",
                        "script": "var x = Packages.com.example.Test;",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="deprecated_apis",
            element_id="sys_script_include:script001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "deprecated" in result["data"]["explanation"].lower()
        assert "OldHelper" in result["data"]["explanation"]
        assert result["data"]["element"] == "sys_script_include:script001"


class TestExplainErrorAnalysis:
    """Tests for error_analysis explain()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_syslog_error(self, settings, auth_provider):
        """explain() returns context for a syslog error entry."""
        respx.get(f"{BASE_URL}/api/now/table/syslog/log001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "log001",
                        "message": "Error evaluating script",
                        "source": "sys_script.My BR",
                        "level": "0",
                        "sys_created_on": "2026-02-20 10:00:00",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="error_analysis",
            element_id="syslog:log001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "sys_script.My BR" in result["data"]["explanation"]
        assert "Error evaluating script" in result["data"]["explanation"]
        assert result["data"]["element"] == "syslog:log001"


class TestExplainSlowTransactions:
    """Tests for slow_transactions explain()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_query_pattern(self, settings, auth_provider):
        """explain() returns context for a slow query pattern."""
        respx.get(f"{BASE_URL}/api/now/table/sys_query_pattern/qp001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "qp001",
                        "name": "incident - complex query",
                        "count": "450",
                        "sys_created_on": "2026-02-20 08:00:00",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="slow_transactions",
            element_id="sys_query_pattern:qp001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "sys_query_pattern" in result["data"]["explanation"]
        assert "incident - complex query" in result["data"]["explanation"]
        assert "450" in result["data"]["explanation"]
        assert result["data"]["element"] == "sys_query_pattern:qp001"


class TestExplainPerformanceBottlenecks:
    """Tests for performance_bottlenecks explain() — both branches."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_table_with_colon(self, settings, auth_provider):
        """explain() with table:sys_id returns record context."""
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script/sj001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "sj001",
                        "name": "Heavy Job",
                        "run_type": "daily",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="sysauto_script:sj001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "Heavy Job" in result["data"]["explanation"]
        assert "bottleneck" in result["data"]["explanation"].lower()
        assert result["data"]["record"]["sys_id"] == "sj001"

    @pytest.mark.asyncio
    async def test_explain_invalid_table_identifier(self, settings, auth_provider):
        """explain() with an invalid table name in element_id returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="../evil_table:sj001",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_explain_denied_table(self, settings, auth_provider):
        """explain() with a denied table name in element_id returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="sys_user_token:sj001",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_heavy_automation_table(self, settings, auth_provider):
        """explain() with a plain table name returns aggregate context."""
        # Aggregate count for 'incident'
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "5000"}}},
            )
        )
        # Active BRs for 'incident'
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"br{i}",
                            "name": f"BR {i}",
                            "when": "before",
                        }
                        for i in range(15)
                    ]
                },
                headers={"X-Total-Count": "15"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="incident",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 5000
        assert result["data"]["br_count"] == 15
        assert "incident" in result["data"]["explanation"]


class TestExplainAclConflicts:
    """Tests for acl_conflicts explain()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_acl_record(self, settings, auth_provider):
        """explain() returns context for an ACL conflict finding."""
        respx.get(f"{BASE_URL}/api/now/table/sys_security_acl/acl001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "acl001",
                        "name": "incident.*.read",
                        "operation": "read",
                        "condition": "active=true",
                        "script": "",
                        "active": "true",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="acl_conflicts",
            element_id="acl001",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "incident.*.read" in result["data"]["explanation"]
        assert "read" in result["data"]["explanation"]
        assert (
            "conflicting" in result["data"]["explanation"].lower()
            or "consolidated" in result["data"]["explanation"].lower()
        )
        assert result["data"]["record"]["sys_id"] == "acl001"


class TestExplainTableHealth:
    """Tests for table_health explain()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_explain_table_health(self, settings, auth_provider):
        """explain() returns aggregate context for a table."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "10000"}}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="table_health",
            element_id="incident",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 10000
        assert "incident" in result["data"]["explanation"]
        assert result["data"]["element"] == "incident"


# ── Security restrictions on explain() ───────────────────────────────────


class TestExplainSecurityRestrictions:
    """Tests that explain() rejects element_ids referencing disallowed tables or invalid sys_ids."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_deprecated_apis_rejects_disallowed_table(self, settings, auth_provider):
        """deprecated_apis explain() returns error for a table not in _ALLOWED_TABLES."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="deprecated_apis",
            element_id="sys_user:abc123456789012345678901234567ab",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"  # Dispatcher succeeds; module returns error in data
        assert "error" in result["data"]
        assert "sys_user" in result["data"]["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_analysis_rejects_non_syslog_table(self, settings, auth_provider):
        """error_analysis explain() returns error for a table other than syslog."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="error_analysis",
            element_id="incident:abc123456789012345678901234567ab",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "incident" in result["data"]["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_slow_transactions_rejects_disallowed_table(self, settings, auth_provider):
        """slow_transactions explain() returns error for a table not in PERFORMANCE_TABLES."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="slow_transactions",
            element_id="sys_user:abc123456789012345678901234567ab",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "sys_user" in result["data"]["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_stale_automations_rejects_disallowed_table(self, settings, auth_provider):
        """stale_automations explain() returns error for a table not in _ALLOWED_TABLES."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="sys_user:abc123456789012345678901234567ab",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "sys_user" in result["data"]["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_sys_id_format(self, settings, auth_provider):
        """Any investigation rejects element_id with an invalid sys_id format."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="error_analysis",
            element_id="syslog:not-a-sys-id",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]


# ── WP5: element_id split guard tests ────────────────────────────────────


class TestExplainElementIdSplitGuard:
    """Tests that explain() returns an error dict for element_ids with no colon."""

    @pytest.mark.asyncio
    async def test_deprecated_apis_no_colon(self, settings, auth_provider):
        """deprecated_apis explain() returns error for element_id without colon."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="deprecated_apis",
            element_id="invalid_no_colon",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "expected 'table:sys_id'" in result["data"]["error"]

    @pytest.mark.asyncio
    async def test_error_analysis_no_colon(self, settings, auth_provider):
        """error_analysis explain() returns error for element_id without colon."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="error_analysis",
            element_id="invalid_no_colon",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "expected 'table:sys_id'" in result["data"]["error"]

    @pytest.mark.asyncio
    async def test_slow_transactions_no_colon(self, settings, auth_provider):
        """slow_transactions explain() returns error for element_id without colon."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="slow_transactions",
            element_id="invalid_no_colon",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "expected 'table:sys_id'" in result["data"]["error"]

    @pytest.mark.asyncio
    async def test_stale_automations_no_colon(self, settings, auth_provider):
        """stale_automations explain() returns error for element_id without colon."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="stale_automations",
            element_id="invalid_no_colon",
        )
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert "error" in result["data"]
        assert "expected 'table:sys_id'" in result["data"]["error"]


# ── WP5: Type coercion tests ─────────────────────────────────────────────


class TestTypeCoercion:
    """Tests that numeric params passed as strings are properly coerced."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_slow_transactions_string_limit(self, settings, auth_provider):
        """slow_transactions run() accepts limit as a string."""
        for table in [
            "sys_query_pattern",
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="slow_transactions",
            params='{"limit": "50"}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["limit"] == 50

    @pytest.mark.asyncio
    @respx.mock
    async def test_stale_automations_string_stale_days(self, settings, auth_provider):
        """stale_automations run() accepts stale_days as a string."""
        for table in [
            "flow_context",
            "sys_script",
            "sys_script_include",
            "sysauto_script",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="stale_automations",
            params='{"stale_days": "30", "limit": "10"}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["stale_days"] == 30
        assert result["data"]["params"]["limit"] == 10

    @pytest.mark.asyncio
    @respx.mock
    async def test_performance_bottlenecks_string_hours_and_limit(self, settings, auth_provider):
        """performance_bottlenecks run() accepts hours and limit as strings."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="performance_bottlenecks",
            params='{"hours": "12", "limit": "5"}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["hours"] == 12
        assert result["data"]["params"]["limit"] == 5

    @pytest.mark.asyncio
    @respx.mock
    async def test_table_health_string_hours(self, settings, auth_provider):
        """table_health run() accepts hours as a string."""
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(200, json={"result": {"stats": {"count": "100"}}})
        )
        for table in [
            "sys_script",
            "sys_script_client",
            "sys_security_acl",
            "sys_ui_policy",
            "syslog",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="table_health",
            params='{"table": "incident", "hours": "48"}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["hours"] == 48

    @pytest.mark.asyncio
    @respx.mock
    async def test_deprecated_apis_string_limit(self, settings, auth_provider):
        """deprecated_apis run() accepts limit as a string."""
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(200, json={"result": {"search_results": []}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="deprecated_apis",
            params='{"limit": "50"}',
        )
        result = toon_decode(raw)
        assert result["status"] == "success"
        assert result["data"]["params"]["limit"] == 50


# ── WP5: check_table_access test for error_analysis ──────────────────────


class TestErrorAnalysisCheckTableAccess:
    """Tests that error_analysis run() calls check_table_access."""

    @pytest.mark.asyncio
    async def test_check_table_access_propagates_through_dispatcher(self, settings, auth_provider):
        """error_analysis run() propagates PolicyError through the dispatcher."""
        from unittest.mock import patch

        from servicenow_mcp.errors import PolicyError

        tools = _register_and_get_tools(settings, auth_provider)

        with patch(
            "servicenow_mcp.investigations.error_analysis.check_table_access",
            side_effect=PolicyError("Access to table 'syslog' is denied by policy"),
        ):
            raw = await tools["investigate_run"](investigation="error_analysis")
            result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


# ── WP5: validate_identifier + check_table_access for perf bottlenecks ───


class TestPerformanceBottlenecksElseBranch:
    """Tests for performance_bottlenecks explain() else-branch (table name only)."""

    @pytest.mark.asyncio
    async def test_explain_invalid_table_chars(self, settings, auth_provider):
        """explain() with invalid chars in table name raises ValueError."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="INVALID_TABLE",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_explain_special_chars_table(self, settings, auth_provider):
        """explain() with special chars in table name (no colon) raises ValueError."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="my-table!",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_explain_denied_table_else_branch(self, settings, auth_provider):
        """explain() with a denied table name in the else-branch returns error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="sys_user_token",
        )
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


# ── Direct module tests for coverage gaps ─────────────────────────────────


class TestPerformanceBottlenecksCoverage:
    """Tests for performance_bottlenecks coverage gaps: invalid params and non-empty loop bodies."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_params_and_nonempty_records(self, settings, auth_provider):
        """Invalid limit/hours fall back to defaults; non-empty sysauto_script and flow_context records hit loop bodies."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import performance_bottlenecks

        # Active BRs — empty (no heavy automation findings)
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # Scheduled jobs — non-empty (hits lines 78-79)
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "abc",
                            "name": "test_job",
                            "run_type": "daily",
                            "run_dayofweek": "1",
                            "sys_updated_on": "2026-02-20 10:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Flow contexts — non-empty (hits lines 100-101)
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "flow1",
                            "name": "Running Flow",
                            "state": "IN_PROGRESS",
                            "sys_created_on": "2026-02-20 08:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await performance_bottlenecks.run(
                client,
                {"limit": "not_a_number", "hours": "bad", "table": "incident"},
            )

        # Invalid limit falls back to 20 (lines 27-28)
        assert result["params"]["limit"] == 20
        # Invalid hours falls back to 24 (lines 34-35)
        assert result["params"]["hours"] == 24
        # Should have findings from both sysauto_script and flow_context loops
        categories = [f["category"] for f in result["findings"]]
        assert "frequent_job" in categories
        assert "long_running_flow" in categories


class TestStaleAutomationsCoverage:
    """Tests for stale_automations coverage gaps: invalid params and non-empty loop bodies."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_params_and_nonempty_records(self, settings, auth_provider):
        """Invalid stale_days/limit fall back to defaults; non-empty records hit loop bodies."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import stale_automations

        # Stuck flows — empty
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # Disabled BRs — non-empty (hits lines 64-65)
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br001",
                            "name": "Disabled BR",
                            "collection": "incident",
                            "sys_updated_on": "2026-01-01 00:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Disabled script includes — non-empty (hits lines 83-84)
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "si001",
                            "name": "OldHelper",
                            "api_name": "global.OldHelper",
                            "sys_updated_on": "2026-01-01 00:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Stale scheduled jobs — non-empty (hits lines 102-103)
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "sj001",
                            "name": "Stale Job",
                            "run_type": "daily",
                            "last_run": "2025-01-01 00:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await stale_automations.run(
                client,
                {"stale_days": "bad", "limit": "bad", "table": "incident"},
            )

        # Invalid stale_days falls back to 30 (lines 27-28)
        assert result["params"]["stale_days"] == 30
        # Invalid limit falls back to 20 (lines 31-32)
        assert result["params"]["limit"] == 20
        # Non-empty loop bodies produce findings
        categories = [f["category"] for f in result["findings"]]
        assert "disabled_business_rule" in categories
        assert "disabled_script_include" in categories
        assert "stale_scheduled_job" in categories


class TestErrorAnalysisCoverage:
    """Tests for error_analysis coverage gaps: invalid params and source filter."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_hours_and_limit(self, settings, auth_provider):
        """Invalid hours/limit fall back to defaults."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import error_analysis

        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await error_analysis.run(client, {"hours": "bad", "limit": "bad"})

        # Invalid hours falls back to 24 (lines 24-25)
        assert result["params"]["hours"] == 24
        # Invalid limit falls back to 100 (lines 29-30)
        assert result["params"]["limit"] == 100

    @pytest.mark.asyncio
    @respx.mock
    async def test_source_filter(self, settings, auth_provider):
        """Source filter triggers the .like() branch (line 36)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import error_analysis

        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await error_analysis.run(client, {"source": "my_source"})

        assert result["params"]["source"] == "my_source"
        assert result["investigation"] == "error_analysis"


class TestDeprecatedApisCoverage:
    """Tests for deprecated_apis coverage gaps: invalid limit and code_search exception."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_limit(self, settings, auth_provider):
        """Invalid limit falls back to default 20."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import deprecated_apis

        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(200, json={"result": {"search_results": []}})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await deprecated_apis.run(client, {"limit": "bad"})

        # Invalid limit falls back to 20 (lines 38-39)
        assert result["params"]["limit"] == 20

    @pytest.mark.asyncio
    @respx.mock
    async def test_code_search_exception_skips_pattern(self, settings, auth_provider):
        """When code_search raises for one pattern, it's skipped (lines 56-58)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import deprecated_apis

        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First pattern raises an error
                return httpx.Response(500, json={"error": {"message": "Server Error"}})
            return httpx.Response(200, json={"result": {"search_results": []}})

        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(side_effect=side_effect)

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await deprecated_apis.run(client, {})

        # Should complete without error, skipping the failed pattern
        assert result["investigation"] == "deprecated_apis"
        assert "patterns_searched" in result


class TestSlowTransactionsCoverage:
    """Tests for slow_transactions coverage gaps."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_hours_and_limit(self, settings, auth_provider):
        """Invalid hours/limit fall back to defaults (lines 36-37, 40-41)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import slow_transactions

        for table in [
            "sys_query_pattern",
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await slow_transactions.run(client, {"hours": "bad", "limit": "bad"})

        assert result["params"]["hours"] == 24
        assert result["params"]["limit"] == 20

    @pytest.mark.asyncio
    @respx.mock
    async def test_categories_filter(self, settings, auth_provider):
        """Category filter skips non-matching tables (lines 45, 51)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import slow_transactions

        # Only mock the table that matches the category filter
        respx.get(f"{BASE_URL}/api/now/table/sys_query_pattern").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await slow_transactions.run(client, {"categories": "slow_query"})

        assert result["params"]["categories"] == "slow_query"
        # Only one table should have been queried (the rest skipped)
        assert result["investigation"] == "slow_transactions"

    @pytest.mark.asyncio
    @respx.mock
    async def test_query_records_exception_skips_table(self, settings, auth_provider):
        """When query_records raises for a table, it's skipped (lines 84-86)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import slow_transactions

        # First table errors, rest succeed with empty
        respx.get(f"{BASE_URL}/api/now/table/sys_query_pattern").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Server Error"}})
        )
        for table in [
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await slow_transactions.run(client, {})

        # Should complete without error despite one table failing
        assert result["investigation"] == "slow_transactions"


class TestTableHealthCoverage:
    """Tests for table_health coverage gaps."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_table_param(self, settings, auth_provider):
        """Missing table param returns error (line 23)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import table_health

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await table_health.run(client, {})

        assert result["error"] == "Missing required parameter: table"
        assert result["finding_count"] == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_hours_param(self, settings, auth_provider):
        """Invalid hours falls back to 24 (lines 38-39)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import table_health

        # Aggregate count
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(200, json={"result": {"stats": {"count": "10"}}})
        )
        for tbl in [
            "sys_script",
            "sys_script_client",
            "sys_security_acl",
            "sys_ui_policy",
            "syslog",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{tbl}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await table_health.run(client, {"table": "incident", "hours": "bad"})

        assert result["hours"] == 24

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_indicators_thresholds(self, settings, auth_provider):
        """Triggers all health indicator thresholds (lines 101, 103, 105)."""
        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import table_health

        # Aggregate count
        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(200, json={"result": {"stats": {"count": "500"}}})
        )
        # >10 business rules (hits line 101)
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"br{i}",
                            "name": f"BR {i}",
                            "when": "before",
                        }
                        for i in range(12)
                    ]
                },
                headers={"X-Total-Count": "12"},
            )
        )
        # Client scripts — empty
        respx.get(f"{BASE_URL}/api/now/table/sys_script_client").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # >20 ACLs (hits line 103)
        respx.get(f"{BASE_URL}/api/now/table/sys_security_acl").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"acl{i}",
                            "name": "incident.*.read",
                            "operation": "read",
                        }
                        for i in range(22)
                    ]
                },
                headers={"X-Total-Count": "22"},
            )
        )
        # UI policies — empty
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # >=1 syslog error (hits line 105)
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "log1",
                            "message": "Error",
                            "source": "incident",
                            "sys_created_on": "2026-02-20 10:00:00",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await table_health.run(client, {"table": "incident"})

        # All three thresholds should be triggered
        assert len(result["health_indicators"]) == 3
        indicators_text = " ".join(result["health_indicators"])
        assert "business rule" in indicators_text.lower()
        assert "acl" in indicators_text.lower()
        assert "errors" in indicators_text.lower()


# ── check_table_access enforcement in run() functions ─────────────────────


class TestPerformanceBottlenecksCheckTableAccess:
    """Tests that performance_bottlenecks run() calls check_table_access for each queried table."""

    @pytest.mark.asyncio
    async def test_run_raises_on_denied_sys_script(self, settings, auth_provider):
        """run() raises PolicyError when sys_script is denied."""
        from unittest.mock import patch

        from servicenow_mcp.errors import PolicyError

        tools = _register_and_get_tools(settings, auth_provider)

        def deny_sys_script(table: str) -> None:
            if table == "sys_script":
                raise PolicyError(f"Access to table '{table}' is denied by policy")

        with patch(
            "servicenow_mcp.investigations.performance_bottlenecks.check_table_access",
            side_effect=deny_sys_script,
        ):
            raw = await tools["investigate_run"](investigation="performance_bottlenecks")
            result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


class TestSlowTransactionsCheckTableAccess:
    """Tests that slow_transactions run() calls check_table_access for each queried table."""

    @pytest.mark.asyncio
    async def test_run_raises_on_denied_table(self, settings, auth_provider):
        """run() raises PolicyError when a performance pattern table is denied."""
        from unittest.mock import patch

        from servicenow_mcp.errors import PolicyError

        tools = _register_and_get_tools(settings, auth_provider)

        def deny_first_table(table: str) -> None:
            if table == "sys_query_pattern":
                raise PolicyError(f"Access to table '{table}' is denied by policy")

        with patch(
            "servicenow_mcp.investigations.slow_transactions.check_table_access",
            side_effect=deny_first_table,
        ):
            raw = await tools["investigate_run"](investigation="slow_transactions")
            result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


class TestStaleAutomationsCheckTableAccess:
    """Tests that stale_automations run() calls check_table_access for each queried table."""

    @pytest.mark.asyncio
    async def test_run_raises_on_denied_flow_context(self, settings, auth_provider):
        """run() raises PolicyError when flow_context is denied."""
        from unittest.mock import patch

        from servicenow_mcp.errors import PolicyError

        tools = _register_and_get_tools(settings, auth_provider)

        def deny_flow_context(table: str) -> None:
            if table == "flow_context":
                raise PolicyError(f"Access to table '{table}' is denied by policy")

        with patch(
            "servicenow_mcp.investigations.stale_automations.check_table_access",
            side_effect=deny_flow_context,
        ):
            raw = await tools["investigate_run"](investigation="stale_automations")
            result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


class TestTableHealthExplainCheckTableAccess:
    """Tests that table_health explain() calls check_table_access."""

    @pytest.mark.asyncio
    async def test_explain_raises_on_denied_table(self, settings, auth_provider):
        """explain() raises PolicyError when the element_id table is denied."""
        from unittest.mock import patch

        from servicenow_mcp.errors import PolicyError

        tools = _register_and_get_tools(settings, auth_provider)

        with patch(
            "servicenow_mcp.investigations.table_health.check_table_access",
            side_effect=PolicyError("Access to table 'incident' is denied by policy"),
        ):
            raw = await tools["investigate_explain"](
                investigation="table_health",
                element_id="incident",
            )
            result = toon_decode(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


# ── Clamping: hours/stale_days minimum 1 ──────────────────────────────────


class TestClampingMinimumOne:
    """Tests that hours/stale_days are clamped to a minimum of 1."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_analysis_clamps_hours_zero_to_one(self, settings, auth_provider):
        """error_analysis run() with hours=0 clamps to 1."""
        from urllib.parse import unquote

        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import error_analysis

        route = respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await error_analysis.run(client, {"hours": 0})

        assert result["params"]["hours"] == 1
        # Verify the actual query used hours_ago with value 1
        request_url = unquote(str(route.calls[0].request.url))
        assert "javascript:gs.hoursAgoStart(1)" in request_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_slow_transactions_clamps_hours_zero_to_one(self, settings, auth_provider):
        """slow_transactions run() with hours=0 clamps to 1."""
        from urllib.parse import unquote

        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import slow_transactions

        routes: dict[str, respx.Route] = {}
        for table in [
            "sys_query_pattern",
            "sys_transaction_pattern",
            "sys_script_pattern",
            "sys_mutex_pattern",
            "sysevent_pattern",
            "sys_interaction_pattern",
            "syslog_cancellation",
        ]:
            routes[table] = respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await slow_transactions.run(client, {"hours": 0})

        assert result["params"]["hours"] == 1
        # Verify that syslog_cancellation query used hours_ago with value 1
        cancellation_url = unquote(str(routes["syslog_cancellation"].calls[0].request.url))
        assert "javascript:gs.hoursAgoStart(1)" in cancellation_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_stale_automations_clamps_stale_days_zero_to_one(self, settings, auth_provider):
        """stale_automations run() with stale_days=0 clamps to 1."""
        from urllib.parse import unquote

        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import stale_automations

        flow_route = respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script_include").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await stale_automations.run(client, {"stale_days": 0})

        assert result["params"]["stale_days"] == 1
        # Verify the flow_context query used older_than_days with value 1
        flow_url = unquote(str(flow_route.calls[0].request.url))
        assert "javascript:gs.daysAgoEnd(1)" in flow_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_performance_bottlenecks_clamps_negative_hours_to_one(self, settings, auth_provider):
        """performance_bottlenecks run() with hours=-5 clamps to 1."""
        from urllib.parse import unquote

        from servicenow_mcp.auth import BasicAuthProvider
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.investigations import performance_bottlenecks

        sys_script_route = respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sysauto_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        auth = BasicAuthProvider(settings)
        async with ServiceNowClient(settings, auth) as client:
            result = await performance_bottlenecks.run(client, {"hours": -5})

        assert result["params"]["hours"] == 1
        # Verify the sys_script query used hours_ago with value 1
        script_url = unquote(str(sys_script_route.calls[0].request.url))
        assert "javascript:gs.hoursAgoStart(1)" in script_url
