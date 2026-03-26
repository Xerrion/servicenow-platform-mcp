# Tool Packages

Tool packages control which tools are loaded when the server starts. Configure the active package via the `MCP_TOOL_PACKAGE` environment variable. The default is `"full"`.

For a complete reference of every tool in each group, see [[Tool-Reference]]. For security guardrails that apply across all packages, see [[Safety-and-Policy]].

---

## Overview

The server organizes tools into 21 **tool groups**. A **tool package** is a named collection of groups that are loaded together. There are 14 preset packages for common workflows, and you can also define custom packages by listing group names directly.

The `list_tool_packages` tool is always available - even with `MCP_TOOL_PACKAGE="none"` - and returns all available packages and their contents at runtime.

---

## Preset Packages

| Package | Groups | Description |
|---|---|---|
| `full` (default) | 20 | All standard tool groups (excludes `testing`) |
| `itil` | 16 | ITIL process tools - incidents, changes, problems, requests, plus platform support tools |
| `developer` | 13 | Development-focused - schema, records, attachments, debugging, investigations, workflows |
| `readonly` | 10 | Read-only operations only - no create, update, or delete tools |
| `analyst` | 8 | Analysis and reporting - schema, records, investigations, documentation, workflows |
| `incident_management` | 9 | Incident lifecycle with supporting platform tools |
| `problem_management` | 9 | Problem lifecycle with supporting platform tools |
| `change_management` | 8 | Change request management with change intelligence |
| `request_management` | 8 | Request and RITM management with workflow tools |
| `cmdb` | 6 | CMDB management with relationships |
| `knowledge_management` | 6 | Knowledge base tools |
| `service_catalog` | 6 | Service catalog browsing, ordering, and cart management |
| `core_readonly` | 4 | Minimal read-only core - table, record, attachment, metadata only |
| `none` | 0 | No tools loaded - only `list_tool_packages` is available (useful for testing) |

---

## Package Contents

### `full` (20 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `metadata`, `artifact_write`, `changes`, `debug`, `investigations`, `documentation`, `workflow`, `flow_designer`, `domain_incident`, `domain_change`, `domain_cmdb`, `domain_problem`, `domain_request`, `domain_knowledge`, `domain_service_catalog`

> **Note:** The `testing` group is excluded from `full`. To include it, use a custom package (see below).

### `itil` (16 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `metadata`, `artifact_write`, `changes`, `debug`, `documentation`, `workflow`, `flow_designer`, `domain_incident`, `domain_change`, `domain_problem`, `domain_request`

### `developer` (13 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `metadata`, `artifact_write`, `changes`, `debug`, `investigations`, `documentation`, `workflow`, `flow_designer`

### `readonly` (10 groups)

`table`, `record`, `attachment`, `metadata`, `changes`, `debug`, `investigations`, `documentation`, `workflow`, `flow_designer`

### `analyst` (8 groups)

`table`, `record`, `attachment`, `metadata`, `investigations`, `documentation`, `workflow`, `flow_designer`

### `incident_management` (9 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_incident`, `debug`, `workflow`, `flow_designer`

### `problem_management` (9 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_problem`, `debug`, `workflow`, `flow_designer`

### `change_management` (8 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_change`, `changes`, `flow_designer`

### `request_management` (8 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_request`, `workflow`, `flow_designer`

### `cmdb` (6 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_cmdb`

### `knowledge_management` (6 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_knowledge`

### `service_catalog` (6 groups)

`table`, `record`, `attachment`, `record_write`, `attachment_write`, `domain_service_catalog`

### `core_readonly` (4 groups)

`table`, `record`, `attachment`, `metadata`

### `none` (0 groups)

No tool groups loaded. Only the always-on `list_tool_packages` tool is available.

---

## Tool Group Reference

All 21 tool groups that can be included in packages:

| Group | Type | Description |
|---|---|---|
| `table` | Standard | Schema description, record queries, aggregation, query building |
| `record` | Standard | Record fetch, inbound/outbound reference discovery |
| `attachment` | Standard | Attachment listing, metadata, content download |
| `attachment_write` | Standard | Attachment upload and delete |
| `record_write` | Standard | Record create, update, delete with preview/apply pattern |
| `metadata` | Standard | Platform artifact listing, inspection, cross-references |
| `artifact_write` | Standard | Platform artifact create and update with script file support |
| `changes` | Standard | Update set inspection, artifact diffing, audit trails, release notes |
| `debug` | Standard | Event timelines, flow execution, email tracing, integration health |
| `investigations` | Standard | Automated analysis modules (7 investigation types) |
| `documentation` | Standard | Automation maps, artifact summaries, test scenarios, review notes |
| `workflow` | Standard | Legacy workflow context, structure, status, and activity inspection |
| `flow_designer` | Standard | Flow Designer flows, executions, snapshots, migration analysis |
| `testing` | Standard | ATF test and suite listing, execution, and health analysis |
| `domain_incident` | Domain | Incident lifecycle with choice label resolution |
| `domain_change` | Domain | Change request lifecycle with choice label resolution |
| `domain_cmdb` | Domain | CMDB browsing, relationships, classes, health |
| `domain_problem` | Domain | Problem lifecycle with choice label resolution |
| `domain_request` | Domain | Request and RITM management with choice label resolution |
| `domain_knowledge` | Domain | Knowledge article search, CRUD, and feedback |
| `domain_service_catalog` | Domain | Catalog browsing, ordering, cart, and checkout |

**Standard** groups receive `settings` and `auth_provider` at registration. **Domain** groups additionally receive a `ChoiceRegistry` instance for resolving human-readable labels (e.g., "open", "high") to ServiceNow internal values.

See [[Tool-Reference]] for the complete list of tools in each group.

---

## Custom Packages

You can create a custom package by setting `MCP_TOOL_PACKAGE` to a comma-separated list of group names:

```bash
MCP_TOOL_PACKAGE="table,record,debug,domain_incident"
```

This loads exactly the specified groups - no more, no less.

### Rules

- Group names must be valid (see the Tool Group Reference table above)
- Invalid group names cause a startup validation error
- Custom package values must not collide with preset package names (e.g., you cannot use `"full"` as a custom list)
- The `list_tool_packages` tool is always available regardless of the custom package contents
- You can include the `testing` group in a custom package (it is only excluded from the `full` preset)

### Examples

**Minimal incident workflow:**
```bash
MCP_TOOL_PACKAGE="table,record,domain_incident"
```

**Developer tools with testing:**
```bash
MCP_TOOL_PACKAGE="table,record,attachment,record_write,attachment_write,metadata,artifact_write,changes,debug,investigations,documentation,workflow,flow_designer,testing"
```

**Read-only CMDB inspection:**
```bash
MCP_TOOL_PACKAGE="table,record,attachment,domain_cmdb"
```

---

## Choosing a Package

| If you need... | Use this package |
|---|---|
| Everything (default) | `full` |
| ITSM process management | `itil` |
| Platform development and debugging | `developer` |
| Read-only exploration and analysis | `readonly` or `analyst` |
| Incident-focused workflow | `incident_management` |
| Change request workflow | `change_management` |
| Problem management | `problem_management` |
| Request/RITM management | `request_management` |
| CMDB exploration | `cmdb` |
| Knowledge base work | `knowledge_management` |
| Service catalog operations | `service_catalog` |
| Minimal read-only access | `core_readonly` |
| Connection test only | `none` |
| Specific combination of groups | Custom (comma-separated group names) |

### Considerations

- **Fewer tools = less noise** - AI agents work better when they have fewer, more relevant tools to choose from. Start with a focused package and expand if needed.
- **Read-only is safe** - The `readonly`, `analyst`, and `core_readonly` packages contain no write operations, making them safe for production exploration.
- **Write gating applies regardless** - Even packages with write tools are subject to production write gating when `SERVICENOW_ENV` is `"prod"` or `"production"`. See [[Safety-and-Policy]] for details.

---

## The `list_tool_packages` Tool

This tool is always available and returns the complete package registry at runtime. Use it to:

- Discover available preset packages and their group contents
- Verify which groups are loaded in the current configuration
- Check valid group names for building custom packages

---

## Next Steps

- [[Tool-Reference]] - Complete tool reference with descriptions and parameters
- [[Safety-and-Policy]] - Security guardrails and write gating
- [[Configuration]] - Environment variables and settings reference
