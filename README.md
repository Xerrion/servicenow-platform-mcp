<p align="center">
  <img src="assets/banner.svg" alt="servicenow-platform-mcp banner" width="900" />
</p>

<p align="center">
  <a href="https://pypi.org/project/servicenow-platform-mcp/"><img src="https://img.shields.io/pypi/v/servicenow-platform-mcp" alt="PyPI version"></a>
  <a href="https://pypi.org/project/servicenow-platform-mcp/"><img src="https://img.shields.io/pypi/pyversions/servicenow-platform-mcp" alt="Python versions"></a>
  <a href="https://github.com/Xerrion/servicenow-platform-mcp/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Xerrion/servicenow-platform-mcp" alt="License"></a>
</p>

# servicenow-platform-mcp

A comprehensive [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for ServiceNow. Provides a comprehensive suite of tools across 20 tool groups for platform introspection, change intelligence, debugging, record management, ITSM workflows, CMDB operations, service catalog, automated investigations, documentation generation, and Flow Designer analysis.

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
| `SERVICENOW_USERNAME` | ServiceNow username | - | Yes |
| `SERVICENOW_PASSWORD` | ServiceNow password | - | Yes |
| `MCP_TOOL_PACKAGE` | Tool package to load | `full` | No |
| `SERVICENOW_ENV` | Environment label (`dev`/`test`/`staging`/`prod`) | `dev` | No |
| `MAX_ROW_LIMIT` | Max rows per query (1-10000) | `100` | No |
| `LARGE_TABLE_NAMES_CSV` | Tables requiring date filters | `syslog,sys_audit,sys_log_transaction,sys_email_log` | No |
| `SCRIPT_ALLOWED_ROOT` | Root dir for `script_path` in artifact write | `""` (disabled) | When using `script_path` |
| `SENTRY_DSN` | Sentry DSN for error reporting | `""` | No |
| `SENTRY_ENVIRONMENT` | Sentry environment label | Falls back to `SERVICENOW_ENV` | No |

The server reads from `.env` and `.env.local` files automatically.

## AI Agent Setup

Copy and paste this prompt to your AI agent (Claude Code, Cursor, OpenCode, etc.):

```
Install and configure servicenow-platform-mcp by following the instructions here:
https://raw.githubusercontent.com/Xerrion/servicenow-platform-mcp/refs/heads/main/INSTALL.md
```

Or read the [Installation Guide](INSTALL.md) directly.

## Key Features

- **Platform introspection** - describe tables, query records, inspect metadata, compute aggregates
- **Change intelligence** - update set inspection, artifact diffs, audit trails, release notes
- **Debug and trace** - event timelines, flow execution, email tracing, import sets, field mutations
- **Record management** - full CRUD with preview-then-apply safety pattern
- **Artifact management** - create/update business rules, script includes, and 15+ artifact types with local script file support
- **ITSM workflows** - incident, change, problem lifecycle management
- **CMDB operations** - browse CIs, relationships, classes, and health checks
- **Service catalog** - browse, order, cart management, checkout
- **Knowledge management** - search, create, update articles and feedback
- **Investigations** - automated analysis of stale automations, deprecated APIs, ACL conflicts, performance bottlenecks
- **Documentation generation** - logic maps, artifact summaries, test scenarios, review notes
- **Flow Designer** - inspect flows, actions, executions, snapshots, migration analysis

## Tool Packages

Control which tools are loaded with `MCP_TOOL_PACKAGE`. There are 14 preset packages:

| Package | Groups | Description |
|---------|--------|-------------|
| `full` | 20 | All standard tools (default) |
| `core_readonly` | 4 | Read-only core tools |
| `none` | 0 | No tools loaded |
| `itil` | 16 | ITIL process tools |
| `developer` | 13 | Development-focused tools |
| `readonly` | 10 | Read-only operations |
| `analyst` | 8 | Analysis and reporting |
| `incident_management` | 9 | Incident lifecycle |
| `change_management` | 8 | Change request tools |
| `cmdb` | 6 | CMDB management |
| `problem_management` | 9 | Problem lifecycle |
| `request_management` | 8 | Request/RITM tools |
| `knowledge_management` | 6 | Knowledge base tools |
| `service_catalog` | 6 | Service catalog tools |

You can also create custom packages with comma-separated group names (e.g. `MCP_TOOL_PACKAGE="table,record,debug"`). See the [Wiki](https://github.com/Xerrion/servicenow-platform-mcp/wiki) for full package and tool group details.

## Safety

- **Table deny list** - blocks access to sensitive system tables (`sys_user_has_password`, `oauth_credential`, `sys_credentials`, etc.)
- **Sensitive field masking** - password, token, secret, and similar fields are automatically masked
- **Write gating** - all write operations blocked when `SERVICENOW_ENV` is set to `prod` or `production`
- **Row limits and large table protection** - prevents runaway queries with configurable caps and mandatory date filters

These guardrails reduce risk but are not a guarantee - always validate in a sub-production environment.

See the [Safety & Policy](https://github.com/Xerrion/servicenow-platform-mcp/wiki) wiki page for complete details.

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
