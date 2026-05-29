# Tool Reference

Canonical per-tool reference for all 14 MCP tools registered by the ServiceNow Platform MCP server. For package selection see [[Tool-Packages]]. For safety policy and write gating see [[Safety-and-Policy]].

---

## Response Envelope

All tools except `list_tool_packages` return a JSON string produced by `format_response`. The envelope shape:

```json
{
  "correlation_id": "<uuid4>",
  "status": "success" | "error",
  "data": <any>,
  "error": {"message": "<str>"},
  "pagination": {"offset": 0, "limit": 20, "total": 42},
  "warnings": ["..."]
}
```

- `correlation_id` is injected by the `@tool_handler` decorator. It is not a caller parameter - do not pass it.
- `error` is present only when `status` is `"error"`. A string error is wrapped as `{"message": "<str>"}`.
- `pagination` and `warnings` are present only when applicable.

**Exception:** `list_tool_packages` is the only tool not wrapped by `@tool_handler`. It has no envelope, no `correlation_id`, and no error safety net.

---

## `list_tool_packages`

List all registered packages and the tool groups in each.

**Packages:** always loaded (regardless of `MCP_TOOL_PACKAGE`).

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| _(none)_ | - | - | - | Takes no parameters |

Returns a raw JSON dump of the package registry - not the standard envelope.

```json
{"full": ["query","build_query","describe","record_read","record_write","attachment","investigate","resolve_choice","service_catalog","audit","flow"], "readonly": [...], "core_readonly": [...], "none": []}
```

---

## `query`

Read records, run aggregates, or fetch a single record from any ServiceNow table.

**Packages:** `full`, `readonly`, `core_readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `table` | str | yes | - | Table name |
| `sys_id` | str | no | - | Fetch one record by sys_id |
| `encoded_query` | str | no | - | ServiceNow encoded query string |
| `fields` | str | no | - | Comma-separated field projection |
| `limit` | int | no | `20` | Max rows (capped by `MAX_ROW_LIMIT`) |
| `offset` | int | no | `0` | Pagination offset |
| `order_by` | str | no | - | Field name; prefix `-` for descending |
| `display_values` | bool | no | `False` | Return display values for reference/choice fields |
| `aggregate` | str | no | - | Aggregation spec (e.g. `count,avg:duration`) |
| `group_by` | str | no | - | Group aggregate results by field |
| `resolve_labels` | str | no | - | `field=label` pairs resolved via ChoiceRegistry |

**Modes:** Three mutually exclusive modes resolved before table validation:

1. **sys_id mode** - `sys_id` set: single-record fetch.
2. **Aggregate mode** - `aggregate` set (with optional `group_by`): Stats API.
3. **Query mode** - neither: paginated record list.

**Mutex rules** (each returns a validation error):

- `sys_id` + `aggregate`: `"Cannot combine sys_id with aggregate; sys_id mode fetches a single record."`
- `sys_id` + `group_by`: `"Cannot combine sys_id with group_by; sys_id mode fetches a single record."`
- `group_by` without `aggregate`: `"group_by requires aggregate to be set (aggregate mode only)."`

**Example:**

```json
{"table": "incident", "encoded_query": "priority=1^state=2", "limit": 5, "order_by": "-sys_created_on"}
```

---

## `describe`

Return field metadata for a table, or list its script-bearing fields.

**Packages:** `full`, `readonly`, `core_readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `table` | str | yes | - | Table name |
| `fields` | str | no | - | Comma-separated field filter (bare mode only) |
| `verbose` | bool | no | `False` | Full sys_dictionary rows minus noise fields |
| `include_docs` | bool | no | `False` | Attach sys_documentation entries |
| `action` | str | no | `""` | Only valid value: `"list_script_fields"` |

**Bare mode** (default, `action` omitted or empty) is the standard field-metadata flow. It requires `table`. Returns `data.fields` (slim field metadata list), `data.field_count`, and optional `data.documentation`. Error when `table` is missing: `"table is required when action is not set."`.

**`action="list_script_fields"`** walks the `super_class` chain and returns script-bearing fields with inheritance info. Returns `data.script_fields` and `data.chain`.

**Unknown action error:** `"Unknown describe action '<value>'. Valid actions: ['list_script_fields']."`

Common mistake: callers pass `action="fields"` or `action="table"` expecting field metadata. Both are wrong. To get field metadata, omit `action` entirely.

**Example:**

```json
{"table": "sys_script", "action": "list_script_fields"}
```

---

## `record_read`

Fetch a single record by sys_id or name, with script-field discovery.

**Packages:** `full`, `readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `table` | str | yes | - | Table name |
| `sys_id` | str | no | - | Record sys_id |
| `name` | str | no | - | Resolve via `name=<value>` query |

**Cross-parameter constraints:** Exactly one of `sys_id` or `name` must be supplied.

- Both: `"Provide exactly one of sys_id or name, not both."`
- Neither: `"Provide exactly one of sys_id or name."`
- Ambiguous name: `"Ambiguous name='<value>' on table '<table>': multiple records match."`
- Not found: `"No record found with name='<value>' on table '<table>'."`

**Returns:** `data.record` (masked), `data.script_fields` (list from DictionaryRegistry).

**Example:**

```json
{"table": "sys_script_include", "name": "TableUtils"}
```

---

## `record_write`

Create, update, or delete a record. **Default is `preview=True`** - the call validates and stores the payload, returning a single-use token. Direct commit requires `preview=False`.

**Packages:** `full`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `create`, `update`, or `delete` |
| `table` | str | yes | - | Target table |
| `sys_id` | str | update/delete | - | Record sys_id |
| `data` | str | create/update | - | JSON string of field values (max 1 MiB) |
| `script_path` | str | no | - | Absolute path to a local script file (max 1 MB, UTF-8) |
| `script_field` | str | no | - | Override target field for script_path content |
| `preview` | bool | no | `True` | When true, returns a preview token instead of committing |

**Per-action requirements:**

| Action | Required | Forbidden |
|--------|----------|-----------|
| `create` | `table`, `data` | `sys_id` |
| `update` | `table`, `sys_id`, `data` | - |
| `delete` | `table`, `sys_id` | `data` |

**Cross-parameter constraints:**

- `script_field` without `script_path`: `"script_field requires script_path to be set."`
- `data` exceeds 1 MiB: `"payload exceeds maximum allowed size of 1 MiB"`
- Unknown action: `"Unknown action '<value>'. Valid actions: ['create', 'delete', 'update']."`

**Preview flow:** Response includes `data.preview_token` (TTL 300s, single-use). Pass it to `record_apply` to commit. Updates include a diff of old vs. new values.

**Script path errors:**

- Opaque rejection (not found, outside root, symlink, not regular file): `"script_path is not readable or is outside the allowed root"`
- Config missing: `"script_allowed_root must be configured when using script_path"`
- XML validation failure: `"XML content is not well-formed: <detail>"`

**Example:**

```json
{"action": "update", "table": "sys_script_include", "sys_id": "abc123...", "data": "{\"active\": \"true\"}", "preview": false}
```

---

## `record_apply`

Commit a previously previewed write operation.

**Packages:** `full`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `preview_token` | str | yes | - | Token from `record_write` preview response |

The parameter is `preview_token`, not `token`.

**Notable errors:**

- Invalid or expired: `"Invalid or expired preview token"`
- Unknown payload action: `"Unknown preview action: '<action>'"`

Re-runs `check_table_access` and `write_gate` as defense in depth before committing.

**Example:**

```json
{"preview_token": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
```

---

## `attachment`

Read attachment metadata and content. This is the read-only half; writes use `attachment_write`.

**Packages:** `full`, `readonly`, `core_readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `list`, `get`, `download`, or `download_by_name` |
| `sys_id` | str | get/download | - | Attachment sys_id |
| `table` | str | list/download_by_name | - | Parent table name |
| `table_sys_id` | str | list/download_by_name | - | Parent record sys_id |
| `file_name` | str | download_by_name | - | Attachment file name |

**Per-action requirements:**

| Action | Required params | Purpose |
|--------|----------------|---------|
| `list` | `table`, `table_sys_id` | List attachments for a parent record |
| `get` | `sys_id` | Get metadata for one attachment |
| `download` | `sys_id` | Download content (base64 in `data.content_base64`) |
| `download_by_name` | `table`, `table_sys_id`, `file_name` | Resolve by name then download |

**Notable errors:**

- Unknown action: `"Unknown action '<value>'. Valid actions: ['download', 'download_by_name', 'get', 'list']."`
- Not found (download_by_name): `"Attachment '<file_name>' was not found for table '<table>' and record '<table_sys_id>'"`

**Example:**

```json
{"action": "download", "sys_id": "abc123def456..."}
```

---

## `attachment_write`

Upload or delete attachments. Disjoint action enum from `attachment` (read). Write-gated in production.

**Packages:** `full`, `readonly`, `core_readonly` (write actions blocked at runtime by `write_gate` in production).

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `upload` or `delete` |
| `table` | str | upload | - | Parent table name |
| `table_sys_id` | str | upload | - | Parent record sys_id |
| `file_name` | str | upload | - | File name for the attachment |
| `content_base64` | str | upload | - | Base64-encoded file content |
| `content_type` | str | no | `"application/octet-stream"` | MIME type |
| `sys_id` | str | delete | - | Attachment sys_id to delete |

**Per-action requirements:**

| Action | Required params | Purpose |
|--------|----------------|---------|
| `upload` | `table`, `table_sys_id`, `file_name`, `content_base64` | Attach file to a record (max 10 MB decoded) |
| `delete` | `sys_id` | Delete an attachment |

**Notable errors:**

- Size exceeded: `"Attachment <operation> size <N> bytes exceeds the maximum supported size of 10485760 bytes"`
- Bad base64: `"Invalid base64 content"`

**Example:**

```json
{"action": "upload", "table": "incident", "table_sys_id": "abc123...", "file_name": "notes.txt", "content_base64": "SGVsbG8gV29ybGQ="}
```

---

## `investigate`

Run predefined investigation playbooks, explain findings, or discover available investigations.

**Packages:** `full`, `readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `run`, `explain`, or `describe` |
| `name` | str | run/describe | - | Investigation name |
| `params` | str | no | `"{}"` | JSON string of run parameters |
| `element_id` | str | explain | - | `table:sys_id` identifier of a finding |

The investigation identifier parameter is `name`, not `investigation`.

**Per-action requirements:**

| Action | Required params | Purpose |
|--------|----------------|---------|
| `run` | `name` | Execute a named investigation |
| `explain` | `element_id` | Explain a finding (trial-dispatches across all modules) |
| `describe` | _(optional `name`)_ | List all investigations, or describe one by name |

**Available investigations:** `acl_conflicts`, `deprecated_apis`, `error_analysis`, `performance_bottlenecks`, `slow_transactions`, `stale_automations`, `table_health`.

**Notable errors:**

- Unknown action: `"Unknown action '<value>'. Expected one of: ['describe', 'explain', 'run']."`
- Unknown investigation: `"Unknown investigation '<name>'. Available: <comma-separated list>"`
- Bad element_id: `"element_id must be in 'table:sys_id' format"`

**Example:**

```json
{"action": "run", "name": "stale_automations", "params": "{\"stale_days\": 60, \"limit\": 10}"}
```

---

## `resolve_choice`

Map a human-readable choice label to its platform value, or list all choices for a field.

**Packages:** `full`, `readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `table` | str | yes | - | Table name |
| `field` | str | yes | - | Field name |
| `label` | str | no | `""` | Label to resolve; empty returns the full mapping |

When `label` is empty, returns `data.choices` (full `{label: value}` map). When set, returns `data.value`.

**Notable errors:**

- Registry unavailable: `"ChoiceRegistry not configured."`
- Unresolved warning: `"resolve_choice: '<field>=<label>' did not resolve via ChoiceRegistry; returning the label verbatim as the value."`

**Example:**

```json
{"table": "incident", "field": "state", "label": "in_progress"}
```

---

## `service_catalog`

Service Catalog operations - browse catalogs, items, and place orders.

**Packages:** `full`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | See action table below |
| `sys_id` | str | varies | - | Catalog/category/item sys_id |
| `item_sys_id` | str | order_now/add_to_cart | - | Item to order |
| `catalog_sys_id` | str | categories_list | - | Parent catalog |
| `catalog` | str | no | - | Filter items_list by catalog |
| `category` | str | no | - | Filter items_list by category |
| `text` | str | no | - | Search text |
| `variables` | str | no | - | JSON object of form variables |
| `limit` | int | no | `20` | Max results |
| `offset` | int | no | `0` | Pagination offset |
| `top_level_only` | bool | no | `False` | Top-level categories only |

**Actions:**

| Action | Required params | Write-gated |
|--------|----------------|-------------|
| `catalogs_list` | - | no |
| `catalog_get` | `sys_id` | no |
| `categories_list` | `catalog_sys_id` | no |
| `category_get` | `sys_id` | no |
| `items_list` | - | no |
| `item_get` | `sys_id` | no |
| `item_variables` | `sys_id` | no |
| `order_now` | `item_sys_id` | yes (`sc_req_item`) |
| `add_to_cart` | `item_sys_id` | yes (`sc_cart_item`) |
| `cart_get` | - | no |
| `cart_submit` | - | yes (`sc_request`) |
| `cart_checkout` | - | yes (`sc_request`) |

**Notable errors:**

- Unknown action: `"Unknown action '<value>'. Valid actions: [<sorted list>]."`

**Example:**

```json
{"action": "item_get", "sys_id": "abc123def456..."}
```

---

## `build_query`

Stateless helper that builds a ServiceNow encoded query string from structured conditions. Pass the result to `query`.

**Packages:** `full` only.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `conditions` | str | yes | - | JSON array of condition objects (max 256 KiB) |

Each condition object requires `operator` (str) and `field` (str, except for `new_query`). Additional keys vary by operator family.

**Operator families:**

- Unary: `is_empty`, `is_not_empty`, `anything`, `empty_string`, `val_changes`
- Binary: `equals`, `not_equals`, `greater_than`, `contains`, `starts_with`, `like`, etc.
- Time: `hours_ago`, `minutes_ago`, `days_ago`, `older_than_days`
- List: `in_list`, `not_in_list`
- Field comparison: `gt_field`, `lt_field`, `same_as`, `not_same_as`, etc.
- OR: `or_equals`, `or_starts_with`
- Special: `between`, `datepart`, `new_query`, `rl_query`, `order_by`

**Notable errors:**

- Size exceeded: `"conditions exceeds maximum size of 262144 bytes"`
- Bad JSON: `"Invalid JSON: <detail>"`
- Not array: `"conditions must be a JSON array"`
- Bad element: `"conditions[<idx>] must be a JSON object, got <type>"`

**Returns:** `data.query` - the encoded query string.

**Example:**

```json
{"conditions": "[{\"operator\": \"equals\", \"field\": \"priority\", \"value\": \"1\"}, {\"operator\": \"order_by\", \"field\": \"sys_created_on\", \"descending\": true}]"}
```

---

## `audit`

Inspect audit configuration and retrieve audit trail history. Read-only.

**Packages:** `full`, `readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `check_field`, `check_fields`, `check_table`, `history`, or `describe` |
| `table` | str | most actions | - | Table name |
| `field` | str | check_field | - | Field name |
| `fields_csv` | str | check_fields | - | Comma-separated fields (max 50) |
| `sys_id` | str | history | - | Record sys_id |
| `since` | str | no | - | `YYYY-MM-DD` cutoff (overrides window_days) |
| `window_days` | int | no | `0` (uses default 90) | Audit window in days |
| `limit` | int | no | `0` (uses max_row_limit) | Row cap for history |

**Per-action requirements:**

| Action | Required params | Purpose |
|--------|----------------|---------|
| `check_field` | `table`, `field` | Resolve audit verdict for one (table, field) pair |
| `check_fields` | `table`, `fields_csv` | Batch verdict (max 50 fields per call) |
| `check_table` | `table` | Table-level posture + field overrides |
| `history` | `table`, `sys_id` | Masked audit trail (date-bounded) |
| `describe` | - | Return action registry |

**Verdict values:** `audited`, `not_audited_field_flag`, `not_audited_table_flag`, `audited_but_inactive`, `inconclusive`.

**Notable errors:**

- Empty fields_csv: `"fields_csv must list at least one field."`
- Too many fields: `"At most 50 fields per check_fields call (got <N>)."`

`sys_audit` is one of the largest platform tables. The default 90-day window exists for performance - widen with care.

**Example:**

```json
{"action": "check_field", "table": "incident", "field": "state"}
```

---

## `flow`

Read-only Flow Designer inspection.

**Packages:** `full`, `readonly`.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `action` | str | yes | - | `inspect`, `find_by_table`, `decode_values`, `list_triggers`, or `describe` |
| `sys_id` | str | inspect | - | Flow sys_id (mutually exclusive with `name`) |
| `name` | str | inspect | - | Flow name (mutually exclusive with `sys_id`) |
| `value` | str | decode_values | - | gzip+base64+JSON blob to decode |
| `table` | str | find_by_table | - | Target table |
| `trigger_type` | str | no | - | Filter for list_triggers |
| `active` | str | no | - | Must be `""`, `"true"`, or `"false"` |
| `limit` | int | no | `0` (default 100) | Row cap for list_triggers |

**Per-action requirements:**

| Action | Required params | Purpose |
|--------|----------------|---------|
| `inspect` | exactly one of `sys_id` or `name` | Full flow assembly: header, triggers, I/O, canvas tree, snapshot drift |
| `find_by_table` | `table` | Find flows with record triggers on a table |
| `decode_values` | `value` | Stateless decode of a V2 values blob |
| `list_triggers` | _(all optional)_ | List V1+V2 triggers with optional filters |
| `describe` | - | Return action registry |

**`active` parameter constraint:** Must be `""`, `"true"`, or `"false"`. Any other value: `"'active' must be 'true', 'false', or '' (got '<value>')."`

**Notable errors (inspect):**

- Both sys_id and name: `"Provide exactly one of sys_id or name (not both)."`
- Neither: `"Either sys_id or name is required."`
- Name ambiguous: `"Name '<name>' is ambiguous (<N> flows match); pass sys_id instead."`
- Not found by name: `"No flow found with name '<name>'."`
- Not found by sys_id: `"Flow <sys_id> not found."`

**Deliberately excluded:** `/api/now/processflow/*` endpoints (undocumented) and `sys_hub_flow_snapshot` (opaque compiled cache).

**Example:**

```json
{"action": "inspect", "name": "My Incident Flow"}
```
