# servicenow-platform-mcp

A Python 3.12+ async MCP server that gives AI agents structured, policy-guarded access to ServiceNow. It exposes schema inspection, record CRUD, attachment operations, audit trail analysis, Flow Designer inspection, and platform investigations - all over the Model Context Protocol's stdio transport.

## What you get

The server presents a unified tool surface to any MCP-compatible client. Capabilities include:

- **Query and inspect** - encoded-query search, aggregation, field-level schema discovery, choice-label resolution.
- **Read and write records** - including script-bearing artifacts (Business Rules, Script Includes, UI Macros) with file-based script injection and preview-then-apply confirmation.
- **Attachments** - list, download, upload, and delete with size-capped base64 transfer.
- **Audit** - field-level audit verdict resolution, batch checks, and masked history retrieval.
- **Flow Designer** - inspect flows, decode compressed action values, find record-trigger bindings.
- **Investigations** - pluggable analysis modules for stale automations, deprecated APIs, ACL conflicts, performance bottlenecks, and more.
- **Service Catalog** - browse catalogs, order items, manage carts.

## Tool-package presets

Control the exposed surface with `MCP_TOOL_PACKAGE`. Four presets ship; custom comma-separated group lists are also accepted.

| Preset | Tools | Purpose |
|--------|-------|---------|
| `full` | 14 | Complete surface including writes and query builder |
| `readonly` | 10 | Read operations, investigations, audit, and flow inspection |
| `core_readonly` | 5 | Query, describe, and attachment only |
| `none` | 1 | Only the `list_tool_packages` introspection tool |

## Quickstart

```bash
# Install dependencies
uv sync

# Configure credentials
cp .env.example .env.local
# Edit .env.local with your instance URL, username, and password

# Run the server (stdio transport)
servicenow-platform-mcp
```

Three environment variables are required:

- `SERVICENOW_INSTANCE_URL` - your instance (HTTPS enforced, e.g. `https://dev12345.service-now.com`)
- `SERVICENOW_USERNAME`
- `SERVICENOW_PASSWORD`

### MCP client configuration

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "servicenow-platform-mcp",
      "transport": "stdio"
    }
  }
}
```

## Safety posture

Eight sensitive tables (credential stores, OAuth entities, SSH keys) are unconditionally denied at the policy layer. Fields matching sensitive patterns - passwords, tokens, secrets, API keys, private keys, credentials - are masked in every response. Write operations use a preview-then-apply flow by default: the server returns a confirmation token that must be explicitly applied before any mutation reaches ServiceNow. In production environments (`SERVICENOW_ENV=prod` or `production`), all write operations are blocked at runtime regardless of which tool package is loaded.

## Where to go next

- [Getting Started](docs/wiki/Getting-Started.md) - detailed setup and first-use walkthrough
- [Configuration](docs/wiki/Configuration.md) - full environment variable reference
- [Tool Reference](docs/wiki/Tool-Reference.md) - per-tool parameter and response documentation
- [Tool Packages](docs/wiki/Tool-Packages.md) - preset details and custom package composition
- [Architecture](docs/wiki/Architecture.md) - server bootstrap, decorator patterns, state management
- [Safety and Policy](docs/wiki/Safety-and-Policy.md) - denied tables, masking, query safety, write gating
- [Investigations](docs/wiki/Investigations.md) - available analysis modules and their parameters
- [Telemetry](docs/wiki/Telemetry.md) - opt-in Sentry integration
- [Development](docs/wiki/Development.md) - contributing, linting, testing
- [INSTALL.md](INSTALL.md) - alternative installation methods

## License

MIT. See [LICENSE](LICENSE).

Built by the ServiceNow MCP Contributors.
