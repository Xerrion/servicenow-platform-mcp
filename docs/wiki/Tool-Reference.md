# Tool Reference

Complete reference for all tools provided by the ServiceNow Platform MCP server. Tools are organized into groups that can be selectively loaded via [[Tool-Packages]].

All tools return responses in TOON format (not JSON) with a standardized envelope containing `correlation_id`, `status`, `data`, and optionally `pagination` and `warnings`. See [[Architecture]] for details on the response format.

For security guardrails that apply across all tools, see [[Safety-and-Policy]].

---

## Always-On Tool

The `list_tool_packages` tool is always available, regardless of which tool package is configured - even with `MCP_TOOL_PACKAGE="none"`.

| Tool | Description | Key Parameters |
|---|---|---|
| `list_tool_packages` | List available tool packages and their contents | - |

---

## Table (4 tools)

Describe table schemas with enriched metadata, query records with encoded queries, compute aggregate statistics, and build structured queries. The `build_query` tool returns a reusable `query_token` that can be passed to other query-accepting tools.

| Tool | Description | Key Parameters |
|---|---|---|
| `table_describe` | Describe a table's schema with enriched metadata from sys_db_object and sys_documentation | `table` |
| `table_query` | Query records from a table using an encoded query | `table`, `query_token`, `fields`, `limit`, `offset`, `order_by`, `display_values` |
| `table_aggregate` | Compute aggregate statistics (count, avg, min, max, sum) for a table | `table`, `query_token`, `group_by`, `avg_fields`, `min_fields`, `max_fields`, `sum_fields` |
| `build_query` | Build a structured query from conditions and return a reusable query_token | `conditions` (JSON array) |

---

## Record (3 tools)

Fetch records by sys_id and explore referential relationships in both directions.

| Tool | Description | Key Parameters |
|---|---|---|
| `record_get` | Fetch a single record by sys_id with optional field selection | `table`, `sys_id`, `fields`, `display_values` |
| `rel_references_to` | Find all records that reference a given record (inbound references) | `table`, `sys_id` |
| `rel_references_from` | Find all records that a given record references (outbound references) | `table`, `sys_id` |

---

## Attachment (4 tools)

List attachment metadata, fetch individual attachment records, and download attachment content as base64.

| Tool | Description | Key Parameters |
|---|---|---|
| `attachment_list` | List attachments for a table or record with filtering and pagination | `table_name`, `table_sys_id`, `file_name`, `limit`, `offset`, `order_by` |
| `attachment_get` | Fetch a single attachment metadata record by sys_id | `sys_id` |
| `attachment_download` | Download attachment content as base64 by sys_id | `sys_id` |
| `attachment_download_by_name` | Download attachment content as base64 by table, record, and file name | `table_name`, `table_sys_id`, `file_name` |

---

## Attachment Write (2 tools)

Upload attachments with base64-encoded content and delete attachments by sys_id. Write operations are subject to [[Safety-and-Policy|write gating]].

| Tool | Description | Key Parameters |
|---|---|---|
| `attachment_upload` | Upload an attachment with base64-encoded content | `table_name`, `table_sys_id`, `file_name`, `content_base64`, `content_type`, `encryption_context`, `creation_time` |
| `attachment_delete` | Delete an attachment by sys_id | `sys_id` |

---

## Metadata (4 tools)

List and inspect platform artifacts (business rules, script includes, client scripts, etc.), find cross-references across script tables, and discover which automations write to a table.

| Tool | Description | Key Parameters |
|---|---|---|
| `meta_list_artifacts` | List platform artifacts of a given type with optional query filtering | `artifact_type`, `query_token`, `limit` |
| `meta_get_artifact` | Fetch a single platform artifact by type and sys_id | `artifact_type`, `sys_id` |
| `meta_find_references` | Find cross-references to a target across script tables | `target`, `limit` |
| `meta_what_writes` | Find automations that write to a specific table and field | `table`, `field` |

---

## Artifact Write (2 tools)

Create and update platform artifacts (business rules, script includes, client scripts, etc.) with optional local script file injection via `script_path`. Write operations are subject to [[Safety-and-Policy|write gating]].

| Tool | Description | Key Parameters |
|---|---|---|
| `artifact_create` | Create a new platform artifact with optional local script file | `artifact_type`, `data`, `script_path` |
| `artifact_update` | Update an existing platform artifact with optional local script file | `artifact_type`, `sys_id`, `changes`, `script_path` |

---

## Change Intelligence (4 tools)

Inspect update sets, diff artifact versions, view audit trails, and generate release notes.

| Tool | Description | Key Parameters |
|---|---|---|
| `changes_updateset_inspect` | Inspect an update set and list its customer updates | `update_set_id` |
| `changes_diff_artifact` | Diff an artifact's current version against its update set version | `table`, `sys_id` |
| `changes_last_touched` | View the audit trail of recent changes to a record | `table`, `sys_id`, `limit` |
| `changes_release_notes` | Generate release notes from an update set's contents | `update_set_id`, `format` |

---

## Debug and Trace (6 tools)

Build event timelines, inspect flow executions, trace email delivery, check integration health, inspect import sets, and trace field-level mutations.

| Tool | Description | Key Parameters |
|---|---|---|
| `debug_trace` | Build an event timeline for a record from system logs | `record_sys_id`, `table`, `minutes` |
| `debug_flow_execution` | Inspect a flow execution context and its action outputs | `context_id` |
| `debug_email_trace` | Trace email delivery for a record | `record_sys_id` |
| `debug_integration_health` | Check integration health by inspecting ECC queue errors | `kind`, `hours` |
| `debug_importset_run` | Inspect an import set run and its row-level results | `import_set_sys_id` |
| `debug_field_mutation_story` | Trace field-level mutations from sys_audit | `table`, `sys_id`, `field`, `limit` |

---

## Record Write (7 tools)

Create, update, and delete records directly or via a preview-then-apply confirmation pattern. The preview tools return a `preview_token` that can be passed to `record_apply` for confirmation. Write operations are subject to [[Safety-and-Policy|write gating]].

| Tool | Description | Key Parameters |
|---|---|---|
| `record_create` | Create a record directly (no preview) | `table`, `data` |
| `record_preview_create` | Preview a record creation and return a preview_token | `table`, `data` |
| `record_update` | Update a record directly (no preview) | `table`, `sys_id`, `changes` |
| `record_preview_update` | Preview a record update and return a preview_token | `table`, `sys_id`, `changes` |
| `record_delete` | Delete a record directly (no preview) | `table`, `sys_id` |
| `record_preview_delete` | Preview a record deletion and return a preview_token | `table`, `sys_id` |
| `record_apply` | Apply a previously previewed operation using its preview_token | `preview_token` |

---

## Investigations (2 tools)

Run automated investigations and explain individual findings. Seven investigation modules are available: `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, and `performance_bottlenecks`.

| Tool | Description | Key Parameters |
|---|---|---|
| `investigate_run` | Run an automated investigation module with parameters | `investigation`, `params` |
| `investigate_explain` | Explain a specific finding from an investigation result | `investigation`, `element_id` |

---

## Documentation (4 tools)

Generate automation maps, artifact summaries with dependency analysis, test scenario suggestions, and code review findings.

| Tool | Description | Key Parameters |
|---|---|---|
| `docs_logic_map` | Generate an automation map for a table (business rules, client scripts, etc.) | `table` |
| `docs_artifact_summary` | Generate a summary of a platform artifact with dependency analysis | `artifact_type`, `sys_id` |
| `docs_test_scenarios` | Generate test scenario suggestions for a platform artifact | `artifact_type`, `sys_id` |
| `docs_review_notes` | Generate code review findings for a platform artifact | `artifact_type`, `sys_id` |

---

## Workflow Analysis (5 tools)

List workflow contexts for a record, map workflow structures, inspect execution status, view activity details, and list workflow versions.

| Tool | Description | Key Parameters |
|---|---|---|
| `workflow_contexts` | List workflow contexts for a record | `record_sys_id`, `table`, `state`, `limit` |
| `workflow_map` | Map the structure of a workflow version (activities and transitions) | `workflow_version_sys_id` |
| `workflow_status` | Inspect execution status of a workflow context | `context_sys_id` |
| `workflow_activity_detail` | View details of a specific workflow activity | `activity_sys_id` |
| `workflow_version_list` | List workflow versions with optional filtering | `table`, `active_only`, `limit` |

---

## Flow Designer (8 tools)

List, inspect, and map Flow Designer flows. View action details, execution history, published snapshots, and analyze legacy workflows for migration readiness.

| Tool | Description | Key Parameters |
|---|---|---|
| `flow_list` | List Flow Designer flows with filtering | `table`, `flow_type`, `status`, `active_only`, `limit` |
| `flow_get` | Fetch a Flow Designer flow by sys_id | `flow_sys_id` |
| `flow_map` | Map the structure of a flow (trigger, actions, conditions) | `flow_sys_id` |
| `flow_action_detail` | View details of a specific flow action instance | `action_instance_sys_id` |
| `flow_execution_list` | List flow executions with filtering | `flow_sys_id`, `source_record`, `state`, `limit` |
| `flow_execution_detail` | View detailed execution results for a flow context | `context_id` |
| `flow_snapshot_list` | List published snapshots (versions) of a flow | `flow_sys_id`, `limit` |
| `workflow_migration_analysis` | Analyze a legacy workflow version for Flow Designer migration readiness | `workflow_version_sys_id` |

---

## Incident Management (6 tools)

Full incident lifecycle: list, fetch, create, update, resolve, and add comments or work notes. State and priority parameters accept human-readable labels (e.g., "open", "high") which are automatically resolved to ServiceNow values via the ChoiceRegistry.

| Tool | Description | Key Parameters |
|---|---|---|
| `incident_list` | List incidents with filtering by state, priority, assignment | `state`, `priority`, `assigned_to`, `assignment_group`, `fields`, `limit` |
| `incident_get` | Fetch a single incident by number | `number` |
| `incident_create` | Create a new incident | `short_description`, `urgency`, `impact`, `priority`, `description`, `caller_id`, `assignment_group`, `assigned_to`, `category`, `subcategory` |
| `incident_update` | Update an existing incident | `number`, `short_description`, `urgency`, `impact`, `priority`, `state`, `description`, `assignment_group`, `assigned_to`, `category`, `subcategory` |
| `incident_resolve` | Resolve an incident with close code and notes | `number`, `close_code`, `close_notes` |
| `incident_add_comment` | Add a comment or work note to an incident | `number`, `comment`, `work_note` |

---

## Change Management (6 tools)

Manage change requests: list, fetch, create, update, view associated tasks, and add comments or work notes.

| Tool | Description | Key Parameters |
|---|---|---|
| `change_list` | List change requests with filtering by state, type, risk, assignment | `state`, `type`, `risk`, `assignment_group`, `fields`, `limit` |
| `change_get` | Fetch a single change request by number | `number` |
| `change_create` | Create a new change request | `short_description`, `description`, `type`, `risk`, `assignment_group`, `start_date`, `end_date` |
| `change_update` | Update an existing change request | `number`, `short_description`, `description`, `type`, `risk`, `assignment_group`, `state` |
| `change_tasks` | List tasks associated with a change request | `number` |
| `change_add_comment` | Add a comment or work note to a change request | `number`, `comment`, `work_note` |

---

## Problem Management (5 tools)

Problem lifecycle: list, fetch, create, update, and document root cause analysis.

| Tool | Description | Key Parameters |
|---|---|---|
| `problem_list` | List problems with filtering by state, priority, assignment | `state`, `priority`, `assigned_to`, `assignment_group`, `fields`, `limit` |
| `problem_get` | Fetch a single problem by number | `number` |
| `problem_create` | Create a new problem | `short_description`, `urgency`, `impact`, `priority`, `description`, `assigned_to`, `assignment_group`, `category`, `subcategory` |
| `problem_update` | Update an existing problem | `number`, `short_description`, `urgency`, `impact`, `priority`, `state`, `description`, `assigned_to`, `assignment_group`, `category`, `subcategory` |
| `problem_root_cause` | Document root cause analysis for a problem | `number`, `cause_notes`, `fix` |

---

## CMDB (5 tools)

Browse configuration items, inspect relationships, list CI classes, and check CMDB health by operational status.

| Tool | Description | Key Parameters |
|---|---|---|
| `cmdb_list` | List configuration items with filtering by class and operational status | `ci_class`, `operational_status`, `fields`, `limit` |
| `cmdb_get` | Fetch a single CI by name or sys_id | `name_or_sys_id`, `ci_class` |
| `cmdb_relationships` | Inspect CI relationships (parent, child, upstream, downstream) | `name_or_sys_id`, `direction`, `ci_class` |
| `cmdb_classes` | List available CI classes | `limit` |
| `cmdb_health` | Check CMDB health by analyzing operational status distribution | `ci_class` |

---

## Request Management (5 tools)

Manage service requests and requested items (RITMs): list, fetch, view items, and update request items.

| Tool | Description | Key Parameters |
|---|---|---|
| `request_list` | List service requests with filtering | `state`, `requested_for`, `assignment_group`, `fields`, `limit` |
| `request_get` | Fetch a single service request by number | `number` |
| `request_items` | List requested items (RITMs) for a service request | `number`, `fields`, `limit` |
| `request_item_get` | Fetch a single requested item by number | `number` |
| `request_item_update` | Update a requested item | `number`, `state`, `assignment_group`, `assigned_to` |

---

## Knowledge Management (5 tools)

Search, read, create, and update knowledge articles and submit feedback or ratings.

| Tool | Description | Key Parameters |
|---|---|---|
| `knowledge_search` | Search knowledge articles by query text | `query`, `workflow_state`, `fields`, `limit` |
| `knowledge_get` | Fetch a single knowledge article by number or sys_id | `number_or_sys_id` |
| `knowledge_create` | Create a new knowledge article | `short_description`, `text`, `kb_knowledge_base`, `kb_category`, `workflow_state` |
| `knowledge_update` | Update an existing knowledge article | `number_or_sys_id`, `short_description`, `text`, `workflow_state`, `kb_knowledge_base`, `kb_category` |
| `knowledge_feedback` | Submit feedback or a rating for a knowledge article | `number_or_sys_id`, `rating`, `comment` |

---

## Service Catalog (12 tools)

Browse catalogs, categories, and items. View item variables, order items directly, manage cart contents, and checkout.

| Tool | Description | Key Parameters |
|---|---|---|
| `sc_catalogs_list` | List available service catalogs | `limit`, `text` |
| `sc_catalog_get` | Fetch a single catalog by sys_id | `sys_id` |
| `sc_categories_list` | List categories in a catalog | `catalog_sys_id`, `limit`, `offset`, `top_level_only` |
| `sc_category_get` | Fetch a single category by sys_id | `sys_id` |
| `sc_items_list` | List catalog items with filtering | `limit`, `offset`, `text`, `catalog`, `category` |
| `sc_item_get` | Fetch a single catalog item by sys_id | `sys_id` |
| `sc_item_variables` | List variables (form fields) for a catalog item | `sys_id` |
| `sc_order_now` | Order a catalog item directly (bypasses cart) | `item_sys_id`, `variables` |
| `sc_add_to_cart` | Add a catalog item to the cart | `item_sys_id`, `variables` |
| `sc_cart_get` | View current cart contents | - |
| `sc_cart_submit` | Submit the current cart as a request | - |
| `sc_cart_checkout` | Checkout the current cart (two-step ordering) | - |

---

## Testing (7 tools)

> **Note:** The testing group is disabled in the `full` package. To use these tools, configure a custom package that includes `testing` (e.g., `MCP_TOOL_PACKAGE="full,testing"` or `MCP_TOOL_PACKAGE="table,record,testing"`).

Automated Test Framework (ATF) tools for listing, running, and analyzing tests and test suites.

| Tool | Description | Key Parameters |
|---|---|---|
| `atf_list_tests` | List ATF tests with optional query filtering | `query_token`, `limit`, `fields` |
| `atf_get_test` | Fetch a single ATF test by sys_id | `test_id` |
| `atf_list_suites` | List ATF test suites with optional query filtering | `query_token`, `limit` |
| `atf_get_results` | Fetch test or suite execution results | `test_id`, `suite_id`, `limit` |
| `atf_run_test` | Run an ATF test with optional polling for results | `test_id`, `poll`, `poll_interval`, `max_poll_duration` |
| `atf_run_suite` | Run an ATF test suite with optional polling for results | `suite_id`, `poll`, `poll_interval`, `max_poll_duration` |
| `atf_test_health` | Analyze test or suite execution health over time | `test_id`, `suite_id`, `days`, `limit` |

---

## Next Steps

- [[Tool-Packages]] - Choose the right tool package for your workflow
- [[Safety-and-Policy]] - Security guardrails, table deny list, write gating
- [[Configuration]] - Environment variables and settings reference
- [[Architecture]] - Server internals and data flow patterns
