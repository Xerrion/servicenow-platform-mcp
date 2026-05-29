# Investigations

The `investigate` tool dispatches structured, read-only analysis modules against a ServiceNow instance. It accepts three actions - `run`, `explain`, and `describe` - and routes by the `name` parameter (not `investigation`).

## Actions

### describe

Returns the registry without platform I/O.

```json
{"action": "describe"}
```

Response: `{"investigations": ["acl_conflicts", "deprecated_apis", ...]}` (sorted).

Pass `name` to get a single module's parameter schema:

```json
{"action": "describe", "name": "error_analysis"}
```

Response includes `name`, `description` (first line of the module docstring), and `params`.

### run

Execute an investigation by name.

```json
{"action": "run", "name": "stale_automations", "params": "{\"stale_days\": 14}"}
```

`params` is a JSON string. When the params dict contains a `table` key, the dispatcher validates the identifier and checks table access before calling the module.

### explain

Provide rich context for a finding's `element_id`. The format is `"table:sys_id"`.

```json
{"action": "explain", "element_id": "sys_script:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"}
```

The dispatcher trial-runs every registered module's `explain` function. Modules decline by returning `{"error": "..."}` (single key). The first non-decline wins. If all decline: `{"error": "No registered investigation can explain element_id '...'"}`.

Bare-table forms are not supported.

---

## stale_automations

Find stuck flows, disabled scripts, and stale scheduled jobs.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `stale_days` | int | 30 | Days of inactivity to qualify as stale. Minimum 1. |
| `limit` | int | 20 | Max findings per category. |

### Tables queried

- `sys_flow_context` - flows stuck in IN_PROGRESS, created more than `stale_days` ago.
- `sys_script` - business rules with `active=false`.
- `sys_script_include` - script includes with `active=false`.
- `sys_trigger` - repeating triggers not updated in `stale_days`.

### Return shape

`investigation`, `finding_count`, `findings`, `params`.

Finding categories: `stuck_flow`, `disabled_business_rule`, `disabled_script_include`, `stale_scheduled_job`. Each finding carries `category`, `element_id` (`table:sys_id`), `name`, `detail`.

### When to reach for it

Platform hygiene reviews. Identify automations that are abandoned, stuck, or consuming resources without value.

### Caveats

- Disabled business rules are not inherently stale - some are intentionally deactivated during maintenance windows.
- `sys_trigger` uses `sys_updated_on` as a staleness proxy; a trigger could be stale even if recently touched by a metadata update.

---

## deprecated_apis

Scan scripts for deprecated API usage patterns via the Code Search API.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 20 | Max findings per pattern. |

### Tables queried

Uses `client.code_search()`. Results may come from: `sys_script_include`, `sys_script`, `sys_ws_operation`, `sys_ui_script`, `sys_processor`, `sp_widget`.

Patterns searched: `Packages.`, `gs.include(`, `current.setWorkflow(false)`, `GlideRecordSecure(`, `g_form.flash(`.

### Return shape

`investigation`, `finding_count`, `findings`, `params`, `patterns_searched`.

Each finding: `pattern`, `element_id`, `name`, `table`, `detail`.

### When to reach for it

Pre-upgrade readiness checks. Surface scripts using APIs that may be removed or behave differently in newer ServiceNow releases.

### Caveats

- Depends on the Code Search API being enabled on the instance. Unavailable patterns are silently skipped.
- Pattern matching is substring-based; false positives are possible (e.g., `Packages.` in a comment).
- The pattern list is hardcoded and does not cover all deprecated APIs.

---

## table_health

Comprehensive health report for a single table: record count, automation density, and recent errors.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `table` | str | (required) | Table name to analyze. |
| `hours` | int or None | None | Lookback window in hours. Omit to query all history. |

### Tables queried

Runs 6 queries in parallel:

- Target table (via Stats API) - total record count.
- `sys_script` - active business rules where `collection` matches.
- `sys_script_client` - active client scripts where `table` matches.
- `sys_security_acl` - ACLs where name equals or starts with the table.
- `sys_ui_policy` - active UI policies for the table.
- `syslog` - error-level entries (`level=0`) with `source LIKE table`, limited to 20.

Automation queries capped at `INTERNAL_QUERY_LIMIT` (1000).

### Return shape

`investigation`, `finding_count`, `findings`, `table`, `hours`, `record_count`, `automation`, `recent_errors`, `health_indicators`.

`automation` contains `business_rules` (count + records), `client_scripts` (count + records), `acl_count`, `ui_policies` (count + records). Health indicator thresholds: >10 business rules, >20 ACLs, any recent syslog errors.

### When to reach for it

Before modifying a table's automation stack. Understand the density of rules, scripts, and policies before adding more.

### Caveats

- The `hours` filter applies to all automation queries (via `sys_updated_on`) and syslog (via `sys_created_on`). Omitting it queries full history, which may be slow on large instances.
- Syslog `source LIKE table` is a heuristic; it may match unrelated sources containing the table name as a substring.

---

## acl_conflicts

Detect overlapping ACL rules for a table - multiple ACLs sharing the same name and operation.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `table` | str | (required) | Table name to check. |

### Tables queried

- `sys_security_acl` - all ACLs where `name` equals the table or starts with `table.` (field-level ACLs). Fetches `sys_id`, `name`, `operation`, `condition`, `script`, `active`. Capped at `INTERNAL_QUERY_LIMIT` (1000).

### Return shape

`investigation`, `finding_count`, `findings`, `table`, `total_acls_checked`.

Each finding: `category` (`acl_conflict`), `name`, `operation`, `count`, `acls` (list of overlapping ACL details), `detail`.

A conflict is reported when 2+ ACLs share the same `name|operation` key.

### When to reach for it

Debugging unexpected access control behavior. When users report inconsistent permissions on a table, this surfaces duplicate ACL rules that may evaluate contradictory conditions.

### Caveats

- Reports any duplication as a conflict, even when both ACLs have identical conditions (harmless but wasteful).
- Does not evaluate ACL conditions or scripts for semantic contradiction - only structural overlap (same name and operation).
- The investigation does not distinguish active from inactive ACLs when grouping; an inactive duplicate is still flagged.

---

## error_analysis

Analyze and cluster syslog errors by source.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `hours` | int | 24 | Lookback window in hours. Minimum 1. |
| `source` | str or None | None | Optional source-name filter (LIKE match). |
| `limit` | int | 100 | Max log entries to fetch. |

### Tables queried

- `syslog` - error-level entries (`level=0`) within the lookback window. Ordered by `sys_created_on` descending.

### Return shape

`investigation`, `finding_count`, `findings`, `params`, `total_errors`.

Each finding: `category` (`error_cluster`), `source`, `frequency`, `first_seen`, `last_seen`, `sample_messages` (up to 3), `element_id`.

### When to reach for it

Initial triage of error spikes. Quickly see which sources are producing the most errors and get sample messages without paging through syslog manually.

### Caveats

- Clustering is by source field only - no message deduplication or similarity grouping.
- The `limit` cap applies to total fetched rows, not per-source. A noisy source may consume most of the budget.
- `syslog` is a large table; wide lookback windows on busy instances may time out.

---

## slow_transactions

Find slow transactions via ServiceNow performance pattern tables.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `hours` | int | 24 | Lookback window in hours. Minimum 1. |
| `limit` | int | 20 | Max findings per table. |
| `categories` | csv or None | None | Comma-separated category filter (e.g., `"slow_query,mutex_contention"`). |

### Tables queried

| Table | Category |
|-------|----------|
| `sys_query_pattern` | `slow_query` |
| `sys_transaction_pattern` | `slow_transaction` |
| `sys_script_pattern` | `slow_script` |
| `sys_mutex_pattern` | `mutex_contention` |
| `sysevent_pattern` | `event_pattern` |
| `sys_interaction_pattern` | `slow_interaction` |
| `syslog_cancellation` | `cancelled_transaction` |

Pattern tables are queried with `window_endISEMPTY^window_startISEMPTY` plus a time filter. `syslog_cancellation` uses only the time filter.

### Return shape

`investigation`, `finding_count`, `findings`, `params`, `tables_queried`.

Each finding: `category`, `table`, `element_id`, `name`, `count`, `detail`, `sys_created_on`.

### When to reach for it

Performance troubleshooting. Identify which query patterns, scripts, or mutex waits are currently active on the instance.

### Caveats

- Not all instances have all 7 pattern tables populated. Missing or inaccessible tables are silently skipped - you will not see an error, just fewer results.
- Pattern tables are ServiceNow internal performance tracking; their availability and schema may vary across platform versions.
- No fields are specified in the query (all fields returned), which may produce large payloads.

---

## performance_bottlenecks

Find performance bottlenecks - tables with excessive automation, frequently-running jobs, and long-running flows.

### Inputs

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `hours` | int or None | None | Lookback window in hours. Omit to query all history. |
| `limit` | int | 20 | Max findings per category. |

### Tables queried

- `sys_script` - active business rules, grouped by `collection` (table). Reports tables exceeding a threshold of 10 active BRs. Capped at `INTERNAL_QUERY_LIMIT` (1000).
- `sysauto_script` - active scheduled scripts (scripted jobs).
- `sys_flow_context` - flows still in IN_PROGRESS state.

### Return shape

`investigation`, `finding_count`, `findings`, `params`.

Finding categories: `heavy_automation`, `frequent_job`, `long_running_flow`. Each finding carries `category`, `element_id`, `name`, `detail`, and category-specific fields (`br_count`, `run_type`).

### When to reach for it

Instance-wide performance posture. Unlike `slow_transactions` (which reads platform-internal pattern tables), this checks automation density and job scheduling - things you can directly act on.

### Caveats

- The heavy automation check counts all active BRs, not just problematic ones. A table with 15 well-optimized BRs will be flagged.
- Scheduled jobs are reported regardless of frequency; a daily job and a per-minute job get equal weight.
- `element_id` for `heavy_automation` findings is just the table name (not `table:sys_id` format), which affects how `explain` dispatches for these findings.

---

## Runtime Discovery

You do not need to memorize this page. The `investigate` tool itself exposes the registry:

```json
{"action": "describe"}
```

Returns the sorted list of all registered investigation names.

```json
{"action": "describe", "name": "slow_transactions"}
```

Returns that module's parameter schema (types, defaults, descriptions) - the same information documented above, available at runtime without platform I/O.

---

## See Also

- [[Tool-Reference]] - the `investigate` tool entry
- [[Safety-and-Policy]] - field masking applies to all investigation results via `mask_sensitive_fields`
