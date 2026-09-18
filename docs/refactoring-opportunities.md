# Refactoring opportunities

Reviewed on 2026-09-18 after the dead-code cleanup. The changes below are
recommended follow-ups; they are not part of the completed cleanup.

## 1. Separate Flow fetching from response assembly

**Priority: high. Scope: medium.**

[`_action_inspect`](../src/servicenow_mcp/tools/flow.py) is 406 lines. It resolves
the flow, fetches selected datasets and dependent metadata, tracks truncation,
builds canvas nodes and triggers, creates the contract, and selects response
sections. Those responsibilities share many intermediate variables, which makes
it difficult to change one section without changing its fetch or limit behavior.

Extract the existing pure canvas/contract projection helpers into a focused
module first. Then separate dataset loading and dependency completion from
response projection, with one explicit data structure carrying rows, probe
limits, and completeness metadata between the two stages. Keep policy and
public action dispatch in `tools/flow.py`.

Preserve section-driven fetching, V1/V2 fallback behavior, canonical flow IDs,
stage sources, continuation instructions, and warnings when dependencies are
incomplete. Validate with `tests/test_flow.py`,
`tests/test_flow_lookup_boundaries.py`, and `tests/test_flow_values.py`. Compare
HTTP call counts as well as response data; a structurally identical response
can still hide an expensive extra query.

## 2. Extract submitted-variable answer assembly

**Priority: high. Scope: small to medium.**

[`_ritm_variables`](../src/servicenow_mcp/tools/analysis.py) is 182 lines. It
fetches the requested item, probes multi-row variable sets, joins submitted
answers to definitions, masks values, reports missing records, and builds
pagination. The definition lookup and answer assembly can be separated without
changing the public `analysis` tool.

Keep HTTP operations and pagination in the action handler. Extract a pure
assembler accepting link, option, and definition rows and returning entries
and warnings. Keep missing-definition masking and incomplete-metadata masking
inside that assembler so every constructed answer follows the same rule.

Preserve duplicate links, orphaned answers, list-collector/reference handling,
and the rule that multi-row variable payloads are not fetched. Validate against
`tests/test_analysis.py`, especially masking and missing-definition scenarios.

## 3. Break investigations into named checks

**Priority: medium. Scope: small per investigation.**

The `run` functions in
[`table_health.py`](../src/servicenow_mcp/investigations/table_health.py),
[`stale_automations.py`](../src/servicenow_mcp/investigations/stale_automations.py),
and [`performance_bottlenecks.py`](../src/servicenow_mcp/investigations/performance_bottlenecks.py)
are 122, 113, and 108 lines respectively. Each combines several independent
checks and constructs its own findings directly alongside query code.

Extract named checks or pure finding builders within each module. For example,
stale flows, disabled rules, disabled includes, and scheduled jobs already form
four clear blocks in `stale_automations.run`. Keep orchestration and the final
result envelope in `run`; retain each check's table policy, field projection,
date bounds, and masking. These checks do not need a new generic framework.

Use `tests/test_investigation_workflows.py` to preserve the real workflow
behavior. Keep the existing query ordering/concurrency during extraction;
changing concurrency or investigation defaults should be a separate change.

## Cleanup completed

- Removed 13 unused internal handler parameters and their forwarded arguments.
- Removed unused settings/authentication fields from choice and audit registries.
- Removed the second choice-cache state and its test-only `None` fallback.
  The metadata cache now owns the cached value and the loader always returns it.
- Reused the cache insertion method for loaded and seeded values, keeping TTL,
  LRU eviction, invalidation version checks, and telemetry behavior together.
- Shared catalog item validation, write gating, and variable parsing between
  `order_now` and `add_to_cart`, while preserving their different target tables.
- Removed unused dictionary re-exports and outdated references to retired tools.
- Corrected `config.pyi` to accept configurable OAuth scopes. The stub is
  still needed by the optional basedpyright checker for environment-based
  Settings construction; ty can also use the runtime model directly.

Static-analysis reports were checked against actual references. MCP-decorated
tool functions, Pydantic validators, telemetry fields, and active fallback paths
were retained. Uncovered code was not treated as evidence of dead code.
