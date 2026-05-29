# Getting Started

This tutorial walks you through a first session with the servicenow-platform-mcp server. You have it installed and connected to an MCP client; now you want to do useful things. Each step builds on the last - by the end you will have queried records, inspected schema, written safely, resolved choice labels, and run an investigation.

## Prerequisites

- Server installed and running (see INSTALL.md)
- Credentials pointing at a dev or sandbox ServiceNow instance
- `MCP_TOOL_PACKAGE` set to `full` (the default)

## 1. Confirm the Server Is Up

Call `list_tool_packages` with no arguments.

This tool is special: it bypasses `@tool_handler` entirely and returns raw JSON - no `status` field, no `correlation_id`, no error envelope. It is always registered regardless of your active package.

Expected output:

```json
{
  "full": ["query", "build_query", "describe", "record_read", "record_write", "attachment", "investigate", "resolve_choice", "service_catalog", "audit", "flow"],
  "readonly": ["query", "describe", "record_read", "attachment", "investigate", "resolve_choice", "audit", "flow"],
  "core_readonly": ["query", "describe", "attachment"],
  "none": []
}
```

If your MCP client shows only `list_tool_packages` and nothing else, your `MCP_TOOL_PACKAGE` value is `none` or does not match a valid preset or comma-separated group list.

## 2. Inspect a Table

Call `describe` with only `table`:

```json
{ "table": "incident" }
```

When `action` is omitted (or empty string), `describe` runs the default field-metadata flow: it queries `sys_dictionary` and returns each field's name, label, type, max_length, mandatory flag, read_only flag, reference_table, and choice_count.

The `action` parameter accepts exactly one value: `"list_script_fields"`. Any other string - for example `action: "fields"` - returns the error:

```
Unknown describe action 'fields'. Valid actions: ['list_script_fields'].
```

Do not guess action names. Omit the parameter for field metadata; pass `"list_script_fields"` when you need the script-bearing fields on a table.

## 3. Query Records

Call `query` with:

```json
{ "table": "incident", "limit": 5 }
```

The response envelope includes `data` (the masked records), `pagination` with `offset`, `limit`, and `total`, plus a `correlation_id` for tracing.

To filter, pass `encoded_query` using ServiceNow's encoded query syntax:

```json
{ "table": "incident", "limit": 5, "encoded_query": "active=true^priority=1" }
```

The server enforces query safety: limits are capped at `MAX_ROW_LIMIT` (default 100), and large tables (syslog, sys_audit, sys_log_transaction, sys_email_log) require a date-bounded filter.

## 4. Read One Record

Take a `sys_id` from the query results and call `record_read`:

```json
{ "table": "incident", "sys_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" }
```

The response includes the full masked record plus `script_fields` - the list of script-bearing fields detected on that table via the DictionaryRegistry.

Exactly one of `sys_id` or `name` must be supplied. Passing both returns an error; passing neither also returns an error. When using `name`, if multiple records match the server returns an ambiguity error rather than guessing.

## 5. Write Safely (Preview-Then-Apply)

Writes use a two-step pattern by default.

**Step 5a - Preview:**

```json
{
  "table": "incident",
  "action": "update",
  "sys_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
  "data": "{\"short_description\": \"Updated via MCP\"}"
}
```

Because `preview` defaults to `True`, the server does not touch ServiceNow. It fetches the current record, computes a diff, stores the payload in a single-use token store (TTL 5 minutes), and returns a `preview_token` along with the masked diff.

**Step 5b - Apply:**

```json
{ "preview_token": "b7e4f9a2-1c3d-4e5f-8a9b-0c1d2e3f4a5b" }
```

Call `record_apply` with the `preview_token` parameter (not `token` - the parameter name is `preview_token`). The server consumes the token, re-validates write permissions, and executes the update. The token is single-use; replaying it returns `"Invalid or expired preview token"`.

## 6. Resolve Choice Labels

Human-readable labels like "In Progress" do not match the numeric values ServiceNow stores. Call `resolve_choice` to map them:

```json
{ "table": "incident", "field": "state" }
```

With no `label` parameter, this returns all known choices for that field (e.g. open=1, in_progress=2, on_hold=3, resolved=6, closed=7, canceled=8). Pass `label` to resolve a single value:

```json
{ "table": "incident", "field": "state", "label": "in_progress" }
```

Labels are normalized: lowercase, spaces replaced with underscores.

## 7. Run an Investigation

The `investigate` tool dispatches predefined analysis playbooks. Call it with:

```json
{
  "action": "run",
  "name": "stale_automations",
  "params": "{\"stale_days\": 90}"
}
```

The parameter is `name` (not `investigation`). The `params` value is a JSON string parsed server-side.

Three actions are available:

- `run` - execute the investigation and return findings
- `explain` - given an `element_id` (format `table:sys_id`), ask all registered investigations to explain that element
- `describe` - list available investigations and their parameters (no platform I/O)

Seven investigations are registered: `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, `performance_bottlenecks`.

## 8. Production Safety

When `SERVICENOW_ENV` is set to `prod` or `production`, ALL write operations are blocked regardless of table. The server returns a serialized error envelope with the message:

```
Write operations are blocked in production environments
```

This applies to `record_write`, `record_apply`, `attachment_write`, and the write actions on `service_catalog` (ordering, cart operations). There is no override flag - the block is absolute for the environment.

Even outside production, 8 security-sensitive tables are permanently denied for writes (and reads): `sys_user_has_password`, `oauth_credential`, `oauth_entity`, `sys_certificate`, `sys_ssh_key`, `sys_credentials`, `discovery_credentials`, `sys_user_token`.

## Where to Go Next

- [[Tool-Reference]] - complete parameter and response documentation for every tool
- [[Investigations]] - the 7 registered playbooks, their parameters, and element_id format
- [[Safety-and-Policy]] - denied tables, field masking, query safety, and write gating in detail
