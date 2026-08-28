# Tool Reference

Complete reference for all tools provided by the ServiceNow Platform MCP server. The surface has been unified into 13 core tools that use dispatcher patterns and ServiceNow encoded queries.

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

ServiceNow encoded queries are the only supported query construction interface. Copy a filter breadcrumb from a ServiceNow list, or construct the encoded query string directly, then pass it in `encoded_query`. Query safety still applies.

### `describe`
Retrieve schema and metadata for a table, or enumerate its script-bearing fields.

- **Purpose:** Understand a table's structure before querying or writing; discover dictionary-driven script fields at runtime.
- **Key Parameters:**
  - `action`: Optional. `describe_table` (default) or `list_script_fields`. When `list_script_fields`, returns the resolved super_class `chain` and the script-bearing fields (`name`, `internal_type`, `inherited_from`, `via_heuristic`) for the supplied `table`.
  - `table`: Target table name (required for both actions).
  - `verbose`: If `true`, returns all platform metadata (otherwise returns a slim 8-key summary per field).
- **Example:**
  ```python
  await describe(table="incident")
  await describe(action="list_script_fields", table="sys_script")
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
  - `script_path`: Local path to a script file. Allowed on any table that has at least one script-bearing field (resolved via `DictionaryRegistry` from `sys_dictionary`). Resolved strictly under `SCRIPT_ALLOWED_ROOT`; capped at 1 MB; UTF-8.
  - `script_field`: Optional. Target a specific script-bearing field on tables with more than one (e.g. `sys_ui_policy.script_true`/`script_false`, `sp_widget.client_script`/`template`/`css`, `sys_ui_page.html`/`processing_script`). Defaults to the first field returned by `DictionaryRegistry.get_script_fields(table)`. Setting `script_field` without `script_path` is rejected.
  - `preview`: If `true` (default), stores the change in `PreviewTokenStore` and returns a `preview_token`.
- **Notes:** When the resolved script field has `internal_type == 'xml'` (e.g. `sys_ui_macro.xml`), `record_write` validates the rendered XML (`xml.etree.ElementTree.fromstring`) before any platform call; malformed content is rejected with a structured error.
- **Example:**
  ```python
  # Stage a create
  preview = await record_write(action="create", table="incident", data='{"short_description": "New issue"}')
  # Returns: {"data": {"preview_token": "uuid-token-here", ...}}
  ```

### `record_read`
Read-only counterpart to `record_write` for any table.

- **Purpose:** Inspect an existing record (and learn its script-bearing fields) before composing a multi-field update via `record_write` + `script_field`.
- **Key Parameters:**
  - `table`: Target table name.
  - `sys_id` **or** `name`: Exactly one must be supplied. Ambiguous names (more than one match) and missing records return a structured error.
- **Response:** Masked record fields plus the `script_fields` list resolved from `sys_dictionary` for the table.
- **Availability:** Included in both the `full` and `readonly` packages.
- **Example:**
  ```python
  await record_read(table="sys_script", name="Validate priority on insert")
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

### `audit`

Inspect ServiceNow field-level auditing posture and masked history.

- **Purpose:** Resolve whether a `(table, field)` pair is actually audited (walking `super_class` and `sys_dictionary`), survey a table's audit posture, and fetch a masked, date-bounded audit trail for one record.
- **Availability:** Included in the `full` and `readonly` packages.
- **Actions:**
  - `check_field`: Resolve the combined audit verdict for one `(table, field)` pair. Returns the chain-walked dictionary flag, the `no_audit` attribute veto, the table-level flag, and a positive-control count from `sys_audit` within `window_days`.
  - `check_fields`: Batch variant of `check_field`. Accepts `fields_csv` (max 50) and returns one verdict per field plus a single shared `table_change_count`.
  - `check_table`: Table-level posture - the table default plus the list of fields whose resolved audit flag differs from that default.
  - `history`: Masked, date-bounded audit trail for one record. Queries `sys_audit` by `tablename` + `documentkey` and masks sensitive fields via `mask_audit_entry`.
  - `describe`: Return the action registry without platform I/O.
- **Key Parameters:**
  - `action`: One of `check_field`, `check_fields`, `check_table`, `history`, or `describe`.
  - `table`: Target table name (required for all actions except `describe`).
  - `field`: Field name (required for `check_field`).
  - `fields_csv`: Comma-separated field names, max 50 (required for `check_fields`).
  - `sys_id`: Document sys_id for `history`.
  - `window_days`: Override the default 90-day window for `sys_audit` queries. Wider windows risk timeouts.
  - `since`: Explicit ISO date floor for `history` (overrides `window_days`).
- **Notes:** `sys_audit` is one of the largest tables on the platform; every action that touches it applies a default 90-day window. Responses include the `window_days` actually used and a `window_note` describing it. `no_audit=true` in the `attributes` blob vetoes the boolean `audit` column. The positive-control count distinguishes "no field activity in window" (`audited_but_inactive`) from "audit not configured" (`inconclusive`).
- **Example:**

  ```python
  await audit(action="check_field", table="incident", field="state")
  await audit(action="check_fields", table="incident", fields_csv="state,priority,assigned_to")
  await audit(action="check_table", table="incident")
  await audit(action="history", table="incident", sys_id="<sys_id>", window_days=30)
  ```

### `flow`

Inspect ServiceNow Flow Designer artifacts from documented Table API records.

- **Purpose:** Read Flow Designer flows and subflows, including concise integration contracts, triggers, declared inputs/outputs/variables, decoded V2 action and logic configuration, canvas structure, and published snapshot drift.
- **Availability:** Included in the `full` and `readonly` packages. Custom packages can include it with `MCP_TOOL_PACKAGE=flow,query,describe`.
- **Actions:**
  - `contract`: Return an agent-oriented data contract for one flow/subflow. Requires exactly one of `sys_id` or `name`.
  - `inspect`: Assemble one flow/subflow. Requires exactly one of `sys_id` or `name`.
  - `find_by_table`: Find flows with a record trigger on `table`.
  - `decode_values`: Decode a gzip+base64+JSON `values` blob from a `sys_hub_*_v2` row. Requires `value`.
  - `list_triggers`: List record triggers across flows. Optional filters: `table`, `trigger_type`, `active` (`true`/`false`), `limit`.
  - `describe`: Return the action registry with names, descriptions, and parameters.
- **Key Parameters:**
  - `action`: One of `contract`, `inspect`, `find_by_table`, `decode_values`, `list_triggers`, or `describe`.
  - `sys_id`: 32-character flow sys_id for `contract` or `inspect`; mutually exclusive with `name`.
  - `name`: Flow name or `internal_name` for `contract` or `inspect`; must resolve to exactly one flow.
  - `table`: Target record table for `find_by_table`; optional filter for `list_triggers`.
  - `trigger_type`: Optional trigger filter for `list_triggers`, such as `record_update`.
  - `active`: Optional `true`/`false` filter for `list_triggers`.
  - `value`: Raw compressed `values` field content for `decode_values`.
  - `limit`: Optional page size for `list_triggers`; defaults to 100 when omitted or `0`.
- **Examples:**
  ```python
  await flow(action="contract", name="Provision Entra ID Group Membership")
  await flow(action="inspect", sys_id="9e858befc3340f105cf89fcd2b01317d")
  await flow(action="inspect", name="My Flow")
  await flow(action="find_by_table", table="incident")
  await flow(action="decode_values", value="H4sIA...")
  await flow(action="list_triggers", trigger_type="record_update", active="true", limit=50)
  await flow(action="describe")
  ```
- **`inspect` response highlights:** `data` contains `flow`, `triggers`, `inputs`, `outputs`, `variables`, `canvas`, `published_state`, `v1_actions`, and `v1_variable_values`.
  - `flow`: Flow metadata (`sys_id`, `name`, `internal_name`, `type`, `active`, `description`, `sys_scope`).
  - `published_state`: `{master_snapshot, latest_snapshot, drift}`. `drift` is `true` when the published snapshot differs from the latest authored snapshot.
  - `canvas`: Nested V2 tree. Root nodes have an empty `parent_ui_id`; children are sorted by `order`. Each node includes `kind` (`action` or `logic`), `ui_id`, `parent_ui_id`, `order`, `decoded_values`, and recursive `children`.
  - `warnings`: Returned in the standard response envelope for mixed V1/V2 flows, snapshot drift, IntegrationHub spoke heuristics, or V1 logic that cannot be woven into the V2 canvas tree.
- **`contract` response highlights:** `data` contains the flow header, published state, concise declared `inputs`, `outputs`, and `variables`, triggers, and ordered `steps`.
  - Each action step exposes its action type, configured `inputs`, and a concise `definition` with declared `inputs` and `outputs`. Definition fields include `name`, `label`, `required`, and, when available, `type`, input `default`, and `reference_table`. Each logic step exposes its `conditions`; `output_assignments` are included when stored configuration has them.
  - Action definitions are read from `sys_hub_action_input` and `sys_hub_action_output`, joined to `sys_hub_action_type_base` through `action_type`. `type` is emitted only when `element_prototype` provides a usable display label. Missing or inaccessible definition schema is reported in contract warnings and the affected action's `definition.limitations`.
  - Binding `value` is preserved exactly as configured. `data_pills` lists only `{{...}}` references found in that value; the tool does not infer action behavior or resolve a data pill's runtime value.
  - Contract steps represent V2 nodes only. If V1 actions or logic are present, `warnings` explains that their bindings cannot be reconstructed into ordered contract steps.
- **Notes:** The tool reads both V1 (`sys_hub_action_instance`, `sys_hub_flow_logic`, `sys_hub_trigger_instance`) and V2 (`sys_hub_action_instance_v2`, `sys_hub_flow_logic_instance_v2`, `sys_hub_trigger_instance_v2`) records. Record-trigger conditions are joined through `sys_flow_record_trigger`. It does not use the undocumented `/api/now/processflow/flow/{sys_id}` endpoint or the opaque `sys_hub_flow_snapshot` compiled cache. A bad per-node `values` blob adds `decode_error` to that node; the rest of `inspect` still succeeds.

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
- Documentation and Workflow families (`docs_*`, `workflow_*`, legacy `flow_*` - Use `flow` for Flow Designer inspection, or `query`/`describe` against platform tables)
- `artifact_create`/`artifact_update` (Folded into `record_write`)

The public `build_query` tool was removed. Pass encoded queries directly to `query`; the `QueryTokenStore` is also gone.

For detailed mapping of old workflows to new tools, see [Agent Recipes](../../docs/agent-recipes.md).
