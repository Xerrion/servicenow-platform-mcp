# Tool Packages

`MCP_TOOL_PACKAGE` controls which tools the server exposes at startup. It defaults to `full`. The server must restart for changes to take effect.

For the complete tool reference see [[Tool-Reference]]. For all environment variables see [[Configuration]].

## Tool Counting Convention

Every tool count in this document includes the always-on `list_tool_packages` tool. This tool is registered unconditionally - it is not part of any package - and returns the package registry so callers can discover the active surface without out-of-band knowledge.

## Preset Packages

| Package | Groups | Tools | Description |
|---------|--------|-------|-------------|
| `full` | 11 | 14 | Complete read/write surface for development instances |
| `readonly` | 8 | 10 | Read-focused; no `record_write`, `record_apply`, `build_query`, or `service_catalog` |
| `core_readonly` | 3 | 5 | Minimal - query, describe, attachments |
| `none` | 0 | 1 | Only `list_tool_packages`; useful for connectivity checks |

## Full Tool List Per Preset

### `full` - 14 tools

`list_tool_packages`, `query`, `build_query`, `describe`, `record_read`, `record_write`, `record_apply`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `service_catalog`, `audit`, `flow`

### `readonly` - 10 tools

`list_tool_packages`, `query`, `describe`, `record_read`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `audit`, `flow`

### `core_readonly` - 5 tools

`list_tool_packages`, `query`, `describe`, `attachment`, `attachment_write`

### `none` - 1 tool

`list_tool_packages`

## Runtime-Gated Writes

The `readonly` and `core_readonly` presets load `attachment_write` because the `attachment` group registers both its read and write tools as a pair. Write operations (`upload`, `delete`) are blocked at runtime by `write_gate` when the environment is production or the target table is denied. In non-production environments, `attachment_write` remains functional even under the readonly presets.

## Custom Packages

Set `MCP_TOOL_PACKAGE` to a comma-separated list of group names to compose a bespoke surface:

```bash
MCP_TOOL_PACKAGE=query,describe,audit
```

A single group name also works - `MCP_TOOL_PACKAGE=service_catalog` loads only that group (plus `list_tool_packages`).

Valid group names: `query`, `build_query`, `describe`, `record_read`, `record_write`, `attachment`, `investigate`, `resolve_choice`, `service_catalog`, `audit`, `flow`.

If a token in the comma-separated list does not match a known group name, the server logs an `ImportError` and captures it via Sentry; the remaining groups still load.

## Tool-Group to Tool Mapping

Most groups register a single MCP tool with the same name as the group. Two groups register a pair:

| Group | Tools registered |
|-------|-----------------|
| `query` | `query` |
| `build_query` | `build_query` |
| `describe` | `describe` |
| `record_read` | `record_read` |
| `record_write` | `record_write`, `record_apply` |
| `attachment` | `attachment`, `attachment_write` |
| `investigate` | `investigate` |
| `resolve_choice` | `resolve_choice` |
| `service_catalog` | `service_catalog` |
| `audit` | `audit` |
| `flow` | `flow` |

## Runtime Introspection

Call `list_tool_packages` to see the registry. It returns group names per preset:

```json
{
  "full": ["query", "build_query", "describe", "record_read", "record_write", "attachment", "investigate", "resolve_choice", "service_catalog", "audit", "flow"],
  "readonly": ["query", "describe", "record_read", "attachment", "investigate", "resolve_choice", "audit", "flow"],
  "core_readonly": ["query", "describe", "attachment"],
  "none": []
}
```

Use the group-to-tool mapping above to determine individual tool names from these group lists.
