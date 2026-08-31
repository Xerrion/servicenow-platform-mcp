<p align="center">
  <img src="assets/banner.svg" alt="servicenow-platform-mcp banner" width="900" />
</p>

<p align="center">
  <a href="https://pypi.org/project/servicenow-platform-mcp/"><img src="https://img.shields.io/pypi/v/servicenow-platform-mcp" alt="PyPI version"></a>
  <a href="https://pypi.org/project/servicenow-platform-mcp/"><img src="https://img.shields.io/pypi/pyversions/servicenow-platform-mcp" alt="Python versions"></a>
  <a href="https://github.com/Xerrion/servicenow-platform-mcp/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Xerrion/servicenow-platform-mcp" alt="License"></a>
</p>

# servicenow-platform-mcp

A comprehensive [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for ServiceNow. Provides 15 unified tools in 13 tool groups for platform introspection, change intelligence, debugging, record management, and automated investigations.

## Quick Start

**1. Set environment variables:**

```bash
export SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
export SERVICENOW_USERNAME=admin
export SERVICENOW_PASSWORD=your-password
```

**2. Run the server:**

```bash
uvx servicenow-platform-mcp
```

**3. Connect your MCP client** (see [Configuration](#configuration) below).

## Configuration

### OpenCode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "servicenow": {
      "type": "local",
      "command": ["uvx", "servicenow-platform-mcp"],
      "environment": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
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
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

### VS Code / Cursor

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "servicenow": {
      "command": "uvx",
      "args": ["servicenow-platform-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

### Generic stdio

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com \
SERVICENOW_USERNAME=admin \
SERVICENOW_PASSWORD=your-password \
uvx servicenow-platform-mcp
```

## Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SERVICENOW_INSTANCE_URL` | Full URL (must start with `https://`) | - | Yes |
| `SERVICENOW_API_KEY` | ServiceNow API key; replaces Basic Auth when set | - | Conditional |
| `SERVICENOW_USERNAME` | ServiceNow username for Basic Auth | - | Conditional |
| `SERVICENOW_PASSWORD` | ServiceNow password for Basic Auth | - | Conditional |
| `MCP_TOOL_PACKAGE` | Tool package to load (`full`, `readonly`, `core_readonly`, `none`) | `full` | No |
| `SERVICENOW_ENV` | Environment label (`dev`/`test`/`staging`/`prod`) | `dev` | No |
| `MAX_ROW_LIMIT` | Max rows per query (1-10000) | `100` | No |
| `LARGE_TABLE_NAMES_CSV` | Tables requiring date filters | `syslog,sys_audit,sys_log_transaction,sys_email_log` | No |
| `SCRIPT_ALLOWED_ROOT` | Root dir for `script_path` in artifact write | `""` (disabled) | When using `script_path` |
| `HTTPX_TIMEOUT_SECONDS` | ServiceNow HTTP timeout in seconds (1-600) | `30` | No |
| `METADATA_CACHE_TTL_SECONDS` | Freshness window for choices, dictionary metadata, and audit configuration | `300` seconds | No |
| `SENTRY_DSN` | Sentry DSN for error reporting | `""` | No |
| `SENTRY_ENVIRONMENT` | Sentry environment label | Falls back to `SERVICENOW_ENV` | No |

The server reads from `.env` and `.env.local` files automatically.

## AI Agent Setup

Copy and paste this prompt to your AI agent (Claude Code, Cursor, OpenCode, etc.):

```
Install and configure servicenow-platform-mcp by following the instructions here:
https://raw.githubusercontent.com/Xerrion/servicenow-platform-mcp/refs/heads/main/INSTALL.md
```

Or read the [Installation Guide](INSTALL.md) directly. For usage examples and patterns, see [Agent Recipes](docs/agent-recipes.md).

## Key Features

- **Platform Introspection** - Describe complete inherited table schemas with `describe` and query records with `query` using encoded queries. The same generic tools support tables such as `sc_task`, `task_sla`, and `cmdb_rel_ci`; there are no table-specific tools for these tables.
- **Record Management** - Unified `record_write` and `record_apply` tools for create, update, and delete. Writes use preview-then-apply by default; callers can explicitly set `preview=false` for an immediate write.
- **Script-Bearing Records** - Write Business Rules, Script Includes, UI Pages, Widgets, UI Macros, ACLs, and any other table whose dictionary fields carry executable script or markup, all via `record_write` with local script file support and per-field targeting (`script_field`). Script fields are discovered at runtime from `sys_dictionary` — no hardcoded artifact catalog. Read the same surface back via `record_read`, or enumerate a table's script fields with `describe(action='list_script_fields', table='<table>')`.
- **Attachment Operations** - Unified `attachment` for read operations and `attachment_write` for mutations.
- **Investigations** - Automated analysis of system health, stale automations, performance bottlenecks, and more via `investigate`.
- **Label Resolution** - Map human-readable choice labels to underlying values automatically with `resolve_choice`.
- **Service Catalog** - Dispatcher-based `service_catalog` tool for browsing and ordering.
- **Read-Only Analysis** - Compose fulfilled RITM variables and bounded journal history with `analysis`. See [Tool Reference](docs/wiki/Tool-Reference.md) for the complete action and parameter reference.

## Example Usage

### Describe a Table
```python
await describe(table="incident")
```

### Query Records
```python
# Fetch high priority incidents using an encoded query
await query(
    table="incident",
    encoded_query="active=true^priority=1",
    fields="number,short_description,priority"
)
```

List mode requires an explicit field projection. Use a small field set for normal reads. Use `fields="*"` only when the full record is intentional. `sys_id` is always included. Successful responses include `selection` metadata describing the projection.

## Tool Packages

Control which tools are loaded with `MCP_TOOL_PACKAGE`.

| Package | Tools | Description |
|---------|-------|-------------|
| `full` | 15 | All unified tools, including `query`, `describe`, `record_read`, `analysis`, `audit`, `flow`, and attachment reads and writes (default) |
| `readonly` | 11 | Read-only tools: `query`, `describe`, `record_read`, `attachment`, `investigate`, `resolve_choice`, `analysis`, `audit`, `flow`, and `code_search` |
| `core_readonly` | 4 | Minimal read surface: `query`, `describe`, `attachment`, and `list_tool_packages` |
| `none` | 1 | Only `list_tool_packages` |

Custom packages are supported via comma-separated tool names: `MCP_TOOL_PACKAGE="query,describe,attachment"`.
The `attachment` group is read-only. Add the separate `attachment_write` group explicitly to a custom package to opt in to upload and delete operations. `full` includes both groups; the `readonly` presets do not expose attachment writes. For package contents and migration details, see [Tool Packages](docs/wiki/Tool-Packages.md).

### Read-only analysis

The `analysis` tool is available in `full` and `readonly`, but not in `core_readonly`. It provides three actions:

- `ritm_variables` composes submitted answers for one requested item (RITM) through the related catalog-option tables. Sensitive answers are masked. List Collector values retain their raw sys_ids, include a warning, and identify multiple comma-separated values. Reference values also retain raw sys_ids because the tool does not automatically resolve display values. If multi-row variable sets (MRVS) are present, the response reports this in `data.unsupported_features.multi_row_variable_sets`; MRVS payloads are not retrieved or decoded.
- `journal_history` returns bounded `sys_journal_field` history for dictionary-confirmed `comments`, `work_notes`, and optional `close_notes`. It is separate from `audit`'s `history` action, which reads field changes from `sys_audit`. Table and field ACLs and journal retention affect completeness.
- `describe` returns the analysis action registry without platform I/O.

Analysis requires Table API read access and applicable table and field ACLs for the target tables and these supporting tables: `sys_db_object`, `sys_dictionary`, `sys_journal_field`, `sc_req_item`, `sc_item_option_mtom`, `sc_item_option`, `item_option_new`, and `sc_multi_row_question_answer`.

The ordinary `describe` tool resolves inherited fields through the bounded `sys_db_object.super_class` chain. Child declarations override ancestor declarations, each field reports its `inherited_from` provenance, and pagination occurs after de-duplication.

Analysis does not provide a Power BI connector or export, semantic Jira or LeanIX joins, MRVS payload decoding, or automatic reference display resolution for submitted variable answers.

## Safety

- **Table Deny List** - Blocks access to sensitive system tables (`sys_user_has_password`, `sys_credentials`, etc.).
- **Sensitive Field Masking** - Passwords, tokens, and secrets are automatically masked in responses.
- **Write Gating** - All mutations are blocked when `SERVICENOW_ENV` is set to `prod` or `production`.
- **Query Safety** - Enforces row limits and mandatory date filters on high-volume system tables.

These guardrails reduce risk but are not a guarantee - always validate in a sub-production environment.

See the [Safety & Policy](https://github.com/Xerrion/servicenow-platform-mcp/wiki/Safety-and-Policy) wiki page for complete details.

## Development

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
uv run pytest                  # Run tests
uv run ruff check .            # Lint
uv run ruff format .           # Format
uv run mypy src/               # Type check
```

## License

[MIT](LICENSE)
