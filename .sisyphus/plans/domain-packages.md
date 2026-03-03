# Domain-Specific Tool Packages

## TL;DR

> **Quick Summary**: Split the monolithic tool system into domain-specific packages (Incident, Change, CMDB, Problem, Request, Knowledge) with higher-level, intent-driven tools. Three phases: preset packages, domain modules, custom group selection.
> 
> **Deliverables**:
> - Phase 0: Prerequisite refactors (derive `_VALID_PACKAGES`, extract `_write_gate`)
> - Phase 1: Preset package combos in PACKAGE_REGISTRY (itil, developer, readonly, analyst)
> - Phase 2: 6 domain tool modules (~5-7 tools each, TDD) in `tools/domains/`
> - Phase 3: Comma-separated custom group selection in config
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Prereq refactors -> Phase 1 registry -> Incident reference module -> Remaining 5 domains in parallel

---

## Context

### Original Request
Split the tool package system into domain-specific packages (ITIL, CMDB, change management, incident management, request management, knowledge management) with higher-level, intent-driven tools.

### Interview Summary
**Key Discussions**:
- Strategy B selected: new domain-specific tool modules (not just registry combos)
- All 3 phases in scope: preset packages -> domain modules -> comma-select
- 6 domains: Incident, Change, CMDB, Problem, Request, Knowledge
- Domain tools coexist with generic tools (not replace)
- Moderately opinionated: enforce required fields, sensible defaults (literal values only), allow overrides
- Domain tools use ServiceNowClient directly (not wrap existing tool functions)
- TDD approach for all domain modules
- Focused core: ~5-7 tools per domain (list/get/create/update + 2-3 workflow actions)

**Research Findings**:
- Current: 36 tools, 11 groups (incl. testing), 3 packages (full, introspection_only, none)
- `_VALID_PACKAGES` is hardcoded frozenset in config.py - blocks adding new packages without code change
- `_write_gate()` is duplicated in developer.py:25 and testing.py:28 - must extract before domain modules add 6 more copies
- `_TOOL_GROUP_MODULES` maps group names to module paths - domain groups need entries here
- All tools follow `safe_tool_call` + inner `_run()` pattern
- ServiceNowClient has: `get_record`, `query_records`, `create_record`, `update_record`, `delete_record`, `aggregate`

### Metis Review
**Identified Gaps** (addressed):
- `_VALID_PACKAGES` hardcoding blocks Phase 1 -> prerequisite refactor added
- `_write_gate()` duplication -> extraction task added
- Tool naming convention undefined -> defined as `{domain}_{action}` (e.g., `incident_create`)
- Phase 3 comma-parsing semantics undefined -> formal grammar defined
- CMDB/Knowledge may not fit standard pattern -> incident first as reference impl
- No tool collision detection -> test assertion for uniqueness added
- `display_values` default question -> domain tools default to `display_values=True`
- Table name ambiguity (Request: `sc_request` vs `sc_req_item`) -> locked down per domain
- Missing `tools/domains/__init__.py` -> explicitly in task

---

## Work Objectives

### Core Objective
Transform the tool system from a monolithic "all or nothing" approach into a composable, domain-aware package system where LLMs get intent-driven tools with self-documenting parameters.

### Concrete Deliverables
- `src/servicenow_mcp/packages.py` updated with preset packages + domain group entries
- `src/servicenow_mcp/config.py` updated to derive `_VALID_PACKAGES` from registry + Phase 3 comma parsing
- `src/servicenow_mcp/policy.py` (or `utils.py`) with extracted `_write_gate()`
- `src/servicenow_mcp/tools/domains/__init__.py` package init
- `src/servicenow_mcp/tools/domains/incident.py` (~6 tools)
- `src/servicenow_mcp/tools/domains/change.py` (~6 tools)
- `src/servicenow_mcp/tools/domains/cmdb.py` (~5 tools)
- `src/servicenow_mcp/tools/domains/problem.py` (~5 tools)
- `src/servicenow_mcp/tools/domains/request.py` (~5 tools)
- `src/servicenow_mcp/tools/domains/knowledge.py` (~5 tools)
- `src/servicenow_mcp/server.py` updated `_TOOL_GROUP_MODULES` with domain entries
- Tests for all of the above (TDD)

### Definition of Done
- [ ] `uv run pytest` passes with all new and existing tests
- [ ] `uv run ruff check .` clean
- [ ] `uv run mypy src/` clean
- [ ] `MCP_TOOL_PACKAGE=itil` starts server with correct tool set
- [ ] `MCP_TOOL_PACKAGE=incident_management` starts server with domain tools
- [ ] `MCP_TOOL_PACKAGE=introspection,domain_incident,debug` (comma syntax) works
- [ ] Existing packages (`full`, `introspection_only`, `none`) work identically

### Must Have
- All domain tools use explicit typed parameters (NOT JSON string blobs)
- Domain tools follow `safe_tool_call` + `_run()` pattern
- Domain tools call `check_table_access()`, `mask_sensitive_fields()`, write-gating
- Tool names follow `{domain}_{action}` convention
- Each domain module is independently registrable (no cross-domain imports)
- Backward compatibility with existing packages
- Domain tools default to `display_values=True`

### Must NOT Have (Guardrails)
- No new `ServiceNowClient` methods - use existing API only
- No domain modules importing from each other
- No computed defaults (no API calls for auto-assignment, priority calculation)
- No cross-domain workflow tools (`incident_link_to_change` etc.)
- No API-call-based field validation (local validation only)
- No shared base class / domain toolkit abstraction
- No more than 7 tools per domain module
- No JSON string blob parameters on domain tools (`data: str`) - use explicit params
- No modifications to existing generic tools

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest + respx + pytest-asyncio + pytest-cov)
- **Automated tests**: TDD (RED-GREEN-REFACTOR)
- **Framework**: pytest with respx HTTP mocking, asyncio_mode=auto

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Domain tools**: Use Bash (uv run pytest) - run tests, assert pass counts
- **Package loading**: Use Bash (uv run python -c "...") - import server, verify tool lists
- **Config validation**: Use Bash (uv run pytest) - test valid/invalid config values

---

## Execution Strategy

### Table Name Reference (Locked Down)

| Domain | Primary Table | Related Tables |
|--------|--------------|----------------|
| Incident | `incident` | `sys_user`, `sys_user_group` |
| Change | `change_request` | `change_task`, `cmdb_ci` |
| CMDB | `cmdb_ci` | `cmdb_rel_ci`, `cmdb_ci_server`, `cmdb_ci_computer` |
| Problem | `problem` | `incident`, `problem_task` |
| Request | `sc_request` | `sc_req_item`, `sc_cat_item` |
| Knowledge | `kb_knowledge` | `kb_category`, `kb_knowledge_base` |

### Tool Manifest (All Domain Modules)

**Incident** (`domain_incident`, 6 tools):
- `incident_list(state, priority, assigned_to, assignment_group, limit)` - Query with ITIL-aware filters
- `incident_get(number)` - Get by INCxxxxxxx number
- `incident_create(short_description, description, urgency, impact, assignment_group, caller_id)` - Create with required field enforcement
- `incident_update(number, **fields)` - Update arbitrary fields by number
- `incident_resolve(number, close_code, close_notes)` - Set resolved state + required closure fields
- `incident_add_comment(number, comment, work_note)` - Add comment or work note via journal

**Change** (`domain_change`, 6 tools):
- `change_list(state, type, risk, assignment_group, limit)` - Query with change-aware filters
- `change_get(number)` - Get by CHGxxxxxxx number
- `change_create(short_description, description, type, risk, assignment_group, start_date, end_date)` - Create with type/risk enforcement
- `change_update(number, **fields)` - Update arbitrary fields by number
- `change_tasks(number)` - List change tasks for a change request
- `change_add_comment(number, comment, work_note)` - Add comment or work note

**CMDB** (`domain_cmdb`, 5 tools):
- `cmdb_list(ci_class, name, operational_status, sys_class_name, limit)` - Query CIs by class/status
- `cmdb_get(name_or_sys_id)` - Get a CI by name or sys_id
- `cmdb_relationships(name_or_sys_id, direction)` - Get upstream/downstream/all relationships
- `cmdb_classes()` - List available CI classes (queries sys_db_object)
- `cmdb_health(ci_class)` - Aggregate operational status counts for a CI class

**Problem** (`domain_problem`, 5 tools):
- `problem_list(state, priority, assignment_group, limit)` - Query with problem-aware filters
- `problem_get(number)` - Get by PRBxxxxxxx number
- `problem_create(short_description, description, urgency, impact, assignment_group)` - Create with required fields
- `problem_update(number, **fields)` - Update arbitrary fields
- `problem_root_cause(number, cause_notes, fix_notes)` - Set root cause analysis + fix

**Request** (`domain_request`, 5 tools):
- `request_list(state, requested_for, limit)` - Query service catalog requests
- `request_get(number)` - Get by REQxxxxxxx number
- `request_items(number)` - List requested items for a request
- `request_item_get(number)` - Get a specific RITM by number
- `request_item_update(number, **fields)` - Update a requested item

**Knowledge** (`domain_knowledge`, 5 tools):
- `knowledge_search(query, knowledge_base, category, limit)` - Full-text search articles
- `knowledge_get(number_or_sys_id)` - Get article by KBxxxxxxx number or sys_id
- `knowledge_create(short_description, text, knowledge_base, category, workflow_state)` - Create article
- `knowledge_update(number_or_sys_id, **fields)` - Update article
- `knowledge_feedback(number_or_sys_id, rating, comment)` - Submit article feedback

### Parallel Execution Waves

```
Wave 1 (Foundation - prerequisite refactors + Phase 1):
  Task 1: Derive _VALID_PACKAGES from registry [quick]
  Task 2: Extract _write_gate to shared location [quick]
  Task 3: Phase 1 - Preset packages in registry [quick]
  Task 4: Phase 3 - Comma-separated group selection [quick]
  Task 5: Domain package scaffolding (tools/domains/__init__.py + server.py entries) [quick]

Wave 2 (Reference Implementation):
  Task 6: Incident domain module (reference impl, TDD) [deep]

Wave 3 (Remaining domains - MAX PARALLEL, follow incident pattern):
  Task 7: Change domain module (TDD) [unspecified-high]
  Task 8: CMDB domain module (TDD) [unspecified-high]
  Task 9: Problem domain module (TDD) [unspecified-high]
  Task 10: Request domain module (TDD) [unspecified-high]
  Task 11: Knowledge domain module (TDD) [unspecified-high]

Wave 4 (Integration + preset packages with domain groups):
  Task 12: Update preset packages with domain groups + integration tests [deep]

Wave FINAL (Verification - 4 parallel):
  Task F1: Plan compliance audit [oracle]
  Task F2: Code quality review (ruff + mypy + tests) [unspecified-high]
  Task F3: Package loading QA (every preset + comma combos) [unspecified-high]
  Task F4: Scope fidelity check [deep]

Critical Path: Task 1 -> Task 3 -> Task 5 -> Task 6 -> Task 12 -> F1-F4
Parallel Speedup: ~60% faster than sequential (Wave 3 = 5 parallel)
Max Concurrent: 5 (Wave 3)
```

### Dependency Matrix

| Task | Blocked By | Blocks |
|------|-----------|--------|
| 1 | - | 3, 4 |
| 2 | - | 6-11 |
| 3 | 1 | 5, 12 |
| 4 | 1 | 12 |
| 5 | 3 | 6 |
| 6 | 2, 5 | 7-11, 12 |
| 7-11 | 6 | 12 |
| 12 | 4, 7-11 | F1-F4 |
| F1-F4 | 12 | - |

### Agent Dispatch Summary

- **Wave 1**: **5** - T1-T5 -> `quick`
- **Wave 2**: **1** - T6 -> `deep`
- **Wave 3**: **5** - T7-T11 -> `unspecified-high`
- **Wave 4**: **1** - T12 -> `deep`
- **FINAL**: **4** - F1 -> `oracle`, F2-F3 -> `unspecified-high`, F4 -> `deep`

---

## TODOs

- [ ] 1. Derive `_VALID_PACKAGES` from Package Registry

  **What to do**:
  - Remove the hardcoded `_VALID_PACKAGES = frozenset({"full", "introspection_only", "none"})` from `config.py`
  - Import `PACKAGE_REGISTRY` from `packages.py` and derive valid packages: `_VALID_PACKAGES = frozenset(PACKAGE_REGISTRY.keys())`
  - Update the `@field_validator("mcp_tool_package")` to use the derived set
  - Write tests FIRST (TDD RED): test that adding a new entry to PACKAGE_REGISTRY automatically makes it a valid config value
  - Make tests pass (TDD GREEN)
  - Verify existing config tests still pass (backward compat)

  **Must NOT do**:
  - Do not change any package definitions
  - Do not modify the validator logic beyond switching from hardcoded to derived

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
    - Simple refactor, no domain knowledge needed

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 2)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 3, 4
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/config.py:9` - `_VALID_PACKAGES` frozenset definition
  - `src/servicenow_mcp/config.py:46-52` - `@field_validator("mcp_tool_package")` method that checks against `_VALID_PACKAGES`
  - `src/servicenow_mcp/packages.py:1-35` - `PACKAGE_REGISTRY` dict definition and `get_package()` function

  **Test References**:
  - `tests/test_config.py` - Existing config validation tests, verify these still pass

  **WHY Each Reference Matters**:
  - `config.py:9` - This is the line to change - replace hardcoded frozenset with derived one
  - `config.py:46-52` - The validator that consumes `_VALID_PACKAGES` - may need error message update
  - `packages.py` - The source of truth for valid package names after this change

  **Acceptance Criteria**:

  - [ ] `_VALID_PACKAGES` no longer hardcoded in config.py
  - [ ] `_VALID_PACKAGES` derived from `PACKAGE_REGISTRY.keys()`
  - [ ] `uv run pytest tests/test_config.py -v` -> PASS (all existing + new tests)
  - [ ] Adding a key to PACKAGE_REGISTRY automatically makes it a valid `mcp_tool_package` value

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Existing packages still accepted
    Tool: Bash (uv run pytest)
    Preconditions: All existing test_config.py tests present
    Steps:
      1. Run `uv run pytest tests/test_config.py -v`
      2. Verify all existing tests pass
    Expected Result: 0 failures, all tests pass
    Failure Indicators: Any test_config.py test fails
    Evidence: .sisyphus/evidence/task-1-backward-compat.txt

  Scenario: New registry entry auto-validates
    Tool: Bash (uv run python -c)
    Preconditions: Temporarily add test entry to PACKAGE_REGISTRY
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.config import Settings; import os; os.environ['SERVICENOW_INSTANCE_URL']='https://test.service-now.com'; os.environ['SERVICENOW_USERNAME']='u'; os.environ['SERVICENOW_PASSWORD']='p'; os.environ['MCP_TOOL_PACKAGE']='full'; s = Settings(_env_file=None); print(s.mcp_tool_package)"`
      2. Verify value is "full"
    Expected Result: Settings loads without ValidationError
    Failure Indicators: ValidationError raised for valid package name
    Evidence: .sisyphus/evidence/task-1-derived-validation.txt
  ```

  **Commit**: YES
  - Message: `refactor(config): derive _VALID_PACKAGES from package registry`
  - Files: `src/servicenow_mcp/config.py`, `tests/test_config.py`
  - Pre-commit: `uv run pytest tests/test_config.py -v`

- [ ] 2. Extract `_write_gate` to Shared Location

  **What to do**:
  - Create or extend `src/servicenow_mcp/policy.py` (if it doesn't exist, add to existing `utils.py`) with the `_write_gate()` function
  - Move `_write_gate(table, settings, correlation_id)` from `developer.py:25-37` to the shared module
  - Update `developer.py` and `testing.py` to import from the shared location
  - Write test FIRST (TDD RED) for the extracted function in isolation
  - Make it pass (TDD GREEN)
  - Verify existing developer and testing tool tests still pass

  **Must NOT do**:
  - Do not change `_write_gate` behavior
  - Do not rename it (keep the underscore prefix, domain modules will import it directly)
  - Do not add new functionality

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 1)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 6-11 (all domain modules need this)
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/developer.py:25-37` - `_write_gate()` implementation (primary copy)
  - `src/servicenow_mcp/tools/testing.py:28` - `_write_gate()` duplicate copy
  - `src/servicenow_mcp/policy.py` - Existing policy module (check if it exists, if yes add there; if not, add to utils.py)

  **Test References**:
  - `tests/test_developer.py` - Tests that exercise write-gating behavior through developer tools
  - `tests/test_testing.py` - Tests that exercise write-gating through testing tools

  **WHY Each Reference Matters**:
  - `developer.py:25-37` - Source of truth for `_write_gate` logic, copy from here
  - `testing.py:28` - Must be updated to import from shared location
  - `policy.py` or `utils.py` - Destination for the extracted function

  **Acceptance Criteria**:

  - [ ] `_write_gate` defined in exactly ONE shared location
  - [ ] `developer.py` imports `_write_gate` from shared location
  - [ ] `testing.py` imports `_write_gate` from shared location
  - [ ] `uv run pytest tests/test_developer.py tests/test_testing.py -v` -> PASS
  - [ ] `rg "_write_gate" src/` shows no duplicated definitions

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: No duplicate definitions remain
    Tool: Bash (rg)
    Preconditions: Extraction complete
    Steps:
      1. Run `rg "def _write_gate" src/`
      2. Count matches
    Expected Result: Exactly 1 match (in the shared module)
    Failure Indicators: 2+ matches means duplication remains
    Evidence: .sisyphus/evidence/task-2-no-dupes.txt

  Scenario: Existing write-gated tools still work
    Tool: Bash (uv run pytest)
    Preconditions: Extraction complete
    Steps:
      1. Run `uv run pytest tests/test_developer.py tests/test_testing.py -v`
      2. Verify all tests pass
    Expected Result: 0 failures
    Failure Indicators: ImportError or test failures
    Evidence: .sisyphus/evidence/task-2-existing-tests.txt
  ```

  **Commit**: YES
  - Message: `refactor(policy): extract _write_gate to shared location`
  - Files: shared module, `developer.py`, `testing.py`, tests
  - Pre-commit: `uv run pytest tests/test_developer.py tests/test_testing.py -v`

- [ ] 3. Phase 1 - Add Preset Package Combos to Registry

  **What to do**:
  - Add new preset packages to `PACKAGE_REGISTRY` in `packages.py`:
    - `"itil"`: `["introspection", "relationships", "metadata", "changes", "debug", "documentation", "utility"]`
    - `"developer"`: `["introspection", "relationships", "metadata", "changes", "debug", "developer", "dev_utils", "investigations", "documentation", "utility"]`
    - `"readonly"`: `["introspection", "relationships", "metadata", "changes", "debug", "investigations", "documentation", "utility"]`
    - `"analyst"`: `["introspection", "relationships", "metadata", "investigations", "documentation", "utility"]`
  - Write tests FIRST (TDD RED): test each preset returns the expected group list
  - Make tests pass (TDD GREEN)
  - Since Task 1 derives `_VALID_PACKAGES` from registry, no config changes needed here

  **Must NOT do**:
  - Do not add domain groups yet (those come in Task 12 after domain modules exist)
  - Do not modify existing package definitions
  - Do not touch config.py (Task 1 handles that)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 5 after Task 1 completes)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 5, 12
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/packages.py:8-30` - Existing `PACKAGE_REGISTRY` structure, follow same pattern
  - `src/servicenow_mcp/packages.py:33-40` - `get_package()` function (no changes needed, just returns registry entry)

  **Test References**:
  - `tests/test_packages.py` (if exists) or create new - follow pattern from `tests/test_config.py`

  **WHY Each Reference Matters**:
  - `packages.py:8-30` - Follow exact dict structure for new entries
  - `get_package()` - Verify it works for new names without modification

  **Acceptance Criteria**:

  - [ ] `get_package("itil")` returns expected 7 groups
  - [ ] `get_package("developer")` returns expected 10 groups
  - [ ] `get_package("readonly")` returns expected 8 groups
  - [ ] `get_package("analyst")` returns expected 6 groups
  - [ ] `get_package("full")` still returns all groups (unchanged)
  - [ ] `list_packages()` includes all new presets
  - [ ] `uv run pytest -v -k packages` -> PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: All presets resolve correctly
    Tool: Bash (uv run python -c)
    Preconditions: New presets added
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; [print(f'{p}: {get_package(p)}') for p in ['itil','developer','readonly','analyst']]"`
      2. Verify each returns the expected group list
    Expected Result: 4 presets, each with correct groups
    Failure Indicators: ValueError or wrong group list
    Evidence: .sisyphus/evidence/task-3-presets.txt

  Scenario: Invalid package name still raises error
    Tool: Bash (uv run python -c)
    Preconditions: New presets added
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; get_package('nonexistent')"`
      2. Verify ValueError is raised
    Expected Result: ValueError with descriptive message
    Failure Indicators: Returns empty list or no error
    Evidence: .sisyphus/evidence/task-3-invalid.txt
  ```

  **Commit**: YES
  - Message: `feat(packages): add preset package combos (itil, developer, readonly, analyst)`
  - Files: `src/servicenow_mcp/packages.py`, `tests/test_packages.py`
  - Pre-commit: `uv run pytest -v -k packages`

- [ ] 4. Phase 3 - Comma-Separated Group Selection

  **What to do**:
  - Update `get_package()` in `packages.py` to accept comma-separated group names when value is not a named preset
  - Grammar: if value is in `PACKAGE_REGISTRY`, return preset. Otherwise, split by comma, validate each group name against `_TOOL_GROUP_MODULES.keys()`, return list
  - Update the config validator in `config.py` to accept comma-separated values: if value is a preset name OR all comma-separated parts are valid group names, accept
  - Handle edge cases: whitespace trimming, deduplication, empty strings, trailing commas
  - Write tests FIRST (TDD RED): valid combos, invalid group names, edge cases
  - Make tests pass (TDD GREEN)

  **Must NOT do**:
  - Do not allow mixing preset names with group names (e.g., `itil,debug` is NOT valid - use all group names or a single preset)
  - Do not allow preset names as group names in comma syntax

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 3, 5 after Task 1 completes)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 12
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/packages.py:33-40` - `get_package()` to extend with comma parsing
  - `src/servicenow_mcp/config.py:46-52` - Validator to update for comma syntax
  - `src/servicenow_mcp/server.py:15-35` - `_TOOL_GROUP_MODULES` dict - keys are the valid group names

  **Test References**:
  - `tests/test_config.py` - Existing config tests, extend with comma-separated cases

  **WHY Each Reference Matters**:
  - `get_package()` - Primary function to extend with comma-parsing logic
  - Config validator - Must accept comma syntax without breaking preset validation
  - `_TOOL_GROUP_MODULES` - Source of valid group names for comma syntax validation

  **Acceptance Criteria**:

  - [ ] `get_package("introspection,debug,utility")` returns `["introspection", "debug", "utility"]`
  - [ ] `get_package("introspection, debug, utility")` (with spaces) also works
  - [ ] `get_package("itil")` still returns the preset (unchanged)
  - [ ] `get_package("invalid_group,debug")` raises ValueError with clear message listing invalid names
  - [ ] `get_package(",,,")` raises ValueError (empty groups)
  - [ ] `get_package("debug,debug,debug")` returns `["debug"]` (deduped)
  - [ ] Config validator accepts comma-separated group names
  - [ ] `uv run pytest tests/test_config.py tests/test_packages.py -v` -> PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Comma-separated groups load correctly
    Tool: Bash (uv run python -c)
    Preconditions: Comma syntax implemented
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; print(get_package('introspection,debug,utility'))"`
      2. Verify returns exactly 3 groups
    Expected Result: ['introspection', 'debug', 'utility']
    Failure Indicators: ValueError or wrong group list
    Evidence: .sisyphus/evidence/task-4-comma-valid.txt

  Scenario: Invalid group name in comma syntax rejected
    Tool: Bash (uv run python -c)
    Preconditions: Comma syntax implemented
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; get_package('introspection,fake_group')"`
      2. Verify ValueError raised
    Expected Result: ValueError mentioning 'fake_group' as unknown
    Failure Indicators: Silently returns empty or partial list
    Evidence: .sisyphus/evidence/task-4-comma-invalid.txt
  ```

  **Commit**: YES
  - Message: `feat(config): support comma-separated group selection in MCP_TOOL_PACKAGE`
  - Files: `src/servicenow_mcp/packages.py`, `src/servicenow_mcp/config.py`, `tests/test_config.py`, `tests/test_packages.py`
  - Pre-commit: `uv run pytest tests/test_config.py tests/test_packages.py -v`

- [ ] 5. Domain Package Scaffolding

  **What to do**:
  - Create `src/servicenow_mcp/tools/domains/__init__.py` with module docstring
  - Create empty stub files for all 6 domain modules with just the `register_tools` signature returning immediately
  - Add 6 new entries to `_TOOL_GROUP_MODULES` in `server.py`:
    - `"domain_incident": "servicenow_mcp.tools.domains.incident"`
    - `"domain_change": "servicenow_mcp.tools.domains.change"`
    - `"domain_cmdb": "servicenow_mcp.tools.domains.cmdb"`
    - `"domain_problem": "servicenow_mcp.tools.domains.problem"`
    - `"domain_request": "servicenow_mcp.tools.domains.request"`
    - `"domain_knowledge": "servicenow_mcp.tools.domains.knowledge"`
  - Create `tests/domains/__init__.py` and `tests/domains/conftest.py` with shared test fixtures (settings, auth_provider)
  - Write a test that imports each stub module and calls `register_tools` without error
  - Verify server can load each domain group name without crashing

  **Must NOT do**:
  - Do not implement any actual tools yet (that's Tasks 6-11)
  - Do not add domain groups to preset packages yet (that's Task 12)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 3, 4 after Task 1 completes)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 6
  - **Blocked By**: Task 3 (needs registry to exist for group validation)

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/__init__.py` - Follow same module docstring pattern
  - `src/servicenow_mcp/server.py:15-35` - `_TOOL_GROUP_MODULES` dict to extend
  - `src/servicenow_mcp/tools/utility.py` - Simplest tool module, follow as skeleton pattern for stubs

  **Test References**:
  - `tests/conftest.py` - Existing fixtures (settings, auth_provider) to reuse/mirror

  **WHY Each Reference Matters**:
  - `tools/__init__.py` - Pattern for package init docstring
  - `_TOOL_GROUP_MODULES` - Must add 6 new entries here for domain group discovery
  - `utility.py` - Minimal `register_tools` pattern to copy for stubs

  **Acceptance Criteria**:

  - [ ] `src/servicenow_mcp/tools/domains/__init__.py` exists
  - [ ] All 6 domain stub files exist with `register_tools(mcp, settings, auth_provider)` signature
  - [ ] `_TOOL_GROUP_MODULES` has 6 new `domain_*` entries
  - [ ] `tests/domains/__init__.py` and `tests/domains/conftest.py` exist
  - [ ] `uv run python -c "from servicenow_mcp.tools.domains import incident, change, cmdb, problem, request, knowledge"` succeeds
  - [ ] `uv run ruff check src/servicenow_mcp/tools/domains/` clean
  - [ ] `uv run mypy src/servicenow_mcp/tools/domains/` clean

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: All domain modules importable
    Tool: Bash (uv run python -c)
    Preconditions: Scaffold complete
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.tools.domains import incident, change, cmdb, problem, request, knowledge; print('All imports OK')"`
    Expected Result: "All imports OK" printed, no ImportError
    Failure Indicators: ImportError for any module
    Evidence: .sisyphus/evidence/task-5-imports.txt

  Scenario: Server loads domain groups without error
    Tool: Bash (uv run python -c)
    Preconditions: Scaffold + server entries complete
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.server import _TOOL_GROUP_MODULES; assert all(f'domain_{d}' in _TOOL_GROUP_MODULES for d in ['incident','change','cmdb','problem','request','knowledge']); print('All domain groups registered')"`
    Expected Result: "All domain groups registered"
    Failure Indicators: AssertionError or KeyError
    Evidence: .sisyphus/evidence/task-5-server-entries.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): scaffold domain tools package + server entries`
  - Files: `src/servicenow_mcp/tools/domains/__init__.py`, 6 stub files, `server.py`, `tests/domains/`
  - Pre-commit: `uv run pytest tests/domains/ -v`

- [ ] 6. Incident Domain Module - Reference Implementation (TDD)

  **What to do**:
  This is the **reference implementation** that all other domain modules will follow. Extra care on patterns and conventions.

  Implement 6 tools in `src/servicenow_mcp/tools/domains/incident.py`:

  1. `incident_list(state: str = "", priority: str = "", assigned_to: str = "", assignment_group: str = "", limit: int = 20) -> str`
     - Build encoded query from params, call `client.query_records("incident", query, display_values=True, limit=limit)`
     - State mapping: "open"->1, "in_progress"->2, "on_hold"->3, "resolved"->6, "closed"->7, "canceled"->8, "all"->skip
     - Return masked, formatted results

  2. `incident_get(number: str) -> str`
     - Query by `number={number}`, return first result with `display_values=True`
     - Validate number starts with "INC" (case-insensitive)

  3. `incident_create(short_description: str, description: str = "", urgency: int = 3, impact: int = 3, assignment_group: str = "", caller_id: str = "") -> str`
     - Validate `short_description` is non-empty
     - Validate urgency/impact are 1-4
     - Call write gate, then `client.create_record("incident", data)`
     - Return created record

  4. `incident_update(number: str, short_description: str = "", description: str = "", urgency: int | None = None, impact: int | None = None, state: str = "", assignment_group: str = "", assigned_to: str = "") -> str`
     - Look up sys_id by number, then `client.update_record("incident", sys_id, changes)`
     - Only include non-empty/non-None fields in changes dict
     - Call write gate

  5. `incident_resolve(number: str, close_code: str, close_notes: str) -> str`
     - Validate close_code and close_notes are non-empty
     - Look up sys_id by number
     - Call write gate, then update with `state=6, close_code, close_notes`

  6. `incident_add_comment(number: str, comment: str = "", work_note: str = "") -> str`
     - Validate at least one of comment/work_note is non-empty
     - Look up sys_id by number
     - Call write gate, then update with `comments` and/or `work_notes` journal fields

  **Pattern to follow for ALL tools**:
  ```python
  @mcp.tool()
  async def incident_list(...) -> str:
      """Docstring with Args section."""
      correlation_id = str(uuid.uuid4())
      async def _run() -> str:
          check_table_access("incident")
          async with ServiceNowClient(settings, auth_provider) as client:
              # ... business logic ...
              records = mask_sensitive_fields(raw_records, settings)
              return json.dumps(format_response(data=records, correlation_id=correlation_id))
      return await safe_tool_call(_run, correlation_id)
  ```

  **TDD workflow**:
  - Write ALL test cases first in `tests/domains/test_incident.py` (RED)
  - Tests should use respx to mock HTTP calls to `/api/now/table/incident`
  - Use `_register_and_get_tools()` helper pattern from existing tests
  - Implement tools to make tests pass (GREEN)
  - Refactor if needed

  **Must NOT do**:
  - No computed defaults (no priority matrix calculation)
  - No cross-domain references
  - No API calls for validation (local only)
  - No more than 6 tools
  - No JSON string blob parameters

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Reference implementation requires extra care, TDD, and establishing patterns for 5 other modules
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (must complete before Wave 3)
  - **Parallel Group**: Wave 2 (solo)
  - **Blocks**: Tasks 7-11 (all other domains follow this pattern), Task 12
  - **Blocked By**: Tasks 2, 5

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/developer.py:40-120` - `register_tools` pattern, `safe_tool_call` usage, write-gating, policy calls
  - `src/servicenow_mcp/tools/introspection.py` - Read-only tool pattern (for list/get tools)
  - `src/servicenow_mcp/utils.py:format_response()` - Response envelope pattern
  - `src/servicenow_mcp/policy.py` - `check_table_access()`, `mask_sensitive_fields()`, `enforce_query_safety()` - MUST call these

  **API/Type References**:
  - `src/servicenow_mcp/client.py:ServiceNowClient` - `query_records`, `create_record`, `update_record`, `get_record` method signatures
  - `src/servicenow_mcp/auth.py:BasicAuthProvider` - Auth provider passed to register_tools

  **Test References**:
  - `tests/test_developer.py:21-29` - `_register_and_get_tools()` helper pattern
  - `tests/test_developer.py:50-90` - respx mock setup for table API calls
  - `tests/test_introspection.py` - Read-only tool test patterns

  **WHY Each Reference Matters**:
  - `developer.py` - Shows the full write-gating + safe_tool_call + policy pattern that incident_create/update/resolve must follow
  - `introspection.py` - Shows read-only pattern for incident_list/get
  - `test_developer.py:21-29` - MUST use this helper pattern for registering and extracting tool callables
  - `client.py` - Exact method signatures to call (query_records returns list of dicts, create_record returns dict)

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_incident.py` has tests for all 6 tools (happy path + error cases)
  - [ ] `uv run pytest tests/domains/test_incident.py -v` -> PASS (minimum 18 tests: 3 per tool)
  - [ ] `uv run ruff check src/servicenow_mcp/tools/domains/incident.py` -> clean
  - [ ] `uv run mypy src/servicenow_mcp/tools/domains/incident.py` -> clean
  - [ ] All tools use `safe_tool_call` pattern
  - [ ] All tools call `check_table_access("incident")`
  - [ ] Write tools call `_write_gate`
  - [ ] All tools use explicit typed parameters (no `data: str`)
  - [ ] Tool count is exactly 6

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: incident_list returns incidents with display values
    Tool: Bash (uv run pytest)
    Preconditions: respx mock for GET /api/now/table/incident returns 2 incidents
    Steps:
      1. Run `uv run pytest tests/domains/test_incident.py::TestIncidentList -v`
      2. Verify tests cover: no filters, state filter, priority filter, empty results
    Expected Result: All TestIncidentList tests pass
    Failure Indicators: Any assertion fails or HTTP call without display_value=all
    Evidence: .sisyphus/evidence/task-6-incident-list.txt

  Scenario: incident_create enforces required fields
    Tool: Bash (uv run pytest)
    Preconditions: Tests mock POST /api/now/table/incident
    Steps:
      1. Run `uv run pytest tests/domains/test_incident.py::TestIncidentCreate -v`
      2. Verify tests cover: valid create, missing short_description, invalid urgency, write-gated env
    Expected Result: All TestIncidentCreate tests pass, empty short_description returns error envelope
    Failure Indicators: Missing validation, write gate not checked
    Evidence: .sisyphus/evidence/task-6-incident-create.txt

  Scenario: incident_resolve requires close_code and close_notes
    Tool: Bash (uv run pytest)
    Preconditions: Tests mock PATCH /api/now/table/incident/{sys_id}
    Steps:
      1. Run `uv run pytest tests/domains/test_incident.py::TestIncidentResolve -v`
      2. Verify tests cover: valid resolve, empty close_code, empty close_notes
    Expected Result: All pass, missing fields return error envelope (not exception)
    Failure Indicators: Resolves without required fields
    Evidence: .sisyphus/evidence/task-6-incident-resolve.txt

  Scenario: Full domain module test suite
    Tool: Bash (uv run pytest)
    Preconditions: All 6 tools implemented
    Steps:
      1. Run `uv run pytest tests/domains/test_incident.py -v --tb=short`
      2. Count total tests
    Expected Result: >= 18 tests, 0 failures
    Failure Indicators: < 18 tests or any failure
    Evidence: .sisyphus/evidence/task-6-full-suite.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add incident management tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/incident.py`, `tests/domains/test_incident.py`
  - Pre-commit: `uv run pytest tests/domains/test_incident.py -v`

- [ ] 7. Change Management Domain Module (TDD)

  **What to do**:
  Follow the **exact pattern** established by Task 6 (incident module).

  Implement 6 tools in `src/servicenow_mcp/tools/domains/change.py`:

  1. `change_list(state: str = "", type: str = "", risk: str = "", assignment_group: str = "", limit: int = 20) -> str`
     - Table: `change_request`
     - State mapping: "new"->-5, "assess"->-4, "authorize"->-3, "scheduled"->-2, "implement"->-1, "review"->0, "closed"->3, "canceled"->4
     - Type mapping: "standard", "normal", "emergency"
     - Risk mapping: map display values to internal if needed

  2. `change_get(number: str) -> str`
     - Table: `change_request`, validate number starts with "CHG"

  3. `change_create(short_description: str, description: str = "", type: str = "normal", risk: str = "", assignment_group: str = "", start_date: str = "", end_date: str = "") -> str`
     - Validate short_description non-empty, type is valid
     - Write gate, create in `change_request`

  4. `change_update(number: str, short_description: str = "", description: str = "", type: str = "", risk: str = "", assignment_group: str = "", state: str = "") -> str`
     - Look up sys_id by CHG number, update non-empty fields

  5. `change_tasks(number: str, limit: int = 20) -> str`
     - Table: `change_task`, query by `change_request.number={number}`
     - Read-only, no write gate needed

  6. `change_add_comment(number: str, comment: str = "", work_note: str = "") -> str`
     - Same journal pattern as incident_add_comment but for `change_request`

  **TDD workflow**: Write all tests first in `tests/domains/test_change.py`, then implement.

  **Must NOT do**: Same guardrails as Task 6

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Follows established pattern from Task 6, doesn't need deep reasoning but has moderate complexity
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 8-11)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/domains/incident.py` - **PRIMARY REFERENCE** - follow this exact pattern for structure, imports, error handling, policy calls
  - `tests/domains/test_incident.py` - **PRIMARY REFERENCE** - follow this exact test pattern

  **API/Type References**:
  - `src/servicenow_mcp/client.py:ServiceNowClient` - query_records, create_record, update_record
  - ServiceNow `change_request` table: states use negative numbers (-5 to 4), type field, risk field

  **WHY Each Reference Matters**:
  - `incident.py` - THE pattern to copy. Same structure, different table/fields/states
  - `test_incident.py` - THE test pattern to copy. Same fixtures, different mocks

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_change.py` has tests for all 6 tools
  - [ ] `uv run pytest tests/domains/test_change.py -v` -> PASS (>= 18 tests)
  - [ ] `uv run ruff check src/servicenow_mcp/tools/domains/change.py` -> clean
  - [ ] `uv run mypy src/servicenow_mcp/tools/domains/change.py` -> clean
  - [ ] All tools follow incident.py pattern exactly
  - [ ] `change_tasks` queries `change_task` table (not `change_request`)

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: change_list filters by type
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_change.py::TestChangeList -v`
    Expected Result: All list tests pass including type filter
    Evidence: .sisyphus/evidence/task-7-change-list.txt

  Scenario: change_tasks returns related change tasks
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_change.py::TestChangeTasks -v`
    Expected Result: Queries change_task table with parent reference
    Evidence: .sisyphus/evidence/task-7-change-tasks.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add change management tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/change.py`, `tests/domains/test_change.py`
  - Pre-commit: `uv run pytest tests/domains/test_change.py -v`

- [ ] 8. CMDB Domain Module (TDD)

  **What to do**:
  Follow the pattern from Task 6 but adapted for CMDB's different structure (CI hierarchy, relationships).

  Implement 5 tools in `src/servicenow_mcp/tools/domains/cmdb.py`:

  1. `cmdb_list(ci_class: str = "cmdb_ci", name: str = "", operational_status: str = "", sys_class_name: str = "", limit: int = 20) -> str`
     - Default table: `cmdb_ci`, allow override with `ci_class` param (e.g., `cmdb_ci_server`)
     - Operational status mapping: "operational"->1, "non_operational"->2, "repair_in_progress"->3, "dr_standby"->4, "ready"->5, "retired"->6
     - Call `check_table_access(ci_class)` with the actual class being queried

  2. `cmdb_get(name_or_sys_id: str, ci_class: str = "cmdb_ci") -> str`
     - Try by sys_id first (if 32-char hex), then by name field
     - Return with display_values=True

  3. `cmdb_relationships(name_or_sys_id: str, direction: str = "both") -> str`
     - Table: `cmdb_rel_ci`
     - direction: "parent" (upstream), "child" (downstream), "both"
     - Query relationships where CI is parent or child, return related CIs

  4. `cmdb_classes(limit: int = 50) -> str`
     - Query `sys_db_object` where `super_class` chains to cmdb_ci
     - Or simpler: query `cmdb_ci` with `GROUPBY sys_class_name` via aggregate
     - Read-only, no write gate

  5. `cmdb_health(ci_class: str = "cmdb_ci") -> str`
     - Use aggregate API: `client.aggregate(ci_class, query="", group_by="operational_status")`
     - Return counts per operational status
     - Read-only, no write gate

  **TDD workflow**: Write all tests first in `tests/domains/test_cmdb.py`, then implement.

  **Must NOT do**: Same guardrails as Task 6, plus: no CI creation/update tools (CMDB is primarily read + discovery, write ops are dangerous)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 7, 9-11)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/domains/incident.py` - Base pattern
  - `src/servicenow_mcp/tools/relationships.py` - Existing relationship tool pattern (rel_references_to, rel_references_from) - shows how to query `cmdb_rel_ci`
  - `src/servicenow_mcp/tools/introspection.py` - Aggregate tool pattern for `cmdb_health`

  **API/Type References**:
  - `src/servicenow_mcp/client.py:aggregate()` - For cmdb_health
  - `src/servicenow_mcp/client.py:query_records()` - For list/get/relationships

  **WHY Each Reference Matters**:
  - `incident.py` - Base pattern for tool structure
  - `relationships.py` - Shows how cmdb_rel_ci relationships are queried (parent/child references)
  - `introspection.py` - Shows aggregate API usage pattern for cmdb_health

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_cmdb.py` has tests for all 5 tools
  - [ ] `uv run pytest tests/domains/test_cmdb.py -v` -> PASS (>= 15 tests)
  - [ ] All 5 tools are read-only (no write gate calls)
  - [ ] `cmdb_relationships` handles both parent and child direction
  - [ ] `cmdb_health` uses aggregate API

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: cmdb_list defaults to cmdb_ci table
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_cmdb.py::TestCmdbList -v`
    Expected Result: Default query hits cmdb_ci, ci_class override changes target table
    Evidence: .sisyphus/evidence/task-8-cmdb-list.txt

  Scenario: cmdb_relationships returns parent and child CIs
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_cmdb.py::TestCmdbRelationships -v`
    Expected Result: direction="both" returns both, "parent"/"child" filters correctly
    Evidence: .sisyphus/evidence/task-8-cmdb-relationships.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add CMDB tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/cmdb.py`, `tests/domains/test_cmdb.py`
  - Pre-commit: `uv run pytest tests/domains/test_cmdb.py -v`

- [ ] 9. Problem Management Domain Module (TDD)

  **What to do**:
  Follow the pattern from Task 6. Very similar to Incident.

  Implement 5 tools in `src/servicenow_mcp/tools/domains/problem.py`:

  1. `problem_list(state: str = "", priority: str = "", assignment_group: str = "", limit: int = 20) -> str`
     - Table: `problem`
     - State mapping: "new"->1 (or "open"), "known_error"->3 (or similar), "closed"->4 - check ServiceNow defaults

  2. `problem_get(number: str) -> str`
     - Validate starts with "PRB"

  3. `problem_create(short_description: str, description: str = "", urgency: int = 3, impact: int = 3, assignment_group: str = "") -> str`
     - Same validation as incident_create

  4. `problem_update(number: str, short_description: str = "", description: str = "", urgency: int | None = None, impact: int | None = None, state: str = "", assignment_group: str = "") -> str`
     - Same pattern as incident_update

  5. `problem_root_cause(number: str, cause_notes: str, fix_notes: str = "") -> str`
     - Validate cause_notes non-empty
     - Look up sys_id, update `cause_notes` and optionally `fix_notes` fields
     - Write gate

  **TDD workflow**: Write all tests first in `tests/domains/test_problem.py`, then implement.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 7-8, 10-11)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/domains/incident.py` - **PRIMARY REFERENCE** - nearly identical structure
  - `tests/domains/test_incident.py` - **PRIMARY REFERENCE**

  **WHY Each Reference Matters**:
  - Problem is structurally almost identical to Incident (same ITIL pattern, similar fields, similar states)
  - `problem_root_cause` is the only unique action, similar to `incident_resolve` pattern

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_problem.py` has tests for all 5 tools
  - [ ] `uv run pytest tests/domains/test_problem.py -v` -> PASS (>= 15 tests)
  - [ ] `problem_root_cause` enforces non-empty cause_notes

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: problem_root_cause enforces required fields
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_problem.py::TestProblemRootCause -v`
    Expected Result: Empty cause_notes returns error envelope
    Evidence: .sisyphus/evidence/task-9-problem-root-cause.txt

  Scenario: Full problem module test suite
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_problem.py -v --tb=short`
    Expected Result: >= 15 tests, 0 failures
    Evidence: .sisyphus/evidence/task-9-full-suite.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add problem management tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/problem.py`, `tests/domains/test_problem.py`
  - Pre-commit: `uv run pytest tests/domains/test_problem.py -v`

- [ ] 10. Request Management Domain Module (TDD)

  **What to do**:
  Follow the pattern from Task 6. Request management involves 2 tables: `sc_request` (parent) and `sc_req_item` (line items).

  Implement 5 tools in `src/servicenow_mcp/tools/domains/request.py`:

  1. `request_list(state: str = "", requested_for: str = "", limit: int = 20) -> str`
     - Table: `sc_request`
     - State mapping: "open", "approved", "in_progress", "closed", "cancelled"

  2. `request_get(number: str) -> str`
     - Table: `sc_request`, validate starts with "REQ"

  3. `request_items(number: str, limit: int = 20) -> str`
     - Table: `sc_req_item`, query by `request.number={number}`
     - Read-only (lists RITM items under a REQ)

  4. `request_item_get(number: str) -> str`
     - Table: `sc_req_item`, validate starts with "RITM"

  5. `request_item_update(number: str, state: str = "", assignment_group: str = "", assigned_to: str = "", short_description: str = "") -> str`
     - Table: `sc_req_item`
     - Look up RITM by number, update non-empty fields
     - Write gate

  **TDD workflow**: Write all tests first in `tests/domains/test_request.py`, then implement.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 7-9, 11)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/domains/incident.py` - **PRIMARY REFERENCE**
  - `tests/domains/test_incident.py` - **PRIMARY REFERENCE**

  **WHY Each Reference Matters**:
  - Request module is unique in using 2 tables (sc_request + sc_req_item) but each tool targets one table
  - `request_items` is similar to `change_tasks` (parent->child query)

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_request.py` has tests for all 5 tools
  - [ ] `uv run pytest tests/domains/test_request.py -v` -> PASS (>= 15 tests)
  - [ ] `request_items` queries `sc_req_item` with parent reference
  - [ ] `request_item_get` validates RITM prefix
  - [ ] `request_get` validates REQ prefix

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: request_items returns child items for a request
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_request.py::TestRequestItems -v`
    Expected Result: Queries sc_req_item table with request parent filter
    Evidence: .sisyphus/evidence/task-10-request-items.txt

  Scenario: Full request module test suite
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_request.py -v --tb=short`
    Expected Result: >= 15 tests, 0 failures
    Evidence: .sisyphus/evidence/task-10-full-suite.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add request management tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/request.py`, `tests/domains/test_request.py`
  - Pre-commit: `uv run pytest tests/domains/test_request.py -v`

- [ ] 11. Knowledge Management Domain Module (TDD)

  **What to do**:
  Follow the pattern from Task 6. Knowledge is primarily read-heavy (search + retrieval).

  Implement 5 tools in `src/servicenow_mcp/tools/domains/knowledge.py`:

  1. `knowledge_search(query: str, knowledge_base: str = "", category: str = "", limit: int = 20) -> str`
     - Table: `kb_knowledge`
     - Use `short_descriptionLIKE{query}^ORtextLIKE{query}` for basic text search
     - Filter by knowledge_base and category if provided
     - Only return published articles: `workflow_state=published`

  2. `knowledge_get(number_or_sys_id: str) -> str`
     - Table: `kb_knowledge`
     - Try by `number` field first (KBxxxxxxx), then by sys_id (32-char hex)

  3. `knowledge_create(short_description: str, text: str, knowledge_base: str = "", category: str = "", workflow_state: str = "draft") -> str`
     - Validate short_description and text non-empty
     - Default workflow_state to "draft" (not "published")
     - Write gate

  4. `knowledge_update(number_or_sys_id: str, short_description: str = "", text: str = "", knowledge_base: str = "", category: str = "", workflow_state: str = "") -> str`
     - Look up by number or sys_id, update non-empty fields
     - Write gate

  5. `knowledge_feedback(number_or_sys_id: str, rating: str = "", comment: str = "") -> str`
     - Validate at least one of rating/comment non-empty
     - This may need to update a related table (kb_feedback) or fields on kb_knowledge
     - If complex, simplify to updating `u_helpful` or similar field on the article itself
     - Write gate

  **TDD workflow**: Write all tests first in `tests/domains/test_knowledge.py`, then implement.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 7-10)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/tools/domains/incident.py` - **PRIMARY REFERENCE**
  - `tests/domains/test_incident.py` - **PRIMARY REFERENCE**

  **WHY Each Reference Matters**:
  - Knowledge is read-heavy, `knowledge_search` is the most complex tool (multi-field text search)
  - `knowledge_create` defaults to draft state (opinionated default)

  **Acceptance Criteria**:

  - [ ] `tests/domains/test_knowledge.py` has tests for all 5 tools
  - [ ] `uv run pytest tests/domains/test_knowledge.py -v` -> PASS (>= 15 tests)
  - [ ] `knowledge_search` defaults to published articles only
  - [ ] `knowledge_create` defaults workflow_state to "draft"
  - [ ] Text search uses LIKE on both short_description and text fields

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: knowledge_search returns only published articles by default
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_knowledge.py::TestKnowledgeSearch -v`
    Expected Result: Query includes workflow_state=published filter
    Evidence: .sisyphus/evidence/task-11-knowledge-search.txt

  Scenario: Full knowledge module test suite
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/domains/test_knowledge.py -v --tb=short`
    Expected Result: >= 15 tests, 0 failures
    Evidence: .sisyphus/evidence/task-11-full-suite.txt
  ```

  **Commit**: YES
  - Message: `feat(domains): add knowledge management tools (TDD)`
  - Files: `src/servicenow_mcp/tools/domains/knowledge.py`, `tests/domains/test_knowledge.py`
  - Pre-commit: `uv run pytest tests/domains/test_knowledge.py -v`

- [ ] 12. Domain-Aware Preset Packages + Integration Tests

  **What to do**:
  Now that all domain modules exist, update preset packages to include domain groups and add integration tests.

  1. Update `PACKAGE_REGISTRY` in `packages.py` - add domain-specific packages:
     - `"incident_management"`: `["introspection", "utility", "domain_incident", "debug"]`
     - `"change_management"`: `["introspection", "utility", "domain_change", "changes"]`
     - `"cmdb"`: `["introspection", "relationships", "utility", "domain_cmdb"]`
     - `"problem_management"`: `["introspection", "utility", "domain_problem", "debug"]`
     - `"request_management"`: `["introspection", "utility", "domain_request"]`
     - `"knowledge_management"`: `["introspection", "utility", "domain_knowledge"]`
     - Update `"itil"` to include domain groups: `["introspection", "relationships", "metadata", "changes", "debug", "documentation", "utility", "domain_incident", "domain_change", "domain_problem", "domain_request"]`
     - Update `"full"` to include ALL domain groups
  2. Write integration-level tests that:
     - Verify each preset loads the correct number of tools
     - Verify tool name uniqueness across all loaded tools (no collisions)
     - Verify comma-separated syntax with domain groups works
     - Verify backward compatibility: `full` still loads all existing tools PLUS domain tools
  3. Update `list_tool_packages` tool to show domain packages in its output

  **Must NOT do**:
  - Do not modify any domain module code
  - Do not change generic tools

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Integration testing requires understanding all modules and their interactions
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (needs all prior tasks)
  - **Parallel Group**: Wave 4 (solo)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 4, 7-11

  **References**:

  **Pattern References**:
  - `src/servicenow_mcp/packages.py` - Registry to update
  - `src/servicenow_mcp/server.py` - `create_mcp_server()` for integration test pattern

  **Test References**:
  - `tests/test_packages.py` - Existing package tests to extend
  - `tests/test_server.py` (if exists) - Server integration tests

  **WHY Each Reference Matters**:
  - Package registry is the ONLY file to update for definitions
  - Server module shows how to create a full MCP instance for integration testing
  - Need to verify actual tool registration, not just registry entries

  **Acceptance Criteria**:

  - [ ] All 6 domain-specific packages in registry
  - [ ] `"itil"` preset includes domain groups
  - [ ] `"full"` preset includes domain groups
  - [ ] `uv run pytest tests/test_packages.py -v` -> PASS
  - [ ] Tool name uniqueness test passes for every package
  - [ ] `uv run pytest -v` -> ALL tests pass (entire suite)
  - [ ] `uv run ruff check .` -> clean
  - [ ] `uv run mypy src/` -> clean

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: incident_management package loads correct tools
    Tool: Bash (uv run python -c)
    Preconditions: All domain modules + packages updated
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; groups = get_package('incident_management'); print(groups); assert 'domain_incident' in groups"`
    Expected Result: domain_incident is in the group list
    Evidence: .sisyphus/evidence/task-12-incident-pkg.txt

  Scenario: full package includes all domain groups
    Tool: Bash (uv run python -c)
    Steps:
      1. Run `uv run python -c "from servicenow_mcp.packages import get_package; groups = get_package('full'); domain_groups = [g for g in groups if g.startswith('domain_')]; print(f'Domain groups: {domain_groups}'); assert len(domain_groups) == 6"`
    Expected Result: Exactly 6 domain groups in full package
    Evidence: .sisyphus/evidence/task-12-full-pkg.txt

  Scenario: No tool name collisions across any package
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest tests/test_packages.py -v -k "uniqueness or collision"`
    Expected Result: All uniqueness tests pass
    Evidence: .sisyphus/evidence/task-12-uniqueness.txt

  Scenario: Full test suite passes
    Tool: Bash (uv run pytest)
    Steps:
      1. Run `uv run pytest -v --tb=short`
    Expected Result: All tests pass, 0 failures
    Failure Indicators: Any test failure
    Evidence: .sisyphus/evidence/task-12-full-suite.txt
  ```

  **Commit**: YES
  - Message: `feat(packages): add domain-aware preset packages + integration tests`
  - Files: `src/servicenow_mcp/packages.py`, `tests/test_packages.py`
  - Pre-commit: `uv run pytest -v`

---

## Final Verification Wave (MANDATORY - after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection -> fix -> re-run.

- [ ] F1. **Plan Compliance Audit** - `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, import module, check tool names). For each "Must NOT Have": search codebase for forbidden patterns (cross-domain imports, JSON string params, base class abstractions) - reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** - `unspecified-high`
  Run `uv run ruff check .` + `uv run mypy src/` + `uv run pytest`. Review all domain tool files for: `as any`, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify all domain tools use `safe_tool_call` pattern. Verify `_write_gate` extracted, not duplicated.
  Output: `Ruff [PASS/FAIL] | Mypy [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Package Loading QA** - `unspecified-high`
  Start server with EVERY preset package name and verify correct tool counts. Test comma-separated configs: `introspection,domain_incident`, `domain_incident,domain_change,utility`, edge cases (empty, duplicates, invalid names). Verify backward compat: `full`, `introspection_only`, `none` work identically.
  Output: `Presets [N/N pass] | Comma [N/N pass] | Backward Compat [PASS/FAIL] | VERDICT`

- [ ] F4. **Scope Fidelity Check** - `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 - everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes. Verify tool counts per domain (max 7).
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **T1**: `refactor(config): derive _VALID_PACKAGES from package registry` - config.py, tests/test_config.py
- **T2**: `refactor(policy): extract _write_gate to shared location` - policy.py, developer.py, testing.py, tests
- **T3**: `feat(packages): add preset package combos (itil, developer, readonly, analyst)` - packages.py, tests/test_packages.py
- **T4**: `feat(config): support comma-separated group selection` - config.py, packages.py, tests
- **T5**: `feat(domains): scaffold domain tools package + server entries` - tools/domains/__init__.py, server.py
- **T6**: `feat(domains): add incident management tools (TDD)` - tools/domains/incident.py, tests/domains/test_incident.py
- **T7**: `feat(domains): add change management tools (TDD)` - tools/domains/change.py, tests/domains/test_change.py
- **T8**: `feat(domains): add CMDB tools (TDD)` - tools/domains/cmdb.py, tests/domains/test_cmdb.py
- **T9**: `feat(domains): add problem management tools (TDD)` - tools/domains/problem.py, tests/domains/test_problem.py
- **T10**: `feat(domains): add request management tools (TDD)` - tools/domains/request.py, tests/domains/test_request.py
- **T11**: `feat(domains): add knowledge management tools (TDD)` - tools/domains/knowledge.py, tests/domains/test_knowledge.py
- **T12**: `feat(packages): add domain-aware preset packages + integration tests` - packages.py, tests

---

## Success Criteria

### Verification Commands
```bash
uv run ruff check .                    # Expected: clean
uv run mypy src/                       # Expected: clean
uv run pytest                          # Expected: all pass
uv run pytest tests/domains/ -v        # Expected: all domain tests pass
uv run python -c "from servicenow_mcp.packages import get_package; print(get_package('itil'))"  # Expected: list of groups
uv run python -c "from servicenow_mcp.packages import get_package; print(get_package('incident_management'))"  # Expected: includes domain_incident
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass (existing + new)
- [ ] All 6 domain modules independently registrable
- [ ] Tool name uniqueness verified across all modules
- [ ] Backward compatibility confirmed
