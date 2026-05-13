# Tool Reference

Complete reference for all tools provided by the ServiceNow Platform MCP server. The surface has been unified into 11 core tools that use dispatcher patterns and ServiceNow encoded queries.

All tools return responses as JSON strings with a standardized envelope containing `correlation_id`, `status`, `data`, and optionally `pagination` and `warnings`. See [[Architecture]] for details on the response format.

For security guardrails that apply across all tools, see [[Safety-and-Policy]]. For worked examples of complex queries and multi-tool workflows, see [Agent Recipes](../../docs/agent-recipes.md).

---

## Always-On Tool

The `list_tool_packages` tool is always available, regardless of which tool package is configured.

| Tool | Description | Key Parameters |
|---|---|---|
| `list_tool_packages` | List available tool packages and their contents | - |

---

## Introspection Tools

### `query`
Search and retrieve records from any table using ServiceNow encoded query strings.

- **Purpose:** Primary tool for finding records, auditing history (`sys_audit`), or checking logs (`syslog`).
- **Key Parameters:**
  - `table`: Target table name (e.g., `incident`).
  - `encoded_query`: ServiceNow query string (e.g., `active=true^priority=1`).
  - `fields`: Comma-separated field names to return.
  - `resolve_labels`: Optional label-to-value resolution (e.g., `state=open`).
  - `display_values`: If `true`, returns human-readable labels in a `_display` object.
  - `limit`, `offset`, `order_by`: Pagination and sorting.
- **Example:**
  ```python
  await query(table="incident", encoded_query="active=true^priority=1", fields="number,short_description")
  ```

### `build_query`
Stateless helper that compiles a JSON array of condition objects into a ServiceNow encoded query string. The result is returned in `data.query` for the caller to forward to `query`.

- **Purpose:** Let agents express filters as structured JSON without learning the encoded-query mini-language. The tool touches no ServiceNow APIs; it is a pure transform.
- **Availability:** `full` package only - readonly presets pass encoded queries to `query` directly.
- **Key Parameters:**
  - `conditions`: JSON-encoded array of condition objects. Each object carries an `operator`, a `field` (omitted for `new_query`), and operator-specific keys (`value`, `start`/`end`, `other_field`, `part`/`dp_operator`/`dp_value`, `related_table`/`related_field`/`rl_operator`, `descending`).
- **Operator groups:**
  - **Comparison:** `equals`, `not_equals`, `greater_than`, `greater_or_equal`, `less_than`, `less_or_equal`
  - **String:** `contains`, `starts_with`, `like`, `ends_with`, `not_like`, `does_not_contain`
  - **Null / special:** `is_empty`, `is_not_empty`, `anything`, `empty_string`
  - **Time:** `hours_ago`, `minutes_ago`, `days_ago`, `older_than_days`
  - **Date:** `on`, `not_on`, `relative_gt`, `relative_lt`, `more_than`
  - **Date part:** `datepart`
  - **Range:** `between`
  - **Field comparison:** `gt_field`, `lt_field`, `gt_or_equals_field`, `lt_or_equals_field`, `same_as`, `not_same_as`
  - **Reference:** `dynamic`, `in_hierarchy`
  - **Change detection:** `val_changes`, `changes_from`, `changes_to`
  - **Logical:** `new_query` (inserts `^NQ` separator)
  - **Related list:** `rl_query`
  - **List:** `in_list`, `not_in_list` (value is a list of strings)
  - **OR:** `or_equals`, `or_starts_with`
  - **Ordering:** `order_by` (optional `descending: true`)
- **Example:**
  ```python
  built = await build_query(conditions=json.dumps([
      {"operator": "equals",     "field": "active",         "value": "true"},
      {"operator": "less_or_equal", "field": "priority",    "value": "2"},
      {"operator": "hours_ago",  "field": "sys_created_on", "value": 24},
      {"operator": "order_by",   "field": "sys_created_on", "descending": True},
  ]))
  # built["data"]["query"] -> "active=true^priority<=2^sys_created_on>=javascript:gs.hoursAgoStart(24)^ORDERBYDESCsys_created_on"
  await query(table="incident", encoded_query=built["data"]["query"], fields="number,priority")
  ```

### `describe`
Retrieve schema and metadata for a table.

- **Purpose:** Understand a table's structure before querying or writing.
- **Key Parameters:**
  - `table`: Target table name.
  - `verbose`: If `true`, returns all platform metadata (otherwise returns a slim 8-key summary per field).
- **Example:**
  ```python
  await describe(table="incident")
  ```

---

## Record Management

The system uses a two-stage preview/apply flow for all record mutations.

### `record_write`
Unified tool for staging `create`, `update`, or `delete` actions.

- **Purpose:** Perform mutations with built-in safety checks and preview flow.
- **Key Parameters:**
  - `action`: One of `create`, `update`, or `delete`.
  - `table`: Target table name.
  - `sys_id`: Required for `update` and `delete`.
  - `data`: JSON string of field-value pairs for `create`/`update`.
  - `artifact_type`: Set this to write scripted artifacts (e.g., `business_rule`, `script_include`).
  - `script_path`: Local path to a script file (requires `artifact_type`).
  - `preview`: If `true` (default), stores the change in `PreviewTokenStore` and returns a `preview_token`.
- **Example:**
  ```python
  # Stage a create
  preview = await record_write(action="create", table="incident", data='{"short_description": "New issue"}')
  # Returns: {"data": {"preview_token": "uuid-token-here", ...}}
  ```

### `record_apply`
Commits a write operation previously staged with `record_write(preview=true)`.

- **Purpose:** Finalize a mutation after inspecting the preview.
- **Key Parameters:**
  - `preview_token`: The token returned by `record_write`.
- **Example:**
  ```python
  await record_apply(preview_token="uuid-token-here")
  ```

---

## Specialized Dispatchers

### `attachment`
Unified dispatcher for reading and downloading record attachments.

- **Actions:**
  - `list`: List metadata for all attachments on a record.
  - `get`: Fetch metadata for a specific attachment by sys_id.
  - `download`: Download attachment content as base64.
- **Example:**
  ```python
  await attachment(action="list", table_name="incident", table_sys_id="...")
  ```

### `attachment_write`
Dispatcher for attachment mutations. Included in all packages; blocked in production via write gating.

- **Actions:**
  - `upload`: Upload a base64-encoded file.
  - `delete`: Delete an attachment by sys_id.

### `investigate`
Runs pre-defined diagnostic and health check modules.

- **Actions:**
  - `run`: Execute a module (e.g., `stale_automations`, `table_health`).
  - `explain`: Interpret a specific finding from a previous run.
- **Modules:** `stale_automations`, `deprecated_apis`, `table_health`, `acl_conflicts`, `error_analysis`, `slow_transactions`, `performance_bottlenecks`.

### `resolve_choice`
Resolves human-readable labels to underlying ServiceNow values using the `sys_choice` table.

- **Key Parameters:**
  - `table`: Table name.
  - `field`: Field name.
  - `label`: Human label (e.g., "In Progress"). If omitted, returns all choices for the field.
- **Example:**
  ```python
  await resolve_choice(table="incident", field="state", label="New")
  ```

### `service_catalog`
Unified dispatcher for Service Catalog operations.

- **Actions:** `list_catalogs`, `get_catalog`, `list_categories`, `get_category`, `list_items`, `get_item`, `get_variables`, `order_now`, `add_to_cart`, `get_cart`, `submit_cart`, `checkout`.
- **Example:**
  ```python
  await service_catalog(action="list_items", text="laptop")
  ```

---

## Migration Note

The following specialized tool families from v0.9.x have been **deleted** and replaced by the unified tools above:
- ATF tools (Deleted entirely)
- Specialized domain tools (`incident_*`, `change_*`, etc. — Use `query`, `record_write`, and `resolve_choice`)
- Change Intelligence and Debug families (`changes_*`, `debug_*` — Use `query` against system tables like `sys_update_xml` or `syslog`)
- Documentation and Workflow families (`docs_*`, `workflow_*`, `flow_*` — Use `query` or `describe` against platform tables)
- `artifact_create`/`artifact_update` (Folded into `record_write`)

`build_query` is retained from v0.9.x but reshaped to be stateless and scoped to the `full` package. The `QueryTokenStore` is gone - `build_query` returns the encoded query string directly in `data.query` for the caller to forward to `query`. Agents may also bypass `build_query` entirely and pass encoded queries to `query` directly.

For detailed mapping of old workflows to new tools, see [Agent Recipes](../../docs/agent-recipes.md).
