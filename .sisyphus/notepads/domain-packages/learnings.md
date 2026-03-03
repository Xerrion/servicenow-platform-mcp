# Domain Packages - Learnings

## [2026-03-03T15:02:00Z] Plan Start
- Transitioning from ATF Tools plan (completed, merged)
- Starting Domain Packages plan: 29 tasks across 4 waves
- Goal: Transform monolithic tool system into composable domain-aware packages

## [2026-03-03T15:05:00Z] Task 2: Extract write_gate() Helper

### Findings
- **Scope Correction**: The function was duplicated in `tools/developer.py` (NOT `record.py`) and `tools/testing.py`
- **Actual Usage**: Only `developer.py` actively used `_write_gate()` (7 call sites across CRUD tools)
- **ATF Pattern**: `testing.py` had the function but never used it; only uses `_atf_execution_gate()` for test execution

### Implementation Details
1. **Extraction Location**: `policy.py` (preferred per architecture)
2. **New Function Signature**: `write_gate(table: str, settings: Settings, correlation_id: str) -> str | None`
3. **Local Import Pattern**: Used local import of `format_response` inside function to avoid circular dependency (policy.py ↔ utils.py)
4. **Public Export**: Function named `write_gate()` (without underscore) to signal reusability for future domain modules

### Changes Made
- ✅ Added `import json` to `policy.py`
- ✅ Added `write_gate()` public function to `policy.py` (lines 142-167)
- ✅ Updated `developer.py` to import and use `write_gate()`
- ✅ Removed 7 `_write_gate()` call sites from `developer.py`
- ✅ Removed unused `_write_gate()` function from `testing.py`
- ✅ Removed stale `write_blocked_reason` import from `developer.py`

### Verification Results
- ✅ All 507 non-integration tests pass
- ✅ 95% code coverage maintained
- ✅ LSP diagnostics: No errors (0 warnings on policy.py, developer.py, testing.py)
- ✅ Type checking: All signatures fully annotated
- ✅ Import structure: No circular dependencies

### Key Pattern for Future Domain Modules
For any new domain tool module needing write-gating:
```python
from servicenow_mcp.policy import write_gate

# Early in tool function:
blocked = write_gate(table, settings, correlation_id)
if blocked:
    return blocked
```

### Notes for Domain Tool Implementation (Tasks 6-11)
- Each domain module (incident, service_catalog, etc.) should use this shared `write_gate()` for consistency
- ATF-style gates (like `_atf_execution_gate()`) still live locally when table-specific
- Never duplicate write-gating logic across modules

### Commit Message
`refactor(policy): extract write_gate to shared module for domain tool reuse`
