"""Tests for investigation tools (investigate_run, investigate_explain) and 7 investigation modules."""

import json

import httpx
import pytest
import respx

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
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["finding_count"] >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_rejects_unknown_investigation(self, settings, auth_provider):
        """Returns error for unknown investigation name."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](investigation="nonexistent")
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)
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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        for table in ["sys_script", "sys_script_client", "sys_security_acl", "sys_ui_policy", "syslog"]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_run"](
            investigation="table_health",
            params='{"table": "incident", "hours": 24}',
        )
        result = json.loads(raw)
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
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]


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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)
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
        result = json.loads(raw)

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
        result = json.loads(raw)
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
        result = json.loads(raw)
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
        result = json.loads(raw)

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
        result = json.loads(raw)
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
        result = json.loads(raw)
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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]

    @pytest.mark.asyncio
    async def test_explain_denied_table(self, settings, auth_provider):
        """explain() with a denied table name in element_id returns an error."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="sys_user_token:sj001",
        )
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"].lower()

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
                json={"result": [{"sys_id": f"br{i}", "name": f"BR {i}", "when": "before"} for i in range(15)]},
                headers={"X-Total-Count": "15"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate_explain"](
            investigation="performance_bottlenecks",
            element_id="incident",
        )
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

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
        result = json.loads(raw)

        assert result["status"] == "error"
        assert "Invalid identifier" in result["error"]

