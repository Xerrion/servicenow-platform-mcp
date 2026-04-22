"""ATF (Automated Test Framework) tools for introspection, execution, and intelligence."""

import asyncio
import logging
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.mcp_state import get_query_store
from servicenow_mcp.policy import (
    check_table_access,
    mask_sensitive_fields,
    write_blocked_reason,
)
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_query_token,
)


logger = logging.getLogger(__name__)

# ATF polling constants
ATF_POLL_INTERVAL = 5
ATF_MAX_POLL_DURATION = 300

_PASS_STATUSES: frozenset[str] = frozenset({"success", "passed"})


def _is_pass(record: dict[str, Any]) -> bool:
    """Return True if the record status indicates a passing result."""
    return record.get("status", "").lower() in _PASS_STATUSES


def _validate_exclusive_ids(test_id: str, suite_id: str, correlation_id: str) -> str | None:
    """Return error if not exactly one of test_id/suite_id is provided, else None."""
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
    return None


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


async def _atf_run_and_poll(
    client: ServiceNowClient,
    run_id: str,
    is_suite: bool,
    poll: bool,
    poll_interval: int,
    max_poll_duration: int,
    correlation_id: str,
) -> str:
    """Run an ATF test or suite and optionally poll for completion.

    Args:
        client: Active ServiceNow client.
        run_id: The sys_id of the test or suite to run.
        is_suite: True for suite execution, False for test.
        poll: If True, poll until completion.
        poll_interval: Seconds between polls (pre-clamped).
        max_poll_duration: Max seconds to poll (pre-clamped).
        correlation_id: Request correlation ID.
    """
    id_key = "suite_id" if is_suite else "test_id"

    result = await client.atf_run(run_id, is_suite=is_suite)
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
                id_key: run_id,
                "polling": False,
            },
            correlation_id=correlation_id,
        )

    start_time = time.monotonic()
    terminal_states = {"completed", "failure", "error", "cancelled"}
    state = "unknown"
    progress = 0

    while (time.monotonic() - start_time) < max_poll_duration:
        progress_result = await client.atf_progress(snboq_id)
        state = progress_result.get("state", "").lower()
        progress = progress_result.get("progress", 0)

        if state in terminal_states:
            return format_response(
                data={
                    "execution_id": snboq_id,
                    "status": state,
                    "progress": progress,
                    id_key: run_id,
                },
                correlation_id=correlation_id,
            )

        await asyncio.sleep(poll_interval)

    return format_response(
        data={
            "execution_id": snboq_id,
            "status": "polling_timeout",
            "progress": progress,
            id_key: run_id,
            "last_known_state": state,
        },
        correlation_id=correlation_id,
        warnings=[f"Polling timeout after {max_poll_duration}s. Use atf_progress with execution_id to check status."],
    )


def _compute_flakiness(records: list[dict[str, Any]]) -> tuple[int, bool]:
    """Compute transition count and flaky flag from ordered result records.

    Returns:
        A tuple of (transition_count, is_flaky).
    """
    transitions = 0
    for i in range(1, len(records)):
        if _is_pass(records[i - 1]) != _is_pass(records[i]):
            transitions += 1

    flaky = transitions >= 3 or (transitions >= 2 and len(records) < 10)
    return transitions, flaky


def _compute_trend(records: list[dict[str, Any]]) -> str:
    """Compute pass-rate trend from ordered result records.

    Returns one of: 'improving', 'degrading', 'stable', 'insufficient_data'.
    """
    total = len(records)
    if total < 4:
        return "insufficient_data"

    mid = total // 2
    first_pass_rate = sum(1 for r in records[:mid] if _is_pass(r)) / mid
    second_pass_rate = sum(1 for r in records[mid:] if _is_pass(r)) / (total - mid)

    diff = second_pass_rate - first_pass_rate
    if diff > 0.05:
        return "improving"
    if diff < -0.05:
        return "degrading"
    return "stable"


def _build_result_query_params(test_id: str, suite_id: str) -> tuple[str, str, list[str], str]:
    """Build query params for atf_get_results based on test_id or suite_id.

    Returns:
        (table, query_string, fields, result_type)
    """
    if test_id:
        check_table_access("sys_atf_test_result")
        return (
            "sys_atf_test_result",
            ServiceNowQuery().equals("test", test_id).order_by("sys_created_on", descending=True).build(),
            ["sys_id", "status", "start_time", "end_time", "run_time", "output", "first_failing_step"],
            "test_results",
        )
    check_table_access("sys_atf_test_suite_result")
    return (
        "sys_atf_test_suite_result",
        ServiceNowQuery().equals("test_suite", suite_id).order_by("sys_created_on", descending=True).build(),
        [
            "sys_id",
            "status",
            "start_time",
            "end_time",
            "success_count",
            "failure_count",
            "error_count",
            "skipped_count",
        ],
        "suite_results",
    )


def _build_health_query_params(test_id: str, suite_id: str, days: int) -> tuple[str, str]:
    """Build query params for atf_test_health based on test_id or suite_id.

    Returns:
        (table, query_string)
    """
    if test_id:
        check_table_access("sys_atf_test_result")
        table = "sys_atf_test_result"
        q = ServiceNowQuery().equals("test", test_id)
    else:
        check_table_access("sys_atf_test_suite_result")
        table = "sys_atf_test_suite_result"
        q = ServiceNowQuery().equals("test_suite", suite_id)

    query_str = q.days_ago("sys_created_on", days).order_by("sys_created_on", descending=False).build()
    return table, query_str


TOOL_NAMES: list[str] = [
    "atf_list_tests",
    "atf_get_test",
    "atf_list_suites",
    "atf_get_results",
    "atf_run_test",
    "atf_run_suite",
    "atf_test_health",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register ATF (Automated Test Framework) tools on the MCP server."""
    query_store: QueryTokenStore = get_query_store(mcp)

    @mcp.tool()
    @tool_handler
    async def atf_list_tests(
        query_token: str = "",
        limit: int = 20,
        fields: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Query ATF tests with filtering and pagination.

        Args:
            query_token: Token from the build_query tool for filtering.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            limit: Maximum records to return (default 20).
            fields: Comma-separated field list (empty for default fields).
        """
        check_table_access("sys_atf_test")

        default_fields = "sys_id,name,description,active,sys_updated_on,test_origin"
        field_list = fields or default_fields

        query_str = resolve_query_token(query_token, query_store, "sys_atf_test", correlation_id)

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

    @mcp.tool()
    @tool_handler
    async def atf_get_test(test_id: str, *, correlation_id: str) -> str:
        """Get full test details including test steps.

        Fetches the test record from sys_atf_test and all associated steps
        from sys_atf_step ordered by step order.

        Args:
            test_id: The sys_id of the test record.
        """
        check_table_access("sys_atf_test")
        check_table_access("sys_atf_step")

        async with ServiceNowClient(settings, auth_provider) as client:
            test, steps_result = await asyncio.gather(
                client.get_record("sys_atf_test", test_id),
                client.query_records(
                    "sys_atf_step",
                    ServiceNowQuery().equals("test", test_id).build(),
                    fields=[
                        "sys_id",
                        "display_name",
                        "step_config",
                        "order",
                        "inputs",
                    ],
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

    @mcp.tool()
    @tool_handler
    async def atf_list_suites(
        query_token: str = "",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Query ATF test suites with member counts.

        Fetches test suites and enriches each with the count of tests in the suite.

        Args:
            query_token: Token from the build_query tool for filtering.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no filter.
            limit: Maximum records to return (default 20).
        """
        check_table_access("sys_atf_test_suite")
        check_table_access("sys_atf_test_suite_test")

        query = resolve_query_token(query_token, query_store, "sys_atf_test_suite", correlation_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "sys_atf_test_suite",
                query,
                fields=[
                    "sys_id",
                    "name",
                    "description",
                    "active",
                    "sys_updated_on",
                ],
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

    @mcp.tool()
    @tool_handler
    async def atf_get_results(
        test_id: str = "",
        suite_id: str = "",
        limit: int = 10,
        *,
        correlation_id: str,
    ) -> str:
        """Get test or suite execution results.

        Fetch recent execution results for either a test or a suite.
        Exactly one of test_id or suite_id must be provided.

        Args:
            test_id: The sys_id of a test (sys_atf_test). Mutually exclusive with suite_id.
            suite_id: The sys_id of a test suite (sys_atf_test_suite). Mutually exclusive with test_id.
            limit: Maximum results to return (default 10).
        """
        err = _validate_exclusive_ids(test_id, suite_id, correlation_id)
        if err:
            return err

        table, query, fields, result_type = _build_result_query_params(test_id, suite_id)

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

    @mcp.tool()
    @tool_handler
    async def atf_run_test(
        test_id: str,
        poll: bool = True,
        poll_interval: int = 5,
        max_poll_duration: int = 300,
        *,
        correlation_id: str,
    ) -> str:
        """Run an ATF test and optionally poll for completion.

        WRITE-GATED: Test execution creates result records and is blocked in production.

        Args:
            test_id: The sys_id of the test to run.
            poll: If True, wait for test completion; if False, return immediately with execution ID.
            poll_interval: Seconds between progress checks (clamped 2-30, default 5).
            max_poll_duration: Maximum seconds to poll (clamped 10-300, default 300).
        """
        gate_error = _atf_execution_gate(settings, correlation_id)
        if gate_error is not None:
            return gate_error

        clamped_interval = max(2, min(poll_interval, 30))
        clamped_max_duration = max(10, min(max_poll_duration, ATF_MAX_POLL_DURATION))

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _atf_run_and_poll(
                client,
                test_id,
                is_suite=False,
                poll=poll,
                poll_interval=clamped_interval,
                max_poll_duration=clamped_max_duration,
                correlation_id=correlation_id,
            )

    @mcp.tool()
    @tool_handler
    async def atf_run_suite(
        suite_id: str,
        poll: bool = True,
        poll_interval: int = 5,
        max_poll_duration: int = 300,
        *,
        correlation_id: str,
    ) -> str:
        """Run an ATF test suite and optionally poll for completion.

        WRITE-GATED: Suite execution creates result records and is blocked in production.

        Args:
            suite_id: The sys_id of the test suite to run.
            poll: If True, wait for suite completion; if False, return immediately with execution ID.
            poll_interval: Seconds between progress checks (clamped 2-30, default 5).
            max_poll_duration: Maximum seconds to poll (clamped 10-300, default 300).
        """
        gate_error = _atf_execution_gate(settings, correlation_id)
        if gate_error is not None:
            return gate_error

        clamped_interval = max(2, min(poll_interval, 30))
        clamped_max_duration = max(10, min(max_poll_duration, ATF_MAX_POLL_DURATION))

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _atf_run_and_poll(
                client,
                suite_id,
                is_suite=True,
                poll=poll,
                poll_interval=clamped_interval,
                max_poll_duration=clamped_max_duration,
                correlation_id=correlation_id,
            )

    @mcp.tool()
    @tool_handler
    async def atf_test_health(
        test_id: str = "",
        suite_id: str = "",
        days: int = 30,
        limit: int = 50,
        *,
        correlation_id: str,
    ) -> str:
        """Analyze test or suite health from historical execution results.

        READ-ONLY: Computes pass rate, flaky detection, and trends from historical results.

        Args:
            test_id: The sys_id of a test (sys_atf_test). Mutually exclusive with suite_id.
            suite_id: The sys_id of a test suite (sys_atf_test_suite). Mutually exclusive with test_id.
            days: How many days of history to analyze (default 30).
            limit: Maximum results to fetch (default 50).
        """
        err = _validate_exclusive_ids(test_id, suite_id, correlation_id)
        if err:
            return err

        table, full_query = _build_health_query_params(test_id, suite_id, days)

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

        pass_count = sum(1 for r in records if _is_pass(r))
        fail_count = total_runs - pass_count
        pass_rate = pass_count / total_runs if total_runs > 0 else 0.0

        transitions, flaky = _compute_flakiness(records)
        trend = _compute_trend(records)

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
