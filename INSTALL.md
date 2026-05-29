# ServiceNow Platform MCP Server - Installation Guide

> **For AI Agents:** You are helping your user install and configure the ServiceNow Platform MCP server.
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
   - **All tools** (default) - set `MCP_TOOL_PACKAGE=full` (13 tools, including `audit` and `flow`)
   - **Read-only** - set `MCP_TOOL_PACKAGE=readonly` (9 tools, including `audit` and `flow`)
   - **Minimal** - set `MCP_TOOL_PACKAGE=core_readonly` (5 tools: query, describe, attachment, attachment_write, list_tool_packages)
   - **Custom** - comma-separated tool names (e.g., `query,describe,attachment`)

## Step 2: Choose MCP Client

Ask the user which MCP client they use, then generate the appropriate configuration file.

### OpenCode

Write to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "servicenow": {
      "type": "local",
      "command": ["uvx", "servicenow-platform-mcp"],
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
      "args": ["servicenow-platform-mcp"],
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
      "args": ["servicenow-platform-mcp"],
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
uvx servicenow-platform-mcp
```

**Important:** Replace all `<placeholder>` values with the user's actual answers from Steps 0-1 before writing the config.

## Step 3: Optional Configuration

Ask the user if they want to configure any of these optional settings:

1. **Row limit** - Maximum records per query (default: 100, range: 1-10000)
   - Add `"MAX_ROW_LIMIT": "<number>"` to the environment/env block

2. **Large tables** - Tables that require date-bounded queries (default: `syslog,sys_audit,sys_log_transaction,sys_email_log`)
   - Add `"LARGE_TABLE_NAMES_CSV": "<comma_separated_tables>"` to the environment/env block

3. **Script file root** - When using `record_write` with `script_path`, constrains file reads to a directory tree
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

The server provides 12 unified tools. Use `list_tool_packages` to see available tools at runtime. For detailed usage patterns and complex queries, see [Agent Recipes](docs/agent-recipes.md).

### query
Search and retrieve records using ServiceNow encoded query strings. Supports `resolve_labels` for human-readable filtering and `display_values` for labeled results.

### build_query
Stateless helper that compiles a JSON array of condition objects into a ServiceNow encoded query string (returned in `data.query`). Pass the returned string as the `query` parameter to the `query` tool. Available in the `full` package only - read-only presets pass encoded queries to `query` directly.

### describe
Retrieve table schema and metadata. Returns a slim set of field attributes by default (8 keys); use `verbose=true` for the full platform payload.

### record_write
Unified tool for `create`, `update`, and `delete` actions. When called with `preview=true` (default), it returns a `preview_token` consumed by `record_apply`. Supports local script injection via `script_path` for any table whose dictionary fields are script-bearing (Business Rules, Script Includes, UI Pages, Widgets, UI Macros, ACLs, etc.) — script fields are discovered at runtime from `sys_dictionary`, no hardcoded catalog. Use `script_field` to target a specific field on tables that have more than one.

### record_read
Read-only counterpart to `record_write` for platform artifacts. Resolves a record by `sys_id` or `name` and returns the masked record plus the `script_fields` list so callers can drive multi-field edits without guessing field names.

### record_apply
Commits a write operation previously staged with `record_write(preview=true)`. Takes the returned `preview_token`.

### attachment
Dispatcher for read operations: `list`, `get`, `download`.

### attachment_write
Dispatcher for write operations: `upload`, `delete`. Included in all standard packages; blocked in production via runtime write gating.

### investigate
Runs automated diagnostic modules. Actions: `run` (execute module) or `explain` (interpret findings). Includes: `stale_automations`, `table_health`, `performance_bottlenecks`, and more.

### resolve_choice
Maps human-readable labels (e.g., "In Progress") to underlying ServiceNow choice values (e.g., "2").

### service_catalog
Dispatcher for Service Catalog operations: browse catalogs, categories, items, and manage carts.

### list_tool_packages
Always-on tool to list the active tool package and its available tools.

## Safety Guardrails

These guardrails are always active. They reduce risk but are not a guarantee - always validate in a sub-production environment.

- **Table Deny List** - Sensitive tables (`sys_user_has_password`, `sys_credentials`, etc.) are blocked.
- **Sensitive Field Masking** - Fields matching `password`, `token`, `secret`, and others are masked.
- **Row Limit Caps** - Query limits capped at `MAX_ROW_LIMIT` (default 100).
- **Large Table Protection** - Configured tables require date-bounded queries.
- **Write Gating** - All write operations blocked when `SERVICENOW_ENV` is set to `prod` or `production`.
- **Attachment Limits** - 10 MB maximum per attachment transfer.
- **Standardized Responses** - All tools return JSON-serialized envelopes with `correlation_id`, `status`, and `data`.
