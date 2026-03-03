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

## Task 3: Add 4 Preset Package Combinations

**Pattern:** PACKAGE_REGISTRY dict entry structure is:
```python
"preset_name": [
    "group1",
    "group2",
    ...
]
```

**Key Finding:** `get_package()` and `list_packages()` functions already support new registry entries automatically. No code changes needed outside PACKAGE_REGISTRY itself.

**Presets Added:**
- `itil`: 7 groups - ITIL process support (incident, change, problem)
- `developer`: 10 groups - Full developer toolkit (all introspection + debug + dev tools)
- `readonly`: 8 groups - Safe read-only operations (no mutations, no developer tools)
- `analyst`: 6 groups - Business analyst tools (reporting, docs, investigations)

**TDD Pattern Used:**
1. Write 9 failing tests (RED) covering all 4 presets + list_packages verification + backward compatibility
2. Implement 4 entries in PACKAGE_REGISTRY (GREEN)
3. All 24 tests pass (15 existing + 9 new)
4. 100% coverage on packages.py
5. Strict mypy pass

**Inheritance from Task 1:** Registry-based validation means adding new keys here automatically makes them valid in config validation via `_VALID_PACKAGES = set(PACKAGE_REGISTRY.keys())`.

## [2026-03-03T16:15:00Z] Task 4: Comma-Separated Group Selection

### Architecture Decision: Moved _TOOL_GROUP_MODULES to packages.py
**Problem:** Circular import - `config.py` → `packages.py`, but `packages.py` needed `_TOOL_GROUP_MODULES` from `server.py` which imports `packages.py`.

**Solution:** Move `_TOOL_GROUP_MODULES` dict from `server.py` (lines 15-28) to `packages.py` (lines 1-12) as the source of truth. This makes logical sense: it maps group names to modules, which is fundamentally a package/grouping concept.

**Result:** No circular dependency. Clean dependency chain: `server.py` imports from `packages.py` only.

### Implementation Pattern: Comma Parsing with Validation

**Core Logic in `get_package()`:**
1. **Preset-first check**: If name in `PACKAGE_REGISTRY`, return preset (backward compatible)
2. **Fallback to comma parsing**: Split by comma, strip whitespace
3. **Empty group detection**: Check for empty strings BEFORE filtering (catches trailing/leading commas)
4. **Deduplication with order preservation**: Track seen groups, skip duplicates
5. **Validation against _TOOL_GROUP_MODULES**: Catch invalid group names and preset names used in comma syntax
6. **Clear error messages**: List ALL invalid groups + provide valid group names

**Key insight:** Separate validation of empty groups (before filtering) from invalid group names (after filtering). This catches `"debug,"` but allows `"debug"`.

### TDD Cycle: 13 Tests in RED → GREEN

**All tests passing:**
- Valid comma-separated groups with/without spaces
- Deduplication (single and mixed duplicates)
- Empty group rejection (empty string, trailing/leading commas)
- Invalid group names with clear error messages
- Preset backward compatibility unchanged
- Preset names rejected in comma syntax (e.g., "introspection,itil" is invalid)
- Order preservation during deduplication

### Config Validator Update

**Pattern:** Use local import of `get_package()` inside validator function:
```python
@field_validator("mcp_tool_package")
@classmethod
def validate_mcp_tool_package(cls, v: str) -> str:
    from servicenow_mcp.packages import get_package
    try:
        get_package(v)  # Validates both presets and comma syntax
    except ValueError as e:
        raise ValueError(f"Invalid mcp_tool_package: {e}") from e
    return v
```

**Why local import:** Avoids module-level circular imports while keeping validation centralized.

### Full Test Suite Impact
- 532 tests pass (18 integration skipped)
- 96% code coverage
- Zero LSP errors on modified files
- All existing functionality preserved

### User-Facing Behavior Examples
```
MCP_TOOL_PACKAGE="introspection,debug,utility"     # Custom composition
MCP_TOOL_PACKAGE="introspection, debug, utility"   # With spaces (auto-trimmed)
MCP_TOOL_PACKAGE="debug,debug,introspection"       # Deduped to ["debug", "introspection"]
MCP_TOOL_PACKAGE="itil"                            # Preset still works
MCP_TOOL_PACKAGE="introspection,itil,debug"        # ERROR: preset name in comma syntax
MCP_TOOL_PACKAGE="introspection,invalid_group"     # ERROR: unknown group
```

### Files Modified
1. `src/servicenow_mcp/packages.py` - Moved _TOOL_GROUP_MODULES, extended get_package()
2. `src/servicenow_mcp/server.py` - Import _TOOL_GROUP_MODULES from packages
3. `src/servicenow_mcp/config.py` - Updated validator to accept comma syntax
4. `tests/test_packages.py` - Added 13 new tests for comma syntax (all passing)
5. `tests/test_config.py` - Updated error message expectation

### Commit Message
`feat(config): support comma-separated group selection in MCP_TOOL_PACKAGE`

### Next Steps (Tasks 5+)
This foundation enables domain-specific tool packages to reference individual groups rather than predefined presets. Example: `"incident,change,debug"` loads only incident + change domain tools + debug utilities.

## [2026-03-03T17:30:00Z] Task 5: Domain Scaffolding

### Scaffolding Pattern Established

**Structure Created:**
1. `src/servicenow_mcp/tools/domains/` package with:
   - `__init__.py` (module docstring describing ITIL domain organization)
   - 6 empty stub modules (incident, change, cmdb, problem, request, knowledge)

2. Each stub module pattern:
   ```python
   from mcp.server.fastmcp import FastMCP  # Note: NOT mcp.FastMCP
   from servicenow_mcp.auth import BasicAuthProvider
   from servicenow_mcp.config import Settings
   
   def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
       pass  # Implementation in Tasks 6-11
   ```

3. Test infrastructure:
   - `tests/domains/conftest.py` - Shared settings/auth_provider fixtures
   - `tests/domains/test_imports.py` - Verification that all 6 modules import + registry entries exist

### Key Findings

**Import Path Gotcha**: 
- FastMCP lives at `mcp.server.fastmcp.FastMCP`, not `mcp.FastMCP`
- Auth type is `BasicAuthProvider`, not `AuthProvider` (which doesn't exist)
- These must match project patterns established in existing tool modules

**Registry Integration:**
- 6 new entries added to `_TOOL_GROUP_MODULES` in `packages.py` (lines 15-20)
- All use "domain_" prefix for clear ITIL domain identification
- Example: `"domain_incident": "servicenow_mcp.tools.domains.incident"`
- This enables comma-separated selection: `"domain_incident,debug,utility"`

**Test Coverage:**
- Import verification: Ensures all 6 modules can be loaded without errors
- Registry verification: Ensures all 6 entries exist in _TOOL_GROUP_MODULES with correct paths
- Both tests pass; full suite: 534 tests pass, no regressions

### Verification Results
- ✅ All imports OK
- ✅ All registered in _TOOL_GROUP_MODULES
- ✅ Ruff lint: clean
- ✅ MyPy: no errors
- ✅ Domain tests: 2 passed
- ✅ Full test suite: 534 passed, 18 deselected

### Ready for Task 6

Domain scaffolding is complete. Next task (Incident reference implementation) can:
1. Add tools to `incident.register_tools()`
2. Pattern follow: Utility.py as reference for tool registration
3. Use `write_gate()` from policy.py for write operations
4. Test pattern: Use fixtures from `tests/domains/conftest.py`

### Notes for Tasks 7-11

Each domain module follows the same stub pattern created here. No additional scaffolding needed.
