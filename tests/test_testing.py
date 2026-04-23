"""Tests for ATF introspection tools (atf_list_tests, atf_get_test, atf_list_suites, atf_get_results)."""

from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp_state import attach_query_store
from servicenow_mcp.state import QueryTokenStore
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings, auth_provider: BasicAuthProvider
) -> tuple[dict[str, Any], QueryTokenStore]:
    """Helper: register testing tools on a fresh MCP server and return tool map + query store."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.testing import register_tools

    mcp = FastMCP("test")
    query_store = QueryTokenStore()
    attach_query_store(mcp, query_store)
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp), query_store


class TestAtfListTests:
    """Tests for the atf_list_tests tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_tests_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns ATF tests with expected fields."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "b444ac06613fc8d63795be9ad0beaf55",
                            "name": "Validate incident creation",
                            "description": "Test incident table automation",
                            "active": "true",
                            "sys_updated_on": "2026-02-20 10:00:00",
                            "test_origin": "manual",
                        },
                        {
                            "sys_id": "109f4b3c50d7b0df729d299bc6f8e9ef",
                            "name": "Check user permissions",
                            "description": "Verify ITIL role assignment",
                            "active": "true",
                            "sys_updated_on": "2026-02-21 14:30:00",
                            "test_origin": "automated",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_list_tests"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 2
        assert result["data"]["records"][0]["sys_id"] == "b444ac06613fc8d63795be9ad0beaf55"
        assert result["data"]["records"][1]["sys_id"] == "109f4b3c50d7b0df729d299bc6f8e9ef"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_tests_with_query(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Verify custom query param is passed through to API call."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_atf_test").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "b444ac06613fc8d63795be9ad0beaf55",
                            "name": "Active test only",
                            "description": "Test",
                            "active": "true",
                            "sys_updated_on": "2026-02-20 10:00:00",
                            "test_origin": "manual",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, query_store = _register_and_get_tools(settings, auth_provider)
        token = query_store.create({"query": "active=true", "table": "sys_atf_test"})
        raw = await tools["atf_list_tests"](query_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert route.called
        request = route.calls.last.request
        assert "active=true" in unquote(str(request.url))

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_tests_empty_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns success with empty data when no tests found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_list_tests"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 0
        assert result["data"]["records"] == []


class TestAtfGetTest:
    """Tests for the atf_get_test tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_test_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns test record and steps in combined response."""
        # Mock test record
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test/7288edd0fc3ffcbe93a0cf06e3568e28").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "7288edd0fc3ffcbe93a0cf06e3568e28",
                        "name": "Incident workflow test",
                        "description": "Validates incident state transitions",
                        "active": "true",
                    }
                },
            )
        )
        # Mock test steps
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_step").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "84f1443f50ba4a894ac616a5f064c686",
                            "display_name": "Open record",
                            "step_config": "Record Producer",
                            "order": "1",
                            "inputs": "{}",
                        },
                        {
                            "sys_id": "4a663a0c99bf1bd49e069a66286dda78",
                            "display_name": "Validate state",
                            "step_config": "Field Values Validation",
                            "order": "2",
                            "inputs": '{"field": "state", "value": "1"}',
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_test"](test_id="7288edd0fc3ffcbe93a0cf06e3568e28")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["test"]["sys_id"] == "7288edd0fc3ffcbe93a0cf06e3568e28"
        assert result["data"]["step_count"] == 2
        assert len(result["data"]["steps"]) == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_test_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error envelope when test not found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test/missing").mock(
            return_value=httpx.Response(
                404,
                json={"error": {"message": "Record not found"}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_test"](test_id="missing")
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_test_with_steps(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns test with 3 steps ordered correctly."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test/a51821834d0fe748cf923a6ee607e647").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a51821834d0fe748cf923a6ee607e647",
                        "name": "Multi-step test",
                        "description": "Complex workflow",
                        "active": "true",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_step").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "640d87e741e6aa4c669a82a4cd304787",
                            "display_name": "Step 1",
                            "order": "1",
                            "step_config": "Config",
                            "inputs": "{}",
                        },
                        {
                            "sys_id": "4205714cdfe14ed9e3d030ddf7887781",
                            "display_name": "Step 2",
                            "order": "2",
                            "step_config": "Config",
                            "inputs": "{}",
                        },
                        {
                            "sys_id": "dd33a084ba223dd231b0aa962f77a592",
                            "display_name": "Step 3",
                            "order": "3",
                            "step_config": "Config",
                            "inputs": "{}",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_test"](test_id="a51821834d0fe748cf923a6ee607e647")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["step_count"] == 3
        assert result["data"]["steps"][0]["order"] == "1"
        assert result["data"]["steps"][1]["order"] == "2"
        assert result["data"]["steps"][2]["order"] == "3"


class TestAtfListSuites:
    """Tests for the atf_list_suites tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_suites_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns suites with member counts."""
        # Mock suite query
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_suite").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "617a1ac5c6b3bf6aa248f570575c6752",
                            "name": "Regression Suite",
                            "description": "Full regression tests",
                            "active": "true",
                            "sys_updated_on": "2026-02-20 09:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock member count aggregation
        respx.get(f"{BASE_URL}/api/now/stats/sys_atf_test_suite_test").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "5"}}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_list_suites"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 1
        assert result["data"]["suites"][0]["sys_id"] == "617a1ac5c6b3bf6aa248f570575c6752"
        assert result["data"]["suites"][0]["member_count"] == "5"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_suites_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns success with empty data when no suites found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_suite").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_list_suites"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record_count"] == 0
        assert result["data"]["suites"] == []


class TestAtfGetResults:
    """Tests for the atf_get_results tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_results_for_test(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns test results from sys_atf_test_result table."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "e80eb64ba7e143bde0ea09702b4417a7",
                            "status": "success",
                            "start_time": "2026-02-20 10:00:00",
                            "end_time": "2026-02-20 10:00:15",
                            "run_time": "15",
                            "output": "All steps passed",
                            "first_failing_step": "",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_results"](test_id="7288edd0fc3ffcbe93a0cf06e3568e28")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["result_type"] == "test_results"
        assert result["data"]["result_count"] == 1
        assert result["data"]["results"][0]["status"] == "success"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_results_for_suite(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns suite results from sys_atf_test_suite_result table."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_suite_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "suite_result1",
                            "status": "failure",
                            "start_time": "2026-02-20 11:00:00",
                            "end_time": "2026-02-20 11:05:00",
                            "success_count": "3",
                            "failure_count": "1",
                            "error_count": "0",
                            "skipped_count": "0",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_results"](suite_id="5fbf10a1272bc00c1777003333454539")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["result_type"] == "suite_results"
        assert result["data"]["result_count"] == 1
        assert result["data"]["results"][0]["status"] == "failure"

    @pytest.mark.asyncio()
    async def test_get_results_both_ids_provided(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when both test_id and suite_id are provided."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_results"](
            test_id="7288edd0fc3ffcbe93a0cf06e3568e28", suite_id="5fbf10a1272bc00c1777003333454539"
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "not both" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_get_results_missing_both_ids(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when neither test_id nor suite_id provided."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_results"]()
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "exactly one" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_results_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns success with empty results when no results found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_get_results"](test_id="6d851dca996d8dc604e7625e49045f60")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["result_count"] == 0
        assert result["data"]["results"] == []


class TestAtfRunTest:
    """Tests for the atf_run_test tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_test_success_with_poll(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock run + progress endpoints. Returns completed status with execution ID."""
        # Mock ATF run endpoint
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "251cd4a5ab4671b4df3e44be2bfb2635"}},
            )
        )
        # Mock progress endpoint - completed immediately
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "Completed", "progress": 100}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_test"](
            test_id="a51821834d0fe748cf923a6ee607e647", poll=True, poll_interval=2, max_poll_duration=10
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["execution_id"] == "251cd4a5ab4671b4df3e44be2bfb2635"
        assert result["data"]["status"] == "completed"
        assert result["data"]["progress"] == 100
        assert result["data"]["test_id"] == "a51821834d0fe748cf923a6ee607e647"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_test_no_poll(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Call with poll=False. Returns immediately with execution ID."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "6d870f06aaaf54f00bca318fe10612d4"}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_test"](test_id="6d851dca996d8dc604e7625e49045f60", poll=False)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["execution_id"] == "6d870f06aaaf54f00bca318fe10612d4"
        assert result["data"]["status"] == "started"
        assert result["data"]["polling"] is False
        assert result["data"]["test_id"] == "6d851dca996d8dc604e7625e49045f60"

    @pytest.mark.asyncio()
    async def test_run_test_write_gate_blocks(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        """Write gate blocks execution in production environment."""
        tools, _query_store = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["atf_run_test"](test_id="7288edd0fc3ffcbe93a0cf06e3568e28")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower() or "blocked" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_test_no_execution_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when ATF run does not return an execution ID."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_test"](test_id="test_no_id", poll=True)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "no execution id" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_test_timeout(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock progress endpoint always returning In Progress. Assert timeout."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "exec_timeout"}},
            )
        )
        # Progress always in progress
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "In Progress", "progress": 50}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_test"](
            test_id="test_long",
            poll=True,
            poll_interval=2,
            max_poll_duration=10,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["status"] == "polling_timeout"
        assert result["data"]["execution_id"] == "exec_timeout"
        assert result["data"]["last_known_state"] == "in progress"
        assert "warnings" in result

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_test_failed_execution(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock progress returning Failure status. Assert failure captured."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "exec_fail"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "Failure", "progress": 100}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_test"](
            test_id="test_fail",
            poll=True,
            poll_interval=2,
            max_poll_duration=10,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["status"] == "failure"
        assert result["data"]["execution_id"] == "exec_fail"


class TestAtfRunSuite:
    """Tests for the atf_run_suite tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_suite_success_with_poll(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock run + progress. Returns completed status."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "suite_exec123"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "Completed", "progress": 100}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_suite"](
            suite_id="5761f555263e769e9d805337dbe8314c",
            poll=True,
            poll_interval=2,
            max_poll_duration=10,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["execution_id"] == "suite_exec123"
        assert result["data"]["status"] == "completed"
        assert result["data"]["suite_id"] == "5761f555263e769e9d805337dbe8314c"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_suite_no_poll(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Call with poll=False. Returns immediately."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "suite_no_poll"}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_suite"](suite_id="suite_fast", poll=False)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["execution_id"] == "suite_no_poll"
        assert result["data"]["status"] == "started"
        assert result["data"]["polling"] is False

    @pytest.mark.asyncio()
    async def test_run_suite_write_gate_blocks(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        """Write gate blocks suite execution in production."""
        tools, _query_store = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["atf_run_suite"](suite_id="7cce8eebf30cbc1823469b4830873c8c")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower() or "blocked" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_suite_no_execution_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when ATF suite run does not return an execution ID."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_suite"](suite_id="suite_no_id", poll=True)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "no execution id" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_suite_timeout(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock progress always returning In Progress. Assert timeout with warnings."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "suite_exec_timeout"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "In Progress", "progress": 50}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_suite"](
            suite_id="suite_long",
            poll=True,
            poll_interval=2,
            max_poll_duration=10,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["status"] == "polling_timeout"
        assert result["data"]["execution_id"] == "suite_exec_timeout"
        assert result["data"]["suite_id"] == "suite_long"
        assert result["data"]["last_known_state"] == "in progress"
        assert "warnings" in result

    @pytest.mark.asyncio()
    @respx.mock
    async def test_run_suite_cancelled(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock progress returning Cancelled. Assert captured in response."""
        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"snboqId": "suite_cancel"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"state": "Cancelled", "progress": 0}},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_run_suite"](
            suite_id="suite_cancel",
            poll=True,
            poll_interval=2,
            max_poll_duration=10,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["status"] == "cancelled"
        assert result["data"]["execution_id"] == "suite_cancel"


class TestAtfTestHealth:
    """Tests for the atf_test_health tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_all_passing(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock 10 results all passed. Assert 100% pass rate, not flaky."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"res{i}",
                            "status": "success",
                            "sys_created_on": f"2026-02-{i + 1:02d} 10:00:00",
                        }
                        for i in range(10)
                    ]
                },
                headers={"X-Total-Count": "10"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_stable", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_runs"] == 10
        assert result["data"]["pass_count"] == 10
        assert result["data"]["fail_count"] == 0
        assert result["data"]["pass_rate"] == pytest.approx(1.0)
        assert result["data"]["flaky"] is False

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_flaky_detection(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock alternating pass/fail pattern. Assert flaky=true."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"res{i}",
                            "status": "success" if i % 2 == 0 else "failure",
                            "sys_created_on": f"2026-02-{i + 1:02d} 10:00:00",
                        }
                        for i in range(10)
                    ]
                },
                headers={"X-Total-Count": "10"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_flaky", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["flaky"] is True
        assert result["data"]["transition_count"] >= 3

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_trending_downward(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock recent failures after earlier passes. Assert trend degrading."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "5573e39b6600496d40f493d00ec76584",
                            "status": "success",
                            "sys_created_on": "2026-02-01 10:00:00",
                        },
                        {
                            "sys_id": "a50126cc2d6c726de0ca203c3b659f65",
                            "status": "success",
                            "sys_created_on": "2026-02-02 10:00:00",
                        },
                        {
                            "sys_id": "aa893358be4b506d8aeb52b29b8a9cac",
                            "status": "success",
                            "sys_created_on": "2026-02-03 10:00:00",
                        },
                        {
                            "sys_id": "7e264138d0ca103b872057862b9b0359",
                            "status": "success",
                            "sys_created_on": "2026-02-04 10:00:00",
                        },
                        {
                            "sys_id": "51236c5daf199e04c283254bc3ac655a",
                            "status": "failure",
                            "sys_created_on": "2026-02-05 10:00:00",
                        },
                        {
                            "sys_id": "9bf579a927659749fe98171e8ab27ac0",
                            "status": "failure",
                            "sys_created_on": "2026-02-06 10:00:00",
                        },
                        {
                            "sys_id": "daf1ccf042adbc9eba53bea801d6170e",
                            "status": "failure",
                            "sys_created_on": "2026-02-07 10:00:00",
                        },
                        {
                            "sys_id": "4e436c8053f56559b1b98f9168983e4d",
                            "status": "failure",
                            "sys_created_on": "2026-02-08 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "8"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_degrading", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["recent_trend"] == "degrading"
        assert result["data"]["pass_rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_no_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock empty results. Assert appropriate zero-state response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_no_data", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_runs"] == 0
        assert result["data"]["pass_rate"] == pytest.approx(0.0, abs=1e-9)
        assert result["data"]["recent_trend"] == "no_data"
        assert "warnings" in result

    @pytest.mark.asyncio()
    async def test_health_missing_both_ids(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Call with neither test_id nor suite_id. Assert error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"]()
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "exactly one" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_health_both_ids_provided(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Call with both test_id and suite_id. Assert error."""
        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](
            test_id="7288edd0fc3ffcbe93a0cf06e3568e28", suite_id="5fbf10a1272bc00c1777003333454539"
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        error_msg = result["error"]["message"].lower()
        assert "exactly one" in error_msg
        assert "not both" in error_msg

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_trending_upward(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock early failures then later passes. Assert trend improving."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "5573e39b6600496d40f493d00ec76584",
                            "status": "failure",
                            "sys_created_on": "2026-02-01 10:00:00",
                        },
                        {
                            "sys_id": "a50126cc2d6c726de0ca203c3b659f65",
                            "status": "failure",
                            "sys_created_on": "2026-02-02 10:00:00",
                        },
                        {
                            "sys_id": "aa893358be4b506d8aeb52b29b8a9cac",
                            "status": "failure",
                            "sys_created_on": "2026-02-03 10:00:00",
                        },
                        {
                            "sys_id": "7e264138d0ca103b872057862b9b0359",
                            "status": "failure",
                            "sys_created_on": "2026-02-04 10:00:00",
                        },
                        {
                            "sys_id": "51236c5daf199e04c283254bc3ac655a",
                            "status": "success",
                            "sys_created_on": "2026-02-05 10:00:00",
                        },
                        {
                            "sys_id": "9bf579a927659749fe98171e8ab27ac0",
                            "status": "success",
                            "sys_created_on": "2026-02-06 10:00:00",
                        },
                        {
                            "sys_id": "daf1ccf042adbc9eba53bea801d6170e",
                            "status": "success",
                            "sys_created_on": "2026-02-07 10:00:00",
                        },
                        {
                            "sys_id": "4e436c8053f56559b1b98f9168983e4d",
                            "status": "success",
                            "sys_created_on": "2026-02-08 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "8"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_improving", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["recent_trend"] == "improving"
        assert result["data"]["pass_rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_insufficient_data_trend(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock fewer than 4 runs. Assert trend is insufficient_data."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "5573e39b6600496d40f493d00ec76584",
                            "status": "success",
                            "sys_created_on": "2026-02-01 10:00:00",
                        },
                        {
                            "sys_id": "a50126cc2d6c726de0ca203c3b659f65",
                            "status": "failure",
                            "sys_created_on": "2026-02-02 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](test_id="test_few_runs", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_runs"] == 2
        assert result["data"]["recent_trend"] == "insufficient_data"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_health_for_suite(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Mock suite results. Assert rolled-up metrics computed."""
        respx.get(f"{BASE_URL}/api/now/table/sys_atf_test_suite_result").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": f"suite_res{i}",
                            "status": "passed",
                            "sys_created_on": f"2026-02-{i + 1:02d} 10:00:00",
                        }
                        for i in range(5)
                    ]
                },
                headers={"X-Total-Count": "5"},
            )
        )

        tools, _query_store = _register_and_get_tools(settings, auth_provider)
        raw = await tools["atf_test_health"](suite_id="suite_healthy", days=30, limit=50)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_runs"] == 5
        assert result["data"]["pass_count"] == 5
        assert result["data"]["pass_rate"] == pytest.approx(1.0)
