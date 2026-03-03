
## [2026-03-03] Task 4: Execution and Intelligence Tools (Combined with Task 3)

### Critical Incident
- Task 3 implementation was lost due to edit error (replaced file header instead of appending)
- Recovery: Restored from git, re-implemented all 7 tools in this task
- Lesson: When appending to large files, verify line ranges before editing

### Polling Implementation Insights
- **time.monotonic() is crucial**: Use monotonic clock for elapsed time, NOT datetime or time.time()
- **Clamping pattern**: `max(min_val, min(user_val, max_val))` prevents abuse and infinite loops
- **Terminal state detection**: Normalize to lowercase before checking set membership
- **Graceful timeout**: Return partial result with `polling_timeout` status + snboq_id so user can check later
- **Non-polling mode**: Essential for async workflows - return immediately with execution ID

### Write Gate Integration
- **Gate first, execute second**: Always call `_atf_execution_gate()` BEFORE client interaction
- **Early return on block**: If gate returns error envelope, return it immediately (no client creation)
- **Consistent with developer.py pattern**: Copy exact structure from existing write-gated tools

### Health Computation Algorithm
- **Flaky detection heuristic**: Count state transitions (pass↔fail), not just pass rate variance
  - Works well for small sample sizes (transitions >= 2 when total_runs < 10)
  - Scales to larger datasets (transitions >= 3 for bigger runs)
- **Trend analysis split**: Divide results in half chronologically, compare pass rates
  - 5% threshold provides meaningful signal without false positives on small datasets
  - Handles insufficient data gracefully (< 4 runs)
- **Status normalization**: ServiceNow uses "success" OR "passed" - normalize to lowercase set

### Query Construction Patterns
- **Date filtering**: `javascript:gs.daysAgoStart(N)` works in encoded queries
- **Multi-condition queries**: Combine ID filter + date filter with `^` separator
- **Empty result handling**: Return structured zero-state with warnings array, not error

### Async Patterns Reinforced
- **Parallel fetch** (asyncio.gather): Use for test + steps in atf_get_test
- **Sequential enrichment**: Unavoidable when second query depends on first results (suite member counts)
- **Client context**: Always `async with ServiceNowClient(...) as client:` - ensures cleanup

### Import Correction
- Added `time` module import for `time.monotonic()` - not part of stdlib asyncio
- All other imports follow existing patterns from Task 3 learnings

### Testing Strategy
- QA scenarios validated: write gate presence, all 7 tools registered, constants correct
- No unit tests yet (Task 7) - QA scenarios provide smoke test coverage
- Integration tests will require mock ATF Cloud Runner responses
