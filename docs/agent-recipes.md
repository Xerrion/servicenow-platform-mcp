# Agent Recipes

Worked examples for AI agents (or developers scripting agents) integrating with the ServiceNow Platform MCP server. Each recipe is a self-contained call sequence you can adapt directly.

For package configuration details, see [Tool Packages](wiki/Tool-Packages.md).

---

## 1. Discover what is available

Start every session by learning which tool packages exist and what they contain.

```
list_tool_packages()
```

This returns the registry of preset packages (`full`, `readonly`, `core_readonly`, `none`) and the tool groups each contains. No `correlation_id` or error envelope - this tool is unwrapped.

To discover which fields on a table carry executable script content:

```
describe(action="list_script_fields", table="sys_script_include")
```

Returns the super_class chain and a list of `{name, internal_type, inherited_from, via_heuristic}` entries for every script-bearing field detected from `sys_dictionary`.

---

## 2. Field metadata

Retrieve the schema for any table with a bare `describe` call (no `action` parameter):

```
describe(table="incident")
```

Returns table metadata, field list (with label, type, max_length, mandatory, read_only, reference_table, choice_count), and field count.

**Anti-pattern:** Do not pass an invented action value. For example, `action="fields"` produces:

```
"Unknown describe action 'fields'. Valid actions: ['list_script_fields']."
```

The only valid action is `list_script_fields`. Omit `action` entirely for normal field metadata.

---

## 3. Filtered query

Fetch active high-priority incidents:

```
query(table="incident", encoded_query="active=true^priority<=2", limit=20)
```

### Encoded-query cheat sheet

| Syntax | Meaning |
|--------|---------|
| `^` | AND |
| `^OR` | OR |
| `=` | Equals |
| `!=` | Not equals |
| `<`, `<=`, `>`, `>=` | Numeric/date comparison |
| `LIKE` | Contains (case-insensitive) |
| `STARTSWITH` | Starts with |
| `IN` | Value in comma-separated list |
| `ISEMPTY` | Field is empty |
| `ISNOTEMPTY` | Field is not empty |

Example combining operators:

```
active=true^priority<=2^stateBETWEEN1@3^categoryIN network,hardware
```

Date-bounded queries are required for large tables (`syslog`, `sys_audit`, `sys_log_transaction`, `sys_email_log`). Add a date constraint like `sys_created_on>=2025-01-01`.

---

## 4. Aggregate query

Count incidents grouped by state:

```
query(table="incident", aggregate="count", group_by="state")
```

The `aggregate` parameter accepts comma-separated tokens: `count`, or `op:field` where op is one of `avg`, `sum`, `min`, `max`.

```
query(table="incident", aggregate="count,avg:reassignment_count", group_by="priority")
```

**Mode mutex rules:**

- `sys_id` cannot combine with `aggregate` or `group_by`.
- `group_by` requires `aggregate` to be set.

Violating these returns an error envelope - not an exception.

---

## 5. Build an encoded query programmatically

The `build_query` tool (available in the `full` package only) accepts a JSON array of condition objects and returns the encoded query string:

```
build_query(conditions='[{"operator":"equals","field":"active","value":"true"},{"operator":"less_or_equal","field":"priority","value":"2"},{"operator":"order_by","field":"sys_created_on","descending":true}]')
```

Returns `{"query": "active=true^priority<=2^ORDERBYDESCsys_created_on"}` in the `data` field. Pass the result directly to the `query` tool's `encoded_query` parameter.

---

## 6. Read a single record

By sys_id:

```
record_read(table="sys_script_include", sys_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")
```

By name:

```
record_read(table="sys_script_include", name="MyScriptInclude")
```

Exactly one of `sys_id` or `name` must be supplied. Providing both returns:

```
"Provide exactly one of sys_id or name, not both."
```

The response includes the masked record and the `script_fields` list resolved by `DictionaryRegistry` for that table.

---

## 7. Update with preview-then-apply

The default write flow uses a two-call sequence with a single-use preview token.

**Step 1 - Preview:**

```
record_write(
  action="update",
  table="incident",
  sys_id="abc123def456abc123def456abc123de",
  data='{"state": "6"}',
  preview=true
)
```

Response includes a `preview_token` and a diff showing old vs new values (sensitive fields masked).

**Step 2 - Apply:**

```
record_apply(preview_token="<token from step 1>")
```

Tokens are single-use and expire after 5 minutes.

**Skipping preview:** Set `preview=false` for a direct commit without the token round-trip:

```
record_write(action="create", table="incident", data='{"short_description":"New issue"}', preview=false)
```

**Production environments block ALL writes.** When `SERVICENOW_ENV` is `prod` or `production`, every write operation returns a gating error - no override exists at the tool level.

---

## 8. Upload an attachment

```
attachment_write(
  action="upload",
  table="incident",
  table_sys_id="abc123def456abc123def456abc123de",
  file_name="screenshot.png",
  content_base64="iVBORw0KGgoAAAANS..."
)
```

Maximum attachment size is 10 MB. The `content_base64` field must be standard base64-encoded file content. An optional `content_type` parameter defaults to `"application/octet-stream"`.

---

## 9. Run an investigation

```
investigate(action="run", name="stale_automations", params='{"stale_days":90}')
```

The `name` parameter selects from 7 registered investigations: `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, `performance_bottlenecks`.

Three actions are available:

| Action | Purpose |
|--------|---------|
| `run` | Execute the investigation (requires `name`) |
| `explain` | Trial-run all modules against an `element_id` in `table:sys_id` format |
| `describe` | Return available investigations and their parameters |

Use `describe` with no `name` to list all investigations, or with a `name` to see that module's accepted parameters.

---

## 10. Resolve a choice label

Map human-readable state labels to their numeric values:

```
resolve_choice(table="incident", field="state")
```

Returns all known label-to-value mappings for that field. To resolve a single label:

```
resolve_choice(table="incident", field="state", label="resolved")
```

Returns `{table: "incident", field: "state", label: "resolved", value: "6"}`.

---

## 11. Inspect a Flow

```
flow(action="inspect", name="My Approval Flow")
```

Returns the full flow structure: triggers, inputs, outputs, variables, decoded V2 action/logic nodes, canvas tree, published snapshot drift, and warnings.

You can also pass `sys_id` instead of `name` (exactly one required).

To find flows triggered by a specific table:

```
flow(action="find_by_table", table="incident")
```

To list triggers with filtering:

```
flow(action="list_triggers", table="incident", active="true")
```

The `active` parameter accepts three values: `"true"`, `"false"`, or `""` (empty string for no filter). Any other value returns:

```
"'active' must be 'true', 'false', or '' (got ...)"
```

---

## 12. Audit a field

Check whether a field is being audited:

```
audit(action="check_field", table="incident", field="priority")
```

Returns a verdict (`audited`, `not_audited_field_flag`, `not_audited_table_flag`, `audited_but_inactive`, or `inconclusive`), the super_class chain walk, attribute analysis, and recent activity counts.

**Default 90-day window.** All audit actions that read `sys_audit` apply a 90-day lookback by default. The response includes a `window_note`:

```
"Default 90-day window used (sys_audit is large; widen with care)."
```

Override with `window_days` if needed, but keep the default unless you have a specific reason - wider windows risk slow queries and timeouts.

To check multiple fields in one call:

```
audit(action="check_fields", table="incident", fields_csv="priority,state,assigned_to")
```

---

## 13. Service Catalog

List available catalogs:

```
service_catalog(action="catalogs_list")
```

Other catalog actions follow the same dispatch pattern - `catalog_get`, `categories_list`, `category_get`, `items_list`, `item_get`, `item_variables`, `order_now`, `add_to_cart`, `cart_get`, `cart_submit`, `cart_checkout`. Write actions (`order_now`, `add_to_cart`, `cart_submit`, `cart_checkout`) are subject to write gating.

---

## General notes

- Every tool except `list_tool_packages` returns a JSON envelope with `correlation_id`, `status`, `data`, and optionally `error`, `pagination`, `warnings`.
- Sensitive fields in responses are masked as `***MASKED***`.
- Tables in the denied list (`sys_user_has_password`, `oauth_credential`, `oauth_entity`, `sys_certificate`, `sys_ssh_key`, `sys_credentials`, `discovery_credentials`, `sys_user_token`) are blocked from all operations.
- Tool functions never raise to MCP - all errors are caught and returned as structured error envelopes.
