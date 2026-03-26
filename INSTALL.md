# ServiceNow MCP Server - AI Installation Guide

> **For AI Agents:** If you're an AI agent helping set up this MCP server, follow the instructions below to configure the server for your user. You'll need the ServiceNow instance URL, username, and password from your user.

This is a developer and debug-focused MCP server for ServiceNow platform introspection, change intelligence, debugging, investigations, and documentation generation. It provides a comprehensive suite of tools organized into multiple tool groups covering schema exploration, record management, ITIL processes, CMDB, service catalog, and more.

---

## Installation

Run directly with uvx (no install required):

```
uvx servicenow-devtools-mcp
```

Or install via pip:

```
pip install servicenow-devtools-mcp
```

---

## Required Environment Variables

- `SERVICENOW_INSTANCE_URL` - Full URL of the ServiceNow instance (must start with `https://`), e.g. `https://dev12345.service-now.com`
- `SERVICENOW_USERNAME` - ServiceNow user with admin or appropriate roles
- `SERVICENOW_PASSWORD` - Password for the user

---

## Optional Environment Variables

- `MCP_TOOL_PACKAGE` - Which tools to load (default: `"full"`). 14 preset packages available - see Tool Packages section.
- `SERVICENOW_ENV` - Environment label: `"dev"` (default), `"test"`, `"staging"`, `"prod"`. Write operations are blocked when set to `"prod"` or `"production"`.
- `MAX_ROW_LIMIT` - Max records per query (default: 100, max: 10000).
- `LARGE_TABLE_NAMES_CSV` - Tables requiring date-bounded queries (default: `syslog,sys_audit,sys_log_transaction,sys_email_log`).
- `SCRIPT_ALLOWED_ROOT` - Constrains `script_path` file reads to this directory tree. Required when using artifact_write tools with `script_path`. File must be UTF-8, <= 1 MB.
- `SENTRY_DSN` - Sentry DSN for error reporting. Sentry activates when this is set.
- `SENTRY_ENVIRONMENT` - Sentry environment label. Falls back to `SERVICENOW_ENV` when empty.

---

## MCP Client Configuration (stdio transport)

```json
{
  "command": "uvx",
  "args": ["servicenow-devtools-mcp"],
  "env": {
    "SERVICENOW_INSTANCE_URL": "<instance_url>",
    "SERVICENOW_USERNAME": "<username>",
    "SERVICENOW_PASSWORD": "<password>",
    "MCP_TOOL_PACKAGE": "full",
    "SERVICENOW_ENV": "dev"
  }
}
```

---

## Available Tools

### Table

`table_describe`, `table_query`, `table_aggregate`, `build_query`

Describe table schema with enriched metadata (sys_db_object + sys_documentation), query with encoded queries, compute aggregate stats, and build structured queries. The `build_query` tool returns a reusable `query_token` that can be passed to other query-accepting tools.

### Record

`record_get`, `rel_references_to`, `rel_references_from`

Fetch records by sys_id, find what references a record, and find what a record references.

### Attachment

`attachment_list`, `attachment_get`, `attachment_download`, `attachment_download_by_name`

List attachment metadata, fetch a single attachment record, and download content as `content_base64`.

### Attachment Write

`attachment_upload`, `attachment_delete`

Upload attachments with `content_base64` and delete attachments by sys_id.

### Metadata

`meta_list_artifacts`, `meta_get_artifact`, `meta_find_references`, `meta_what_writes`

List and inspect platform artifacts (business rules, script includes, etc.), find cross-references across script tables, and find writers to a table.

### Change Intelligence

`changes_updateset_inspect`, `changes_diff_artifact`, `changes_last_touched`, `changes_release_notes`

Inspect update sets, diff artifact versions, view audit trail, and generate release notes.

### Debug & Trace

`debug_trace`, `debug_flow_execution`, `debug_email_trace`, `debug_integration_health`, `debug_importset_run`, `debug_field_mutation_story`

Build event timelines, inspect flow executions, trace emails, check integration errors, inspect import sets, and trace field mutations.

### Record Write

`record_create`, `record_preview_create`, `record_update`, `record_preview_update`, `record_delete`, `record_preview_delete`, `record_apply`

Create, update, and delete records directly or via a preview-then-apply confirmation pattern.

### Artifact Write

`artifact_create`, `artifact_update`

Create and update platform artifacts (business rules, script includes, client scripts, etc.) with optional local script file injection via `script_path`.

### Investigations

`investigate_run`, `investigate_explain`

Modules: `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, `performance_bottlenecks`.

### Documentation

`docs_logic_map`, `docs_artifact_summary`, `docs_test_scenarios`, `docs_review_notes`

Generate automation maps, artifact summaries with dependencies, test scenario suggestions, and code review findings.

### Workflow Analysis

`workflow_contexts`, `workflow_map`, `workflow_status`, `workflow_activity_detail`, `workflow_version_list`

List workflow contexts for a record, map workflow structure, inspect execution status, and view activity details.

### Flow Designer

`flow_list`, `flow_get`, `flow_map`, `flow_action_detail`, `flow_execution_list`, `flow_execution_detail`, `flow_snapshot_list`, `workflow_migration_analysis`

List, inspect, and map Flow Designer flows. View action details, execution history, published snapshots, and analyze legacy workflows for migration readiness.

### Incident Management

`incident_list`, `incident_get`, `incident_create`, `incident_update`, `incident_resolve`, `incident_add_comment`

Full incident lifecycle: list, fetch, create, update, resolve, and add comments/work notes.

### Change Management

`change_list`, `change_get`, `change_create`, `change_update`, `change_tasks`, `change_add_comment`

Manage change requests: list, fetch, create, update, view tasks, and add comments/work notes.

### Problem Management

`problem_list`, `problem_get`, `problem_create`, `problem_update`, `problem_root_cause`

Problem lifecycle: list, fetch, create, update, and document root cause analysis.

### CMDB

`cmdb_list`, `cmdb_get`, `cmdb_relationships`, `cmdb_classes`, `cmdb_health`

Browse CIs, inspect relationships, list CI classes, and check CMDB health by operational status.

### Request Management

`request_list`, `request_get`, `request_items`, `request_item_get`, `request_item_update`

Manage service requests and RITMs: list, fetch, view items, and update request items.

### Knowledge Management

`knowledge_search`, `knowledge_get`, `knowledge_create`, `knowledge_update`, `knowledge_feedback`

Search, read, create, and update knowledge articles and submit feedback/ratings.

### Service Catalog

`sc_catalogs_list`, `sc_catalog_get`, `sc_categories_list`, `sc_category_get`, `sc_items_list`, `sc_item_get`, `sc_item_variables`, `sc_order_now`, `sc_add_to_cart`, `sc_cart_get`, `sc_cart_submit`, `sc_cart_checkout`

Browse catalogs, categories, and items. View item variables, order directly, manage cart, and checkout.

### Core

`list_tool_packages`

List available tool packages and their contents.

---

## Tool Packages

The server supports 14 preset tool packages to control which tools are loaded. The `full` package (default) loads all standard tool groups. Use `list_tool_packages` to see available packages and their contents at runtime.

| Package | Description |
|---|---|
| `full` (default) | All standard tool groups |
| `itil` | ITIL process tools (incidents, changes, problems, requests + platform tools) |
| `developer` | Development-focused (table, record, attachments, debug, investigations, workflows) |
| `readonly` | Read-only operations only |
| `analyst` | Analysis and reporting (table, record, attachments, investigations, docs, workflows) |
| `incident_management` | Incident lifecycle with supporting tools |
| `problem_management` | Problem lifecycle with supporting tools |
| `change_management` | Change request management with change intelligence |
| `request_management` | Request and RITM management |
| `cmdb` | CMDB management with relationships |
| `knowledge_management` | Knowledge base tools |
| `service_catalog` | Service catalog tools |
| `core_readonly` | Minimal read-only core (table, record, attachment, metadata) |
| `none` | Only `list_tool_packages` - for testing |

Custom packages are also supported via comma-separated group names, e.g. `MCP_TOOL_PACKAGE="table,record,debug,domain_incident"`.

---

## Safety Guardrails

- **Table deny list** - Sensitive tables (`sys_user_has_password`, `oauth_credential`, `sys_credentials`, and others) are blocked from queries.
- **Sensitive fields** - Password, token, and secret fields are masked with `***MASKED***` in responses.
- **Row limits** - User-supplied limit parameters are capped at `MAX_ROW_LIMIT` (default 100).
- **Large tables** - `syslog`, `sys_audit`, and other high-volume tables require date-bounded filters.
- **Write gating** - All write operations are blocked when `SERVICENOW_ENV` is set to `"prod"` or `"production"`. There is no override.
- **Attachment limits** - Uploads accept `content_base64`; download tools return `content_base64`. Max attachment size is 10 MB.
- **Mandatory field validation** - Record creation validates all required fields before submission.
- **Standardized responses** - Tools return TOON-serialized envelopes with `correlation_id`, `status`, `data`, and optionally `pagination` and `warnings`.

These guardrails operate on a best-effort basis and do not guarantee complete prevention of unintended actions. Always validate behavior in your specific environment and follow your organization's security policies.

---

## Response Format

All tools return responses in TOON format (not JSON). When parsing tool output programmatically, use `toon_decode` from the `toon-format` package, not `json.loads`.
