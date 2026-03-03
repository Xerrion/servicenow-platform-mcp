# Wave 1 Task 1: ATF Cloud Runner Client Methods - Learnings

## Task Summary
Successfully added 4 async methods to `ServiceNowClient` class to support ATF (Automated Test Framework) Cloud Runner REST API interactions.

## Key Implementation Patterns

### URL Builder Pattern
- Private URL builder method `_atf_cloud_runner_url(endpoint: str)` returns scoped REST endpoint
- Format: `f"{self._instance_url}/api/now/sn_atf_tg/{endpoint}"`
- No identifier validation needed for ATF endpoints (unlike table-based URLs)

### Method Structure (Consistent Pattern)
All three main methods follow identical structure:
1. `http = self._ensure_client()` - Guard against uninitialized client
2. Build request data (json body for POST, params dict for GET)
3. Wrap httpx call in try/except to catch `httpx.HTTPStatusError`
4. Check specifically for 404 and raise `NotFoundError` with plugin-not-installed message
5. Re-raise other HTTP errors
6. Extract response: try `self._extract_result(data)` first, fallback to `data` if KeyError

### Defensive Response Extraction
Pattern discovered in code:
```python
response_data = response.json()
try:
    return self._extract_result(response_data)
except KeyError:
    return response_data
```
This handles APIs that may or may not wrap result in a "result" key, ensuring robustness.

### Error Handling Pattern
- Custom 404 handling with context-specific message
- All other HTTP errors allowed to propagate via `self._raise_for_status(response)`
- Plugin-not-installed detection happens ONLY for ATF methods (unique to this API)

## Type Hints Enforced
- All methods have full type annotations (mypy strict mode: `disallow_untyped_defs=true`)
- Return types: `dict[str, Any]` (never `Optional` - use `|` syntax)
- Parameters: `str`, `bool` with defaults where appropriate
- No implicit `None` returns

## Testing Verification
- Scenario 1: Method signatures verified (parameters, return types)
- Scenario 2: mypy type checking passes (0 errors on client.py)
- All 56 existing tests still pass (no regressions)

## File Changes
- File: `src/servicenow_mcp/client.py`
- Added: 4 methods after line 514 (Encoded Query Translator section)
- Total lines added: 95 lines (methods + docstrings + blank lines)
- File grew from 514 lines → 611 lines

## Import Notes
- `NotFoundError` already imported at top of client.py
- `httpx` already imported (used for HTTPStatusError)
- No new dependencies needed

## Decisions Made

1. **Scoped URL vs. Generic URL Builder**: Used scoped `_atf_cloud_runner_url()` (not `_api_url()`) to follow project patterns for different API families (Email, Import Set, Reporting, Code Search, CMDB, etc.)

2. **Defensive Result Extraction**: KeyError handling in response extraction makes methods robust if API response format changes or varies between instances.

3. **404 Handling**: Plugin-specific error message ("sn_atf_tg may not be installed") helps users troubleshoot missing functionality rather than generic "Not Found" error.

4. **Async All The Way**: All methods are `async` despite being simple HTTP calls, following project convention that ALL ServiceNow API calls are async for consistency and future extensibility.

## QA Evidence Files
- `task-1-client-signatures.txt` - Method signature verification output
- `task-1-mypy-client.txt` - mypy type checking (empty = no errors)
