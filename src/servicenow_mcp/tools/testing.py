"""ATF (Automated Test Framework) tools for introspection, execution, and intelligence."""

import asyncio
import logging
import time

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields, write_blocked_reason
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    generate_correlation_id,
    resolve_query_token,
    safe_tool_call,
)

logger = logging.getLogger(__name__)

# ATF polling constants
ATF_POLL_INTERVAL = 5
ATF_MAX_POLL_DURATION = 300


def _atf_execution_gate(settings: Settings, correlation_id: str) -> str | None:
    """Gate ATF execution tools - running tests creates result records."""
    reason = write_blocked_reason("sys_atf_test_result", settings)
    if reason:
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=reason,
        )
    return None


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register ATF (Automated Test Framework) tools on the MCP server."""
    query_store: QueryTokenStore = mcp._sn_query_store  # type: ignore[attr-defined]

    @mcp.tool()
    async def atf_list_tests(
        query_token: str = "",
        limit: int = 20,
        fields: str = "",
    ) -> str:
        """Query ATF tests with filtering and pagination.

        Args:
            query_token: Token from the build_query tool for filtering.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            limit: Maximum records to return (default 20).
            fields: Comma-separated field list (empty for default fields).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            check_table_access("sys_atf_test")

            default_fields = "sys_id,name,description,active,sys_updated_on,test_origin"
            field_list = fields if fields else default_fields

            query_str = resolve_query_token(query_token, query_store, correlation_id)

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    "sys_atf_test",
                    query_str,
                    fields=field_list.split(","),
                    limit=limit,
                    order_by="sys_updated_on",
                )

            records = [mask_sensitive_fields(rec) for rec in result["records"]]

            return format_response(
                data={
                    "record_count": len(records),
                    "records": records,
                },
                correlation_id=correlation_id,
            )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_get_test(test_id: str) -> str:
        """Get full test details including test steps.

        Fetches the test record from sys_atf_test and all associated steps
        from sys_atf_step ordered by step order.

        Args:
            test_id: The sys_id of the test record.
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            check_table_access("sys_atf_test")
            check_table_access("sys_atf_step")

            async with ServiceNowClient(settings, auth_provider) as client:
                test, steps_result = await asyncio.gather(
                    client.get_record("sys_atf_test", test_id),
                    client.query_records(
                        "sys_atf_step",
                        ServiceNowQuery().equals("test", test_id).build(),
                        fields=["sys_id", "display_name", "step_config", "order", "inputs"],
                        limit=1000,
                        order_by="order",
                        display_values=True,
                    ),
                )

            test_record = mask_sensitive_fields(test)
            steps = [mask_sensitive_fields(step) for step in steps_result["records"]]

            return format_response(
                data={
                    "test": test_record,
                    "steps": steps,
                    "step_count": len(steps),
                },
                correlation_id=correlation_id,
            )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_list_suites(
        query_token: str = "",
        limit: int = 20,
    ) -> str:
        """Query ATF test suites with member counts.

        Fetches test suites and enriches each with the count of tests in the suite.

        Args:
            query_token: Token from the build_query tool for filtering.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            limit: Maximum records to return (default 20).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            check_table_access("sys_atf_test_suite")
            check_table_access("sys_atf_test_suite_test")

            query = resolve_query_token(query_token, query_store, correlation_id)

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    "sys_atf_test_suite",
                    query,
                    fields=["sys_id", "name", "description", "active", "sys_updated_on"],
                    limit=limit,
                    order_by="sys_updated_on",
                )

                suites = [mask_sensitive_fields(rec) for rec in result["records"]]

                for suite in suites:
                    suite_id = suite.get("sys_id", "")
                    count_result = await client.aggregate(
                        "sys_atf_test_suite_test",
                        ServiceNowQuery().equals("test_suite", suite_id).build(),
                    )
                    suite["member_count"] = count_result.get("stats", {}).get("count", 0)

            return format_response(
                data={
                    "record_count": len(suites),
                    "suites": suites,
                },
                correlation_id=correlation_id,
            )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_get_results(
        test_id: str = "",
        suite_id: str = "",
        limit: int = 10,
    ) -> str:
        """Get test or suite execution results.

        Fetch recent execution results for either a test or a suite.
        Exactly one of test_id or suite_id must be provided.

        Args:
            test_id: The sys_id of a test (sys_atf_test). Mutually exclusive with suite_id.
            suite_id: The sys_id of a test suite (sys_atf_test_suite). Mutually exclusive with test_id.
            limit: Maximum results to return (default 10).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            if not test_id and not suite_id:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="Must provide exactly one of test_id or suite_id.",
                )

            if test_id and suite_id:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="Must provide exactly one of test_id or suite_id, not both.",
                )

            if test_id:
                check_table_access("sys_atf_test_result")
                table = "sys_atf_test_result"
                query = ServiceNowQuery().equals("test", test_id).order_by("sys_created_on", descending=True).build()
                fields = [
                    "sys_id",
                    "status",
                    "start_time",
                    "end_time",
                    "run_time",
                    "output",
                    "first_failing_step",
                ]
                result_type = "test_results"
            else:
                check_table_access("sys_atf_test_suite_result")
                table = "sys_atf_test_suite_result"
                query = (
                    ServiceNowQuery().equals("test_suite", suite_id).order_by("sys_created_on", descending=True).build()
                )
                fields = [
                    "sys_id",
                    "status",
                    "start_time",
                    "end_time",
                    "success_count",
                    "failure_count",
                    "error_count",
                    "skipped_count",
                ]
                result_type = "suite_results"

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table,
                    query,
                    fields=fields,
                    limit=limit,
                )

            records = [mask_sensitive_fields(rec) for rec in result["records"]]

            return format_response(
                data={
                    "result_type": result_type,
                    "result_count": len(records),
                    "results": records,
                },
                correlation_id=correlation_id,
            )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_run_test(
        test_id: str,
        poll: bool = True,
        poll_interval: int = 5,
        max_poll_duration: int = 300,
    ) -> str:
        """Run an ATF test and optionally poll for completion.

        WRITE-GATED: Test execution creates result records and is blocked in production.

        Args:
            test_id: The sys_id of the test to run.
            poll: If True, wait for test completion; if False, return immediately with execution ID.
            poll_interval: Seconds between progress checks (clamped 2-30, default 5).
            max_poll_duration: Maximum seconds to poll (clamped 10-300, default 300).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            gate_error = _atf_execution_gate(settings, correlation_id)
            if gate_error is not None:
                return gate_error

            clamped_interval = max(2, min(poll_interval, 30))
            clamped_max_duration = max(10, min(max_poll_duration, ATF_MAX_POLL_DURATION))

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.atf_run(test_id, is_suite=False)
                snboq_id = result.get("snboqId") or result.get("snboq_id") or result.get("executionId", "")

                if not snboq_id:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="ATF execution started but no execution ID returned.",
                    )

                if not poll:
                    return format_response(
                        data={
                            "execution_id": snboq_id,
                            "status": "started",
                            "test_id": test_id,
                            "polling": False,
                        },
                        correlation_id=correlation_id,
                    )

                start_time = time.monotonic()
                terminal_states = {"completed", "failure", "error", "cancelled"}
                state = "unknown"
                progress = 0

                while (time.monotonic() - start_time) < clamped_max_duration:
                    progress_result = await client.atf_progress(snboq_id)
                    state = progress_result.get("state", "").lower()
                    progress = progress_result.get("progress", 0)

                    if state in terminal_states:
                        return format_response(
                            data={
                                "execution_id": snboq_id,
                                "status": state,
                                "progress": progress,
                                "test_id": test_id,
                            },
                            correlation_id=correlation_id,
                        )

                    await asyncio.sleep(clamped_interval)

                return format_response(
                    data={
                        "execution_id": snboq_id,
                        "status": "polling_timeout",
                        "progress": progress,
                        "test_id": test_id,
                        "last_known_state": state,
                    },
                    correlation_id=correlation_id,
                    warnings=[
                        f"Polling timeout after {clamped_max_duration}s. Use atf_progress with execution_id to check status."
                    ],
                )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_run_suite(
        suite_id: str,
        poll: bool = True,
        poll_interval: int = 5,
        max_poll_duration: int = 300,
    ) -> str:
        """Run an ATF test suite and optionally poll for completion.

        WRITE-GATED: Suite execution creates result records and is blocked in production.

        Args:
            suite_id: The sys_id of the test suite to run.
            poll: If True, wait for suite completion; if False, return immediately with execution ID.
            poll_interval: Seconds between progress checks (clamped 2-30, default 5).
            max_poll_duration: Maximum seconds to poll (clamped 10-300, default 300).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            gate_error = _atf_execution_gate(settings, correlation_id)
            if gate_error is not None:
                return gate_error

            clamped_interval = max(2, min(poll_interval, 30))
            clamped_max_duration = max(10, min(max_poll_duration, ATF_MAX_POLL_DURATION))

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.atf_run(suite_id, is_suite=True)
                snboq_id = result.get("snboqId") or result.get("snboq_id") or result.get("executionId", "")

                if not snboq_id:
                    return format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="ATF execution started but no execution ID returned.",
                    )

                if not poll:
                    return format_response(
                        data={
                            "execution_id": snboq_id,
                            "status": "started",
                            "suite_id": suite_id,
                            "polling": False,
                        },
                        correlation_id=correlation_id,
                    )

                start_time = time.monotonic()
                terminal_states = {"completed", "failure", "error", "cancelled"}
                state = "unknown"
                progress = 0

                while (time.monotonic() - start_time) < clamped_max_duration:
                    progress_result = await client.atf_progress(snboq_id)
                    state = progress_result.get("state", "").lower()
                    progress = progress_result.get("progress", 0)

                    if state in terminal_states:
                        return format_response(
                            data={
                                "execution_id": snboq_id,
                                "status": state,
                                "progress": progress,
                                "suite_id": suite_id,
                            },
                            correlation_id=correlation_id,
                        )

                    await asyncio.sleep(clamped_interval)

                return format_response(
                    data={
                        "execution_id": snboq_id,
                        "status": "polling_timeout",
                        "progress": progress,
                        "suite_id": suite_id,
                        "last_known_state": state,
                    },
                    correlation_id=correlation_id,
                    warnings=[
                        f"Polling timeout after {clamped_max_duration}s. Use atf_progress with execution_id to check status."
                    ],
                )

        return await safe_tool_call(_run, correlation_id)

    @mcp.tool()
    async def atf_test_health(
        test_id: str = "",
        suite_id: str = "",
        days: int = 30,
        limit: int = 50,
    ) -> str:
        """Analyze test or suite health from historical execution results.

        READ-ONLY: Computes pass rate, flaky detection, and trends from historical results.

        Args:
            test_id: The sys_id of a test (sys_atf_test). Mutually exclusive with suite_id.
            suite_id: The sys_id of a test suite (sys_atf_test_suite). Mutually exclusive with test_id.
            days: How many days of history to analyze (default 30).
            limit: Maximum results to fetch (default 50).
        """
        correlation_id = generate_correlation_id()

        async def _run() -> str:
            if not test_id and not suite_id:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="Must provide exactly one of test_id or suite_id.",
                )

            if test_id and suite_id:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="Must provide exactly one of test_id or suite_id, not both.",
                )

            if test_id:
                check_table_access("sys_atf_test_result")
                table = "sys_atf_test_result"
                q = ServiceNowQuery().equals("test", test_id)
            else:
                check_table_access("sys_atf_test_suite_result")
                table = "sys_atf_test_suite_result"
                q = ServiceNowQuery().equals("test_suite", suite_id)

            full_query = q.days_ago("sys_created_on", days).order_by("sys_created_on", descending=False).build()

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table,
                    full_query,
                    fields=["sys_id", "status", "sys_created_on"],
                    limit=limit,
                )

            records = result.get("records", [])
            total_runs = len(records)

            if total_runs == 0:
                return format_response(
                    data={
                        "total_runs": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                        "pass_rate": 0.0,
                        "flaky": False,
                        "recent_trend": "no_data",
                        "last_run": None,
                    },
                    correlation_id=correlation_id,
                    warnings=["No execution results found in the specified time window."],
                )

            pass_count = sum(1 for r in records if r.get("status", "").lower() in {"success", "passed"})
            fail_count = total_runs - pass_count
            pass_rate = pass_count / total_runs if total_runs > 0 else 0.0

            transitions = 0
            for i in range(1, len(records)):
                prev_status = records[i - 1].get("status", "").lower()
                curr_status = records[i].get("status", "").lower()
                prev_pass = prev_status in {"success", "passed"}
                curr_pass = curr_status in {"success", "passed"}
                if prev_pass != curr_pass:
                    transitions += 1

            flaky = transitions >= 3 or (transitions >= 2 and total_runs < 10)

            if total_runs >= 4:
                mid = total_runs // 2
                first_half = records[:mid]
                second_half = records[mid:]

                first_pass_rate = sum(
                    1 for r in first_half if r.get("status", "").lower() in {"success", "passed"}
                ) / len(first_half)
                second_pass_rate = sum(
                    1 for r in second_half if r.get("status", "").lower() in {"success", "passed"}
                ) / len(second_half)

                diff = second_pass_rate - first_pass_rate
                if diff > 0.05:
                    trend = "improving"
                elif diff < -0.05:
                    trend = "degrading"
                else:
                    trend = "stable"
            else:
                trend = "insufficient_data"

            last_run = records[-1] if records else None

            return format_response(
                data={
                    "total_runs": total_runs,
                    "pass_count": pass_count,
                    "fail_count": fail_count,
                    "pass_rate": round(pass_rate, 3),
                    "flaky": flaky,
                    "transition_count": transitions,
                    "recent_trend": trend,
                    "last_run": mask_sensitive_fields(last_run) if last_run else None,
                },
                correlation_id=correlation_id,
            )

        return await safe_tool_call(_run, correlation_id)
