# ATF Tools - ServiceNow Automated Test Framework Integration

## TL;DR

> **Quick Summary**: Add a new "testing" tool group to servicenow-devtools-mcp that provides ATF introspection, execution, and intelligence capabilities. Follows all existing codebase patterns exactly.
> 
> **Deliverables**:
> - New tool module: `src/servicenow_mcp/tools/testing.py` with 7 tools
> - New client methods in `client.py` for Cloud Runner REST API
> - Registration wiring in `server.py` and `packages.py`
> - Comprehensive test suite: `tests/test_testing.py` (25+ tests)
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves + final verification
> **Critical Path**: Task 1 (client methods) -> Task 3 (tools) -> Task 5 (wiring) -> Task 6 (tests) -> Final QA

---

## Context

### Original Request
Research and implement ServiceNow Automated Test Framework (ATF) tools for the servicenow-devtools-mcp MCP server, enabling developers to introspect tests, execute them via Cloud Runner, and analyze test health.

### Interview Summary
**Key Discussions**:
- Exhaustive research of all ATF tables on live instance (dev272070) - 10 tables fully described
- Cloud Runner Test Runner API confirmed: 3 endpoints under `/api/now/sn_atf_tg/` (run, progress, cancel)
- BOQ table (`sn_atf_tg_sn_boq`) exists but schema not accessible - OUT OF SCOPE for v1
- Only ATF Agent web service found - no other ATF REST APIs enumerable on instance
- Proposed 7 tools in 3 categories: Introspection (4), Execution (2), Intelligence (1)

**Research Findings**:
- 9+ ATF tables fully described with field schemas on live instance
- `sys_atf_test`: 12 fields (name, description, active, test_origin)
- `sys_atf_step`: 17 fields (test ref, step_config ref, order, inputs)
- `sys_atf_step_config`: 22 fields (step type templates)
- `sys_atf_test_suite`: 8 fields (name, parent self-ref)
- `sys_atf_test_suite_test`: 6 fields (M2M join: suite <-> test)
- `sys_atf_test_result`: 44 fields (status, timing, first_failing_step)
- `sys_atf_test_result_step`: 5 fields (per-step results)
- `sys_atf_test_result_item`: 26 fields (per-step timing granularity)
- `sys_atf_test_suite_result`: 32 fields (rolled-up counts)
- `sys_atf_schedule`: 5 fields (scheduled runs)
- Cloud Runner uses scripted REST (3 endpoints confirmed via user-provided docs)
- `sn_atf_tg` plugin required for execution features
- 32 active tests exist on dev instance
- Existing codebase has 37 tools, 10 tool groups, 482 tests

### Metis Review
**Identified Gaps** (addressed):
- Cloud Runner API response format uncertainty: Build client methods defensively - handle both `{"result": {...}}` and raw JSON responses
- Plugin not installed handling: Execution tools catch 404 and return clear "sn_atf_tg plugin not installed" error
- Polling parameters: Explicit defaults (max 300s, 5s interval, configurable)
- Write gating for execution: YES - `atf_run_test` and `atf_run_suite` use `write_blocked_reason` gate
- Tool naming: `atf_` prefix consistently

---

## Work Objectives

### Core Objective
Add ATF introspection, execution, and intelligence tools to servicenow-devtools-mcp following all existing patterns.

### Concrete Deliverables
- `src/servicenow_mcp/tools/testing.py` - 7 ATF tools
- Modified `src/servicenow_mcp/client.py` - ATF REST API client methods
- Modified `src/servicenow_mcp/server.py` - "testing" in `_TOOL_GROUP_MODULES`
- Modified `src/servicenow_mcp/packages.py` - "testing" in `PACKAGE_REGISTRY["full"]`
- `tests/test_testing.py` - 25+ unit tests with respx mocking

### Definition of Done
- [ ] All 7 ATF tools register and respond correctly
- [ ] `uv run pytest --tb=short` passes (all existing + new tests)
- [ ] `uv run ruff check .` returns 0 errors
- [ ] `uv run ruff format --check .` returns 0 issues
- [ ] `uv run mypy src/` returns 0 errors
- [ ] Tool registration verification script passes

### Must Have
- `atf_list_tests` - Query/filter ATF tests with standard pagination
- `atf_get_test` - Full test details including steps and step configs
- `atf_list_suites` - Query ATF test suites with member counts
- `atf_get_results` - Test/suite execution results with step detail
- `atf_run_test` - Trigger test execution via Cloud Runner (write-gated)
- `atf_run_suite` - Trigger suite execution via Cloud Runner (write-gated)
- `atf_test_health` - Analyze test failure patterns and flaky tests
- All tools use `safe_tool_call`, `format_response`, `correlation_id`
- Write-gated execution tools using `write_blocked_reason`
- Polling with explicit max timeout (300s default) and configurable interval

### Must NOT Have (Guardrails)
- MUST NOT modify any existing tool modules (only `server.py`, `packages.py`, `client.py`)
- MUST NOT introduce new dependencies
- MUST NOT create investigation modules - keep intelligence as regular tools
- MUST NOT add BOQ table querying (save for v2)
- MUST NOT support legacy ATF Agent API (Cloud Runner only)
- MUST NOT create test step manipulation/authoring tools (read-only introspection)
- MUST NOT manage ATF schedules (read-only at most)
- MUST NOT create polling loops without max timeout (hard cap 300s)
- MUST NOT use `asyncio.sleep` without configurable interval parameter
- MUST NOT create more than 8 tools
- MUST NOT update README or docs (separate task)
- MUST NOT write integration tests (unit tests only)
- MUST NOT bypass `_raise_for_status` in client methods

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (Tests-after)
- **Framework**: pytest + respx + pytest-asyncio (existing infrastructure)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **API/Backend**: Use Bash (uv run pytest, uv run ruff, uv run mypy) - Run commands, assert pass
- **Library/Module**: Use Bash (python -c) - Import, verify tool registration, check types

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - foundation, MAX PARALLEL):
+-- Task 1: ATF client methods in client.py [quick]
+-- Task 2: Type definitions and helpers for testing.py [quick]

Wave 2 (After Wave 1 - tools + wiring):
+-- Task 3: Testing tool module - introspection tools [unspecified-high]
+-- Task 4: Testing tool module - execution + intelligence tools [deep]
+-- Task 5: Registration wiring (server.py, packages.py) [quick]

Wave 3 (After Wave 2 - tests + verification):
+-- Task 6: Unit tests for introspection tools [unspecified-high]
+-- Task 7: Unit tests for execution + intelligence tools [deep]
+-- Task 8: Full lint/type/test verification pass [quick]

Wave FINAL (After ALL tasks - independent review, 4 parallel):
+-- Task F1: Plan compliance audit (oracle)
+-- Task F2: Code quality review (unspecified-high)
+-- Task F3: Real manual QA (unspecified-high)
+-- Task F4: Scope fidelity check (deep)

Critical Path: Task 1 -> Task 3 -> Task 5 -> Task 6 -> Task 8 -> F1-F4
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 2 (Waves 1 & 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | - | 3, 4 | 1 |
| 2 | - | 3, 4 | 1 |
| 3 | 1, 2 | 5, 6 | 2 |
| 4 | 1, 2 | 5, 7 | 2 |
| 5 | 3, 4 | 6, 7, 8 | 2 |
| 6 | 3, 5 | 8 | 3 |
| 7 | 4, 5 | 8 | 3 |
| 8 | 6, 7 | F1-F4 | 3 |

### Agent Dispatch Summary

- **Wave 1**: 2 tasks - T1 `quick`, T2 `quick`
- **Wave 2**: 3 tasks - T3 `unspecified-high`, T4 `deep`, T5 `quick`
- **Wave 3**: 3 tasks - T6 `unspecified-high`, T7 `deep`, T8 `quick`
- **FINAL**: 4 tasks - F1 `oracle`, F2 `unspecified-high`, F3 `unspecified-high`, F4 `deep`

---

## TODOs

- [ ] 1. Add ATF Cloud Runner REST API client methods to client.py

  **What to do**:
  - Add private URL builder method `_atf_cloud_runner_url(endpoint: str)` returning `f"{self._instance_url}/api/now/sn_atf_tg/{endpoint}"`
  - Add `async def atf_run(self, test_or_suite_id: str, is_suite: bool = False) -> dict` - POST to `/test_runner` with `{"testId": id}` or `{"suiteId": id}`. Returns parsed response with `snboqId`
  - Add `async def atf_progress(self, snboq_id: str) -> dict` - GET to `/test_runner_progress?snboqId={id}`. Returns progress/state dict
  - Add `async def atf_cancel(self, snboq_id: str) -> dict` - POST to `/cancel_test_runner` with `{"snboqId": id}`. Returns result dict
  - All methods: use `self._ensure_client()`, `await self._headers()`, `self._raise_for_status(response)`, then extract result
  - Handle response defensively: try `self._extract_result(data)` first, fallback to `data` if "result" key missing
  - Catch `httpx.HTTPStatusError` with 404 and raise `NotFoundError` with message "ATF Cloud Runner plugin (sn_atf_tg) may not be installed"

  **Must NOT do**:
  - Do NOT bypass `_raise_for_status`
  - Do NOT add methods for table API queries (tools use ServiceNowClient's existing `query_table`/`get_record` methods)
  - Do NOT add polling logic in client (polling belongs in the tool layer)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small, focused change - 3 methods + 1 URL builder in existing file, following exact patterns
  - **Skills**: []
    - No special skills needed - pure Python async code following existing patterns

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Tasks 3, 4
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References** (existing code to follow):
  - `src/servicenow_mcp/client.py` - `_code_search_url()` method is the exact pattern for the URL builder. Returns `f"{self._instance_url}/api/sn_codesearch/code_search/search"`. Mirror this for ATF endpoint.
  - `src/servicenow_mcp/client.py` - `code_search()` method is the exact pattern for making scoped REST API calls. Uses `_ensure_client()`, `await self._headers()`, `http.get(url, ...)`, `self._raise_for_status(response)`, `self._extract_result(response.json())`.
  - `src/servicenow_mcp/client.py` - `create_record()` method shows POST pattern with `json=data` parameter.

  **API/Type References**:
  - `src/servicenow_mcp/errors.py` - `NotFoundError` for 404 handling, `ServerError` for 5xx
  - Cloud Runner API: POST `/api/now/sn_atf_tg/test_runner` body `{"testId": "<sys_id>"}` returns `{"result": {"snboqId": "<id>"}}`
  - Cloud Runner API: GET `/api/now/sn_atf_tg/test_runner_progress?snboqId=<id>` returns `{"result": {"progress": 100, "state": "completed"}}`
  - Cloud Runner API: POST `/api/now/sn_atf_tg/cancel_test_runner` body `{"snboqId": "<id>"}` returns `{"result": {"message": "success"}}`

  **Acceptance Criteria**:
  - [ ] `_atf_cloud_runner_url` method exists and returns correct URL format
  - [ ] `atf_run` method accepts test_or_suite_id and is_suite params, POSTs correctly
  - [ ] `atf_progress` method accepts snboq_id, GETs with query param
  - [ ] `atf_cancel` method accepts snboq_id, POSTs correctly
  - [ ] All methods use `_raise_for_status` and handle response extraction

  **QA Scenarios**:

  ```
  Scenario: Client methods exist and have correct signatures
    Tool: Bash
    Preconditions: uv sync completed
    Steps:
      1. Run: uv run python -c "from servicenow_mcp.client import ServiceNowClient; import inspect; sig = inspect.signature(ServiceNowClient.atf_run); print(sig); assert 'test_or_suite_id' in sig.parameters; assert 'is_suite' in sig.parameters"
      2. Run: uv run python -c "from servicenow_mcp.client import ServiceNowClient; import inspect; sig = inspect.signature(ServiceNowClient.atf_progress); print(sig); assert 'snboq_id' in sig.parameters"
      3. Run: uv run python -c "from servicenow_mcp.client import ServiceNowClient; import inspect; sig = inspect.signature(ServiceNowClient.atf_cancel); print(sig); assert 'snboq_id' in sig.parameters"
    Expected Result: All 3 methods exist with correct parameter names, exit code 0
    Failure Indicators: ImportError, AttributeError, AssertionError
    Evidence: .sisyphus/evidence/task-1-client-signatures.txt

  Scenario: Type checking passes on client.py
    Tool: Bash
    Preconditions: uv sync --group dev completed
    Steps:
      1. Run: uv run mypy src/servicenow_mcp/client.py --no-error-summary 2>&1 | tail -5
    Expected Result: "Success: no issues found" or only pre-existing suppressions
    Failure Indicators: New type errors mentioning atf_run, atf_progress, atf_cancel
    Evidence: .sisyphus/evidence/task-1-mypy-client.txt
  ```

  **Commit**: YES
  - Message: `feat(client): add ATF Cloud Runner REST API client methods`
  - Files: `src/servicenow_mcp/client.py`
  - Pre-commit: `uv run mypy src/servicenow_mcp/client.py`

- [ ] 2. Create testing.py module skeleton with shared helpers

  **What to do**:
  - Create `src/servicenow_mcp/tools/testing.py` with module docstring and imports following `debug.py` pattern exactly
  - Import: `json`, `logging`, `asyncio`, `FastMCP`, `BasicAuthProvider`, `ServiceNowClient`, `Settings`, policy functions (`check_table_access`, `mask_sensitive_fields`, `INTERNAL_QUERY_LIMIT`), utils (`ServiceNowQuery`, `format_response`, `generate_correlation_id`, `safe_tool_call`, `validate_identifier`), `write_blocked_reason` from policy
  - Add `logger = logging.getLogger(__name__)`
  - Create `_write_gate(table: str, settings: Settings, correlation_id: str) -> str | None` helper following `developer.py` pattern (copy exactly)
  - Create `_atf_execution_gate(settings: Settings, correlation_id: str) -> str | None` helper that calls `write_blocked_reason("sys_atf_test_result", settings)` - this gates execution tools since running tests creates result records
  - Create empty `register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:` with pass placeholder
  - Define module-level constants: `ATF_POLL_INTERVAL = 5` (seconds), `ATF_MAX_POLL_DURATION = 300` (seconds)

  **Must NOT do**:
  - Do NOT implement any tools yet (just skeleton + helpers)
  - Do NOT add tools to server.py or packages.py yet (Task 5)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Boilerplate setup following exact existing patterns, <50 lines
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Tasks 3, 4
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/debug.py:1-19` - Exact import pattern to follow. Copy the import block and adapt.
  - `src/servicenow_mcp/tools/developer.py:15-30` - `_write_gate` helper pattern. Copy and rename.
  - `src/servicenow_mcp/utils.py` - `safe_tool_call`, `format_response`, `generate_correlation_id`, `ServiceNowQuery`, `validate_identifier` exports

  **Acceptance Criteria**:
  - [ ] File `src/servicenow_mcp/tools/testing.py` exists
  - [ ] Module imports match debug.py pattern
  - [ ] `_atf_execution_gate` helper defined
  - [ ] `register_tools` function defined (empty body OK)
  - [ ] Constants `ATF_POLL_INTERVAL` and `ATF_MAX_POLL_DURATION` defined

  **QA Scenarios**:

  ```
  Scenario: Module imports successfully
    Tool: Bash
    Preconditions: uv sync completed
    Steps:
      1. Run: uv run python -c "from servicenow_mcp.tools.testing import register_tools; print('OK')"
      2. Run: uv run python -c "from servicenow_mcp.tools.testing import ATF_POLL_INTERVAL, ATF_MAX_POLL_DURATION; assert ATF_POLL_INTERVAL == 5; assert ATF_MAX_POLL_DURATION == 300; print('Constants OK')"
    Expected Result: Both print OK, exit code 0
    Failure Indicators: ImportError, AssertionError
    Evidence: .sisyphus/evidence/task-2-module-import.txt

  Scenario: Lint passes on new module
    Tool: Bash
    Steps:
      1. Run: uv run ruff check src/servicenow_mcp/tools/testing.py
      2. Run: uv run ruff format --check src/servicenow_mcp/tools/testing.py
    Expected Result: No errors from either command
    Failure Indicators: Any ruff error output
    Evidence: .sisyphus/evidence/task-2-lint.txt
  ```

  **Commit**: NO (groups with Task 3/4)

- [x] 3. Implement introspection tools (atf_list_tests, atf_get_test, atf_list_suites, atf_get_results)

  **What to do**:
  - Implement 4 tools inside `register_tools()` in `testing.py`:

  **Tool 1: `atf_list_tests`**
  - Params: `query: str = ""`, `limit: int = 20`, `fields: str = ""`
  - Query `sys_atf_test` via `client.query_table()` with ServiceNowQuery
  - Default fields if empty: `sys_id,name,description,active,sys_updated_on,test_origin`
  - Apply `mask_sensitive_fields` to results
  - Return formatted response with record count summary

  **Tool 2: `atf_get_test`**
  - Params: `test_id: str`
  - Get test record from `sys_atf_test` via `client.get_record()`
  - Also query `sys_atf_step` where `test={test_id}` ordered by `order`
  - For each step, include `display_name`, `step_config` display value, `order`, `inputs`
  - Return combined dict: `{"test": {...}, "steps": [...], "step_count": N}`

  **Tool 3: `atf_list_suites`**
  - Params: `query: str = ""`, `limit: int = 20`
  - Query `sys_atf_test_suite` via `client.query_table()`
  - For each suite, do a count query on `sys_atf_test_suite_test` where `test_suite={suite.sys_id}` to get member count
  - Return suites with `member_count` appended

  **Tool 4: `atf_get_results`**
  - Params: `test_id: str = ""`, `suite_id: str = ""`, `limit: int = 10`
  - If `test_id`: query `sys_atf_test_result` where `test={test_id}` ordered by `sys_created_on` desc
  - If `suite_id`: query `sys_atf_test_suite_result` where `test_suite={suite_id}` ordered by `sys_created_on` desc
  - Must provide exactly one of test_id or suite_id
  - Fields for test results: `sys_id,status,start_time,end_time,run_time,output,first_failing_step`
  - Fields for suite results: `sys_id,status,start_time,end_time,success_count,failure_count,error_count,skipped_count`
  - Return results list with type indicator

  **All tools MUST**:
  - Use `@mcp.tool()` decorator
  - Use `correlation_id = generate_correlation_id()`
  - Define inner `async def _run() -> str`
  - Return `await safe_tool_call(_run, correlation_id)`
  - Use `check_table_access(table)` before any table query
  - Have comprehensive docstrings with `Args:` section (MCP uses for schema)

  **Must NOT do**:
  - Do NOT implement execution tools (Task 4)
  - Do NOT add to server.py/packages.py yet (Task 5)
  - Do NOT use GlideRecord patterns - use Table API via client methods
  - Do NOT make unbounded queries - always respect limit parameter

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 4 tools with non-trivial query logic, needs careful pattern following
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Tasks 5, 6
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References** (follow these exactly):
  - `src/servicenow_mcp/tools/debug.py` - `debug_trace` tool (lines ~30-100) shows the pattern: @mcp.tool(), async def, correlation_id, inner _run, client context manager, ServiceNowQuery, format_response, safe_tool_call
  - `src/servicenow_mcp/tools/debug.py` - `debug_field_mutation_story` tool shows single-record + related-records query pattern (get main record then query related)
  - `src/servicenow_mcp/tools/documentation.py` - `docs_artifact_summary` shows enrichment pattern (fetch base record, then enrich with related data)

  **API/Type References**:
  - `sys_atf_test` table: fields `sys_id, name, description, active, sys_updated_on, test_origin, sys_class_name`
  - `sys_atf_step` table: fields `sys_id, test (ref->sys_atf_test), step_config (ref->sys_atf_step_config), order, inputs, display_name, description`
  - `sys_atf_test_suite` table: fields `sys_id, name, description, active, parent (self-ref)`
  - `sys_atf_test_suite_test` table: fields `sys_id, test_suite (ref), test (ref), order, abort_on_failure`
  - `sys_atf_test_result` table: fields `sys_id, test (ref), status, start_time, end_time, run_time, output, first_failing_step, sys_created_on` (44 total)
  - `sys_atf_test_suite_result` table: fields `sys_id, test_suite (ref), status, start_time, end_time, success_count, failure_count, error_count, skipped_count` (32 total)

  **Acceptance Criteria**:
  - [ ] 4 tool functions exist in testing.py: `atf_list_tests`, `atf_get_test`, `atf_list_suites`, `atf_get_results`
  - [ ] All use `safe_tool_call` wrapper
  - [ ] All have `Args:` docstrings
  - [ ] All use `check_table_access` before table queries
  - [ ] `atf_get_test` returns combined test + steps data
  - [ ] `atf_list_suites` includes member counts
  - [ ] `atf_get_results` handles both test_id and suite_id params

  **QA Scenarios**:

  ```
  Scenario: All 4 introspection tools importable and registered
    Tool: Bash
    Preconditions: Tasks 1-2 complete
    Steps:
      1. Run: uv run python -c "
from unittest.mock import patch
env = {'SERVICENOW_INSTANCE_URL': 'https://test.service-now.com', 'SERVICENOW_USERNAME': 'admin', 'SERVICENOW_PASSWORD': 's3cret'}
with patch.dict('os.environ', env, clear=True):
    from mcp.server.fastmcp import FastMCP
    from servicenow_mcp.config import Settings
    from servicenow_mcp.auth import BasicAuthProvider
    from servicenow_mcp.tools.testing import register_tools
    mcp = FastMCP('test')
    s = Settings(_env_file=None)
    register_tools(mcp, s, BasicAuthProvider(s))
    tools = [t.name for t in mcp._tool_manager._tools.values()]
    for name in ['atf_list_tests', 'atf_get_test', 'atf_list_suites', 'atf_get_results']:
        assert name in tools, f'{name} not found'
    print(f'All 4 introspection tools registered: {tools}')
"
    Expected Result: All 4 tool names printed, exit code 0
    Failure Indicators: ImportError, AssertionError, missing tool name
    Evidence: .sisyphus/evidence/task-3-tools-registered.txt

  Scenario: atf_get_results rejects missing both test_id and suite_id
    Tool: Bash
    Preconditions: Tool implemented
    Steps:
      1. Verify tool docstring mentions "must provide test_id or suite_id" via: uv run python -c "from servicenow_mcp.tools.testing import register_tools; ..."
    Expected Result: Tool has clear parameter documentation
    Evidence: .sisyphus/evidence/task-3-params-validation.txt
  ```

  **Commit**: NO (groups with Task 4 into single commit)

- [x] 4. Implement execution and intelligence tools (atf_run_test, atf_run_suite, atf_test_health)

  **What to do**:
  - Add 3 more tools to `register_tools()` in `testing.py`:

  **Tool 5: `atf_run_test`**
  - Params: `test_id: str`, `poll: bool = True`, `poll_interval: int = 5`, `max_poll_duration: int = 300`
  - **WRITE-GATED**: Call `_atf_execution_gate(settings, correlation_id)` first. If blocked, return error envelope.
  - Call `client.atf_run(test_id, is_suite=False)` to start execution
  - Extract `snboq_id` from response
  - If `poll=True`: loop with `asyncio.sleep(poll_interval)` calling `client.atf_progress(snboq_id)` until state is terminal ("completed", "failure", "error", "cancelled") OR elapsed time >= `max_poll_duration`
  - Clamp: `poll_interval = max(2, min(poll_interval, 30))`, `max_poll_duration = max(10, min(max_poll_duration, ATF_MAX_POLL_DURATION))`
  - If timeout: return partial result with `"status": "polling_timeout"` and the snboq_id so user can check later
  - If `poll=False`: return immediately with snboq_id
  - Return: `{"execution_id": snboq_id, "status": state, "progress": pct, "test_id": test_id}`

  **Tool 6: `atf_run_suite`**
  - Same pattern as `atf_run_test` but with `is_suite=True`
  - Params: `suite_id: str`, `poll: bool = True`, `poll_interval: int = 5`, `max_poll_duration: int = 300`
  - Same write-gating, same polling logic
  - Return: `{"execution_id": snboq_id, "status": state, "progress": pct, "suite_id": suite_id}`

  **Tool 7: `atf_test_health`**
  - Params: `test_id: str = ""`, `suite_id: str = ""`, `days: int = 30`, `limit: int = 50`
  - READ-ONLY tool (no write gating needed)
  - If `test_id`: query `sys_atf_test_result` for recent results, compute pass/fail ratio, identify flaky patterns (alternating pass/fail), find common failure steps
  - If `suite_id`: query `sys_atf_test_suite_result` for recent results, compute rolled-up health metrics from success/failure/error/skip counts
  - If neither: return error "must provide test_id or suite_id"
  - Time filter: `sys_created_on` >= N days ago using ServiceNowQuery
  - Compute and return: `{"total_runs": N, "pass_count": N, "fail_count": N, "pass_rate": float, "flaky": bool, "recent_trend": "improving|degrading|stable", "last_run": {...}}`

  **Must NOT do**:
  - Do NOT create unbounded polling - enforce ATF_MAX_POLL_DURATION hard cap
  - Do NOT skip write gating for execution tools
  - Do NOT create investigation module - keep atf_test_health as regular tool
  - Do NOT add coverage_map tool (descoped - test_health is sufficient for v1)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Polling logic with timeout, write gating, health analysis computation - needs careful implementation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 5)
  - **Blocks**: Tasks 5, 7
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/developer.py:15-30` - Write gate pattern: `_write_gate` helper returns error envelope or None. Call at top of `_run()`, if result is not None, return it immediately.
  - `src/servicenow_mcp/tools/debug.py` - Standard tool structure for all tools
  - `src/servicenow_mcp/client.py` - `atf_run`, `atf_progress`, `atf_cancel` methods (created in Task 1)

  **API/Type References**:
  - Cloud Runner states: "Pending", "Processing", "Browsers requested", "Running", "Completed", "Failed" (from user-provided docs)
  - `sys_atf_test_result.status` field: string values for test outcomes
  - `sys_atf_test_suite_result` fields: `success_count`, `failure_count`, `error_count`, `skipped_count` for health computation

  **External References**:
  - Python `asyncio.sleep()` for polling interval
  - Python `time.monotonic()` for elapsed time tracking (don't use datetime for duration)

  **Acceptance Criteria**:
  - [ ] 3 tool functions exist: `atf_run_test`, `atf_run_suite`, `atf_test_health`
  - [ ] Execution tools are write-gated via `_atf_execution_gate`
  - [ ] Polling respects max_poll_duration (hard capped at ATF_MAX_POLL_DURATION=300s)
  - [ ] Polling interval clamped between 2-30 seconds
  - [ ] `atf_test_health` is read-only (no write gate)
  - [ ] `atf_test_health` computes pass_rate and flaky detection

  **QA Scenarios**:

  ```
  Scenario: Execution tools are write-gated
    Tool: Bash
    Preconditions: Tasks 1-4 complete
    Steps:
      1. Run: uv run python -c "
import ast, inspect
from servicenow_mcp.tools import testing
source = inspect.getsource(testing)
# Verify both execution tools reference the gate
assert '_atf_execution_gate' in source, 'Missing execution gate'
print('Write gate reference found')
"
    Expected Result: "Write gate reference found" printed, exit code 0
    Failure Indicators: AssertionError
    Evidence: .sisyphus/evidence/task-4-write-gate.txt

  Scenario: All 7 tools registered
    Tool: Bash
    Steps:
      1. Run: uv run python -c "
from unittest.mock import patch
env = {'SERVICENOW_INSTANCE_URL': 'https://test.service-now.com', 'SERVICENOW_USERNAME': 'admin', 'SERVICENOW_PASSWORD': 's3cret'}
with patch.dict('os.environ', env, clear=True):
    from mcp.server.fastmcp import FastMCP
    from servicenow_mcp.config import Settings
    from servicenow_mcp.auth import BasicAuthProvider
    from servicenow_mcp.tools.testing import register_tools
    mcp = FastMCP('test')
    s = Settings(_env_file=None)
    register_tools(mcp, s, BasicAuthProvider(s))
    tools = [t.name for t in mcp._tool_manager._tools.values()]
    expected = ['atf_list_tests', 'atf_get_test', 'atf_list_suites', 'atf_get_results', 'atf_run_test', 'atf_run_suite', 'atf_test_health']
    for name in expected:
        assert name in tools, f'{name} not found'
    print(f'All 7 tools registered: {tools}')
"
    Expected Result: All 7 tool names printed, exit code 0
    Failure Indicators: Missing tool name
    Evidence: .sisyphus/evidence/task-4-all-tools.txt

  Scenario: Polling constants have correct defaults
    Tool: Bash
    Steps:
      1. Run: uv run python -c "from servicenow_mcp.tools.testing import ATF_POLL_INTERVAL, ATF_MAX_POLL_DURATION; assert ATF_POLL_INTERVAL == 5; assert ATF_MAX_POLL_DURATION == 300; print('OK')"
    Expected Result: "OK" printed
    Evidence: .sisyphus/evidence/task-4-poll-constants.txt
  ```

  **Commit**: YES
  - Message: `feat(tools): add ATF testing tool group with 7 tools`
  - Files: `src/servicenow_mcp/tools/testing.py`
  - Pre-commit: `uv run ruff check src/servicenow_mcp/tools/testing.py && uv run mypy src/servicenow_mcp/tools/testing.py`

- [x] 5. Wire testing tool group into server and package registry

  **What to do**:
  - In `src/servicenow_mcp/server.py`: Add `"testing": "servicenow_mcp.tools.testing"` to the `_TOOL_GROUP_MODULES` dict (alphabetical order, after "records")
  - In `src/servicenow_mcp/packages.py`: Add `"testing"` to the `PACKAGE_REGISTRY["full"]` list (alphabetical order, after "records")
  - Verify import works by running the registration verification script

  **Must NOT do**:
  - Do NOT add "testing" to `introspection_only` package (execution tools are write-equivalent)
  - Do NOT modify `_VALID_PACKAGES` in config.py (it's about package names, not group names)
  - Do NOT touch any other files

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 2-line change in 2 files, mechanical insertion
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (can run after Tasks 3, 4 complete)
  - **Parallel Group**: Wave 2 (with Tasks 3, 4)
  - **Blocks**: Tasks 6, 7, 8
  - **Blocked By**: Tasks 3, 4

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/server.py:16-27` - `_TOOL_GROUP_MODULES` dict. Currently has 10 entries. Add `"testing": "servicenow_mcp.tools.testing"` maintaining alphabetical order.
  - `src/servicenow_mcp/packages.py:5-25` - `PACKAGE_REGISTRY`. The `"full"` key has a list of all group names. Add `"testing"` maintaining alphabetical order.

  **Acceptance Criteria**:
  - [ ] `"testing"` key exists in `_TOOL_GROUP_MODULES` in server.py
  - [ ] `"testing"` appears in `PACKAGE_REGISTRY["full"]` in packages.py
  - [ ] Tool registration verification script passes (all 7 ATF tools + all 37 existing tools)

  **QA Scenarios**:

  ```
  Scenario: Full MCP server starts with all tools including ATF
    Tool: Bash
    Preconditions: testing.py complete and working
    Steps:
      1. Run the full tool registration verification script from Success Criteria section
    Expected Result: "ALL CHECKS PASSED" printed, total tools >= 44 (37 existing + 7 new), all 7 atf_ tools present
    Failure Indicators: ImportError, AssertionError, missing tool names
    Evidence: .sisyphus/evidence/task-5-full-registration.txt

  Scenario: Existing tools still register correctly
    Tool: Bash
    Steps:
      1. Run: uv run python -c "
from unittest.mock import patch
env = {'SERVICENOW_INSTANCE_URL': 'https://test.service-now.com', 'SERVICENOW_USERNAME': 'admin', 'SERVICENOW_PASSWORD': 's3cret', 'MCP_TOOL_PACKAGE': 'full'}
with patch.dict('os.environ', env, clear=True):
    from servicenow_mcp.server import create_mcp_server
    mcp = create_mcp_server()
    tools = [t.name for t in mcp._tool_manager._tools.values()]
    # Verify some existing tools still work
    assert 'table_query' in tools, 'table_query missing'
    assert 'debug_trace' in tools, 'debug_trace missing'
    assert 'meta_list_artifacts' in tools, 'meta_list_artifacts missing'
    print(f'Total: {len(tools)} tools, all existing tools present')
"
    Expected Result: All existing tools still registered, total count increased
    Failure Indicators: Any existing tool name missing
    Evidence: .sisyphus/evidence/task-5-no-regression.txt
  ```

  **Commit**: YES
  - Message: `feat(server): register ATF testing tool group in package registry`
  - Files: `src/servicenow_mcp/server.py`, `src/servicenow_mcp/packages.py`
  - Pre-commit: `uv run ruff check src/servicenow_mcp/server.py src/servicenow_mcp/packages.py`

- [x] 6. Write unit tests for introspection tools (atf_list_tests, atf_get_test, atf_list_suites, atf_get_results)

  **What to do**:
  - Create `tests/test_testing.py` following `tests/test_debug.py` pattern exactly
  - Setup: `BASE_URL = "https://test.service-now.com"`, `_register_and_get_tools` helper, `settings` and `auth_provider` fixtures
  - Use `@respx.mock` decorator on all async test methods
  - Use `@pytest.mark.asyncio` on all test methods (asyncio_mode auto should handle but be explicit)

  **Test classes and methods**:

  **Class `TestAtfListTests`**:
  - `test_list_tests_success` - Mock `GET /api/now/table/sys_atf_test` returning 2 test records. Assert response has `status: "success"`, `data` contains records with expected fields.
  - `test_list_tests_with_query` - Verify custom query param is passed through to API call
  - `test_list_tests_empty_results` - Mock returning empty result array. Assert response has `status: "success"` with empty data.

  **Class `TestAtfGetTest`**:
  - `test_get_test_success` - Mock GET for test record + GET for steps. Assert combined response has `test` and `steps` keys.
  - `test_get_test_not_found` - Mock 404 response. Assert error envelope returned (not raised).
  - `test_get_test_with_steps` - Mock test with 3 steps. Assert step_count = 3 and steps are ordered.

  **Class `TestAtfListSuites`**:
  - `test_list_suites_success` - Mock suite query + member count queries. Assert member_count is included.
  - `test_list_suites_empty` - Mock empty results. Assert success with empty data.

  **Class `TestAtfGetResults`**:
  - `test_get_results_for_test` - Mock test results query. Assert correct table queried.
  - `test_get_results_for_suite` - Mock suite results query. Assert correct table queried.
  - `test_get_results_missing_both_ids` - Call with neither test_id nor suite_id. Assert error response.
  - `test_get_results_empty` - Mock empty results. Assert success with empty data.

  **Must NOT do**:
  - Do NOT write integration tests (unit tests with respx mocking only)
  - Do NOT test execution tools here (Task 7)
  - Do NOT import from servicenow_mcp.server (test tools/testing directly)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 12+ tests with careful mock setup, needs pattern-following precision
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 7)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 3, 5

  **References**:

  **Pattern References**:
  - `tests/test_debug.py` - Primary test template. Shows BASE_URL, auth_provider fixture, `_register_and_get_tools` helper, @respx.mock decorator, response parsing with json.loads
  - `tests/test_introspection.py` - Shows test pattern for table query tools (mock GET /api/now/table/{table} responses)
  - `tests/conftest.py` - Shared fixtures (`settings`, `prod_settings`) using patch.dict

  **Test References**:
  - `tests/test_client.py` - Shows respx mock patterns for client HTTP calls (respx.get/post with httpx.Response)

  **Acceptance Criteria**:
  - [ ] `tests/test_testing.py` exists with 12+ test methods
  - [ ] All tests use `@respx.mock` decorator
  - [ ] `uv run pytest tests/test_testing.py -v` passes all tests
  - [ ] Each introspection tool has at least 2 tests (happy path + edge case)

  **QA Scenarios**:

  ```
  Scenario: All introspection tests pass
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/test_testing.py -v -k "not run_test and not run_suite and not test_health" --tb=short 2>&1
    Expected Result: 12+ tests PASSED, 0 FAILED
    Failure Indicators: FAILED, ERROR, or fewer than 12 passed
    Evidence: .sisyphus/evidence/task-6-introspection-tests.txt

  Scenario: Tests use correct mocking pattern
    Tool: Bash
    Steps:
      1. Run: uv run python -c "
import ast
with open('tests/test_testing.py') as f:
    source = f.read()
assert '@respx.mock' in source, 'Missing respx.mock decorator'
assert 'BASE_URL' in source, 'Missing BASE_URL constant'
assert '_register_and_get_tools' in source, 'Missing helper function'
assert 'json.loads' in source, 'Missing response parsing'
print('All patterns present')
"
    Expected Result: "All patterns present" printed
    Evidence: .sisyphus/evidence/task-6-test-patterns.txt
  ```

  **Commit**: NO (groups with Task 7)

- [x] 7. Write unit tests for execution and intelligence tools (atf_run_test, atf_run_suite, atf_test_health)

  **What to do**:
  - Add test classes to `tests/test_testing.py`:

  **Class `TestAtfRunTest`**:
  - `test_run_test_success_with_poll` - Mock Cloud Runner run endpoint + progress endpoint (returns completed). Assert snboq_id and final status returned.
  - `test_run_test_no_poll` - Mock run endpoint only. Call with `poll=False`. Assert immediate return with snboq_id.
  - `test_run_test_write_blocked` - Use `prod_settings` (env=prod). Assert write gate blocks execution with error envelope.
  - `test_run_test_plugin_not_found` - Mock 404 from run endpoint. Assert clear error about missing plugin.
  - `test_run_test_poll_timeout` - Mock progress endpoint that never reaches terminal state. Assert `polling_timeout` status after max duration.

  **Class `TestAtfRunSuite`**:
  - `test_run_suite_success` - Same pattern as test_run_test_success but with suite_id
  - `test_run_suite_write_blocked` - Write gate blocks in prod

  **Class `TestAtfTestHealth`**:
  - `test_health_for_test` - Mock results with mix of pass/fail. Assert pass_rate computed correctly.
  - `test_health_for_suite` - Mock suite results. Assert rolled-up metrics.
  - `test_health_no_results` - Mock empty results. Assert appropriate response (not error).
  - `test_health_missing_ids` - Call with neither test_id nor suite_id. Assert error.
  - `test_health_flaky_detection` - Mock alternating pass/fail results. Assert `flaky: true`.

  **Must NOT do**:
  - Do NOT use real asyncio.sleep in tests (mock it or use very short intervals)
  - Do NOT test against live ServiceNow instance

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex mock setups (polling sequences, timeout simulation), needs careful async testing
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 6)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 4, 5

  **References**:

  **Pattern References**:
  - `tests/test_developer.py` - Shows write-gate test pattern: use `prod_settings` fixture, assert error response with "write" or "blocked" in error message
  - `tests/test_debug.py` - Standard test structure
  - `tests/test_client.py` - Shows respx POST mocking patterns

  **External References**:
  - `unittest.mock.patch` for mocking `asyncio.sleep` to avoid real delays in poll tests
  - `respx.mock` patterns for sequential mock responses (progress polling)

  **Acceptance Criteria**:
  - [ ] 12+ additional test methods added for execution and intelligence tools
  - [ ] Write gate tests verify production blocking
  - [ ] Polling timeout test verifies max duration is respected
  - [ ] `uv run pytest tests/test_testing.py -v` passes ALL tests (24+ total)

  **QA Scenarios**:

  ```
  Scenario: All tests in test_testing.py pass
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/test_testing.py -v --tb=short 2>&1
    Expected Result: 24+ tests PASSED, 0 FAILED, 0 ERROR
    Failure Indicators: Any FAILED or ERROR
    Evidence: .sisyphus/evidence/task-7-all-tests.txt

  Scenario: Write gate tests work correctly
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/test_testing.py -v -k "write_blocked" --tb=short 2>&1
    Expected Result: 2+ tests PASSED (run_test + run_suite blocked in prod)
    Failure Indicators: FAILED
    Evidence: .sisyphus/evidence/task-7-write-gate-tests.txt
  ```

  **Commit**: YES
  - Message: `test(testing): add comprehensive unit tests for ATF tools`
  - Files: `tests/test_testing.py`
  - Pre-commit: `uv run pytest tests/test_testing.py -v --tb=short`

- [x] 8. Full verification pass - lint, types, all tests

  **What to do**:
  - Run the complete verification suite:
    1. `uv run ruff check .` - zero lint errors
    2. `uv run ruff format --check .` - zero formatting issues
    3. `uv run mypy src/` - zero type errors
    4. `uv run pytest --tb=short` - ALL tests pass (existing 482+ and new 25+)
    5. Run the tool registration verification script from Success Criteria
  - Fix any issues found (format with `uv run ruff format .`, fix lint with `uv run ruff check --fix .`)
  - If any test failures: diagnose and fix in the appropriate file
  - Final commit only if fixes were needed

  **Must NOT do**:
  - Do NOT skip any verification step
  - Do NOT suppress errors with `# noqa` or `# type: ignore` unless absolutely necessary and documented

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Running commands and fixing minor issues if any
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after all implementation)
  - **Blocks**: Final Verification Wave
  - **Blocked By**: Tasks 6, 7

  **References**:

  **Pattern References**:
  - `pyproject.toml` - ruff config, mypy config, pytest config (addopts, markers)
  - `AGENTS.md` - Verification commands and expected outputs

  **Acceptance Criteria**:
  - [ ] `uv run ruff check .` exits 0
  - [ ] `uv run ruff format --check .` exits 0
  - [ ] `uv run mypy src/` exits 0
  - [ ] `uv run pytest --tb=short` exits 0 with all tests passing
  - [ ] Tool registration verification passes

  **QA Scenarios**:

  ```
  Scenario: Complete verification suite passes
    Tool: Bash
    Steps:
      1. Run: uv run ruff check . 2>&1 | tail -3
      2. Run: uv run ruff format --check . 2>&1 | tail -3
      3. Run: uv run mypy src/ 2>&1 | tail -5
      4. Run: uv run pytest --tb=short 2>&1 | tail -10
    Expected Result: All 4 commands exit 0, pytest shows 507+ passed (482 existing + 25+ new)
    Failure Indicators: Any non-zero exit code, FAILED in pytest output
    Evidence: .sisyphus/evidence/task-8-full-verification.txt

  Scenario: No regressions in existing tests
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/ --ignore=tests/test_testing.py --tb=short 2>&1 | tail -5
    Expected Result: All existing tests pass (482+ passed)
    Failure Indicators: FAILED, ERROR
    Evidence: .sisyphus/evidence/task-8-no-regression.txt
  ```

  **Commit**: YES (only if fixes needed)
  - Message: `chore: fix lint/type/format issues from ATF tools integration`
  - Files: Any files that needed fixing
  - Pre-commit: `uv run pytest --tb=short`

---

## Final Verification Wave (MANDATORY - after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection -> fix -> re-run.

- [ ] F1. **Plan Compliance Audit** - `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns - reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** - `unspecified-high`
  Run `uv run mypy src/` + `uv run ruff check .` + `uv run pytest`. Review all changed files for: `as any`/`type: ignore`, empty catches, print() in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (data/result/item/temp).
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** - `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task - follow exact steps, capture evidence. Test cross-task integration (tools working together). Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** - `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 - everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `feat(client): add ATF Cloud Runner REST API client methods` - client.py
- **Wave 2**: `feat(tools): add ATF testing tool group with 7 tools` - testing.py, server.py, packages.py
- **Wave 3**: `test(testing): add comprehensive unit tests for ATF tools` - test_testing.py
- **Final**: `chore: verify all lint, type, and test checks pass` (if needed)

---

## Success Criteria

### Verification Commands
```bash
uv run pytest --tb=short          # Expected: all pass (482+ existing + 25+ new)
uv run ruff check .               # Expected: 0 errors
uv run ruff format --check .      # Expected: 0 formatting issues
uv run mypy src/                  # Expected: 0 type errors
```

### Tool Registration Verification
```python
uv run python -c "
from unittest.mock import patch
env = {'SERVICENOW_INSTANCE_URL': 'https://test.service-now.com', 'SERVICENOW_USERNAME': 'admin', 'SERVICENOW_PASSWORD': 's3cret', 'MCP_TOOL_PACKAGE': 'full'}
with patch.dict('os.environ', env, clear=True):
    from servicenow_mcp.config import Settings
    settings = Settings(_env_file=None)
    from servicenow_mcp.server import create_mcp_server
    mcp = create_mcp_server()
    tools = [t.name for t in mcp._tool_manager._tools.values()]
    atf_tools = [t for t in tools if t.startswith('atf_')]
    print(f'Total tools: {len(tools)}, ATF tools: {len(atf_tools)}')
    print(f'ATF tools: {atf_tools}')
    assert len(atf_tools) >= 7, f'Expected >= 7 ATF tools, got {len(atf_tools)}'
    assert 'atf_list_tests' in tools
    assert 'atf_get_test' in tools
    assert 'atf_list_suites' in tools
    assert 'atf_get_results' in tools
    assert 'atf_run_test' in tools
    assert 'atf_run_suite' in tools
    assert 'atf_test_health' in tools
    print('ALL CHECKS PASSED')
"
```

### Final Checklist
- [ ] All 7 ATF tools registered and working
- [ ] All "Must Have" features present
- [ ] All "Must NOT Have" guardrails respected
- [ ] All existing tests still pass (zero regressions)
- [ ] New test file has 25+ tests
- [ ] Zero lint, format, or type errors
