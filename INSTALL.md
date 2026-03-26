# ServiceNow DevTools MCP Server - Installation Guide

> **For AI Agents:** You are helping your user install and configure the ServiceNow DevTools MCP server.
> Follow each step below in order. Ask the user questions where indicated, then generate the appropriate configuration.

## Step 0: Gather Credentials

Ask the user for their ServiceNow instance details:

1. **What is your ServiceNow instance URL?**
   - Must start with `https://` (e.g., `https://dev12345.service-now.com`)
   - Trailing slash will be stripped automatically

2. **What is your ServiceNow username?**
   - Needs admin or appropriate roles for the tools being used

3. **What is your ServiceNow password?**
   - Will be stored in the MCP client config (environment variable, not committed to source)

## Step 1: Determine Environment

Ask the user:

1. **Is this a production instance?**
   - If **yes** - set `SERVICENOW_ENV=prod` (all write operations will be blocked)
   - If **no** - set `SERVICENOW_ENV=dev` (default, writes allowed)

2. **Do you want all tools or a specific subset?**
   - **All tools** (default) - set `MCP_TOOL_PACKAGE=full` (20 tool groups)
   - **Read-only** - set `MCP_TOOL_PACKAGE=readonly`
   - **ITIL processes** - set `MCP_TOOL_PACKAGE=itil`
   - **Developer tools** - set `MCP_TOOL_PACKAGE=developer`
   - **Specific domain** - options: `incident_management`, `change_management`, `problem_management`, `cmdb`, `request_management`, `knowledge_management`, `service_catalog`
   - **Analyst** - set `MCP_TOOL_PACKAGE=analyst`
   - **Minimal** - set `MCP_TOOL_PACKAGE=core_readonly` (table, record, attachment, metadata only)
   - **Custom** - comma-separated group names (e.g., `table,record,debug,domain_incident`)

## Step 2: Choose MCP Client

Ask the user which MCP client they use, then generate the appropriate configuration file.

### OpenCode

Write to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "servicenow": {
      "type": "local",
      "command": ["uvx", "servicenow-devtools-mcp"],
      "environment": {
        "SERVICENOW_INSTANCE_URL": "<instance_url>",
        "SERVICENOW_USERNAME": "<username>",
        "SERVICENOW_PASSWORD": "<password>",
        "MCP_TOOL_PACKAGE": "<package>",
        "SERVICENOW_ENV": "<env>"
      }
    }
  }
}
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uvx",
      "args": ["servicenow-devtools-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "<instance_url>",
        "SERVICENOW_USERNAME": "<username>",
        "SERVICENOW_PASSWORD": "<password>",
        "MCP_TOOL_PACKAGE": "<package>",
        "SERVICENOW_ENV": "<env>"
      }
    }
  }
}
```

### VS Code / Cursor (Copilot MCP)

Write to `.vscode/mcp.json` in the workspace:

```json
{
  "servers": {
    "servicenow": {
      "command": "uvx",
      "args": ["servicenow-devtools-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "<instance_url>",
        "SERVICENOW_USERNAME": "<username>",
        "SERVICENOW_PASSWORD": "<password>",
        "MCP_TOOL_PACKAGE": "<package>",
        "SERVICENOW_ENV": "<env>"
      }
    }
  }
}
```

### Generic stdio

If the user's client is not listed above, provide the generic command:

```bash
SERVICENOW_INSTANCE_URL=<instance_url> \
SERVICENOW_USERNAME=<username> \
SERVICENOW_PASSWORD=<password> \
MCP_TOOL_PACKAGE=<package> \
SERVICENOW_ENV=<env> \
uvx servicenow-devtools-mcp
```

**Important:** Replace all `<placeholder>` values with the user's actual answers from Steps 0-1 before writing the config.

## Step 3: Optional Configuration

Ask the user if they want to configure any of these optional settings:

1. **Row limit** - Maximum records per query (default: 100, range: 1-10000)
   - Add `"MAX_ROW_LIMIT": "<number>"` to the environment/env block

2. **Large tables** - Tables that require date-bounded queries (default: `syslog,sys_audit,sys_log_transaction,sys_email_log`)
   - Add `"LARGE_TABLE_NAMES_CSV": "<comma_separated_tables>"` to the environment/env block

3. **Script file root** - When using `artifact_create`/`artifact_update` with `script_path`, constrains file reads to a directory tree
   - Add `"SCRIPT_ALLOWED_ROOT": "<absolute_path>"` to the environment/env block

4. **Sentry error tracking** - MCP servers run as child processes, so stdout/stderr is invisible. Sentry provides error visibility.
   - Add `"SENTRY_DSN": "<dsn_url>"` to the environment/env block
   - Optionally add `"SENTRY_ENVIRONMENT": "<label>"` (defaults to `SERVICENOW_ENV`)

## Step 4: Verify Setup

After writing the configuration, tell the user to:

1. **Restart their MCP client** (or reload the MCP server)
2. **Test with a simple tool call** - try `list_tool_packages` to verify connectivity
3. **If it fails**, check:
   - Instance URL starts with `https://`
   - Credentials are correct
   - The user has network access to the ServiceNow instance
   - `uvx` is installed (requires `uv` - install via `curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Tool Reference

The server provides a comprehensive set of tools organized into the following groups. Use `list_tool_packages` to see available packages and their tool groups at runtime.

### Table
`table_describe`, `table_query`, `table_aggregate`, `build_query`
- Describe table schema, query with encoded queries, compute aggregates, build structured queries

### Record
`record_get`, `rel_references_to`, `rel_references_from`
- Fetch records by sys_id, find inbound/outbound references

### Attachment
`attachment_list`, `attachment_get`, `attachment_download`, `attachment_download_by_name`
- List, inspect, and download attachment content (base64)

### Attachment Write
`attachment_upload`, `attachment_delete`
- Upload (base64) and delete attachments

### Metadata
`meta_list_artifacts`, `meta_get_artifact`, `meta_find_references`, `meta_what_writes`
- Inspect platform artifacts (business rules, script includes, etc.), find cross-references

### Change Intelligence
`changes_updateset_inspect`, `changes_diff_artifact`, `changes_last_touched`, `changes_release_notes`
- Update set inspection, artifact diffs, audit trail, release notes

### Debug & Trace
`debug_trace`, `debug_flow_execution`, `debug_email_trace`, `debug_integration_health`, `debug_importset_run`, `debug_field_mutation_story`
- Event timelines, flow executions, email tracing, integration health, import sets, field mutation history

### Record Write
`record_create`, `record_preview_create`, `record_update`, `record_preview_update`, `record_delete`, `record_preview_delete`, `record_apply`
- CRUD with optional preview-then-apply confirmation pattern

### Artifact Write
`artifact_create`, `artifact_update`
- Create/update platform artifacts with optional local script file injection via `script_path`

### Investigations
`investigate_run`, `investigate_explain`
- Run investigation modules: `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, `performance_bottlenecks`

### Documentation
`docs_logic_map`, `docs_artifact_summary`, `docs_test_scenarios`, `docs_review_notes`
- Generate automation maps, artifact summaries, test scenarios, code review notes

### Workflow Analysis
`workflow_contexts`, `workflow_map`, `workflow_status`, `workflow_activity_detail`, `workflow_version_list`
- Workflow contexts, structure mapping, execution status, activity details

### Flow Designer
`flow_list`, `flow_get`, `flow_map`, `flow_action_detail`, `flow_execution_list`, `flow_execution_detail`, `flow_snapshot_list`, `workflow_migration_analysis`
- Flow Designer flows, actions, executions, snapshots, migration analysis

### Incident Management
`incident_list`, `incident_get`, `incident_create`, `incident_update`, `incident_resolve`, `incident_add_comment`
- Full incident lifecycle

### Change Management
`change_list`, `change_get`, `change_create`, `change_update`, `change_tasks`, `change_add_comment`
- Change request lifecycle

### Problem Management
`problem_list`, `problem_get`, `problem_create`, `problem_update`, `problem_root_cause`
- Problem lifecycle and root cause documentation

### CMDB
`cmdb_list`, `cmdb_get`, `cmdb_relationships`, `cmdb_classes`, `cmdb_health`
- CI browsing, relationships, classes, health checks

### Request Management
`request_list`, `request_get`, `request_items`, `request_item_get`, `request_item_update`
- Service requests and RITMs

### Knowledge Management
`knowledge_search`, `knowledge_get`, `knowledge_create`, `knowledge_update`, `knowledge_feedback`
- Knowledge articles and feedback

### Service Catalog
`sc_catalogs_list`, `sc_catalog_get`, `sc_categories_list`, `sc_category_get`, `sc_items_list`, `sc_item_get`, `sc_item_variables`, `sc_order_now`, `sc_add_to_cart`, `sc_cart_get`, `sc_cart_submit`, `sc_cart_checkout`
- Catalog browsing, ordering, cart management

### Core
`list_tool_packages`
- Always available. Lists all packages and their tool groups.

## Safety Guardrails

These guardrails are always active. They reduce risk but are not a guarantee - always validate in a sub-production environment.

- **Table deny list** - Sensitive tables (`sys_user_has_password`, `oauth_credential`, `sys_credentials`, and others) are blocked
- **Sensitive field masking** - Fields matching `password`, `token`, `secret`, `credential`, `api_key`, or `private_key` patterns are masked with `***MASKED***`
- **Row limit caps** - Query limits capped at `MAX_ROW_LIMIT` (default 100)
- **Large table protection** - Configured tables require date-bounded queries
- **Write gating** - All write operations blocked when `SERVICENOW_ENV` is set to `prod` or `production`
- **Attachment limits** - 10 MB maximum per attachment transfer
- **Field validation** - Required fields validated before record creation
- **Standardized responses** - All tools return TOON-serialized envelopes (not raw JSON) with `correlation_id`, `status`, and `data`
