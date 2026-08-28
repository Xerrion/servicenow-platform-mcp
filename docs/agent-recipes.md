# 📖 Agent Recipes

This document provides a set of "recipes" for common ServiceNow workflows using the unified tool surface.

The previous specialized helper tools (e.g., `incident_list`, `debug_trace`, `changes_updateset_inspect`) have been collapsed into a minimal, dispatcher-oriented API. Agents and developers now achieve complex tasks through multi-call joins, encoded query strings, and explicit choice resolution. These recipes demonstrate how to translate legacy tool usage into this new, more flexible vocabulary.

## 🛠 The Unified Surface at a Glance

| Tool | Purpose |
| :--- | :--- |
| `query` | Fetch records, aggregates, or single records from any table using encoded queries. |
| `describe` | Retrieve slim field metadata (8 keys) for a table to understand its structure. Use `action='list_script_fields'` with a `table` argument to discover the dictionary-driven script-bearing fields and the resolved super_class chain. |
| `record_read` | Read a record by `sys_id` or `name`. Returns the masked record plus the `script_fields` list resolved from `sys_dictionary` for the table. |
| `record_write` | Dispatcher for creating, updating, or deleting records (with `script_path` file injection and `script_field` targeting for tables with multiple script-bearing fields). |
| `record_apply` | Commits a write operation previously staged with `preview=True`. |
| `attachment` | Dispatcher for reading, listing, and downloading record attachments. |
| `attachment_write` | Dispatcher for uploading or deleting record attachments. |
| `investigate` | Runs pre-defined diagnostic investigations (e.g., health checks, bottleneck analysis). |
| `resolve_choice` | Maps human-readable labels to underlying ServiceNow choice values. |
| `service_catalog` | Dispatcher for Service Catalog operations (cart, categories, items). |
| `flow` | Inspects Flow Designer artifacts, finds record-triggered flows, lists triggers, and decodes compressed `values` blobs. |

## 🔍 Quick Reference: Encoded Query Syntax

ServiceNow [Encoded Queries](https://docs.servicenow.com/bundle/vancouver-platform-user-interface/page/use/using-lists/concept/c_EncodedQueryStrings.html) are the primary filter mechanism for the `query` tool.

| Operator | Syntax | Description |
| :--- | :--- | :--- |
| Equals | `state=1` | Field equals value |
| Not Equals | `state!=1` | Field does not equal value |
| In List | `priorityIN1,2` | Field is one of the comma-separated values |
| Starts With | `short_descriptionSTARTSWITHOutage` | Field starts with string |
| Contains | `short_descriptionLIKEvpn` | Field contains string |
| Date Filter | `sys_created_on>=javascript:gs.daysAgoStart(7)` | Use for large tables (syslog, sys_audit) |
| Sorting | `ORDERBYDESCnumber` | Append to query for ordering (use `ORDERBY<field>` for ascending) |
| AND | `active=true^priority=1` | Combine conditions with carets |
| OR | `state=1^ORstate=2` | Or condition for the same field |
| Dot-walk | `assignment_group.nameSTARTSWITHNetwork` | Access fields on referenced tables |

## 👨‍🍳 Recipes

### 1. List my open incidents by priority
**Goal:** Retrieve open incidents and filter by a human-readable priority label.  
**Old way:** `incident_list(state="open", priority="high")`  
**New way:**
```python
# Use resolve_labels to handle label-to-value mapping automatically
# This appends (state=1^priority=1) to the encoded_query internally
await query(
    table="incident",
    resolve_labels="state=open,priority=high",
    fields="number,short_description,priority,state"
)
```
**Notes:** `resolve_labels` is the most efficient way to query by label. It performs a ChoiceRegistry lookup before executing the query.

---

### 2. Create an incident with proper state value
**Goal:** Create a new record ensuring the 'state' field uses the correct underlying integer.  
**Old way:** `incident_create(state="open", short_description="...")`  
**New way:**
```python
import json

# 1. Resolve the label to a value
# Tool returns a JSON-serialized envelope string - parse it before indexing.
state_resp = json.loads(await resolve_choice(table="incident", field="state", label="New"))
state_val = state_resp["data"]["value"]  # "1"

# 2. Stage the create (preview=True by default)
# Returns a preview_token inside data
preview = json.loads(await record_write(
    action="create",
    table="incident",
    data='{"short_description": "Email service down", "state": "1"}'
))

# 3. Commit the change
await record_apply(preview_token=preview["data"]["preview_token"])
```
**Notes:** The preview/apply two-step is a mandatory safety mechanism for all writes. It allows the agent (or human) to inspect the impact before commitment. Every tool returns a JSON-serialized envelope string (see `format_response` in `responses.py`); always `json.loads(...)` before indexing into `data`.

---

### 3. Build a complex multi-condition query
**Goal:** Find incidents that are either New or In Progress, have High/Critical priority, and belong to a Network group.  
**Method:** Construct the encoded query string directly, or copy it from a ServiceNow filter breadcrumb.
```python
# Compose the query string directly
# (state=1 OR state=2) AND (priority <= 2) AND (group name starts with Network)
query_string = "stateIN1,2^priority<=2^assignment_group.nameSTARTSWITHNetwork"

await query(
    table="incident",
    encoded_query=query_string,
    fields="number,short_description,assignment_group.name"
)
```
**Notes:** Dot-walking (`assignment_group.name`) is supported. Use `^NQ` (New Query) for top-level OR conditions that require entirely separate filter sets.

---

### 4. Find recently modified business rules
**Goal:** Audit recent logic changes in the system.  
**Old way:** `meta_list_artifacts` or `meta_what_writes`  
**New way:**
```python
await query(
    table="sys_script",
    encoded_query="active=true^sys_updated_on>=javascript:gs.daysAgoStart(7)",
    fields="name,collection,sys_updated_on,sys_updated_by",
    order_by="-sys_updated_on",
    limit=50
)
```
**Notes:** `sys_script` stores Business Rules. The `collection` field indicates the target table.

---

### 5. Inspect an Update Set's contents
**Goal:** See exactly what files are included in a specific Update Set.  
**Old way:** `changes_updateset_inspect`  
**New way:**
```python
# 1. Verify the Update Set exists
# await query(table="sys_update_set", sys_id="<sys_id>")

# 2. List the captured changes (sys_update_xml)
await query(
    table="sys_update_xml",
    encoded_query="update_set=<sys_id>",
    fields="name,type,target_name,action",
    limit=100
)
```
**Notes:** `sys_update_xml` is the "Customer Update" table where individual modifications are tracked.

---

### 6. Debug recent script errors
**Goal:** Search system logs for errors occurring in the last 15 minutes.  
**Old way:** `debug_trace` or `debug_log_errors`  
**New way:**
```python
# level=2 is Error. Date filter is mandatory for syslog.
await query(
    table="syslog",
    encoded_query="level=2^sys_created_on>=javascript:gs.minutesAgo(15)",
    fields="message,source,sys_created_on",
    order_by="-sys_created_on",
    limit=50
)
```
**Notes:** Always include a time-based filter when querying `syslog` to avoid performance degradation and query rejection.

---

### 7. Audit who last touched a record
**Goal:** Get a history of field mutations for a specific record.  
**Old way:** `debug_field_mutation_story`  
**New way:**
```python
# sys_audit is a massive table; strict filtering is required.
await query(
    table="sys_audit",
    encoded_query="tablename=incident^documentkey=<sys_id>",
    fields="fieldname,oldvalue,newvalue,sys_created_by,sys_created_on",
    order_by="-sys_created_on",
    limit=100
)
```
**Notes:** For high-volume production instances, always combine `documentkey` with a `sys_created_on` filter if the history is expected to be long.

---

### 8. Discover a table's choice values for a field
**Goal:** Understand the available states or categories for a table without guessing.  
**Old way:** Implicitly handled by domain tools.  
**New way:**
```python
import json

# 1. Check if choices exist
meta = json.loads(await describe(table="incident", fields="state"))
# If meta["data"]["state"]["choice_count"] > 0...

# 2. Get the full mapping (label="" returns all)
choices = json.loads(await resolve_choice(table="incident", field="state"))
```
**Notes:** `resolve_choice` returns a dictionary mapping labels (e.g., "In Progress") to values (e.g., "2").

---

### 9. Update a Business Rule from a local file
**Goal:** Sync a script developed locally into a ServiceNow Business Rule.  
**Old way:** `artifact_update`  
**New way:**
```python
import json

# Stage the update using a local path.
# content is read, validated, and placed in the first script-bearing field
# resolved by DictionaryRegistry (here: sys_script.script).
preview = json.loads(await record_write(
    action="update",
    table="sys_script",
    sys_id="<sys_id>",
    script_path="/Users/dev/project/br_logic.js",
    preview=True
))

# Commit
await record_apply(preview_token=preview["data"]["preview_token"])
```
**Notes:** `script_path` must be within the directory defined by the `SCRIPT_ALLOWED_ROOT` setting. Files are capped at 1MB and must be UTF-8.

---

### 10. Aggregate incident counts by assignment group
**Goal:** Perform ad-hoc reporting to find which groups have the most active work.  
**Old way:** ad-hoc manual queries.  
**New way:**
```python
import json

# Get counts grouped by the reference field 'assignment_group'
report = json.loads(await query(
    table="incident",
    encoded_query="active=true",
    aggregate="count",
    group_by="assignment_group"
))

# report["data"] will contain list of {assignment_group: "sys_id", count: "42"}
```
**Notes:** Aggregate queries return the raw sys_id of the group. Call `query` with `sys_id` mode on `sys_user_group` to resolve the top group's name if needed.

---

### 11. Inspect Flow Designer artifacts

**Goal:** Understand which flows run for a table, obtain a concise data contract, inspect one flow's full canvas, or decode a compressed `values` field fetched manually.  
**Old way:** Query `sys_hub_*` tables by hand and decode `values` outside the MCP server.  
**New way:**

```python
# Find all flows triggered by a given table
await flow(action="find_by_table", table="incident")

# Inspect a specific flow by name
await flow(action="inspect", name="My Flow")

# Get fields, checks/actions, and literal data-pill mappings without raw canvas metadata
await flow(action="contract", name="My Flow")

# Decode a values blob fetched manually via query
await flow(action="decode_values", value="H4sIA...")

# Survey active record-update triggers across the platform
await flow(
    action="list_triggers",
    trigger_type="record_update",
    active="true",
    limit=50,
)
```

**Notes:** Use `flow(action="contract", ...)` when documenting or implementing an integration: each V2 action step includes configured `inputs` bindings plus a `definition` containing the action type's declared inputs and outputs. Declared fields come from `sys_hub_action_input` and `sys_hub_action_output` through their `action_type` relation; `type` appears only when `element_prototype` supplies a usable display label. The contract does not infer runtime values, infer semantics from an action label, or resolve data pills. Definition-table access or release variance is reported as a warning and per-action limitation instead of discarding the contract. V1 action or logic nodes are reported as warnings because their bindings cannot be reconstructed as contract steps. Use `inspect` only when raw Flow Designer metadata is required.

## 💡 Tips and Patterns

### Describe First
When working with an unfamiliar table, always call `describe(table="...")` first. It provides the field names, types, and mandatory flags in a slim format (8 keys per field). This is significantly lower "context cost" for the agent than fetching actual records.

### Use Display Values
When you need human-readable labels for reference fields (like `assigned_to`) or choice fields (like `state`) in a single pass, use `display_values=True` in your `query` call. This returns a `_display` object alongside the raw values, avoiding the need for multiple `resolve_choice` calls.

### Large Table Constraints
Queries against `syslog`, `sys_audit`, `sys_log_transaction`, and `sys_email_log` are gated. You **must** include a date filter (e.g., `sys_created_on>=javascript:gs.daysAgoStart(1)`) or the server will reject the query to protect instance performance.

### Pagination
The `query` tool returns a `pagination` object containing `offset`, `limit`, and `total`. To fetch the next page, call the tool again with the same parameters but increment the `offset` by the `limit`.

---

For detailed per-tool API documentation, see [Tool Reference](./wiki/Tool-Reference.md).
