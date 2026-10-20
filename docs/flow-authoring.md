# Experimental flow authoring

Enable the `flow_write` tool group to register `flow_preview` and `mutation_apply`.
The `full` package includes this group. Read-only packages do not include it.

This adapter uses the undocumented `/api/now/processflow` API used by Workflow Studio.
Compatibility with other ServiceNow releases and OAuth REST access policies is not guaranteed.
The adapter does not use browser cookies, run server scripts, or write flow snapshots through the Table API.

## Supported operations

1. `flow_preview(action="create", name="Example draft", scope="global")` previews an inactive flow header.
   For a scoped application, supply its resolved `sys_scope.sys_id`. Scope has no inferred default.
2. `flow_preview(action="add_action", sys_id="<flow sys_id>", action_type_sys_id="<published action snapshot sys_id>", inputs={...})`
   previews appending one action with static string, choice, boolean, or integer inputs.
   Use `action="add_subflow"` with `subflow_sys_id="<subflow definition sys_id>"` to append a published subflow call.
   The adapter resolves the current published snapshot and sets Wait For Completion to true.
3. Apply the returned token with `mutation_apply(preview_token=...)` after review and authorization.
4. Inspect the saved flow with `flow(action="inspect", sys_id=..., sections=["flow", "canvas", "warnings"])`.
   Check Customer Updates to confirm capture in the intended update set.

`add_action` and `add_subflow` accept only linear drafts. Use `edit` for flows with triggers or flow logic.

## Structural edits

`flow_preview(action="edit", sys_id=..., edits=[...])` applies an ordered list of edits to one draft and
saves the result with one request. The preview shows the flow outline before and after the change.

| `op`                            | Fields                                                                                                                 | Effect                                                                                                                                                                                                                                                                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `set_trigger`                   | `type`, `inputs`, `replace`                                                                                            | Authors Service Catalog, record create/update/create-or-update, or classic daily/weekly/monthly/run-once/repeat draft trigger configuration.                                                                                                                                                            |
| `update_trigger`                | `inputs`                                                                                                               | Updates selected static trigger inputs without replacing its identity or opaque metadata.                                                                                                                                                                                                               |
| `remove_trigger`                | None                                                                                                                   | Removes the saved instance with an ID tombstone. Used references block removal.                                                                                                                                                                                                                         |
| `insert_logic`                  | `logic`, `inputs`, `parent` or `after` or `before`, `ref`                                                              | Adds If/Else If/Else, For Each, Try with paired Catch, Parallel/Parallel Branch, End, Exit Loop, or Skip Iteration.                                                                                                                                                                                     |
| `move_step`                     | `step`, `parent` or `after` or `before`                                                                                | Moves an existing step, also into or out of a branch.                                                                                                                                                                                                                                                   |
| `update_inputs`                 | `step`, `inputs`                                                                                                       | Changes typed input values. Native objects and arrays use declared child schemas.                                                                                                                                                                                                                       |
| `delete_step`                   | `step`, `children`, `cascade`                                                                                          | Deletes saved actions or logic with ID tombstones. Deletes saved subflow calls by omission from the complete draft list, not by deleting the referenced definition. Child handling is `reject` (default), `delete`, or `promote`. Connected branches require `cascade=connected` and `children=delete`. |
| `set_variables`                 | `inputs`, placement                                                                                                    | Assigns existing flow variables through Set Flow Variables.                                                                                                                                                                                                                                             |
| `assign_outputs`                | `outputs`, optional `step` or placement and `ref`                                                                      | Creates a native Assign Subflow Outputs step or updates selected assignment rows. Scalar values and typed pills only.                                                                                                                                                                                   |
| `clear_output_assignment`       | `step`, `name`                                                                                                         | Removes one assignment row. The declared output and logic step remain.                                                                                                                                                                                                                                  |
| `create_field`                  | `collection`, `name`, `label`, `type`, optional `parent`, `reference`, `hint`, `maxsize`, `mandatory`, `default_value` | Creates a scalar variable or a scalar/nested subflow interface declaration.                                                                                                                                                                                                                             |
| `update_field`                  | `collection`, `name`, declaration settings                                                                             | Updates metadata or an unused scalar's type/reference without changing its identity or name.                                                                                                                                                                                                            |
| `delete_field`                  | `collection`, `name`                                                                                                   | Removes an unused declaration, including Object descendants or an array item schema. No automatic consumer cleanup.                                                                                                                                                                                     |
| `create_stage`                  | `label`, `value`, optional `duration`, `always_show`, `states`, `ref`                                                  | Creates a standard stage definition. The optional ref addresses it in later edits.                                                                                                                                                                                                                      |
| `update_stage`                  | `stage`, `label`, `value`, `duration`, `always_show`, `states`, `order`                                                | Updates a standard stage without replacing unknown metadata. Order is a zero-based position.                                                                                                                                                                                                            |
| `attach_stage` / `detach_stage` | `stage`, `step`                                                                                                        | Explicitly attaches or detaches a stage at the beginning of a step. No implicit replacement.                                                                                                                                                                                                            |
| `delete_stage`                  | `stage`                                                                                                                | Removes an unattached standard stage. Dependent error stages block removal.                                                                                                                                                                                                                             |

Steps are referenced by sys_id, UI identifier, 1-based display order, or the `ref` alias of a block added
earlier in the same call. References are resolved before each edit changes the tree.

Example. Add a Service Catalog trigger, then place the third step inside an If block after the first step:

```json
[
  { "op": "set_trigger", "type": "service_catalog" },
  {
    "op": "insert_logic",
    "logic": "If",
    "after": "1",
    "ref": "open",
    "inputs": { "condition": "{{Service Catalog_1.request_item.state}}=1" }
  },
  { "op": "move_step", "step": "3", "parent": "open" }
]
```

The catalogs establish availability, not authoring support. `flow(action="describe")` includes the `studio-27-v2` capability ledger.
Each entry states inspectability, authorability, verification scope, and the reason for an unavailable adapter.
No new capability has live save verification in this phase.

Pills reference trigger outputs, `<producer UI ID>.<output path>`, `flow_variable.<name>`, or declared `subflow.<input>` fields.
The editor checks producer order, branch/loop scope, parallel path isolation and joins, and Try/Catch status visibility.
Reference fields require compatible tables. Collections cannot substitute for scalar values.
Whole Object and Array.String/Integer/Boolean/Object pills are supported for proven compatible schemas.
Use one exact pill value. Source and target must have matching recursive field names, types, and array item schemas.
Moves, deletions, and input updates revalidate all remaining bindings. Dangling references fail without clearing input values.
Record paths use bounded `sys_dictionary` inheritance reads. Each edit supports at most 100 distinct pills and ten path fields.
The editor rebuilds `label_cache` from the bindings in use.
Preview fingerprints include the reference metadata and dictionary evidence used for type validation.
Apply resolves them again and rejects a changed revision before saving.

Saved subflow-call deletion follows `removeActionFromFlow` (local bundle:205202-205236),
`addToDeletedInstances` (205365-205377), and `splitComponentInstancesForSavingToServer` (130432-130460).
The first removes the selected UI identity; the second adds tombstones only for action/logic; the third rebuilds the
complete `subFlowInstances` list. No subflow deleted flag or tombstone collection is sent. Preview reports removed call
IDs/UI IDs in `deleted_instances.subFlowInstances` and `deletion_methods.subFlowInstances=omit_from_complete_list`.
This manifest is local verification evidence, not a wire field. Save echo and independent GET must exclude both removed
identities and preserve every surviving call, published snapshot reference, input, and setting.
Surviving references to a deleted producer fail, including dynamic-input and output-assignment consumers.
Nonempty or malformed connection override mappings block deletion until their cleanup adapter is implemented.
The source-derived fixture is not a captured server response. This capability is offline-regression verified, not live verified.

For saved-call deletion, an existing `componentInstances` list must exactly mirror the three ordered step lists.
The planner rebuilds that duplicate list from the surviving steps. Missing, reordered, or mirror-only metadata blocks planning.
An absent mirror stays absent. This correction does not normalize save echoes or identify the earlier indexed mismatch.
Studio's `removeActionFromFlow` updates this list before `getUpdatedFlowToSaveOrPublish` splits it for saving.
`saveFlowToServer` then calls `cleanupFlowBeforePut` (local bundle:97521-97527,101697-101712).
When `TRIM_FLOW_PAYLOAD` is enabled, cleanup selects `FLOW_SCHEMA` fields (350835-350903), excluding the mirror and `alerts`.
Otherwise, cleanup retains them. This adapter does not infer that configuration or drop unknown top-level settings.
Ordinary call deletion leaves `alerts` unchanged. Connection override cleanup is a separate native path
(253620-253663); this adapter still blocks nonempty mappings rather than guessing which mapping to remove.

### Record and classic scheduled triggers

These operations configure an inactive unpublished V2 flow draft.
They do not create an active schedule or a separate saved trigger definition.
`set_trigger` creates a trigger, or replaces it only with `replace=true`.
`update_trigger` changes selected input values while preserving the trigger ID, other inputs, and opaque settings.
`remove_trigger` removes the instance using native `deletedTriggers` ID tombstones for saved instances.
Subflow triggers, saved/template triggers, external triggers, and newer Recurrence/business-calendar definitions are not supported.
All operations use the existing single preview/apply draft PUT. They never publish, activate, run, or register a schedule separately.

#### Record triggers

Use `record_create`, `record_update`, or `record_create_or_update` from the current instance trigger catalog.
The instance definition supplies every parameter, choice value, default, and output schema.

```json
[
  {
    "op": "set_trigger",
    "type": "record_create",
    "inputs": {
      "table": "incident",
      "condition": "active=true",
      "run_flow_in": "background"
    }
  },
  {
    "op": "update_inputs",
    "step": "ui-of-existing-log",
    "inputs": { "log_message": "Created: {{Created_1.current.number}}" }
  }
]
```

Replace the step ID and use the current catalog's trigger label for the pill prefix.
`current` is typed through its catalog `internal_link` to the selected table without replacing the output parameter schema.
Record-field pills use the existing inherited dictionary resolver.
The table must have an exact readable `sys_db_object` entry and Studio table descriptor with a display field.
Table input metadata and dependent condition fields follow the native table/dependency reducers.
System tables and Requested Item (`sc_req_item`) are excluded. Use the dedicated Service Catalog family for Requested Item.

`condition` accepts a static encoded query. Blank conditions serialize as Studio's `^EQ`.
The adapter checks referenced dictionary fields using bounded reads, up to 50 clauses and ten fields per path.
Common comparisons, IN, empty checks, text operators, change checks, and static date operators are supported.
JavaScript, NQ query segments, ordering/grouping directives, and unsupported operators are rejected.
Changing `table` requires explicit values for every condition input. Conditions and consumers are never cleared implicitly.
Existing nonempty parsed-condition metadata blocks condition updates until its metadata adapter exists.

Run/session/user/extended-table settings use exact native catalog choices, including `run_on_extended`, `run_when_setting`,
`run_flow_in`, `run_when_user_setting`, and `run_when_user_list` where offered.
User lists are comma-separated sys_ids. Selecting an include/exclude user mode requires a nonempty list.
The catalog supplies any additional Updated/Created-or-Updated run-mode parameter. No run-mode key or choice value is invented.
Omitted inputs retain catalog defaults. An explicit choice must occur in the current choice list.
The fixture catalog contains a legacy `run_flow_in="any"` default outside its choice list.
That exact omitted default is preserved. Explicit edits must use a listed value, such as `background` or `foreground`.
Run-as/role settings outside the trigger remain unchanged.

#### Classic schedules

The five classic families use these exact input contracts:

| Type       | Native inputs                                                               |
| ---------- | --------------------------------------------------------------------------- |
| `daily`    | `glide_time="1970-01-01 HH:MM:SS"`                                          |
| `weekly`   | `day_of_week` from the current catalog choice list, plus `glide_time`       |
| `monthly`  | Native integer `integer` from 1-31, plus `glide_time`                       |
| `run_once` | `glide_date_time="YYYY-MM-DD HH:MM:SS"`                                     |
| `repeat`   | `glide_duration="YYYY-MM-DD HH:MM:SS"`, measured from `1970-01-01 00:00:00` |

```json
[
  {
    "op": "set_trigger",
    "type": "daily",
    "inputs": { "glide_time": "1970-01-01 09:30:00" }
  },
  { "op": "update_trigger", "inputs": { "glide_time": "1970-01-01 10:00:00" } }
]
```

Supply schedule values explicitly. No current clock value, next hour, or inferred timezone enters the preview.
Classic schedules use the instance timezone. The adapter does not convert values to the OAuth user's timezone or UTC.
`run_once` accepts past dates as configuration, matching Studio. This does not authorize activation or execution.
Repeat durations must be positive and meet the catalog's `minimum_seconds` when present. One day is `1970-01-02 00:00:00`.
Weekly choices are instance supplied. No weekday-to-number translation is guessed.
Unknown extra schedule inputs, timezone overrides, end dates, or Recurrence configuration return unavailable.
This adapter does not establish runtime DST handling, queue timing, or monthly dates absent from a particular month.

#### Preview and verification

Preview exposes `triggers_before` and `triggers` with static inputs and classic schedule timezone ownership.
The complete definition catalog, table descriptor, and condition dictionary evidence bind the preview and are read again at apply.
Removing/replacing a trigger or changing its source table rejects surviving pills, default/transform references, and inline trigger scripts.
Update consumers explicitly first. Source-preserving input updates keep existing pill paths valid.
Echo and independent GET must agree on the new assigned trigger ID. Existing IDs, values, outputs, and opaque settings remain strict.
An uncertain write still returns `UNKNOWN_OUTCOME` and is never replayed automatically.

Local source: catalog-to-instance construction (58112-58179), native time/duration/condition defaults (203551-203605),
trigger input updates and deletion (204942-205069), dependent table fields (156320-156374),
record descriptor read (164322-164328,446879-446893), output internal links (63341-63368), and classic schedule fields (96437-96464).
Local official references: `flow-triggers.md` describes classic flow triggers and instance timezone.
`create-scheduled-trigger.md` describes a different saved Recurrence contract, which remains excluded.
Source-derived tests cover all supported families offline. No live save or scheduling behavior is verified.

### Variables and subflow interfaces

`create_field`, `update_field`, and `delete_field` accept `collection="flowVariables"`, `"inputs"`, or `"outputs"`.
Supported scalar types are `string`, `integer`, `boolean`, `reference`, and `json`.
Variables work in inactive, unpublished flows and subflows. Interface CRUD requires an existing unpublished subflow without a trigger.
Flow-owned and trigger-owned interfaces are not replaced. Existing flow interface label/hint edits remain supported.
This increment edits existing subflow drafts. Header creation and action/subflow appending remain flow-only.

Example: declare a variable, assign it, then consume its typed pill in another step:

```json
[
  {
    "op": "create_field",
    "collection": "flowVariables",
    "name": "total",
    "label": "Total",
    "type": "integer"
  },
  { "op": "set_variables", "inputs": { "total": 3 }, "after": "1" },
  {
    "op": "update_inputs",
    "step": "ui-of-existing-log",
    "inputs": { "log_message": "Total: {{flow_variable.total}}" }
  }
]
```

Use `subflow.<name>` for a declared subflow input. New declarations are available to later edits in the same request.
Names are fixed lowercase identifiers, at most 80 characters. Labels must be unique within each collection.
`reference` requires a readable exact table match. `maxsize` accepts 1-65000. `mandatory` is a JSON boolean for inputs only.
`default_value` accepts static native scalar values for interfaces, not data pills. JSON defaults are valid JSON text.
Initialize variables through `set_variables`, not declaration defaults. Runtime values remain outside local verification.
Boolean declaration metadata uses Studio's false default. This does not prove runtime initialization.
Subflow interfaces also support nested Object/Array declarations as described below.
Dynamic types and declaration rename remain unavailable.
Unrelated complex declarations and opaque metadata are preserved.

Type/reference changes require no surviving consumers and no stored value. Defaults must remain valid for the resulting type.
Removal rejects pill references, assignment targets, transform prescriptions, source links, and script dependencies.
Update or remove those consumers explicitly before deleting the declaration. No values are silently cleared.
Only supported native bindings can be changed. Scripts and unsupported assignment families may require Studio editing first.
Local dependency checks cannot establish external callers of a subflow interface. Review caller compatibility before applying interface changes.

Preview returns `summary.fields_before` and `summary.fields` with declared settings, not opaque metadata.
New client UI identities remain stable across planning. Only new declarations may receive database IDs.
Save echo and independent read must agree on assigned IDs. Existing IDs, all values, types, interfaces, and unknown settings remain strict.
Removed identities cannot reappear under another field name. No new token or write endpoint is introduced.

Source evidence: `addInputsOutputs` (local bundle:22076-22125), `deleteInputsOutputs` (22298-22312),
scalar registration and mapping (22753-22826,24010-24023), variable reducer/modal (6382-6453,458852-459117),
typed variable pills (232960-233028), complete flow save (99103-99170), and separate declaration database IDs (471965,458913-458917).
Official local references: `create-flow-variables.md`, `flow-variables.md`, and `flow-logic-assign-subflow-outputs.md`
under `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/`.
These contracts are source-derived and fixture-tested, not live save verification.

### Nested interface declarations

Existing unpublished, triggerless subflows support `object`, `array.string`, `array.integer`, `array.boolean`, and `array.object` declarations.
Use the existing field operations. `create_field` accepts a local `name` and optional `parent` declaration path.
`update_field` and `delete_field` use the full declaration path as `name`.
Declare parents before children. Names are lowercase identifiers with at most 80 characters per segment and nine path segments.
Labels are unique among siblings. Each child receives its own stable UI identity and parent registration.

```json
[
  {
    "op": "create_field",
    "collection": "inputs",
    "name": "payload",
    "label": "Payload",
    "type": "object"
  },
  {
    "op": "create_field",
    "collection": "inputs",
    "parent": "payload",
    "name": "count",
    "label": "Count",
    "type": "integer"
  },
  {
    "op": "create_field",
    "collection": "inputs",
    "parent": "payload",
    "name": "rows",
    "label": "Rows",
    "type": "array.object"
  },
  {
    "op": "create_field",
    "collection": "inputs",
    "parent": "payload.rows.rows_child0",
    "name": "message",
    "label": "Message",
    "type": "string"
  },
  {
    "op": "update_field",
    "collection": "inputs",
    "name": "payload.rows.rows_child0.message",
    "hint": "Row message"
  }
]
```

Arrays register an item schema automatically at `<array path>.<local array name>_child0`.
The item type follows the array type and cannot change independently. Edit its label, hint, or maximum length by item path.
Add Object item fields under that path. Nested objects and arrays can occur within Object item fields.
Primitive array items cannot contain declarations. Removing an array removes its item schema.
Removing an object removes its declared descendants. Explicit child removal is also supported.
No consumer is removed or cleared. Used children, ancestor pills, output assignments, source links, transforms, and script dependencies block destructive changes.
Scalar leaf types can change only without consumers or stored values. Replace complex types through explicit deletion and creation.

New input leaves can supply typed pills such as `{{subflow.payload.count}}` in later edits of the same preview.
Collection pills still require supported loop or complex-value adapters. This increment does not allow arbitrary array indexing.
Outputs with nested schemas accept native Object/Array assignment values as described below.
Complex defaults remain unavailable. Whole Object/Array pills require the schema checks described below.

Studio restricts flow variables to scalar types (bundle:11156,344267-344269).
Nested flow-variable creation therefore remains unavailable. This restriction does not affect existing scalar variable authoring.
The Object child picker excludes direct Reference and JSON declarations (1222).
Existing Reference metadata inside an unrelated schema is preserved. Array.Reference, Array.Array, Choice, and date/time arrays require additional adapters.

The save payload stores Object children recursively. Array item settings are packed into the root array's `attributes`,
or a nested array's `fieldFacetMap`, including the item UI identity. Array.Object retains its field children beneath the array.
Only the edited root is converted. Its read/pack round trip must preserve every existing field before editing proceeds.
Unknown attributes, field facets, existing identities, unrelated schemas, and values remain strict during save verification.
Only explicitly new persisted nodes can receive database IDs. Echo and independent GET must agree on assigned IDs.
Synthetic array item IDs are UI identities in packed metadata, not invented database records.
Preview includes recursive schema settings under `fields_before` and `fields`, without opaque metadata or stored values.

Local source: child registration (22837-22878), Object/Array type selection (1196-1224), array child creation (21943-21998),
array item packing (23273-23319), read reconstruction (23379-23434,248895-248917), and complete save (99168-99170).
Official local references: `object-data.md` and `array-object-data.md` in the Workflow Studio documentation directory.
The implementation is source-derived and fixture-tested. Live saves and runtime complex values are not verified.

### Output assignments

`assign_outputs` assigns declared outputs in an existing inactive, unpublished subflow without a trigger.
The operation creates native `ASSIGNSUBFLOWOUTPUTS` flow logic from the instance catalog.
It writes ordered `outputsToAssign` rows and the corresponding `data` mirror, not ordinary action inputs.
Declare the output first. Declaration and assignment can occur in the same edit request:

```json
[
  {
    "op": "create_field",
    "collection": "outputs",
    "name": "total",
    "label": "Total",
    "type": "integer"
  },
  {
    "op": "assign_outputs",
    "outputs": { "total": "{{ui-of-earlier-producer.count}}" },
    "after": "ui-of-last-step",
    "ref": "return"
  },
  { "op": "assign_outputs", "step": "return", "outputs": { "total": 0 } }
]
```

Replace the example UI identifiers with identities from the current flow read.
Without `step`, the operation inserts a step using optional `parent`, `after`, or `before` placement.
Without placement, it appends at the root. `ref` addresses that new step in later edits.
With `step`, it updates the named rows and appends missing rows in request order.
Unmentioned rows retain their values, metadata, and relative order. Updating cannot also change placement or alias.
Use `move_step` separately when placement must change. Producer visibility is checked after all edits.

Supported targets are `string`, `integer`, `boolean`, `reference`, `json`, `object`, and supported Array declarations.
Values use the existing native type checks, reference-table compatibility, and typed pill resolver.
False, zero, and empty string remain distinct values. JSON values require valid JSON text.
Output-to-output pills, inline scripts, and assignments inside loops or parallel branches remain unavailable.
Assignments do not create independently consumable step outputs.

To remove a row, use `{"op":"clear_output_assignment","step":"return","name":"total"}`.
Assigning `""` sets an empty value. Removing a row stops that step from assigning the output.
An earlier assignment may still supply the output. Use `delete_step` to remove an entire assignment step.
Clear or delete every assignment before deleting the declared output or changing its type/reference.
No assignment or declaration is removed implicitly.

Preview includes `summary.output_assignments_before` and `summary.output_assignments` in step and row order.
Sensitive target values are masked. Opaque settings stay in the payload without entering the summary.
New assignment step identities remain stable between preview and apply.
Saved step IDs and unrelated metadata must remain unchanged. Assigned IDs must agree between echo and independent GET.
Studio's output element mapping can return a blank `value` with the binding in `displayValue`.
Only that source-proven fallback is reconciled. The display field, actual value, mirror, target, and metadata remain checked.
The native logic-instance table policy and existing revision/context/authorization checks apply.
No new endpoint or remote execution operation is introduced.

Local source: two-column selection/value rows (bundle:293955-294228),
`updateInstance` and `setOutputsToAssignToInstance` (207223-207332),
`fillInstanceFieldData` (150454-150481), complete-save serialization (23462-23469),
and assignment creation from the catalog (473867-473903).
The official local reference is `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/flow-logic-assign-subflow-outputs.md`.
These contracts are source-derived and fixture-tested. Live saves and runtime output behavior are not verified.

#### Complex output values

Assign a declared Object with a native object. Use local child names as keys.
Assign Array.String, Array.Integer, Array.Boolean, and Array.Object with native arrays.
Object fields and Array.Object elements can contain nested Objects and supported Arrays.
Scalar leaves can mix literals and typed pills from earlier producers or declared subflow inputs.

For the `payload` and `rows` schemas declared earlier:

```json
{
  "op": "assign_outputs",
  "outputs": {
    "payload": {
      "count": "{{ui-of-earlier-producer.count}}",
      "rows": [
        { "message": "First row" },
        { "message": "Count: {{ui-of-earlier-producer.count}}" }
      ]
    }
  },
  "after": "ui-of-last-step"
}
```

Replace the example producer and placement identifiers with identities from the current flow read.
Each named output replaces its complete value. Unmentioned output rows remain unchanged.
Omitted Object fields use declared scalar defaults, false for booleans without defaults, or empty values.
Omitted nested Objects expand their declared fields. Omitted Arrays become empty arrays.
An unknown field or a value with the wrong type rejects the preview.
JSON text does not substitute for a native Object or Array. Only `json` leaves accept JSON text.
False, zero, empty string, empty Object, and empty Array retain their native distinctions.

Only top-level outputs are selectable assignment rows, matching Studio's two-column picker (294176-294180).
Set a child through its parent's Object value. Dotted target names and array indexes are not separate assignment operations.
`clear_output_assignment` still removes the whole named row. Use an empty native array to replace an array's contents.
The existing declaration guards reject deletion or type changes while a parent assignment remains.

The native save stores blank complex-parent `value` and `displayValue` fields, with actual values in recursive `children`.
Primitive Arrays have one child binding per element. Array.Object has one Object node per element.
Element metadata follows the declared item schema. Existing binding metadata survives updates by field name or array position.
Studio builds `data` before serializing the cloned output rows. Its top-level Array values remain arrays while wire parents become blank.
Array.Object mirror entries contain Object nodes, including nested array values. The adapter checks this mirror against serialized children.
Preview reconstructs native values for review without copying opaque metadata.
Sensitive child values are masked recursively, including fields inside Array.Object elements.

Save comparison checks every child value, ordering, count, type, identity, display field, and unknown setting.
This increment adds no blanket normalization for complex values. An unexpected server representation returns `UNKNOWN_OUTCOME` without replay.
Empty-parent values are not interpreted as display substitutes for complex parents.
Complex schema defaults, unsupported array subtypes, inline scripts, and remote execution remain outside this increment.

Local source: Object/Array editing (293995-294036,244898-245012), Array.Object item construction (93333-93353),
recursive nested updates (16252-16301,207263-207300), and complete-save serialization (23462-23535).
Array read reconstruction is documented at 23564-23710. The adapter currently consumes the strict saved wire form.
These forms are source-derived and fixture-tested, not live save or execution verified.

#### Whole Object and Array pills

`update_inputs` and `assign_outputs` accept one exact whole Object/Array pill such as `"{{producer-ui.payload}}"`.
Supported complex kinds are `object`, `array.string`, `array.integer`, `array.boolean`, and `array.object`.
Consumers can be existing action inputs, saved subflow-call inputs, or declared subflow output assignments.
Whole pills also work inside a native complex value when the selected child schema matches.

```json
[
  {
    "op": "update_inputs",
    "step": "consumer-ui",
    "inputs": { "payload": "{{producer-ui.payload}}" }
  },
  {
    "op": "assign_outputs",
    "outputs": { "result": "{{subflow.request}}" },
    "after": "consumer-ui"
  }
]
```

This fragment requires compatible existing input/output declarations and actual producer/consumer identities.
Use a producer step's UI identity and output path, or `subflow.<input path>` for a declared subflow input.
New subflow input declarations can supply a whole pill in later edits of the same preview.
Output declarations are not independent producers. Output-to-output bindings remain unavailable.

The adapter preserves fixed target schemas instead of adopting a producer's different structure.
Object schemas must declare the same local child names recursively. Field order and declaration labels need not match.
Field types must be compatible at every level. Reference fields require known compatible tables using the existing inheritance checks.
Arrays require one item schema matching the array kind. Item names can differ. Nested Object item fields follow the same Object checks.
Choice leaves require a nonempty declared producer choice set contained in the target choice set.
The adapter does not infer fields from JSON, dynamic schemas, missing `children`, defaults, or sample values.
Missing/extra Object fields, ambiguous names, wrong item types, unknown schemas, or unsupported array kinds reject the preview.
No source-field superset projection or missing-field default filling is implied by a whole binding.
These are structural checks. They do not prove runtime values satisfy mandatory/maximum-length constraints or contain non-null data.

Action and saved subflow-call producers require an independently readable, entitled published snapshot definition.
The selected current output schema must agree with the saved producer schema before it can supply a whole pill.
The current definition and complete source schema enter the preview fingerprint and are checked again at apply.
Producer visibility, order, branch isolation, reference tables, and dangling-reference checks still apply after all edits.
Trigger and flow-logic whole-schema producers remain unavailable. Arbitrary array indexing is not supported.

The native value is the pill expression, not a JSON string representing a binding.
Serialized `children` contains no literal values. Explicitly replacing a literal value with a whole pill removes its child values.
The target's parameter/declaration schema and unknown parent metadata remain unchanged.
Output assignments store the pill in `value`, `displayValue`, and `data`. Input pills keep their existing empty-display convention.
Whole Array pills remain expressions in the mirror instead of turning into an empty array.
Returning to a literal value uses the existing declared schema. It does not reuse stale child literals.
Saved whole pills with child values are rejected. Save echo and independent GET compare the exact path, display, mirror, schema, and metadata.
This increment adds no whole-pill readback exemptions.

Local source: whole-pill save branching (23453-23468), exact Array pill read form (23559-23562),
whole-structure replacement confirmation (244180-244224), and output parent handling (207263-207295).
The source shows broader UI structure adoption at 22216-22224. This adapter deliberately preserves declared target schemas.
Local official reference: `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/complex-data.md`.
Source-derived regression tests establish these forms offline. Live save and execution behavior remain unverified.

### Standard stages

Stages are separate from flow-logic steps. Create, update, attach, detach, and remove standard stages in one ordered edit request:

```json
[
  { "op": "create_stage", "label": "Fulfilment", "value": "fulfilment", "ref": "work" },
  { "op": "attach_stage", "stage": "work", "step": "1" },
  {
    "op": "update_stage",
    "stage": "work",
    "always_show": false,
    "duration": "1970-01-02 00:00:00"
  }
]
```

Use a saved stage's `stageId` key or a request-local `ref`.
Preview returns `summary.stages_before` and `summary.stages` with reviewed settings and `componentIndexes` attachments.
Stage keys remain stable between preview and apply. A new stage may receive a separate database `id`.
Only that assigned ID may differ from the request. The save echo and independent GET must agree on it.
Existing IDs, stage keys, state labels, attachments, and unknown settings remain strict.
Labels and values must be unique and nonempty. `always_show` is a JSON boolean.
`duration` is a ServiceNow epoch-based timestamp, not an ISO duration. `1970-01-02 00:00:00` represents one day.
`states`, when supplied, replaces the five display labels: `pending`, `in_progress`, `skipped`, `complete`, and `error`.
Unknown stored state metadata is preserved but is not copied into preview.
Creation defaults to the Studio English state labels. Supply explicit labels when another language is required.
Order changes affect stage display order, not step execution order.

Detach every attachment before deleting a stage, including within the same edit request.
Another stage at the same step must be detached explicitly before replacement. Attached step deletion is rejected.
Stages cannot attach inside For Each, Parallel, or sandwich blocks, before Else/Else If, or at a grouping block's first child.
Existing attached steps cannot move across parent regions in the same request. Error-stage ancestry blocks structural edits.
Error-stage creation and modification remain unavailable. Dependent error stages block parent-stage updates, detachment, and removal.
Legacy metadata-only stage records retain the existing label/value adapter.

Source contracts: `createStage` (bundle:184105-184139), default states (1461-1463),
`addNewFlowStage`/`detachStandardStage`/`deleteStandardStage` (204322-204398),
stage creation and reorder controls (458244-458271),
stage display ordering (184277-184295), and assigned IDs (204158-204167,476178-476187).
The complete flow save retains keyed `stages` (99104,350891). This adapter does not call the separate stage-modal endpoint.
The local official reference is `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/add-stages.md`.
These operations are source-grounded and offline-regression verified, not live save or execution verified.

Unavailable: nested flow-variable creation, unsupported array subtypes, error-stage creation, handler creation/toggling,
saved Recurrence/timezone-specific trigger serialization, and dynamic family inputs without a dedicated adapter.
Existing opaque settings remain unchanged. Stage attachments follow stable step identities during reordering.
Detach a stage before deleting its step. Moving a previously attached step across parent regions requires a separate edit request.
Structural edits with inline-script order references fail rather than rewrite scripts.

The Studio adapter is grounded in read-only client source for `designer-bundle.min.js` version `27.0.13`:
`constructInstanceValuesForArrays`, `addToDeletedInstances`, `removeTriggerFromFlow`,
`prepArgsForTryCatchFlowLogicInstance`, and `updateStageIndexes`.
Scope rules follow the local official If, For Each, Parallel, and Try references under
`docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/`.
These sources establish local serializer behavior. They do not prove OAuth save compatibility or runtime execution.
Called subflows can contain complex logic. The adapter reads their published input contract without editing them.
It checks publication, read access, entitlement, scope access, and mandatory inputs before staging a call.
Creating a header and appending an action are separate mutations. Failure during the second operation can leave an empty draft.

## Safety and failure handling

Preview performs reads only. Apply rechecks policy, instance identity, authorization generation, duplicates,
and the flow/action fingerprint. For `edit`, the fingerprint also covers the trigger and flow-logic catalogs.
After each save, apply checks the response and reads the saved flow through a separate Studio GET.
The shared semantic comparison checks topology, definition IDs, every input value and pill reference,
interfaces, variables, stages, handler fields, and opaque settings. The outline is display text only.
Generated instance IDs, integer/string step orders, and the observed trigger `name`/`label` difference are normalized.
The top-level Studio `updated` field is the last-save timestamp, not an editable flow setting.
Only request-to-save-echo and request-to-independent-GET comparisons allow it to advance or remain equal.
The echo and independent GET must describe the same timestamp; a later readback revision remains `UNKNOWN_OUTCOME`.
Supported revision encodings are UTC `YYYY-MM-DD HH:mm:ss` text and decimal epoch-millisecond text.
Malformed values and regressions fail. Absent or empty legacy metadata may become populated but cannot replace a populated timestamp.
Null is not a valid timestamp. Actor fields and other revision-like keys are not ignored without an established adapter contract.
Preview fingerprints still cover the complete saved definition, including `updated`; pre-write drift still blocks apply.
Strict context binding continues to check the Table API `sys_mod_count` independently.
Within `label_cache` entries only, omitted `attributes` and JSON null are equivalent.
Studio's `processLabelCacheIncoming` and `processLabelCacheOutgoing` both use null when that field is absent.
Empty objects, non-null attributes, and every other cache field remain significant. Payloads are not changed by comparison.
Within each `label_cache` entry, choice-list position is presentation only. Semantic comparison orders choices by typed value
and full metadata. Labels, raw labels, explicit order fields, dependencies, disabled state, unknown keys, and duplicate counts
remain strict. Choice lists in definitions and input bindings are not normalized by this exception.
Authorized deletion tombstones may clear after saving. The independent read must exclude every deleted identity.
An incomplete read, ACL-filtered content, malformed state, or mismatch returns `UNKNOWN_OUTCOME` without replay.
Independent verification errors name the failed phase: `request.echo`, `readback.get`, `request.readback`, or `echo.readback`.
Comparison errors include the first differing field path, side (`expected`, `actual`, or `both`), and mismatch kind.
Only application-declared field names appear. Unknown keys use numeric `[field:N]` positions. No values are returned.
Fixed Studio settings additionally identify `alerts`, `componentInstances`, `deletedActions`,
`deletedFlowLogicInstances`, `deletedTriggers`, and `aliasOverride` at the flow root.
Only `flow.aliasOverride` additionally identifies `aliasOverrideMappings`. These names remain anonymous inside custom mappings.
The allowlist follows the local bundle at 203732-203750 and 204678-204690. It does not identify an earlier indexed mismatch.
List-count failures append numeric `expected_count=N actual_count=N` details. Counts follow the comparison's left/right direction.
The error code, first-difference reporting, payload limits, and strict equality checks remain unchanged.
Malformed semantics retain their safe parser invariant. Equality checks and unknown-setting preservation remain strict.

For an `update_inputs` preview whose requested values already equal saved values, `summary.serialization_check`
compares the planned serialization with the current Studio read, without saving.
It reports `mode="planned_vs_current_read_only"`, `matches`, and safe mismatch details when present.
This comparison cannot reconstruct a previous save request or echo. It does not prove server-side save normalization.
Never apply this diagnostic preview to replay a write that already persisted.

Tokens expire after 300 seconds and are consumed once, including on failure.
State checks and remote writes are not atomic. Simultaneous edits can still race.
ServiceNow can assign a new persisted action ID. Apply matches the added action by its stable UI identity.
It checks order, definition, inputs, and existing steps, then returns the persisted ID as `action_sys_id`.
For a subflow call, the persisted step ID is `subflow_instance_sys_id`.
Step ordering includes both actions and subflow calls. Existing steps remain unchanged.
`flow.contract` steps and the inspect canvas include V2 subflow calls from `sys_hub_sub_flow_instance_v2`.
They retain the persisted subflow snapshot reference, decoded inputs, V2 version, order, parent, and wait setting.
Structural counts include these calls. Truncation metadata identifies bounded, unreadable, or malformed sections.
Unknown dataset counts are null. A subflow snapshot reference does not establish a numeric publication version.
No activation, publication, execution, credential handling, or automatic retry is exposed.

## Local preview boundary trace

`flow_preview` writes bounded `Flow preview boundary` records through the existing stderr diagnostic logger.
During this temporary investigation, the same sanitized JSON records also append to this fixed local file:

```text
/private/var/folders/7n/70rkdwfs0v53vjm2cm0vf3xw0000gp/T/opencode/servicenow-flow-preview-trace/boundaries.jsonl
```

The directory must belong to the process user with mode `0700`. The regular file must have mode `0600` and one link.
The sink rejects symlinks and unsafe ownership or permissions. Its parent is the approved, user-owned temporary root.
Writes stop at 256 KiB. Records exceed neither the byte cap nor 4096 bytes each. There is no rotation or automatic deletion.
File errors and lock contention skip the append without changing the tool response. Stderr logging remains enabled.
No request argument or environment variable can select a different path.
After the discrepancy is diagnosed, remove the temporary sink code and delete this trace directory after retaining needed evidence.

The marker is `flow-preview-boundaries-v1`. One locally generated `trace_id` joins these boundaries:
`protocol`, `handler`, `command`, `planner.in`, `planner.out`, `handler.out`, `wire.structured`, and `wire.text`.
Invalid requests can stop before later boundaries. Other tools do not emit these records.

Records include PID, caller module path, and a SHA-256 hash of the loaded Python code object.
This hash identifies loaded code, not a source file that can change after startup.
Input records include only declared argument names, the action enum, and edit count.
Planner records count action, flow-logic, and subflow nodes. Output records count changes, outline lines, and pills.
An unavailable count is `-1`. No submitted values, platform values, tokens, headers, or raw exceptions are included.
The trace does not change the MCP response or publish a new tool.

To investigate one preview, read the fixed JSONL file or stderr records and compare counts under the same trace ID.
`protocol` precedes SDK argument handling. `wire.structured` and `wire.text` observe the SDK-converted result before transport delivery.
A live process must reload once to load new instrumentation. Do not restart unrelated server processes.

## Validation, status, and capture context

`flow(action="validate", sys_id=...)` reads the saved draft and applies local semantic, tree, family,
reference, mandatory-value, and declared-schema checks. It does not compile, save, publish, activate, or execute.
`valid=true` applies only to the implemented local checks. Unsupported families, scripts, partial reads,
dependency drift, and revision races return `valid=null`, `complete=false`, and safe diagnostic paths.
Ordinary supported validation failures return `valid=false`. The result includes opaque flow and dependency revisions.

Flow-logic `children` is a derived UI cache. An absent cache does not prevent validation or independent save verification.
Persisted `parent` UI identifiers and global `order` establish direct children.
An explicit cache must list those UI identifiers in order, without omissions, duplicates, or indirect descendants.
An empty cache is valid only when the block has no direct children. Parent, cycle, and identity checks remain mandatory.
Studio reconstructs `depth`, `ancestorUiId`, `ancestorType`, `isFirstFlowLogicChild`, and `isPrecededByThen` after GET.
These fields may be absent. Explicit values must match the same ordered parent tree before their presence is normalized.
Root ancestor fields accept null or an empty string. Nested ancestors identify the outermost enclosing flow-logic block,
with Studio's hidden top-level Catch boundary respected. First-child/Then flags use the immediate parent and sibling order;
Then applies to the first child of If, Else If, or Catch. Action/subflow `children`, when present, must be empty.
The serializer and semantic parser share this derivation. `connectedTo`, definitions, handlers, and opaque fields remain strict.

Schema comparison uses nested `parameter` metadata when present, including constraints and nested child schemas.
Choice signatures ignore labels, list position, and display `order`. They retain typed values and all other choice metadata.
Duplicate equivalent choices and malformed lists remain incomplete. Dependency, disabled, and unknown properties remain significant.
Choice diagnostics distinguish `CHOICE_VALUE_SET_MISMATCH` from `CHOICE_METADATA_MISMATCH` without returning choice values.
Outer UI copies do not establish a different schema. Missing trigger outputs are not replaced from the current catalog.
Subflow validation reads the persisted `subflowSysId` snapshot with Studio's minimal-definition flags.
It does not substitute the current draft or another published version.
`UNVERIFIED_DEPENDENCY` includes a safe `reason` and, for failed HTTP reads, `http_status` when available.
The message also contains that category and known HTTP status.
Schema failures include `schema_mismatch` with `side` (`saved`, `current`, or `both`), `path`, and `kind`.
The message repeats this location. Paths identify the first difference in inputs, outputs, or nested child schemas.
Only sanitized schema identifiers from the current dependency appear in comparison paths. Other names use numeric indexes.
Malformed schemas identify the unreadable side and location. Metadata values, choice values, and labels are never returned.
Unexpected read exceptions use `DEFINITION_READ_FAILED`. An unknown HTTP status is omitted.
Remote error bodies are never included. An unverified dependency still returns `valid=null`, `complete=false`.

`flow(action="status", sys_id=..., execution_sys_id=...)` reads lifecycle fields and optionally one exact
existing `sys_flow_context`. It reports authoring status separately from active state and published/latest
snapshot references. Unknown execution states remain unknown. This operation never starts, retries, or cancels execution.
Execution-to-version correlation is not established by a matching flow identity.
Customer Update metadata and current application/update-set context are separate observed/unavailable results.

For strict draft-write context binding, pass `expected_scope`, `expected_update_set`, and, for an existing flow,
`expected_revision` from `flow.status.flow.revision.mod_count` to `flow_preview`.
Preview and apply check the current application/update-set read contract. They never switch the session context.
Apply checks it again immediately before saving. The existing 300-second single-use token and authorization,
connection, production, policy, flow, and dependency checks remain in force.
For bound operations, Customer Update lookup filters by exact flow-header name, application, and expected update set.
It reads at most 100 candidates plus one overflow sentinel. All returned metadata must be readable and consistent.
Selection requires a unique latest timestamp that does not predate the flow-header revision.
Older records in the same set do not make that latest record ambiguous. Ties, duplicate identities, wrong context,
partial rows, stale metadata, or the candidate cap still block the operation. Unbound status keeps its ambiguity warning.
Bound preview and the immediate pre-write check retain every matching candidate's ID, timestamp, revision, and context.
Response order does not affect that snapshot. After saving, exactly one candidate may change or be added; unchanged historical
records must remain present. The changed record must be the unique fresh latest record. An existing record's revision must
advance by exactly one against its own prior revision, even when it was not the previously newest record. Its timestamp cannot
regress. A newly observed record is accepted only at initial revision zero. Ties, multiple changes, disappearing candidates,
or incomplete evidence remain `UNKNOWN_OUTCOME`. The flow revision must advance by exactly one, and both the header and
OAuth session context must remain stable through the final read. Moving a historical row alone does not prove a new capture.

After a bound save, independent semantic readback and fresh `sys_update_xml` metadata must identify the expected
application and update set. Missing, ambiguous, old, ACL-filtered, async, or raced readback returns `UNKNOWN_OUTCOME`.
The draft can already be saved at this point. Do not replay it. Inspect status and Customer Updates.
This establishes flow-header capture metadata only, not payload equality or capture of child artifacts.
Unbound legacy calls remain compatible and report `capture.state="unverified"` explicitly.
The installed concourse picker response shape is source-grounded but OAuth compatibility was not live-verified.

## Lifecycle contract gaps and safe capture

`flow.describe.authoring.lifecycle` reports each operation separately. No remote lifecycle effect is enabled.
The installed Studio client `designer-bundle.min.js` version `27.0.13` establishes these request-side facts:

| Operation             | Observed contract                                                                              | Missing evidence                                                                                                                             |
| --------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Local validate        | Read-only checks implemented here                                                              | Runtime behavior is not established by local validity.                                                                                       |
| Remote validate       | No standalone compile request established                                                      | Request, side effects, response, dependency/version preconditions.                                                                           |
| Flow publish/activate | `publishFlowToServer` posts cleaned flow data to `flow/{id}/snapshot`; subflows use `/subflow` | Separate flow publish-only semantics, complete success/error/async schemas, OAuth and reconciliation. Publication must not alias activation. |
| Deactivate            | `deactivateFlowCall` uses effectful GET `flow/{id}/deactivate`                                 | Complete response, revision/context conditions, reconciliation. GET does not make this operation read-only.                                  |
| Test                  | `sendTestToServer` posts `{flow, outputMap, runOnThread}` to `flow/{id}/test`                  | Trigger-specific input mapping, context/version response and async reconciliation. Test is execution and can change records.                 |
| Run                   | No independent callable contract established                                                   | Endpoint, typed inputs, target version/mode, dependency revision, result/context status. Test is not an alias.                               |

Local official sources are `flow-test.md`, `flow-activate.md`, `publish-subflow.md`, and `flow-execution-details.md`
under `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/`. The current-update-set behavior is documented in
`docs/ServiceNowDocs/markdown/application-development/system-update-sets/select-update-set-system-settings.md`.
Installed shell source establishes GET `/api/now/ui/concoursepicker/current` with `currentApplication.sysId` and
`currentUpdateSet.sysId`. Read-only Table API metadata established lifecycle, execution-state choices, and capture fields.

Authorized unsaved capture was attempted with a default-deny request guard installed before navigation.
The guard allowed only same-origin GET navigation, static resources, and known read endpoints. It blocked all
non-GET methods, effectful GETs including deactivate, WebSockets, and redirects. No credentials or headers were extracted.
Studio did not reach an authoring editor because bootstrap required blocked POSTs to `/api/now/v1/batch`,
`/api/now/uxframework/component_drivers`, `/amb/handshake`, and `/web/config`.
A root service worker also reappeared after bypass/cleanup. Capture stopped without authoring interactions or save bodies.
The effect guard was not relaxed, and no alternative authenticated transport was used.

The following bodies are still needed from a version-matched, safely isolated UI or sanitized existing fixtures:

| Family                            | Smallest missing serializer evidence                                                                                                                                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unsupported complex forms         | Nested declarations, literal/leaf-pill assignments, and schema-checked whole pills are implemented. Additional array subtypes and dynamic schema adoption need adapters. Complex flow variables are excluded by Studio's type picker. |
| Error stages                      | Create/remove bodies with ancestor links and conditional ancestry. Standard-stage CRUD is implemented from local client source.                                                                                                       |
| Top-level handler                 | Toggle-on/off bodies with hidden order-zero wrappers, enabled input, parent rewriting, and handler boundary index.                                                                                                                    |
| Saved Recurrence/dynamic triggers | Classic record and daily/weekly/monthly/run-once/repeat draft triggers are implemented. Saved Recurrence/timezone-specific contracts and external interface regeneration still need adapters.                                         |
| Try/Catch paired moves            | Pair movement across parent regions with connectedTo, children, order, and ancestry fields.                                                                                                                                           |

Further capture requires an authenticated editor bootstrap that works within the GET-only/no-worker boundary,
or sanitized version-matched request fixtures. Do not permit the blocked POSTs merely to make the UI load.
Blocked requests cannot establish success responses, OAuth effect behavior, runtime results, or capture guarantees.
All remote lifecycle response gaps remain open.

After a timeout or incomplete response, inspect the exact target before another preview.
For create, resolve the reviewed name and scope before retrying to avoid a duplicate.
The OAuth session can have a different current update set from the browser session.
The tool does not change that session setting or guarantee update-set capture.
