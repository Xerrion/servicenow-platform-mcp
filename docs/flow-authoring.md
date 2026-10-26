# Experimental Flow creation and editing

The `flow` tool is read-only: inspect configuration, validate locally, or read lifecycle and exact execution status.
Enable `flow_write` to register `flow_preview` and `mutation_apply`. The `full` package includes this group.
Read-only packages do not include it. Select `flow,flow_write` when you need both inspection and mutation tools without `full`.
Use a permitted development or test connection with `SERVICENOW_ENV=dev`, local mutation policy, and the required ServiceNow permissions.
Production policy blocks both mutation previews and application. Restart the server after changing tool or environment settings.

This adapter uses the undocumented `/api/now/processflow` API used by Workflow Studio.
Compatibility with other ServiceNow releases and OAuth REST access policies is not guaranteed.
Browser access does not prove OAuth access. The adapter does not use browser cookies or write flow snapshots through the Table API.

Use `create` and `edit` to configure native blocks, triggers, interfaces, and bindings without executing the authored flow.
Configuring a trigger does not trigger the flow. Draft authoring never publishes or activates it.
Some dynamic input schemas require a [separately reviewed schema-provider action](#reviewed-dynamic-schema-resolution); that action can have side effects and is never run implicitly during create/edit.
Existing [lifecycle actions](#explicit-lifecycle-effects-and-remaining-contract-gaps) remain separate from authoring:
activation can start real work, subflow publication can affect callers, and tests execute actions with real side effects.
Neither local validation nor a successful save proves runtime correctness.

## Supported operations

1. `flow_preview(action="create", name="Example draft", scope="global", edits=[...])` previews a complete inactive flow.
   Omit `edits` only when you want an empty header.
   Set `flow_type="subflow"` to create an inactive triggerless subflow instead.
   For a scoped application, supply its resolved `sys_scope.sys_id`. Scope has no inferred default.
2. `flow_preview(action="edit", sys_id=..., edits=[...])` previews supported structural edits in one save.
   The same planner handles calls, logic trees, triggers, declarations, assignments, stages, and handlers during creation or later editing.
   Supported native release-27800/engine-2 active/published owners also permit [working-draft editing](#editing-an-active-draft) while preserving their selected runtime.
   That profile requires `expected_scope`, `expected_revision`, and explicitly disabled save-as-you-go.
3. `add_action` and `add_subflow` retain narrower append-only support for inactive, unpublished, linear drafts.
   `add_action` requires a published `action_type_sys_id` and accepts static string, choice, boolean, or integer inputs.
   `add_subflow` requires a published definition `subflow_sys_id`, resolves its exact published snapshot, and waits for completion.
   Use `edit` with `insert_action` or `insert_subflow` for supported structured authoring.
   Both convenience actions use the same insertion planner, native bindings, and independent readback as create/edit. Advanced modes and `schema_tokens` use create/edit only.
   Logical append-last and existing component IDs remain stable; a fresh preview's proposed numeric order is authoritative when sparse orders are renumbered.
4. Apply the returned authoring token with `mutation_apply(preview_token=...)` once after review and authorization.
   After `UNKNOWN_OUTCOME`, reconcile the exact target through reads before another preview. Never automatically replay the operation.
5. Inspect the saved definition with `flow(action="inspect", sys_id=..., sections=["flow", "canvas", "warnings"])`.
   ServiceNow performs its normal update-set capture. MCP does not inspect or manage update sets.

For dynamic action input metadata, use the separate `resolve_schema` preview/apply operation, then pass its evidence tokens to a **new** create/edit preview.
Do not apply a schema token as a mutation token. See [reviewed dynamic schema resolution](#reviewed-dynamic-schema-resolution).
Existing `activate`, subflow-only `publish`, `deactivate`, and saved-owner `test` require their own explicit lifecycle previews with `expected_scope` and `expected_revision`.
Independent remote validation, independent production run, and flow publish-only remain unavailable; none is required to author a draft.

## Native authoring routes

Use the installed definitions and `flow(action="describe")` capability ledger together.
Catalog presence identifies a native representation; it does not establish entitlement, OAuth access, a licensed plugin, or support for a different release.
Hidden companions and assignments are not standalone picker blocks. The routes below distinguish direct insertion, dedicated edits, and native internal representations.

| Native logic representation                                | Create/edit route                                                    | Important boundary                                                                                                                                    |
| ---------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `IF`, `ELSEIF`, `ELSE`                                     | `insert_logic`                                                       | Else If/Else belong to a connected conditional chain.                                                                                                 |
| `FOREACH`, `DOUNTIL`                                       | `insert_logic`, then children with `parent`                          | Loop scope, non-nesting attributes, sandwich closing orders, and collection types remain native constraints.                                          |
| `BREAK`, `CONTINUE`, `GOBACKTO`, `END`                     | `insert_logic`                                                       | Placement and terminal-sibling rules apply; Go back to retains a validated target identity.                                                           |
| `PARALLEL`, `PARALLELBLOCK`                                | Insert the Parallel construct and its structural branches            | Parallel Branch is a child of Parallel, not an independent root block.                                                                                |
| `DECISION`, `DECISIONBLOCK`                                | Insert/update Decision with `decision_inputs` and ordered `branches` | Decision Branch companions are generated from native answers; never insert, move, or delete them independently.                                       |
| `TIMER`, `RUNWORKFLOW`, `RUNDYNAMICFLOW`, `GETFLOWOUTPUTS` | `insert_logic` / `update_inputs`                                     | Use declared wait settings and independently readable workflow/template/call interfaces; configuration does not invoke them.                          |
| `ASSIGNSUBFLOWOUTPUTS`                                     | `assign_outputs` / `clear_output_assignment`                         | Dedicated output-assignment serializer, not generic insertion.                                                                                        |
| `SETFLOWVARIABLES`                                         | `set_variables`                                                      | Dedicated variable-assignment serializer.                                                                                                             |
| `TRY`, `CATCH`                                             | Insert Try; its Catch is paired automatically                        | Catch cannot be independently inserted or moved. A strictly identified legacy root Try can represent the top-level wrapper.                           |
| `TOP_LEVEL_TRY`, `TOP_LEVEL_CATCH`                         | `set_error_handler` / `remove_error_handler`                         | Hidden owner-level wrappers, not generic steps.                                                                                                       |
| `UPDATEFLOWTEMPLATE`                                       | Preserve existing native DTO only                                    | Hidden, source-generated/system-locked representation. Its native renderer forces read-only fields; never generic insert, update, move, or delete it. |
| `PLACEHOLDER`                                              | None                                                                 | Internal missing-definition/connector representation, not an authorable block.                                                                        |

| Native trigger family                  | `set_trigger.type`                                          | Authoring route and dependencies                                                                       |
| -------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Created / Updated / Created or Updated | `record_create`, `record_update`, `record_create_or_update` | Catalog inputs, readable table descriptor, and condition dictionary metadata.                          |
| Classic schedules                      | `run_once`, `daily`, `weekly`, `monthly`, `repeat`          | Native date/time/duration/choice inputs; instance timezone.                                            |
| Service Catalog                        | `service_catalog`                                           | Native Requested Item call interface and output metadata.                                              |
| Inbound Email                          | `email`                                                     | Email conditions, processing order/stop settings, and optional reply-record table.                     |
| Remote Table Query                     | `remote_table_query`                                        | Readable remote-table definition through native `u_table`.                                             |
| REST API - Asynchronous                | `rest_async`                                                | Scalar configuration plus typed `interface_schemas`; server-generated base path is read-only.          |
| Proactive Analytics                    | `analytics`                                                 | Readable analytics trigger definition, derived event table, and dependent condition.                   |
| MetricBase                             | `metric`                                                    | Readable metric trigger definition, derived metric table, optional moderator, and dependent condition. |
| SLA Task                               | `sla_task`                                                  | No authored inputs in the installed definition; preserve its native output schemas.                    |

`update_trigger` and `remove_trigger` address the saved trigger instance. Saved picker definitions use the separate `template_id` route below, when their native metadata exposes that capability.
Adding a trigger only configures the inactive draft; it does not start listening, call a REST endpoint, send email, query a remote table, or trigger the authored flow.
Missing plugins, references, schemas, access, or source-defined capabilities return a strict error rather than a fabricated substitute.

## Complete creation in one request

Supply the full ordered `edits` array with `action="create"`. The same planner handles creation and incremental edits.
This contract avoids a separate graph language and reuses typed serializers, placement rules, and dependency checks.
The server discovers trigger and logic definitions. Action snapshots and published subflow definitions use existing resolved IDs.
Callers do not supply new database IDs, UI IDs, raw Studio definitions, or separate requests for each step.

Each request accepts 1-200 edits, at most 256 KiB and nesting depth 32.
Complete creation permits at most 200 nodes, including hidden handler wrappers.
The existing limit of 100 distinct pills still applies. Dynamic inputs require their declared native schema and, where needed, separately reviewed provider evidence before creation.
`ref` names address new steps in later edits and pill bindings within this request.
The preview returns their stable UI identities. Apply returns `refs[alias]` with persisted `sys_id`, `ui_id`, and `kind`.
Use those identities in subsequent requests. Aliases are request-local, not stored names.
`stage_refs` similarly returns native stage keys.

This example assumes two readable, entitled published definitions:

- A Format message action with string input `message` and string output `text`.
- A Dispatch request subflow with string input `message` and string output `text`.

Replace the two placeholders with your resolved action snapshot and subflow definition IDs.
The definition names describe example custom definitions. They are not built-in action names or a name-resolution API.
The source-derived wire tests exercise this complete request shape with fixture definitions. No example executes a flow.

Call `flow_preview` once with:

```json
{
  "action": "create",
  "name": "Dispatch request",
  "scope": "global",
  "expected_scope": "global",
  "edits": [
    { "op": "set_trigger", "type": "service_catalog" },
    {
      "op": "create_field",
      "collection": "flowVariables",
      "name": "message",
      "label": "Message",
      "type": "string"
    },
    { "op": "set_variables", "inputs": { "message": "Dispatch" } },
    {
      "op": "insert_action",
      "action_type_sys_id": "<format-message-snapshot-id>",
      "ref": "source",
      "inputs": { "message": "{{flow_variable.message}}" }
    },
    {
      "op": "insert_logic",
      "logic": "If",
      "ref": "branch",
      "inputs": { "condition": "{{source.text}}ISNOTEMPTY" }
    },
    {
      "op": "insert_subflow",
      "subflow_sys_id": "<dispatch-subflow-definition-id>",
      "parent": "branch",
      "ref": "dispatch",
      "inputs": { "message": "{{source.text}}" }
    },
    {
      "op": "insert_action",
      "action_type_sys_id": "<format-message-snapshot-id>",
      "parent": "branch",
      "ref": "audit",
      "inputs": { "message": "{{dispatch.text}}" }
    },
    { "op": "insert_logic", "logic": "Else", "after": "branch", "ref": "otherwise" },
    {
      "op": "insert_action",
      "action_type_sys_id": "<format-message-snapshot-id>",
      "parent": "otherwise",
      "ref": "skip",
      "inputs": { "message": "Skipped" }
    },
    {
      "op": "insert_action",
      "action_type_sys_id": "<format-message-snapshot-id>",
      "after": "otherwise",
      "ref": "tail",
      "inputs": { "message": "Done" }
    },
    { "op": "create_stage", "ref": "work", "label": "Work", "value": "work" },
    { "op": "attach_stage", "stage": "work", "step": "source" }
  ]
}
```

Review the preview. After authorization, call `mutation_apply` once with its `preview_token`.
Let `created` be the returned `data` object. A later `flow_preview` can make all these changes together:

```python
flow_preview(
    action="edit",
    sys_id=created["sys_id"],
    expected_scope="global",
    expected_revision=created["revision"],
    edits=[
        {
            "op": "move_step",
            "step": created["refs"]["tail"]["ui_id"],
            "before": created["refs"]["branch"]["ui_id"],
        },
        {
            "op": "insert_action",
            "action_type_sys_id": "<format-message-snapshot-id>",
            "ref": "inserted",
            "after": created["refs"]["source"]["ui_id"],
            "inputs": {"message": "Additional audit"},
        },
        {
            "op": "update_inputs",
            "step": created["refs"]["tail"]["sys_id"],
            "inputs": {"message": "Completed"},
        },
        {"op": "delete_step", "step": created["refs"]["audit"]["sys_id"]},
    ],
)
```

Apply that preview once after authorization. No complete-flow resubmission is needed.
Surviving identities and unmentioned settings remain unchanged. Moves revalidate branch scope and every remaining binding.
For subflows, use `flow_type="subflow"`, omit triggers and stages, and declare interfaces with `create_field`.
`assign_outputs`, nested declarations, and supported handlers use their existing edit contracts below.
Complete creation remains inactive. Incremental `edit` also supports the bounded [versioned-owner profile](#editing-an-active-draft).
That profile permits an explicit published-to-working-draft save without changing the published runtime.

### Editing an active draft

`edit` can save a supported active or published flow/subflow owner while retaining its executable version.
Supply both `expected_scope` and `expected_revision`. Existing edit operations and their validation limits still apply.
The saved root must report these explicit fields:

- Explicit Boolean activation and either `status=draft, isPublished=false` or `status=published, isPublished=true`.
- `snapshot=false`, `jsonSnapshot=false`, and `masterSnapshot=false`.
- Valid nonempty `masterSnapshotId` and `latestSnapshot`, plus a readable version. Active flows also require `remoteTriggerSysId`.
- `authoredOnReleaseVersion=27800` and `flowEngineVersion=2`.
- Complete definition content and Studio write access.

The readable `sn_flow_designer.save_as_you_go_enabled` property must explicitly equal `false`.
The adapter uses the local Studio source and the native DTO contract. It does not fetch a runtime bundle.
Immutable snapshots, lock-enabled profiles, and unknown release/engine contracts remain rejected. No edit lock is acquired.

Preview reads the target header, configuration property, runtime selection and published content.
An active flow's exact trigger-plan relationship establishes selection. A callable subflow uses its explicit master reference.
Tests can create several non-master records with the same version and `status=published`. Those records do not establish callable selection.
The adapter does not infer selection from the name `latestSnapshot`.
Main-snapshot ownership and header version metadata must also agree. All evidence binds preview and apply.
Apply repeats these reads and checks for drift immediately before saving.

The single native PUT retains activation, version, references, and other settings. It sends Studio's `status=modified` save marker.
Echo and independent GET must agree on the resulting working draft. The header revision must increase exactly once.
The executable content, plan, trigger activation, references, version, scope, and published-owner metadata must remain unchanged.
JSON runtime preservation reads the exact `sys_hub_snapshot` storage envelope and ordered chunks, decodes bounded base64/gzip/JSON, and compares the complete serialized `authoredFlow`, including unknown fields.
The raw serialized format is distinct from the Studio GET DTO: a native stored engine marker of `0` is not a rewritten `flowEngineVersion=2` DTO field.
Native non-JSON versions compare complete version-owned action, logic, subflow, trigger, declaration and stage records, plus exact referenced flow-block rows.
Subflow calls use the native `subflow_inputs`, `subflow`, and `wait_for_completion` columns. Referenced blocks are not assigned an invented variable-model ownership relationship.
The reads are bounded to 31 chunks, 4 MiB encoded and 8 MiB decoded payload, depth 64, 100,000 JSON nodes, and fewer than 1,000 records per native dataset.
Unreadable storage, unrelated owners, malformed data, exhausted bounds, or drift block verification. A native master GET facade is not the runtime invariant; it can expose current editor state.
These checks preserve selected runtime storage during authoring. They do not certify an engine instruction stream or change execution-to-authored-version status flags.

Configuration verification stays strict. Unknown declaration or mirrored metadata changes are not normalized.
A save can persist and then return `UNKNOWN_OUTCOME` with `error.outcome.runtime_preservation="verified"` and `configuration="unverified"`.
Inspect that saved flow before further work. Never replay or compensate for an uncertain save.
The read/check/write sequence is not atomic. Concurrent changes can still produce an unknown outcome.

The 2026-10-23 live matrix passed active-flow reorder/insert/input-update, inactive published-flow editing and published-subflow insert/update.
The isolated future Run Once flow retained its runtime content and trigger behavior during editing, then was deactivated.
The scalar-input Log subflow test reached `COMPLETE` before explicit revised publication.
This evidence is limited to the owned native Log fixtures, not all operations or platform releases.
See [live verification](flow-live-verification.md#native-working-drafts-and-safe-lifecycle-completion-2026-10-23).
Offline active-draft round trips cover structural edits, trigger updates/replacement, stages, handlers, and scalar-variable declarations with assigned identities.
Those fixtures preserve other requested metadata. Separate drift cases reject unexpected metadata, including declaration mirrors, without inferring real-server normalization.

### Write boundaries and partial outcomes

Preview and apply validate the complete plan and readable dependencies before the first remote write.
Native creation uses a property POST, independent header readback, then one complete-definition PUT and independent semantic readback.
The second plan uses the persisted header, including unknown server defaults. It never substitutes the local planning seed.
The server reads the created `sys_mod_count` instead of assuming an initial revision.
Complete-create results always return a revision. Target-bound incremental edits also return a fresh revision.
Scope and revision checks use the target flow header. They never read the session picker, update sets, or Customer Updates.
ServiceNow performs its normal capture. Missing or unreadable capture metadata does not affect a verified operation.

The two upstream writes are not transactional. There is no automatic retry, recreation, or destructive compensation.
After a failure, `UNKNOWN_OUTCOME` contains a value-free `error.outcome`:

- `sys_id`: the created definition ID when the property response supplied a valid ID. Otherwise null.
- `phase`: the operation that stopped.
- `header`: `unknown`, `returned`, or independently `verified`.
- `definition`: `not_sent`, `unknown`, or independently `verified`.
- `retry_safe`: always false. `recovery` directs inspection before any further write.

For example, a PUT timeout reports a verified header ID and an unknown definition outcome.
A dependency change after header creation reports the header ID and `definition="not_sent"`.
A target-revision verification failure after semantic readback reports `definition="verified"` and retains the known flow ID.
Inspect that existing flow before an incremental edit preview. Never repeat the create request blindly.
If the property response times out without an ID, resolve the possible created flow before any further write.
Tokens remain single-use. Publication, activation, testing, and execution require separate explicit lifecycle actions.

### Declaration identity verification

Studio stores a declaration's client UI identity in `attributes.uiUniqueId`, or in `fieldFacetMap.uiUniqueId` for nested declarations.
The top-level `uiUniqueId` is a reconstructed UI cache. Its absence does not establish an identity change.
Comparison restores that cache only from an existing nonempty stored identity. Explicit conflicting copies still fail.
Declaration names and UI identities must be unique within their collection. Names are never repaired or inferred from labels.

A newly saved flow variable can receive a separate database `id`.
Set Flow Variables parameters may receive that same declaration ID in both their `inputs` and `flowVariables` copies.
Request comparison permits this assignment only for a declared new variable with the same exact name and UI identity.
The parameter ID must match its owning declaration. Another variable's ID or an unrelated ID fails verification.
Echo and independent GET must then retain the same assigned IDs. Existing database IDs cannot normalize.
Pill bindings, stage fields, defaults, schema constraints, values, and unknown metadata remain strict.

Local source: `appendPropertyToItem` (22009-22035), `mapProperties` (24010-24023),
`getFlowVariables` (232977-232978), and assignment mirrors (207311-207315) in `docs/designer-bundle.min.js`.
The regression fixture contains only a sanitized identity projection of a saved-flow GET, not a historical PUT response.
It does not certify other server transformations or retroactively verify an earlier unknown outcome.

### New String variable DTOs

New String flow variables use the native persisted parameter shape when the planner constructs the request.
The request explicitly includes `attributes.element_mapping_provider="com.glide.flow_design.action.data.FlowDesignVariableMapper"`,
`uiType="string"`, and `uiTypeLabel="String"`. The provider and UI type remain strict.
The native defaults include `type_label="string"`, `order=0`, `maxsize=0`, blank value/default/display fields, and false flags.
An explicit `maxsize` and nonempty hint remain unchanged. Empty source attributes and modal-only fields are omitted at construction.
Existing declarations and other types retain their current construction paths. No response attributes are discarded.

Local source: source-state clearing (22246-22254), mapper facets (93420-93431), variable label hydration (232973),
and parameter serialization (471783-471840) in `docs/designer-bundle.min.js`.
`tests/fixtures/flow_string_variable_dto.json` contains the complete sanitized current GET DTO observed on 2026-10-23.
It is not a historical request or save response.

Fresh native actions use the independently read published definition, without copying a saved instance profile or its record IDs.
New Set Flow Variables instances are constructed from the current readable logic definition, with fresh bindings and native DTO defaults.
They do not clone saved steps, block references, identities, or unknown instance metadata. Existing assignments retain all their stored fields.
The catalog must contain one readable Set Flow Variables definition with the supported input schema.

Studio's `addNewInstance` (206651-206672) calls `buildNewFlowLogicInstance` (221783-221799) without a saved `flowBlockId`.
The assignment reducer mirrors `flowVariables` into `inputs` (207302-207318). The save path retains those instances (130432-130460, 99193-99197).
The optional serializer omits undefined fields (471736-471743), including the block reference named at 472392.
New String assignment parameters use the parameter DTO (471783-471840), retaining attributes and excluding empty variable-only runtime/cache fields.

A newly saved assignment can receive a server-created block reference. The verifier checks its exact logic-instance Table API record.
The record must match the flow, UI ID, instance ID, class, logic definition, block reference, and readable referenced block ID.
Dictionary metadata establishes `block -> sys_hub_flow_block` and inherited `flow -> sys_hub_flow_base`.
Only this verified new relation is bound into request comparison. Echo and independent GET must agree, and all submitted settings remain strict.
Existing block references and unknown metadata are never normalized. See [live verification](flow-live-verification.md) for observed results.

For scalar String flow variables and Set Flow Variables parameter copies only, comparison accepts the observed `String` and `string` display spellings.
This applies to `type_label` and `attributes.uiTypeLabel`, including component mirrors. It does not change the outgoing saved metadata.
Studio obtains type display text from `GLIDE_TYPES` (232966-232973), copies it into the picker attribute (344082-344085), and renders it (491623-491638).
Other types are excluded because display labels can select reference controls (164783). Unknown display strings still compare strictly.
User-defined `name` and `label`, provider, type, reference/table, defaults, values, IDs, unknown attributes, and parameter order remain strict.

New String assignment parameters use the observed native default `maxsize=8000` when the declaration reports zero, meaning no explicit size.
An explicit positive declared length is preserved. Existing parameter sizes are not rewritten, and save comparison never equates different sizes.
New parameter `type_label` uses the String control label initialized at 147809-147822. Declaration DTOs retain their separate native defaults.

### Native root cache mirror

`labelCacheAsJsonString` is the server's serialized duplicate of `label_cache`, not an independent authoring setting.
Studio deletes it at the diagram callback boundary (227783-227788). Its native PUT `FLOW_SCHEMA` does not contain this field (350835-350903).
Planning and transport omit only this fixed root field. Unknown settings and identically named fields at other locations remain unchanged.
When a returned definition includes the field, local semantics parses it and verifies equality with that definition's authoritative `label_cache`.
Malformed JSON, a non-list result, or changed mirror content fails verification. JSON formatting and property order do not define cache content.

`processLabelCacheOutgoing` emits absent `attributes`, `choices`, `column_name`, and `parent_table_name` as null (101724-101727).
Only these null/absent cache-entry forms compare equivalently. Nonempty values, empty lists, false values, and unknown keys remain strict.
Existing choice ordering retains its value-based comparison. The native handler index `startingIndexOfErrorHandlingInstances` remains authoritative.
Ordinary edits do not add the editor-only `startOfErrorHandlingInstances` alias when the saved definition contains only the native index.

### Service Catalog call inputs

The backend creates a flow-owned call interface when an empty flow receives a Service Catalog trigger during full save.
The observed inputs are `table_name` (`table_name`, default table `sc_req_item`, hidden test input) and `request_item` (mandatory reference to `sc_req_item`).
They are separate from trigger output-definition records and from authored subflow declarations.

The planner binds their complete expected native DTOs to the independently read Service Catalog definition before the write.
Names, types, references, defaults, labels, constraints, and unknown metadata come from its table-name and reference output parameters, not hardcoded field names.
The captured native declaration projection adds the flow-variable mapper/UI attributes, uses declaration `readonly=false`, and omits the empty parameter-only `internal_link`.
Hidden runtime date/time outputs remain trigger outputs. Ambiguous or unsupported catalog shapes reject before saving.

The header POST remains empty, with its existing strict verification. The native full PUT retains backend-generated interface behavior.
Preview `fields.inputs` describes the expected generated interface. During echo and GET verification, the exact input records must match their own IDs, `sys_hub_flow_input.model`, class, and catalog-bound `element` names.
Table API `element` is the Studio DTO's technical `name`, as in the existing contract projection.
Table API `name` identifies the shared virtual table `var__m_sys_hub_flow_input_<flow-id>`. It is not a declaration name and does not distinguish the two inputs.
Only then does comparison bind their generated IDs to the expected DTOs. Extra, missing, or changed declarations still fail, and echo-to-GET IDs remain strict.
Saved interfaces are preserved on later edits. Triggerless subflow input CRUD, other triggers, and unexpected flow inputs receive no such allowance.
Input-table write policy is checked for a Service Catalog trigger operation. This does not add interface update/delete permissions.

Service Catalog is not REST/external: the client `createFlowInputs` branch at 184408-184414 does not apply to it.
The supported backend-owned form comes from the retained native GET and catalog metadata, with requested-record semantics documented in `create-sc-flow.md:27`.
The historical one-shot outcome remains unknown. The complete native serialization checks and subsequent live acceptance result are recorded in [live verification](flow-live-verification.md).

### Fresh native instance DTOs

Fresh actions from the full published-action endpoint use native seven-field binding wrappers and explicit instance defaults.
The constructor maps the endpoint's known nullable descriptor slots into the native embedded definition, including action-status metadata.
It preserves unknown nonnullable metadata. It does not copy another instance's settings, IDs, aliases, or block references.
Abbreviated call definitions retain their existing adapter. Subflow calls and active-draft adapters are unchanged.

New If and Else instances use the current logic catalog, native binding wrappers, and explicit defaults, as Set Flow Variables already does.
New Service Catalog triggers use native input/output wrappers while retaining the independently bound parameter schemas.
The REST wire uses writable `parameter.type_label` for the native getter's `parameter.uiDisplayTypeLabel`.
The expected DTO retains the getter form. Conflicting label copies reject before saving, and readback still compares strictly.
These constructors apply only to new instances. Saved metadata remains unchanged.
New conditional and assignment block IDs must pass exact logic-instance owner reads before comparison.
Existing blocks, echo-to-GET identities, unknown metadata, branches, and semantic values remain strict.

## Structural edits

`flow_preview(action="edit", sys_id=..., edits=[...])` previews an ordered list of edits to one supported editable owner.
`mutation_apply` saves the result with one request. The preview shows the flow outline before and after the change.

| `op`                            | Fields                                                                                                                                                                                                                   | Effect                                                                                                                                                                                                                                                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `set_trigger`                   | `type` or `template_id`, `inputs`, `interface_schemas`, `replace`                                                                                                                                                        | Configures a catalog trigger or an eligible saved picker definition. `interface_schemas` is REST-only.                                                                                                                                                                                                  |
| `update_trigger`                | `inputs`, optional REST `interface_schemas`                                                                                                                                                                              | Updates selected configuration while preserving identity and opaque metadata.                                                                                                                                                                                                                           |
| `remove_trigger`                | None                                                                                                                                                                                                                     | Removes the saved instance with an ID tombstone. Used references block removal.                                                                                                                                                                                                                         |
| `insert_logic`                  | `logic`, `inputs`, placement, `ref`, supported `input_scripts` / `input_transforms`; Decision also `decision_inputs`, `branches`                                                                                         | Adds a directly authorable native logic family using the routes above; companions use their source operation.                                                                                                                                                                                           |
| `move_step`                     | `step`, `parent` or `after` or `before`                                                                                                                                                                                  | Moves an existing step, also into or out of a branch.                                                                                                                                                                                                                                                   |
| `update_inputs`                 | `step`, `inputs`, supported `input_scripts` / `input_transforms`; call-only `connection_overrides`; Decision `decision_inputs`, `branches`, `children`                                                                   | Changes typed bindings/configuration through the selected native adapter, never arbitrary instance metadata.                                                                                                                                                                                            |
| `delete_step`                   | `step`, `children`, `cascade`                                                                                                                                                                                            | Deletes saved actions or logic with ID tombstones. Deletes saved subflow calls by omission from the complete draft list, not by deleting the referenced definition. Child handling is `reject` (default), `delete`, or `promote`. Connected branches require `cascade=connected` and `children=delete`. |
| `set_connection_overrides`      | `connection_overrides`                                                                                                                                                                                                   | Changes flow-global declared connection-alias selections; per-call overrides use call insertion/update fields.                                                                                                                                                                                          |
| `set_variables`                 | `inputs`, supported `input_scripts` / `input_transforms`, placement                                                                                                                                                      | Assigns existing declared flow variables through native value and basic/script/transform bindings.                                                                                                                                                                                                      |
| `assign_outputs`                | `outputs`, optional `step` or placement and `ref`                                                                                                                                                                        | Creates a native Assign Subflow Outputs step or updates selected rows. Supports scalar values, declared Object/Array values, and schema-compatible typed pills.                                                                                                                                         |
| `clear_output_assignment`       | `step`, `name`                                                                                                                                                                                                           | Removes one assignment row. The declared output and logic step remain.                                                                                                                                                                                                                                  |
| `create_field`                  | `collection`, `name`, `label`, `type`, optional `parent`, `reference`, `reference_qualifier`, `dependent_on`, `choices` or `choice_source`, `choice_option`, `hint`, `maxsize`, `max_rows`, `mandatory`, `default_value` | Creates a scalar variable or scalar/nested subflow interface declaration using native metadata.                                                                                                                                                                                                         |
| `update_field`                  | `collection`, `name`, declaration settings, eligible `new_name`, explicit `reset_structure` for type changes                                                                                                             | Updates native metadata/schema with identity, editability, default, and consumer guards.                                                                                                                                                                                                                |
| `delete_field`                  | `collection`, `name`                                                                                                                                                                                                     | Removes an unused declaration, including Object descendants or an array item schema. No automatic consumer cleanup.                                                                                                                                                                                     |
| `create_stage`                  | `label`, `value`, optional `duration`, `always_show`, `states`, `ref`                                                                                                                                                    | Creates a standard stage definition. The optional ref addresses it in later edits.                                                                                                                                                                                                                      |
| `update_stage`                  | `stage`, `label`, `value`, `duration`, `always_show`, `states`, `order`                                                                                                                                                  | Updates a standard stage without replacing unknown metadata. Order is a zero-based position.                                                                                                                                                                                                            |
| `attach_stage` / `detach_stage` | `stage`, `step`                                                                                                                                                                                                          | Explicitly attaches or detaches a stage at the beginning of a step. No implicit replacement.                                                                                                                                                                                                            |
| `delete_stage`                  | `stage`                                                                                                                                                                                                                  | Removes an unattached standard stage. Dependent error stages block removal.                                                                                                                                                                                                                             |

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
Live evidence is limited to the documented native Log fixtures. Other families retain offline verification.

Pills reference trigger outputs, `<producer UI ID>.<output path>`, `flow_variable.<name>`, or declared `subflow.<input>` fields.
The editor checks producer order, branch/loop scope, parallel path isolation and joins, and Try/Catch status visibility.
Reference fields require compatible tables. Collections cannot substitute for scalar values.
Whole Object and declared Array pills are supported for proven compatible schemas, including the source-native choice/date-time/array forms described below.
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
Call deletion uses native connection-override cleanup: shared aliases remain while another call uses them; last-user deletion removes unsaved mappings or retains saved mapping IDs as empty-source/target tombstones.
The source-derived fixture is not a captured server response. This capability is offline-regression verified, not live verified.

For saved-call deletion, an existing `componentInstances` list must exactly mirror the three ordered step lists.
The planner rebuilds that duplicate list from the surviving steps. Missing, reordered, or mirror-only metadata blocks planning.
An absent mirror stays absent. This does not reconstruct a mismatch from an earlier save.
When a complete save assigns new database IDs, readback checks the mirror against the authoritative lists before comparing IDs.
Only IDs of explicitly new steps can normalize in both copies. Existing IDs, bindings, and mirror-only metadata remain strict.
Studio's `removeActionFromFlow` updates this list before `getUpdatedFlowToSaveOrPublish` splits it for saving.
`saveFlowToServer` then calls `cleanupFlowBeforePut` (local bundle:97521-97527,101697-101712).
When `TRIM_FLOW_PAYLOAD` is enabled, cleanup selects `FLOW_SCHEMA` fields (350835-350903), excluding the mirror and `alerts`.
Otherwise, cleanup retains them. This adapter does not infer that configuration or drop unknown top-level settings.
Ordinary call deletion leaves `alerts` unchanged. Connection cleanup follows the separate native path (253620-253682) and preserves unrelated mappings and opaque saved metadata rather than clearing all overrides.

### Conditional, loop, Parallel, and paired Try construction

`insert_logic.logic` accepts the catalog name or native type case-insensitively. `parent` appends a direct child; `after` / `before` select a sibling position in the evolving ordered tree.
An optional `ref` names the inserted block for later placement or pill bindings in this request; it is not a persisted label.

| Native type         | Declared configuration and route                                                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `IF`, `ELSEIF`      | Required String `condition`, optional String `condition_name`. Else If follows its connected If/Else If chain.                                         |
| `ELSE`              | No inputs; connected sibling of an existing conditional chain.                                                                                         |
| `FOREACH`           | Required `items` Records value or compatible collection pill; body children use `parent`. Its scoped `item` output inherits the selected record table. |
| `PARALLEL`          | No inputs. Insert the parent, then explicit `PARALLELBLOCK` children, each with at least one body step before the complete plan validates.             |
| `PARALLELBLOCK`     | No inputs. Insert only with its Parallel parent; `ref` can name the branch for body placement. Nested Parallel is prohibited by the native attributes. |
| `TRY`               | No inputs. Insertion creates an adjacent linked Catch automatically; body steps use the Try `ref`.                                                     |
| `CATCH`             | Generated with Try, not independently inserted. It remains paired during moves and deletion.                                                           |
| `BREAK`, `CONTINUE` | No inputs; require a valid For Each/Do Until ancestor and the native conditional placement within it.                                                  |
| `END`               | No inputs; terminal sibling with no children.                                                                                                          |

For example, these ordered edit fragments configure a nonempty Parallel tree without invoking any step:

```json
[
  { "op": "insert_logic", "logic": "PARALLEL", "ref": "parallel" },
  {
    "op": "insert_logic",
    "logic": "PARALLELBLOCK",
    "parent": "parallel",
    "ref": "first"
  },
  { "op": "insert_logic", "logic": "END", "parent": "first" },
  {
    "op": "insert_logic",
    "logic": "PARALLELBLOCK",
    "parent": "parallel",
    "ref": "second"
  },
  { "op": "insert_logic", "logic": "END", "parent": "second" }
]
```

Try's generated Catch has no `catch_ref` field or automatically assigned caller alias.
It can be addressed by its actual projected display order once that order is derived from the edits-to-date, or by its native/UI identity after save and inspection.
Do not guess a Catch alias or insert a sibling between its Try and Catch. A top-level error-handler region instead uses the dedicated `error_handler` parent contract below.

### Loops, jumps, and Decision branches

`insert_logic` accepts the native `logic` type or resolved catalog name; `update_inputs` changes an existing block's declared bindings.
Both creation and later editing use the same topology checks. Placement, connected branches, stages, and all surviving references are validated before saving.

| Family     | Native configuration                                                                                                                            | Topology                                                                                                                                                                                                                          |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DOUNTIL`  | `inputs.condition` required string; optional `condition_name`                                                                                   | One sandwich opener with body children placed using `parent`. It cannot nest inside another Do Until. Its closing condition can consume a direct body child's output; that exception does not expose body pills outside the loop. |
| `GOBACKTO` | `inputs.go_back_to_step` selects an existing alias, UI ID, sys_id, or display order                                                             | Target selection is pinned to the native UI identity before renumbering. It must be the final child of an eligible conditional, Decision Branch, or Catch and cannot occur in the top-level error handler.                        |
| `DECISION` | `inputs.decision_table`, optional `decision_label`, `execution`, `has_branches`, `include_otherwise`; separate `decision_inputs` and `branches` | Metadata supplies typed inputs, native answer identities/order, derived answer table, and generated Decision Branch companions.                                                                                                   |

Do Until honors native non-nesting and sandwich closing-order restrictions. A populated nested sandwich whose closing order is unresolved and has no saved integer `orderBottom` rejects; the planner does not invent one.
Go back to offers only native previous sibling/ancestor construct starts within the enclosing Parallel boundary, not arbitrary earlier cousins, future steps, or a node inside a previous subtree.
Direct arbitrary children of Decision remain prohibited; branch content belongs under generated `DECISIONBLOCK` companions.
Moves or deletion that invalidate an existing jump fail. Retarget or remove it explicitly; no target is cleared silently.
Exit Loop, Skip Iteration, and End retain their native loop-placement and terminal-sibling restrictions.

A Decision Table must be active, readable, and independently revision-bound. `/api/now/decisiontable/decisions/{id}` reads its typed authoring metadata; it does not evaluate the decision.
`answer_table` and generated outputs are read-only. `execution` uses the native `first_match` or `run_all` choice.
`decision_inputs` is a native object keyed by the selected table's declared input names.
`branches` is optional: omission generates the complete authoritative native answer order automatically, including Otherwise when enabled.
To supply labels or request-local branch aliases, pass the complete ordered array of `{ "answer": "<native-answer-sys-id>", "label": "Optional label", "ref": "optional-alias" }`.
Use `answer=""` only for Otherwise, last. Do not invent answer IDs, omit native answers from an explicit list, or sort branches yourself.
For example, an installed table with one answer can use this edit shape after its metadata has been read:

```json
{
  "op": "insert_logic",
  "logic": "DECISION",
  "ref": "routing",
  "inputs": {
    "decision_table": "<active-readable-decision-table-sys-id>",
    "execution": "first_match",
    "has_branches": true,
    "include_otherwise": true
  },
  "decision_inputs": { "<native-input-name>": "<native-typed-value>" },
  "branches": [
    { "answer": "<native-answer-sys-id>", "ref": "matched" },
    { "answer": "", "label": "Otherwise", "ref": "otherwise" }
  ]
}
```

Replace placeholder names, IDs, and values using the exact selected table metadata. Later edits place branch steps with `parent="matched"` or `parent="otherwise"`.
`update_inputs` on a Decision accepts the same `decision_inputs` / `branches` fields. Changing its table requires an explicit complete mapping for every writable native decision input; protected read-only values cannot be overridden or silently inherited as new writable values.
Removing a generated branch with populated children requires explicit `children="delete"`; children are never silently discarded. Empty native wrappers may disappear under the explicit table/mode/Otherwise change without that flag.
Surviving pills and stage attachments can still block destructive changes. A generated Decision Branch permits only its label through `update_inputs`; answer identity, independent insert/move/delete, and arbitrary outputs remain protected.
Native custom Decision answers can expose `{{<branch-ui>.answer.result_elements.<field>}}` paths from independently read result-element metadata.
For `run_all` without branches, iterate the declared `{{<decision-ui>.records}}` output with For Each, then use `{{<loop-ui>.item.result_elements.<field>}}`.
Only metadata-declared fields/types are offered; reading result schemas does not evaluate the decision.

### Timer, workflow, and dynamic-call configuration

These native logic families use `insert_logic` and later `update_inputs`. Their reference/configuration reads do not execute a workflow, a called flow, or an existing context.

| Native type      | Public `inputs`                                                                                                                                                               | Declared metadata required                                                                                                                                                   |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TIMER`          | Explicit `duration_type`; mode-specific values below; optional `timer_schedule`                                                                                               | Current timer definition and optional readable `cmn_schedule` identity/name or typed schedule pill.                                                                          |
| `RUNWORKFLOW`    | `workflow_reference` sys_id/exact name, `workflow_inputs` native object, explicit Boolean `wait`, and `current` for a table-bound workflow                                    | Active published workflow availability, native configuration/interface, and a compatible current-record pill/sys_id. A global workflow has no table-specific current record. |
| `RUNDYNAMICFLOW` | `flow_template` sys_id/exact name, `flow` callable sys_id/display name or typed string expression, explicit Boolean `wait_for_completion`, and `dynamic_inputs` native object | Readable entitled published master snapshot/interface; a static callable must match that interface and satisfy scope access. Activation state does not gate configuration.   |
| `GETFLOWOUTPUTS` | Required compatible `context` pill/sys_id; optional `flow_template`                                                                                                           | Context is read, never resumed/executed. A selected readable published template supplies the output schema; without it no dynamic output schema is inferred.                 |

Timer modes use these exact installed names:

- `explicit_duration`: `timer_duration`.
- `relative_duration`: `timer_duration`, `timer_relative_duration_operator` (`before` or `after`), and `timer_relative_duration_datetime`.
- `percentage_duration`: integer `timer_percentage` from 0-100 and `percentage_timer_input_datetime`.

Supply the selected mode's values explicitly. Native durations are offsets from `1970-01-01 00:00:00`, not ISO durations; zero is valid for this timer.
The native timer widget permits at most 9999 hours, 59 minutes, 59 seconds. Temporal values use `YYYY-MM-DD HH:mm:ss` or one compatible typed pill, without timezone reinterpretation.
For example, this complete creation request configures a five-minute wait without starting it:

```json
{
  "action": "create",
  "name": "Timer configuration example",
  "scope": "global",
  "edits": [
    {
      "op": "insert_logic",
      "logic": "TIMER",
      "inputs": {
        "duration_type": "explicit_duration",
        "timer_duration": "1970-01-01 00:05:00"
      }
    }
  ]
}
```

Workflow/dynamic-call input objects use the independently read interface's native field names and declared types. Caller values cannot replace read-only fields or generated schemas.
Global legacy workflows use absent/null/empty or native `global` table metadata and accept no table-specific `current` value; the original metadata spelling remains preserved. Requested Item workflows remain excluded by the native picker; use the dedicated Service Catalog route.
Call a Workflow with `wait=false` exposes no output pills, including context, matching the native source gate; its static output metadata remains serialized.
Dynamic Flow with `wait_for_completion=false` retains its static context pill but not dynamic template-output pills; use Get Flow Outputs with compatible context/template metadata for later declared outputs.
A concrete Dynamic Flow target may use the source-native qualified `namespace.display-name` form when scope metadata resolves it unambiguously.
Reference identities, published metadata, source revisions, wait visibility, output schemas, and unknown saved metadata bind preview/apply and independent readback.
The hidden `UPDATEFLOWTEMPLATE` definition is not an extra callable block: its native renderer is system-locked/read-only and existing DTOs are preserved strictly.
No ordinary Dynamic Flow operation is claimed to synthesize that hidden representation. `PLACEHOLDER` is internal/transient, not a missing authoring adapter.

### Trigger configuration

These operations configure supported V2 drafts, including roots that meet the active-draft requirements above.
They do not create an active schedule or a separate saved trigger definition.
`set_trigger` selects a catalog `type` or an eligible saved `template_id`; replacing an existing trigger requires `replace=true`.
`update_trigger` preserves the trigger ID, unmentioned inputs, and opaque settings.
`remove_trigger` uses native `deletedTriggers` ID tombstones for saved instances. Used references block replacement/removal.
Subflows remain triggerless. All operations use the existing preview/apply draft save and never publish, activate, run, or invoke the configured trigger.
Input names, defaults, choices, types, read-only flags, outputs, and capability attributes come from the installed definition.
Do not copy a trigger name into guessed recurrence, calendar, timezone, or REST fields.

#### Saved picker definitions

Use `set_trigger.template_id` with an independently readable `sys_hub_trigger_template` identity:

```json
{
  "op": "set_trigger",
  "template_id": "<saved-trigger-template-sys-id>",
  "inputs": { "<native-writable-input-name>": "<native-value>" }
}
```

This is a shape example, not a built-in recurrence schema. Replace the template and input names/types from the selected native picker definition; omit `inputs` when no writable overrides are needed.
Optional `type` must exactly equal the selected definition's native type. Existing triggers still require `replace=true`.
Later `update_trigger` supplies `inputs` and, for writable native REST roots, optional `interface_schemas`; it derives the selected saved definition from the existing instance. `remove_trigger` is unchanged.
Saved-trigger attachment requires a readable string flow version other than `"1"`; a missing version follows native v1 exclusion.
The effective picker supplies its exact saved identity/class, flat input metadata/defaults, outputs, underlying definition, trigger values, and template properties.
The independent template row, its revision, and the effective picker must agree before saving. Inputless saved templates are identified by their native table/definition identity, not a fabricated inherited-input marker.
Native constructor `fromTemplate` flags are retained exactly; an inputless definition need not set that Boolean true to retain its saved-table ownership.

Inherited `fromTemplate` inputs are read-only even if their ordinary `readonly` flag is false; callers cannot override them, including with the same value.
Inherited Object/Array values and opaque metadata remain unchanged. Writable scalar/time/choice/reference inputs use their declared schemas; native REST structural configuration uses `interface_schemas`, not runtime Object values in `inputs`.
Calendar references require native static table/qualifier metadata and readable referenced-record revisions. Scripted/dynamic qualifiers reject.
Timezone choices or strings are accepted only when the selected metadata declares them. Values are exact: there is no inferred timezone or wall-time conversion.
Recurrence/business-calendar support therefore depends on the installed saved definition, not a universal field-name adapter.
No concrete live Recurrence template compatibility is established by the classic schedule evidence.
The tools attach/edit an existing saved definition; they do not create or edit the saved template itself.
Full picker/template/underlying-trigger-definition revisions and referenced-record evidence bind preview, apply, local saved validation, and strict independent readback.

Saved Service Catalog definitions use their selected flat metadata for the backend-generated call-input manifest.
Native REST-owned interfaces (REST category, or Application `rest_async` / `rest_sync`) and `external_trigger` derive flow-owned inputs from the selected trigger's declared outputs.
There is no blanket saved-family blacklist. The native ownership predicate and selected metadata determine whether that interface route applies; absent path controls never gain guessed REST fields.
Writable REST roots honor their native type allowlists. Inherited `attributes.fromTemplate="true"` or read-only roots cannot be structurally overridden.
External interfaces follow the native attributes-only projection; arbitrary child-schema edits are not introduced where the source does not author them.
Cross-family replacement/removal binds existing flow-owned input record identities, owners, and revisions and rejects surviving pill/default/transform/script consumers instead of silently rebinding them.

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
Existing nonempty parsed-condition metadata blocks condition updates; it is never silently discarded or treated as a new empty builder state.

Run/session/user/extended-table settings use exact native catalog choices, including `run_on_extended`, `run_when_setting`,
`run_flow_in`, `run_when_user_setting`, and `run_when_user_list` where offered.
User lists are comma-separated sys_ids. Selecting an include/exclude user mode requires a nonempty list.
The catalog supplies any additional Updated/Created-or-Updated run-mode parameter. No run-mode key or choice value is invented.
Omitted inputs retain catalog defaults. An explicit choice must occur in the current choice list.
The fixture catalog contains a legacy `run_flow_in="any"` default outside its choice list.
That exact omitted default is preserved. Explicit edits must use a listed value, such as `background` or `foreground`.
Run-as/role settings outside the trigger remain unchanged.

#### Classic schedules

Use parameter names from the current catalog. The observed native catalog uses these contracts:

| Type       | Native inputs                                                       |
| ---------- | ------------------------------------------------------------------- |
| `daily`    | `time="1970-01-01 HH:MM:SS"`                                        |
| `weekly`   | `day_of_week` from the current catalog choice list, plus `time`     |
| `monthly`  | Native integer `day_of_month` from 1-31, plus `time`                |
| `run_once` | `run_in="YYYY-MM-DD HH:MM:SS"`                                      |
| `repeat`   | `repeat="YYYY-MM-DD HH:MM:SS"`, measured from `1970-01-01 00:00:00` |

```json
[
  {
    "op": "set_trigger",
    "type": "daily",
    "inputs": { "time": "1970-01-01 09:30:00" }
  },
  { "op": "update_trigger", "inputs": { "time": "1970-01-01 10:00:00" } }
]
```

Supply schedule values explicitly. No current clock value, next hour, or inferred timezone enters the preview.
Historical catalogs can use type names such as `glide_time` or `glide_date_time` as parameter names.
The adapter binds roles to independently declared names and types. Do not substitute historical names without catalog evidence.
Classic schedules use the instance timezone. The adapter does not convert values to the OAuth user's timezone or UTC.
`run_once` accepts past dates as configuration, matching Studio. This does not authorize activation or execution.
Repeat durations must be positive and meet the catalog's `minimum_seconds` when present. One day is `1970-01-02 00:00:00`.
Weekly choices are instance supplied. No weekday-to-number translation is guessed.
Classic schedules do not accept undeclared timezone overrides, end dates, or recurrence fields. Use eligible saved picker metadata for those configurations instead of extending a classic type with guessed inputs.
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
`create-scheduled-trigger.md` describes saved Recurrence behavior; its actual authoring inputs remain owned by the selected installed saved definition.
Source-derived tests cover all supported families offline. Live Run Once creation and activation used a date safely in 2035.
No automatic schedule execution, DST handling, or other live schedule family was tested.

#### Application triggers and REST interfaces

Use the native family names in the route table. Configuration reads catalog/table/reference/dictionary metadata only; it does not run a provider script or invoke the trigger.
The installed Inbound Email definition uses `email_conditions`, `stop_condition_evaluation`, `order`, and `target_table`.
Remote Table Query uses `u_table`, restricted to an installed `sys_script_vtable` remote-table definition.
MetricBase uses mandatory `metric_trigger` referencing `sys_metric_trigger_definition`, optional `moderator`, and dependent `condition`.
Its `table_name` is read-only and derives from the selected metric definition's `metric_table.name`.
Proactive Analytics uses `proactive_analytics_trigger` referencing `sys_analytics_trigger_definition`; the source owns its derived `table_name` through `event_table.name`.
The native `encoded_query` is only an empty-condition suggestion; an explicit condition is preserved.
Changing a source table/provider in a family with condition inputs requires explicit condition values; no condition or consumer is cleared implicitly.
SLA Task has no authored inputs in this catalog and retains its complete `task_sla_record` / `sla_flow_inputs` output schemas.

REST trigger `inputs` contains scalar configuration such as `http_method`, `requires_authentication`, and `display_path`.
Use current catalog choice values; the installed HTTP method choices use lowercase values such as `post`.
`interface_schemas` is a separate mapping of native root names (`body`, `headers`, `query_parameters`, `path_parameters` where declared) to complete typed schemas:

```json
{
  "op": "set_trigger",
  "type": "rest_async",
  "inputs": {
    "http_method": "post",
    "requires_authentication": true,
    "display_path": "/authoring-example"
  },
  "interface_schemas": {
    "body": {
      "type": "object",
      "children": [
        {
          "name": "message",
          "label": "Message",
          "type": "string",
          "mandatory": true,
          "maxsize": 200
        }
      ]
    }
  }
}
```

The same `interface_schemas` field works with `update_trigger`. Each listed root completely replaces that root's schema; omitted roots are preserved.
`children: []` explicitly removes fields. Child objects use `name`, `label`, `type`, optional `mandatory`, `maxsize`, and recursive `children`.
Native `customSupportedRootLevelFieldTypes` and `customSupportedChildFieldTypes` restrict each root and child type; generic subflow declaration support does not broaden those allowlists.
Read-only `base_path` is server-generated using a fixed GraphQL query POST to `/api/now/graphql`, which reads authoring metadata and does not invoke the REST trigger.
`media_type` retains its read-only catalog default, such as `application/json`. Native `path` is derived from `display_path` and its own declared path-parameter pills.
Callers cannot replace protected fields or submit Object runtime values through scalar trigger `inputs`.
Definitions, generated metadata, interface schemas, reference/table identities and revisions enter the preview fingerprint and strict save/readback comparison.
Availability still depends on the instance's installed feature, permissions, and licensing. This authoring contract does not establish REST request handling, inbound-email processing, MetricBase evaluation, or other runtime behavior.

### Structured calls and paired moves

`insert_action` and `insert_subflow` add calls inside an existing structured draft, including an enabled handler.
They accept `inputs`, optional `ref`, and the existing `parent`/`after`/`before` placement fields.
Action insertion requires a published snapshot `action_type_sys_id`.
Subflow insertion requires a published root `subflow_sys_id` and resolves its exact active snapshot independently.
Read access, entitlement, cross-scope public access, self-call rejection, and the complete definition bind the preview.

```json
[
  {
    "op": "insert_action",
    "action_type_sys_id": "<published-action-snapshot-sys-id>",
    "inputs": { "message": "Example" },
    "parent": "error_handler",
    "ref": "notify"
  },
  {
    "op": "update_inputs",
    "step": "<later-step-ui-id>",
    "inputs": { "message": "{{notify.<declared-output-name>}}" }
  }
]
```

Replace all placeholders and use actual declared input/output names.
`inputs` uses the existing scalar and declared complex serializers. Later edits can use `ref` as a placement or pill producer alias.
Aliases become stable native UI identities before serialization. Subflow calls wait for completion.
Dynamic input modes use the separately reviewed schema-evidence route below. The action's published definition remains immutable; only source-native effective binding metadata is adopted.
Dedicated adapters also cover native table-backed document/field parameters and Get Catalog Variables selections.
Catalog selection uses the declared slushbucket input's native CSV: bare legacy IDs refer to `item_option_new`; `sys_id:source_table` composites follow the installed `include_multirow_variablesets="true"` capability.
Selection reads `{catalog}/service_catalog_variables` and the read-only preserve-selected POST `{catalog}/service_get_catalog_variables`; neither executes an action, provider, or default script.
Complete selected native metadata is retained, including declared defaults, unknown fields, and supported scalar/date/list/choice/reference/Object/native Array schemas; output types are not inferred from sample values or replaced with blank scalar stubs.
Native catalog filtering and Service Catalog flow/model ownership still apply. An Array.Choice `table` copied as selected-record provenance is not automatically a declaration choice-provider table.
Preview/apply binds the independently read selection schema/default/revision evidence and rejects unavailable fields or drift without modifying stored defaults or values.
If the native available-options list omits a selected ID, only an unchanged preexisting current-server output can be preserved: its saved producer identity/type/full DTO, active native selection, and independent variable identity/name/type/owner/reference/revision evidence must all agree.
Unknown raw types or owners, including unproven multi-row variable metadata, reject without schema inference. Missing native child UI fields are reconstructed only in a read-only consumer schema view; the raw selected DTO remains unchanged.
The catalog template source remains one static `sc_cat_item` sys_id or native picker-JSON value, not a guessed CSV template list.
Approval rules and due dates use their complete source-native widget grammars described below, including explicit active deadlines. Configuring an approval never requests one during authoring.
Read-only table defaults may be resolved from a typed record pill, but callers cannot override read-only parameters.
Static table-dependent action conditions support bounded encoded queries and typed pill values.
The planner checks condition fields through the inherited dictionary resolver. It rejects scripts and query directives.
Action `internal_link` record outputs use the published output's linked table parameter, a readable table descriptor, and dictionary field types.
Preview/apply binds these definitions and metadata. The published output schema and unknown settings remain unchanged.
A filter such as `valueINADD,REMOVE` does not declare a choice type for `Record.value`.
String outputs still cannot supply choice inputs without an actual declared compatible choice set.
The older `add_action`/`add_subflow` header-append actions retain their narrower compatibility contract.

### Typed input modes and connection overrides

`insert_action`, `insert_subflow`, `insert_logic`, `update_inputs`, and `set_variables` accept their declared native values plus explicit `input_scripts` and `input_transforms` where the native input supports them.
Connection overrides apply only to action/subflow calls or the dedicated flow-global operation.
These fields configure saved bindings without mutating published definitions or executing code, transforms, connections, workflows, decisions, or called flows.
Mandatory state combines parameter and binding metadata. Hidden UI fields are not automatically optional or writable; read-only/derived inputs remain protected.
Metadata flags, readable entitled definitions, scope access, local policy, and ServiceNow ACLs remain authorization boundaries. Browser UI feature flags are not a substitute for server authorization.

| Declared widget                                                 | Caller value and constraint                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Text, UTF-8, long/multi text, HTML, translated HTML, Email, URL | Native text with declared length constraints and compatible interpolation. The adapter does not prove rendered content or business semantics.                                                                                                                                          |
| Boolean, Integer/Long Integer, Float/Decimal/Number             | Native booleans; finite JSON numbers or valid native numeric widget text as declared. Integer metadata bounds apply; booleans are not numbers and no universal invented bit-width limit is added.                                                                                      |
| Time, Date/Time, Duration                                       | Native timestamp/epoch-offset text or one compatible typed pill; Time uses the epoch date, Duration cannot be negative, and no timezone is inferred.                                                                                                                                   |
| Reference / Document ID / record or Glide lists / table         | Exact declared table metadata and compatible typed pills. References accept sys_id or native picker JSON text; lists use native comma-separated IDs, not invented arrays. Literal IDs do not by themselves establish runtime record access.                                            |
| JSON / XML / password-like string types                         | Use the declared native string representation; JSON values require valid JSON text. Saving a field is not execution or proof of content behavior; keep secret values out of review logs.                                                                                               |
| Name-Values / Simple Name-Values                                | Native JSON **text** containing string `name` / `value` rows. Supported advanced forms use the exact `ADV_NV` envelope and declared boolean options; unresolved advanced configurations reject.                                                                                        |
| Slushbucket                                                     | Generic widgets accept native `{label,value}` option arrays matching declared available options. Catalog wrappers use native CSV; table-qualified IDs require the installed multi-row-variable selection capability, while bare legacy IDs retain the native `item_option_new` source. |
| Conditions / field templates                                    | Bounded native encoded queries without script/query directives; or native `field=value` template text with declared columns and typed values. Template column scripts and upsert coalescing use the explicit native modes below; unknown fields and unbound script markers reject.     |
| Approval rules                                                  | Native goals `Approves`, `Rejects`, `ApprovesRejects`; `Any`, `All`, `Res`, count (`#`), percentage (`%`), manual (`M`), and typed `U`/`G`/`Z` approver groups with native conjunctions. Reference types and native threshold bounds are checked.                                      |
| Approval due date                                               | Native JSON text with `action`, `date_type`, `date`, `duration`, `duration_type`, `schedule`, `schedule_label`. Active approve/reject/cancel outcomes require one compatible Date/Time pill; action `none` retains the native `date="{{}}"` sentinel.                                  |
| Object / Array / dynamic modes                                  | Declared recursive native values or compatible whole pills. Provider-resolved modes use the separate reviewed evidence workflow below; a JSON string does not substitute for an Object/Array.                                                                                          |

#### Saved inline scripts and basic mode

`input_scripts` maps uniquely named declared inputs to JavaScript source strings or null. Native ordinary, workflow, Decision runtime, and Set Flow Variables inputs share this route; ambiguous names reject. For example:

```json
{
  "op": "update_inputs",
  "step": "<call-ui-id>",
  "input_scripts": { "message": "return 'Configured only';" }
}
```

This saves inline code; it does not evaluate it, prove JavaScript correctness, or prove the returned runtime type.
Nonempty source enables native script mode. Null switches explicitly to basic mode while preserving inactive source and its producer dependencies.
An empty string clears the stored source and returns to basic mode, allowing a producer to be removed once no other dependency remains.
Native read-only and `is_scriptable=false` metadata reject script changes.
Whole input scripts follow native widget availability: parent Object/Array inputs can use the generic mode, but no invented nested/indexed script selectors are offered. Existing saved child scripts remain recursively preserved and dependency-checked.
Native dynamic-input/choice/template/glide-variable modes, Name-Values, whole field templates, dynamic-produced fields, and `fd_hide_inline_script_widget=true` do not gain a generic whole-input script widget. Field templates use their separate qualified column mode.
Static structural selectors remain protected: Go back to's target, Decision table/execution/branch/Otherwise/answer-table selectors, generated Decision Branch answer IDs, and Do Until's non-scriptable condition cannot be replaced with scripts or transforms.
The ordinary `inputs` value may accompany the same `input_scripts` name as a retained basic fallback; null/empty source explicitly selects basic mode and applies normal value validation.
An ordinary value and an `input_transforms` option for the same name conflict because the transform's source is authoritative. Active nonempty script plus transform also conflicts; explicit script disable/clear plus transform is allowed.
Direct named `fd_data` references are lexically discovered, pinned to producer UI identities before topology edits, and rebound after native renumbering; strings, comments, and regular-expression text are not rewritten as references.
Script producer names use the current native `fd_data._N__normalizedproducer.field` spelling, not request-local `ref` aliases. Ordinary transform source/parameter pills can use request aliases and are rebound to stable UI identities.
The scanner is not a general JavaScript parser. Computed/optional roots, root rebinding, ambiguous lexical forms, and unresolved reference syntax fail closed instead of silently keeping stale order references.
Limits are 65,000 source characters, 100 references, and 32 nested template interpolations. Producer order, scope, handler/branch isolation, and declaration dependencies remain checked even for retained inactive code.

#### Template column scripts and coalescing

For `template_value` or a reviewed `dynamic_template`, `input_scripts` can address a selected column with `"<input-name>.<column-name>"`.
The template input and selected column must already exist or be supplied as a basic encoded value in the same edit; native read/write/scriptability flags and independently read column types remain enforced.
For example, when the native call declares template input `fields` and a writable String column `short_description`:

```json
{
  "op": "update_inputs",
  "step": "<call-ui-id>",
  "inputs": { "fields": "short_description=Configured only" },
  "input_scripts": { "fields.short_description": "return 'Configured only';" }
}
```

The adapter writes the native per-column script map and marker, preserving the saved basic value and any JSON mirror. Callers do not submit raw script maps or an unbound `fd-scripted` marker.
Null disables the column script and restores its saved basic value while retaining inactive source; an empty string clears that source and its saved basic value.
References use the same lexical producer-identity and scope checks as ordinary input scripts; no code executes during authoring.
Static templates require a declared static table/reference dependency and independently read native columns. Dynamic template columns come only from retained reviewed provider evidence.
Use encoded `coalesce_fields=field1,field2` only for a template whose native metadata enables upsert. Names must be unique selected columns; password columns cannot be coalesce keys.
Whole-template pills are limited to the native standard-template subflow context with compatible reference tables, not ordinary flows or dynamic-template inputs.
Native source: template encoding (bundle:53733-53884), coalescing (53938-54004,54074-54104), column scripts (54156-54179,108621-108677), and marker updates (243966-243990).

#### Input transforms

Use `input_transforms` on supported insertion/update inputs with an ordinary typed source pill and exact independently readable transform/composition IDs:

```json
{
  "op": "update_inputs",
  "step": "<call-ui-id>",
  "input_transforms": {
    "message": {
      "source": "{{<producer-ui-id>.text}}",
      "transforms": [
        {
          "transform_sys_id": "<native-transform-sys-id>",
          "compositions": [
            {
              "composition_sys_id": "<native-composition-sys-id>",
              "parameters": { "<native-parameter-name>": "<typed-value>" }
            }
          ]
        }
      ]
    }
  }
}
```

This is a shape example: replace IDs, names, and typed values from the native transform definitions. Omit `compositions` or individual parameter overrides when the native metadata's current `parameter.value` should be retained.
Every declared composition remains applied in native order; an omitted override is not an invented `defaultValue` fallback.
Null for an input removes its saved transform and restores its ordinary source binding, only if that source remains compatible with the input.
The adapter reads `/api/now/transformdefinitions`; it never executes a transform. Type contracts bind exact metadata IDs/labels to the local documented per-function semantics, not guessed generic `inputType` / `outputType` fields.
Exact native function labels bind to documented type contracts, including source-established punctuation/capitalization variants. Unknown/localized undocumented labels, End of Month's contradictory Integer/date output, unresolved dynamic Split separator result types, unresolved advanced Name-Values configuration, or complex literal parameters without a native `parameter.value` representation reject strictly.
Native numeric/`int` parameters retain widget text and declared bounds; Name-Values parameters accept native JSON text and compatible per-cell pills. Custom String/Date formats require the native Custom Format parameter when that option is selected. These checks do not evaluate transforms or prove their runtime results.

`set_variables` uses the same mode fields keyed by actual declared flow-variable names and preserves native `inputs` / `flowVariables` mirrors. Null/empty script switching and transform conflict rules are unchanged; a native default enters only for an omitted value.
Assign Subflow Outputs does not expose script/transform mode fields: its native UI event path remains the dedicated output-value serializer.

#### Declared connection aliases

`connection_overrides` maps a declared source-alias sys_id to one of its declared child-alias sys_ids, or null to restore the source default.
Use it on `insert_action`, `insert_subflow`, or `update_inputs` for per-call selections; use `{"op":"set_connection_overrides","connection_overrides":{...}}` for flow-global selections.
The global operation is constrained to aliases used by readable native call definitions, not arbitrary credential or alias records.
No credentials/secrets are read, no connection/alias is created or reconfigured, and no integration is invoked.
Native precedence selects the **whole** per-call mapping list when it has any rows, including reset tombstones; otherwise it uses the flow-global list. It is not a per-key merge.
Saved clears retain the server mapping ID with empty source/target tombstones; clearing an existing unsaved mapping removes it. Shared mappings remain while another call uses them.
The first explicit per-call null/default with no local mapping creates the native empty draft marker, which suppresses inherited root context; an absent root mapping cleared to default is a no-op.
Alias/child relationships, metadata revisions, effective context, and unknown saved fields bind preview/apply and strict readback.
Connection-specific schema providers must use the complete inspected/reported effective context in their separate `resolve_schema` request, including any native reset artifact; never guess a marker or provider default.

### Reviewed dynamic schema resolution

`GET action/action_types/{id}` supplies provider metadata, not the effective dynamic input schema.
Studio resolves it by starting the metadata-declared provider action through `/api/now/action_execution/start` and reading `output.data`.
This is **action execution with potentially arbitrary side effects**, even when its intended result is authoring metadata.
It is not execution of the authored flow. Create/edit previews and saves never start it implicitly.
Review and authorize the provider operation separately from the authoring save.

The public sequence is:

1. Resolve a readable, entitled published target action snapshot and its declared dynamic `input_name`.
   Call `flow_preview(action="resolve_schema", action_type_sys_id=..., input_name=..., inputs={...})`.
   `inputs` contains the native sibling/dependency values used to resolve that schema; `expected_scope` optionally binds the provider application.
   Optional `connection_overrides` maps a declared source connection-alias sys_id to a declared child-alias sys_id or null; its effective context must match the later authoring component/flow context.
   Only this metadata-resolution operation also accepts the exact native reset marker `{ "": "" }` when preserving a persisted provider context. Other empty keys/values reject; the marker grants no alias access.
   Use the complete inspected native context, which can include aliases used by other calls; extra root/child relationships and revisions are independently bound. Do not guess the reset marker or silently substitute provider defaults.
   This preview performs reads only. Review the exact metadata-derived provider, dependencies, limits, and potential effects.
2. After authorization, call `mutation_apply` once with that preview's `preview_token`.
   Apply executes only the selected metadata-derived schema provider once and returns `action="resolve_schema"`, `provider_executed=true`, `schema_token`, and `expires_in_seconds`.
3. Start a **new** `create` or `edit` preview with top-level `schema_tokens=[...]` and the intended `edits`.
   Supply the same target action and dependency values. Use normal native `inputs`, not a caller-created schema or evidence object.
   Review the retained `resolved_schemas` alongside the complete authoring plan.
4. Apply the new authoring preview token once after authorization. It checks provider, dependency, and retained-schema bindings without rerunning the provider.

For example, if the published action declares dynamic input `options` depending on sibling `table`:

```python
metadata_preview = flow_preview(
    action="resolve_schema",
    action_type_sys_id="<published-target-action-snapshot-id>",
    input_name="options",
    inputs={"table": "incident"},
)
# Review and authorize the provider's potential effects before this call.
metadata = mutation_apply(preview_token=metadata_preview["data"]["preview_token"])
authoring_preview = flow_preview(
    action="edit",
    sys_id="<editable-flow-id>",
    schema_tokens=[metadata["data"]["schema_token"]],
    edits=[
        {
            "op": "insert_action",
            "action_type_sys_id": "<published-target-action-snapshot-id>",
            "inputs": {"table": "incident", "options": {"count": 0, "enabled": False}},
        }
    ],
)
```

This illustrative shape requires the action's resolved schema to declare those exact fields and types.
Creation uses the same top-level `schema_tokens` with `action="create"`, explicit `name`/`scope`, and its ordered edits.
Existing action instances use `update_inputs` in the new edit preview. No `schema_adoption` edit or public raw `schema_evidence` is accepted.
`dynamic_inputs` accepts a native object of resolved field values; `dynamic_choice` accepts a resolved choice name;
`dynamic_template` accepts the native encoded selections, such as `count=0^enabled=false`, against the retained provider columns.
Output-side `dynamic_object` metadata is not an input-mode resolution request; no `output_name` argument is offered.
This adapter bounds retained provider schemas to 1-40 uniquely named scalar fields for `dynamic_inputs` / `dynamic_template`, or 1-5000 labeled named rows for `dynamic_choice`; these are adapter bounds, not invented native UI limits.
The native dynamic-input formatter persists scalar definition slots, not nested child schemas; nonempty provider `children` or unresolved dependent providers reject rather than being invented or flattened.

Schema tokens are opaque server-held evidence, not mutation tokens or approval. They expire after 300 seconds and do not survive restart.
Only create/edit accepts them, with at most 200 distinct tokens. An accepted create/edit command atomically consumes all supplied tokens before policy, authorization, and planning checks, including when that command fails.
Malformed public arguments rejected before command acceptance leave them unconsumed. Tokens cannot be shared across connection or authorization generations.
An authoring preview's mutation token remains separate and single-use.
Applying a schema token directly returns `PREVIEW_TOKEN_KIND`; supplying the wrong token kind for authoring returns `SCHEMA_TOKEN_INVALID`.
Missing or expired evidence returns `PREVIEW_EXPIRED`; changed connection or authorization returns `PREVIEW_CONNECTION_CHANGED` or `SCHEMA_AUTHORIZATION_CHANGED`.

Provider snapshot/current-master metadata, parent revisions, dependencies, table/record identities, and output schemas are bound before and after execution and checked again for authoring.
The start endpoint selects by scoped name, so observed metadata binding does not atomically prove the exact executed snapshot:
`observed_binding_verified=true` and `exact_execution_version_verified=false` remain distinct.
Retained schema evidence is the observed output snapshot, not a universal remote-freshness guarantee.
Unreadable provider metadata, unsupported output grammar, unresolved nested dependencies, scripted reference qualifiers, or drift fail strictly; fields and types are never guessed from sample values.

If the provider executes but its evidence cannot be retained, `SCHEMA_EVIDENCE_NOT_RETAINED` reports `provider_executed=true` and `schema_retained=false`.
This does not roll back the provider. Do not retry automatically or claim no effect; reconcile the provider's possible effects first.
The same no-replay rule applies after an uncertain provider outcome. A schema token never authorizes authored-flow execution.
Local source: provider dispatch (bundle:234108-234131), dependency/schema collection (234223-234400), choice/template hydration (207523-207574), and scoped-name caching (158834-158839).

### Paired Try/Catch moves

`move_step` on a Try moves its adjacent linked Catch and both complete subtrees together.
An independent Catch move is rejected. The pair retains persisted identities, `connectedTo`, values, and opaque metadata.
Placement inside either subtree, self-relative anchors, broken pairs, invalid scope, script renumbering, or dangling pills are rejected.
Existing stage and handler guards apply across parent regions.
Local source: `gatherLogicBlock`, `gatherCatch`, and `gatherCatchBlock` (154568-154706).
Native call construction: `buildNewActionInstance` and `buildNewSubflowInstance` (156128-156263).
These operations are source-derived and offline-tested, not live-verified.

### Error handlers

| `op`                   | Fields            | Effect                                                                           |
| ---------------------- | ----------------- | -------------------------------------------------------------------------------- |
| `set_error_handler`    | `enabled` boolean | Creates/enables a handler or disables it with native content-preservation rules. |
| `remove_error_handler` | None              | Removes an empty handler and restores main root ownership.                       |

`set_error_handler` accepts `enabled=true` or `enabled=false` for a supported flow or subflow draft.
Enabling a draft without a handler creates native `TOP_LEVEL_TRY` and `TOP_LEVEL_CATCH` instances from the current readable catalog, or uses the source-native root `TRY` fallback when the top-level Try definition is absent.
The hidden Try has order zero. Existing root steps become its children without changing their identities or values.
The Catch starts the separate handler region. The saved handler boundary index follows its array position, not its display label.
Preview includes `error_handler_before` and `error_handler`, including region identities, enabled state, handler steps, and catalog output paths.

```json
[
  { "op": "set_error_handler", "enabled": true },
  { "op": "move_step", "step": "existing-log-ui-id", "parent": "error_handler" }
]
```

Replace the step ID with an existing step. `parent="main"` selects the hidden Try region.
Supported `insert_logic` operations can populate either region. Existing actions and saved subflow calls can move into the handler.
Placement without a parent defaults to the main region. Use stable IDs or branch aliases when orders change.
Wrapper identities cannot be moved, deleted, or configured through generic step operations.

Disabling a populated handler changes the Catch's native `enabled` input and marks its contents read-only.
The steps, parameters, opaque settings, and identities remain present. Enable the handler before editing those contents.
Disabling an empty handler removes the wrappers and restores normal root ownership, matching Studio.
`remove_error_handler` also requires an empty handler. Delete or move its contents explicitly first.
Saved wrapper removal uses flow-logic ID tombstones. Stage indexes follow the retained step identities.
Neither operation activates, publishes, compiles, or executes the flow.

The handler supports at most ten actions, logic steps, or subflow calls. Paired logic counts as separate instances.
Native top-level wrappers support bounded nested For Each, If, ordinary paired Try/Catch, and Parallel layouts when native order projection resolves.
Parallel requires explicit nonempty branches. Unsupported closing-order cases remain unavailable as described below.
The existing edit, move, disable/enable, and empty-removal rules apply to those layouts.
Legacy/Rome root `TRY` wrappers are supported only with a complete native root-pair association: the first root Try has order zero, the unique other root is `TOP_LEVEL_CATCH`, its definition links to the exact Try definition, and the current readable catalog matches both saved schemas/identities.
The saved raw `TRY` type, IDs, ancestor metadata, values, stages, and pills are preserved; only its internal top-level role is projected.
Ordinary `TRY` plus `CATCH`, or order zero alone, never establishes this legacy wrapper profile.
Table inspection/contract recognition additionally requires the complete untruncated combined dataset and readable Catch-definition association.
Use the current instance catalog. The adapter does not create guessed definition IDs or enable a missing platform feature.

#### Handler data pills

Preview lists the Catch definition's actual variable paths and types under `error_handler.outputs`.
Use `{{error_handler.<catalog leaf path>}}` in an input during the same ordered preview.
The planner replaces the alias with the Catch UI identity before serialization.
The adapter does not assume a fixed Error Status variable name or synthesize its fields.
Native Catch definitions with `__status__` plus `enabled` expose Error Status through their input schema, matching Studio.
The runtime value is opaque and preserved during toggles. Only its declared leaf types are used for pill validation.
The test catalog uses deliberately synthetic variable names to check this boundary.

Error Status leaf pills are visible only inside their handler. Main-step outputs cannot cross into the handler.
Trigger inputs and declared flow/subflow values retain their existing rules.
Whole handler Error Status Object/Array pills remain unavailable when only its catalog leaf schema is established; this does not exclude declared utility/trigger whole-schema producers.
Surviving consumers, defaults, transforms, and script dependencies block invalid wrapper removal or scope changes. Supported script order references are rebound through stable producer identities.

Local source: `errorHandlingToggledSaga` (477258-477299), `updateInputsEnabledFlag` (163491-163498),
`buildComponentInstancesWithErrorHandling` (163513-163524), `updateActionInstanceIndices` (156510-156513),
and `setErrorHandlingInstancesReadOnly` (204244-204299).
The normal save path restores wrapper parents before splitting the three step lists (99076-99079,99193-99197).
Native sandwich/Parallel layout uses typed order projections that distinguish display labels, array positions, and closing `orderBottom` values.
Studio arrow labels do not define persisted array positions. The save restores direct wrapper parents before splitting action, logic, and subflow lists.
Existing stages reindex by actual instance positions. Native stage exclusions cover For Each and Parallel ancestry, not a blanket sandwich ban; Do Until and Try follow the ordinary placement and first-grouping-child rules.
Source-carried nested `orderBottom` values that Studio does not reconstruct remain strict metadata, not fields to ignore.
A populated nested sandwich rejects when Studio leaves its closing order unresolved and the saved row lacks an integer `orderBottom`.
The adapter does not invent or repair that closing order. Projection validation also rejects malformed arrow labels outside handlers.
Identities, opaque settings, disabled-handler read-only state, pill visibility, tombstones, and independent echo/GET comparison remain unchanged.
Local source: projection (156494-156713), arrow-label sorting (92779), save splitting (130432-130460), and wrapper-parent restoration (163513-163524).
This source-derived support was exercised by targeted offline MCP smoke scenarios. It does not establish live handler compatibility.
Local official references: `flow-error-handler.md` and `add-error-handler-flow.md`.
All new behavior has source-derived offline tests. No live handler save or execution is verified.

### Conditional error stages

| `op`           | Error-stage fields                           | Effect                                                      |
| -------------- | -------------------------------------------- | ----------------------------------------------------------- |
| `create_stage` | `type="error"`, `step`, optional `ref`       | Creates and attaches the Error state with derived ancestry. |
| `update_stage` | `stage`, `duration`, `always_show`, `states` | Changes supported settings without replacing its identity.  |
| `detach_stage` | `stage`, `step`                              | Removes the Error state and its attachment.                 |
| `delete_stage` | `stage`                                      | Removes the Error state and its attachment.                 |

An error stage reports the current stage's Error state. It does not throw an exception, stop execution, or create an error handler.
Use the existing stage operations with `create_stage(type="error")`:

```json
[
  { "op": "create_stage", "label": "Work", "value": "work", "ref": "work" },
  { "op": "attach_stage", "stage": "work", "step": "existing-first-step" },
  {
    "op": "insert_logic",
    "logic": "If",
    "inputs": { "condition": "true" },
    "after": "existing-first-step",
    "ref": "failed"
  },
  { "op": "move_step", "step": "existing-second-step", "parent": "failed" },
  {
    "op": "create_stage",
    "type": "error",
    "step": "existing-second-step",
    "ref": "failure_state"
  }
]
```

Replace both step IDs. The example supplies a literal condition for the serialization example, not a production failure detector.
An error stage attaches during creation. It requires an enclosing If/Else If/Else branch and an eligible preceding standard-stage attachment.
The planner derives `ancestorStageId`, `ancestorArrPos`, and `ancestralIfElseLogic` from the current tree.
It uses Studio's native Error label/value and null order. It does not accept caller-supplied ancestor indexes or arbitrary error labels.
Stages remain excluded inside For Each and Parallel, but Do Until/Try are not categorically excluded. Native attachment/first-child rules still apply. Subflow stage authoring remains unavailable in this adapter.

`update_stage` accepts duration, state labels, and `always_show` for an error stage.
Its identity and opaque metadata remain unchanged. Its native label, value, order, and source cannot be renamed.
`delete_stage` removes the keyed error state, including its attachment.
`detach_stage` also removes the error state, matching Studio's `deleteFlowStage` dispatch.
There is no detached reusable error-stage state. To attach elsewhere, remove the old error stage and create another explicitly.
`attach_stage` remains the standard-stage operation.

Existing guards block changes or removal of a standard stage with dependent error states.
Stage indexes and error ancestor positions follow stable step identities during supported reordering and wrapper insertion/removal.
A change in source stage, source attachment, or conditional ancestor is rejected. Remove and recreate the error state explicitly.
Duplicate error states for one conditional source and implicit replacement of another stage attachment are rejected.
New client stage keys remain stable across preview/apply. Only new stages may receive database IDs, and echo/GET must agree.

Local source: `createErrorStage` and `extractAncestorInfoForErrorStage` (184141-184165),
`getPreviousAncestorStageInfo` (184179-184197), `obtainStageDataForInstance` (184316-184355),
`deleteFlowStage`/`deleteErrorStage` (204353-204409), and sibling cleanup (204446-204457).
Local official references: `add-stages.md` and `flow-designer-stages.md`.
All new behavior has source-derived offline tests. No live error-stage save or runtime state display is verified.

### Variables and subflow interfaces

`create_field`, `update_field`, and `delete_field` accept `collection="flowVariables"`, `"inputs"`, or `"outputs"`.
Flow-variable scalar types are `string`, `integer`, `boolean`, `glide_date_time`, `decimal`, `float`, `reference`, and `json`. Subflow interfaces additionally support `choice`, `number`, and the declared Object/Array forms below.
Variables and interfaces work in supported editable flow/subflow owners. Interface CRUD requires a triggerless subflow, including a published working draft only when it meets the existing active-draft/runtime-preservation requirements; immutable snapshots remain rejected.
Flow-owned and trigger-owned interfaces are not replaced. Existing flow interface label/hint edits remain supported.
Complete creation supports inactive triggerless subflows with declarations in the same ordered plan.
The older append-only actions retain their narrower linear-flow contract.

Additional root subflow input/output widgets are admitted only when the instance's native field-type catalog offers that exact type/label and the existing value serializer supports it.
The adapter reads that bounded metadata during the typed edit; there is no new public field-type discovery action, caller-provided type schema, or plugin-provider fallback.

| Root widget group         | Conditional native type strings                                                                                            |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Text/content              | `string_full_utf8`, `text`, `longtext`, `multi_small`, `html`, `translated_html`, `email`, `url`                           |
| Additional numeric        | `double`, `longint`, `long`                                                                                                |
| Records/tables            | `document_id`, `records`, `glide_list`, `table_name`, `field_list`                                                         |
| Date/time                 | `glide_duration`, `duration`, `glide_time`, `datetime`, `date`, `glide_date`, `due_date`, `calendar_date_time`             |
| Native structured widgets | `slushbucket`, `approval_rules`, `schedule_date_time`, `template_value`, `conditions`, `name_values`, `simple_name_values` |

This does not broaden the scalar-only flow-variable picker or the narrower Object child picker. Missing or mismatched native type metadata rejects; an accepted declaration does not execute its widget or evaluate a default script.

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
Creation and eligible renames use lowercase identifiers of at most 80 characters. Labels must be unique within each collection. Saved native name-editability flags remain authoritative.
`reference` requires a readable exact table match. `maxsize` accepts 1-65000. `mandatory` is a JSON boolean for inputs only.
`default_value` accepts static native scalar interface values, not data pills; JSON defaults are valid JSON text. Array.Choice additionally accepts a scalar item-choice default through this field, not an array literal default.
Initialize variables through `set_variables`, not declaration defaults. Runtime values remain outside local verification.
Boolean declaration metadata uses Studio's false default. This does not prove runtime initialization.
Subflow interfaces also support nested Object/Array declarations as described below.
Declaration changes use explicit native settings and consumer guards; an action input's schema token does not create an arbitrary dynamic interface type.
Unrelated complex declarations and opaque metadata are preserved.

Type/reference changes require no surviving consumers and no stored value. Defaults must remain valid for the resulting type.

#### Native declaration settings

- `new_name` is a new **local** identifier on `update_field`, for example `{"op":"update_field","collection":"inputs","name":"old.path","new_name":"new_name"}`.
  It applies only to declarations newly created in this request or native editable interface rows marked `nameToBeEdited`; saved flow-variable names remain read-only even if an unrelated flag suggests otherwise.
  Consumers are not rewritten or aliased. Existing uses must be explicitly removed/rebound, and duplicate or invalid names reject.
- `reset_structure=true` is an explicit authorization only alongside an actual type change. Nonempty child structure requires it unless those children were explicitly deleted first.
  Surviving consumers, stored values, read-only descendants, or owned child defaults block destructive reset. False, zero, and empty-string child defaults are still defaults, not permission to discard them.
  Clear an eligible scalar default with an earlier explicit `default_value=null`, or explicitly remove child declarations/defaults before changing structure. Root identity and unrelated metadata remain strict.
- `max_rows` applies to Array declarations and accepts a positive integer or null to clear the configured maximum. Array item types remain fixed by the parent; names/settings change only where the native row permits them.
- `reference_qualifier` applies to a Reference declaration and accepts bounded static encoded conditions, not JavaScript/dynamic qualifiers. Changing it with a nonempty default requires explicit `default_value`.
- `dependent_on` is a bare sibling root-input name, or `""` to clear it, for native `document_id`, `conditions`, `template_value`, or `field_list` **input** declarations.
  It sets the native dependency flag. These types may also remain unconfigured with neither static reference nor dependency while the definition is authored; no execution-readiness requirement is inferred.
  Clear a static `reference` explicitly when switching to a dependent source. A nonempty default requires a static reference, so unresolved/dependent configuration cannot silently retain one.

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

Supported editable triggerless subflows, including eligible published working drafts, support `object`, `array.string`, `array.integer`, `array.boolean`, `array.object`, `array.reference`, `array.choice`, `array.datetime`, and `array.array` declarations.
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
The item type follows the array type and cannot change independently. Edit its eligible name, label, hint, or maximum length by item path; native editability and consumer guards still apply.
Add Object item fields under that path. Nested objects and arrays can occur within Object item fields.
Primitive array items cannot contain declarations. Removing an array removes its item schema.
Removing an object removes its declared descendants. Explicit child removal is also supported.
No consumer is removed or cleared. Used children, ancestor pills, output assignments, source links, transforms, and script dependencies block destructive changes.
Scalar leaf types can change only without consumers or stored values. Complex type changes use explicit `reset_structure` and its protected-default/descendant guards, or explicit unused deletion/creation.

New input leaves can supply typed pills such as `{{subflow.payload.count}}` in later edits of the same preview.
Collection pills still require supported loop or complex-value adapters. This increment does not allow arbitrary array indexing.
Outputs with nested schemas accept native Object/Array assignment values as described below.
The inspected native Object settings expose structure, and Array settings expose name/hint/maximum rows, not whole Object/Array defaults (bundle:332287+,332922-332940).
Configure defaults on scalar child declarations; Array.Choice's packed synthetic-item default remains scalar. No whole-parent literal default is fabricated. Whole Object/Array pills use the schema checks below.

Studio restricts flow variables to scalar types (bundle:11156,344267-344269).
Nested flow-variable creation therefore remains unavailable. This restriction does not affect existing scalar variable authoring.
The native Object child picker offers String, Boolean, Choice, Object, Integer, Date/Time, Number, and the declared Array families (bundle:1222); root-only scalar choices do not automatically become direct Object children. Reference/JSON metadata already present in an unrelated schema remains preserved.
Choice, date/time, and Array.Array declarations use the source-native forms below; root type support does not broaden the Object child picker.

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

#### Choice, date/time, and nested arrays

For a `choice` or `array.choice` interface declaration, supply explicit static choices with unique string values:

```json
{
  "op": "create_field",
  "collection": "inputs",
  "name": "priorities",
  "label": "Priorities",
  "type": "array.choice",
  "choices": [
    { "label": "Normal", "value": "normal" },
    { "label": "Urgent", "value": "urgent" }
  ]
}
```

The parent owns Array.Choice choices, choice options, and scalar item default (`default_value`); its synthetic item preserves the native packed schema.
Static choice changes retain identity and reject incompatible surviving consumers or stored values. Static `choices` entries use `label`, string `value`, and optional nonnegative integer `order`.
For native table-backed Choice or Array.Choice, use mutually exclusive `choice_source={"table":"<native-table>","field":"<native-choice-column>"}` instead of `choices`.
`choice_option="1"` includes None; `choice_option="3"` excludes it. A no-None dropdown requires a nonempty valid default.
For example, after discovering a column offered by the native choice picker:

```json
{
  "op": "update_field",
  "collection": "inputs",
  "name": "priority",
  "choice_source": { "table": "<native-table>", "field": "<native-choice-column>" },
  "choice_option": "1",
  "default_value": ""
}
```

Replace the table/column placeholders from readable native metadata. Reusing the same source preserves its default; switching static/table mode or table/column requires explicit `default_value`, including `""` to clear only when None is allowed.
The native table/schema choice picker owns inherited/language choice selection. The save retains table/column source metadata, empty native `choices`, and exact picker `defaultChoices`, not a locally flattened union or ChoiceRegistry fallback.
Array.Choice keeps this source metadata on its authoritative parent and mirrors/packs its synthetic item; parent/item edits preserve that relationship.
The adapter binds table ancestry, dictionary, choice identities/revisions, and complete native column metadata with ten-ancestor and 5000-choice/column bounds.
Concrete native dependent/qualified/redirected choices remain supported: preserve the picker list exactly, including dependency metadata and repeated values in different native contexts. Resolved redirected origin identities/revisions are bound rather than flattened into local choice unions.
Only actual unresolved dynamic/data-structure providers, nonchoice columns, malformed/unbindable metadata, exhausted bounds, or drift reject. Qualifier metadata is retained, not executed by a local fallback or used to invent choices.
Source: table/schema picker (bundle:226655-226768), Choice column filter (300687-300699), and persisted source projection (331779-331830).
Provider-resolved action choice inputs use their separate evidence route; they are not declaration choice-source metadata.
Use `glide_date_time` for a scalar interface date/time and `array.datetime` for Array.Date/Time.
Literal date/time values use native `YYYY-MM-DD HH:mm:ss` text. Typed leaves, whole pills, bounds, and reference/schema checks retain their existing strict behavior.

`array.array` registers Studio's fixed generic Array item automatically, with stable item identity. There is no caller-supplied `item_type` or independent item type reassignment.
Its authoring values can be an empty outer array, one exact compatible Array.Array pill, or an array of exact generic Array pills.
Literal nested arrays are rejected because Studio's native `constructInstanceValuesForArrays` clears that literal form; accepting them would misrepresent what survives saving.
This is a native save constraint, not an unavailable array type-name adapter. Arbitrary array indexing remains unavailable.
Source: array registration/packing (bundle:21968-21998,23273-23319), generic Array read form (23426-23431), and literal serialization (23510-23535).

#### Reference arrays

`array.reference` supports subflow interface CRUD at the root, inside an Object, or inside Array.Object fields.
Set the parent `reference` to an exact static table name, for example `"reference":"sys_user"`.
The table must pass existing `sys_db_object` existence, readability, and local policy checks.
The synthetic reference item inherits that parent table. Its packed child metadata does not contain a separate table.
Item identity, type, and table cannot change independently. The parent table can change only without consumers or stored values.
Complex flow variables remain excluded by the native scalar-only picker. No whole-array Reference default is fabricated; eligible scalar declaration defaults retain their own native settings.

Literal values use native arrays of lowercase 32-hex sys_id strings or compatible scalar reference leaf pills.
`[]` is valid. Blank or null elements reject. Native save serialization uses a blank parent and one reference child per element.
Literal validation checks identifier syntax and declared table metadata only.
It does not establish record existence, table membership, record revision, runtime access, or per-record ACL access.
This differs from root record inputs in lifecycle tests, which retain their explicit readable-record/revision binding.

`update_inputs`, structured call inputs, output assignments, and whole-array pills use the declared reference-array schema.
Whole pills retain existing cardinality, table-hierarchy, producer-visibility, and independently read producer-schema checks.
The complete static item schema is required even for `[]`. A raw unresolved `dynamic`, dependent schema, or `data_structure` descriptor does not establish a usable reference-array schema.
Array.Reference lifecycle test inputs remain unavailable; that separate execution contract does not limit create/edit authoring. Direct nested scalar Reference/JSON creation remains excluded by the native Object child picker.
Targeted offline MCP smoke exercised this source-derived reference-array adapter and independent readback drift rejection.
No live reference-array save/runtime compatibility is established.
Local source: type/item construction (bundle:1196-1214,21968-22005), parent table propagation (23297-23300),
schema/value packing and reconstruction (23347-23424,23445-23587), and reference-array input controls (297380-297416).

### Output assignments

`assign_outputs` assigns declared outputs in a supported editable triggerless subflow, including complete creation and eligible published working drafts with strict runtime preservation.
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

Supported targets are declared scalar types, including Choice and Date/Time interfaces, `object`, and the source-native Array declarations described above.
Values use the existing native type checks, reference-table compatibility, and typed pill resolver.
False, zero, and empty string remain distinct values. JSON values require valid JSON text.
Output-to-output pills and inline assignment scripts remain unavailable. Assignments may occur inside loops or Parallel branches when their values satisfy the normal producer visibility, reference-table, branch-isolation, and output-schema checks.
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
Assign declared Array.String, Array.Integer, Array.Boolean, Array.Object, Array.Reference, Array.Choice, and Array.Date/Time with native arrays. Array.Array retains its narrower native empty/typed-pill forms.
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
Complex schema defaults and inline output-assignment scripts remain outside this serializer. Authoring assignments never remotely execute the flow.

Local source: Object/Array editing (293995-294036,244898-245012), Array.Object item construction (93333-93353),
recursive nested updates (16252-16301,207263-207300), and complete-save serialization (23462-23535).
Array read reconstruction is documented at 23564-23710. The adapter currently consumes the strict saved wire form.
These forms are source-derived and fixture-tested, not live save or execution verified.

#### Whole Object and Array pills

`update_inputs` and `assign_outputs` accept one exact whole Object/Array pill such as `"{{producer-ui.payload}}"`.
Supported complex kinds are `object`, `array.string`, `array.integer`, `array.boolean`, `array.object`, `array.reference`, `array.choice`, `array.datetime`, and `array.array` with the native item-schema restrictions above.
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
The adapter does not infer fields from JSON, unresolved dynamic descriptors, missing `children`, defaults, or sample values. Provider evidence resolves only its declared dynamic input's native effective metadata, not arbitrary target declarations.
Missing/extra Object fields, ambiguous names, wrong item types, unknown schemas, or unsupported array kinds reject the preview.
No source-field superset projection or missing-field default filling is implied by a whole binding.
These are structural checks. They do not prove runtime values satisfy mandatory/maximum-length constraints or contain non-null data.

Action and saved subflow-call producers require an independently readable, entitled published snapshot definition.
The selected current output schema must agree with the saved producer schema before it can supply a whole pill.
The current definition and complete source schema enter the preview fingerprint and are checked again at apply.
Producer visibility, order, branch isolation, reference tables, and dangling-reference checks still apply after all edits.
Declared whole trigger producers include supported SLA catalog objects and flow-owned REST interface schemas when native schemas/mirrors and current catalog identities agree.
Dynamic Flow and Get Flow Outputs can supply whole template outputs from independently read published interfaces, subject to wait visibility and compatible context/template metadata.
This does not establish all ordinary flow-logic whole schemas or independently remote-owned REST schemas. Arbitrary array indexing is not supported.

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
Stages cannot attach inside For Each or Parallel, before Else/Else If, or at a grouping block's first child. Do Until and Try are not blanket exclusions; their eligible body steps retain the ordinary attachment/reordering rules.
Existing attached steps cannot move across parent regions in the same request. Error-stage ancestry blocks structural edits.
Conditional error-stage creation and settings use the adapter described above.
Dependent error stages block parent-stage updates, detachment, and removal.
Legacy metadata-only stage records retain the existing label/value adapter.

Source contracts: `createStage` (bundle:184105-184139), default states (1461-1463),
`addNewFlowStage`/`detachStandardStage`/`deleteStandardStage` (204322-204398),
stage creation and reorder controls (458244-458271),
stage display ordering (184277-184295), and assigned IDs (204158-204167,476178-476187).
The complete flow save retains keyed `stages` (99104,350891). This adapter does not call the separate stage-modal endpoint.
The local official reference is `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/add-stages.md`.
These operations are source-grounded and offline-regression verified, not live save or execution verified.

Source constraints remain in force: flow variables are scalar, Object child pickers exclude direct Reference/JSON declarations, and unresolved native schemas cannot be inferred from names or sample values.
Saved-trigger calendars/timezones and dynamic input modes depend on their declared metadata routes above, rather than a blanket unavailable-family list.
Existing opaque settings remain unchanged. Stage attachments follow stable step identities during reordering.
Detach a stage before deleting its step. Moving a previously attached step across parent regions requires a separate edit request.
Structural edits rebind supported named inline-script references; ambiguous/computed reference syntax fails rather than guessing a rewrite.

The Studio adapter is grounded in read-only client source for `designer-bundle.min.js` version `27.0.13`:
`constructInstanceValuesForArrays`, `addToDeletedInstances`, `removeTriggerFromFlow`,
`prepArgsForTryCatchFlowLogicInstance`, and `updateStageIndexes`.
Scope rules follow the local official If, For Each, Parallel, and Try references under
`docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/`.
These sources establish local serializer behavior. They do not certify every OAuth save profile or runtime outcome.
Bounded live compatibility evidence applies only to the [documented native Log fixtures](flow-live-verification.md#native-working-drafts-and-safe-lifecycle-completion-2026-10-23).
Called subflows can contain complex logic. The adapter reads their published input contract without editing them.
It checks publication, read access, entitlement, scope access, and mandatory inputs before staging a call.
Complete creation uses two nontransactional upstream writes within one reviewed preview/apply operation.
A failure can leave an empty or populated inactive definition. Read reconciliation, not recreation, determines the next safe action.

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
The comparison error code, first-difference reporting, and strict equality checks remain unchanged.
Malformed semantics retain their safe parser invariant. Equality checks and unknown-setting preservation remain strict.

Complete Studio flow and call definitions have a separate 4 MiB UTF-8 JSON limit.
These definitions include embedded schemas and metadata that can exceed the 256 KiB limit for caller input.
The 32-level nesting limit and JSON value checks still apply. Caller inputs and edits retain their existing limits.
Payload rejection reports the specific limit or invalid JSON condition without returning source values.
Deactivation preflight rejection returns `UNAVAILABLE` and confirms that no lifecycle request was sent.
Failures after lifecycle dispatch remain `UNKNOWN_OUTCOME` and must not trigger automatic replay.

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
Draft saves never activate, publish, or execute. The separate explicit lifecycle actions below can cause those effects.
No automatic retry is exposed.

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

## Validation, status, and target binding

`flow(action="validate", sys_id=...)` reads the saved draft and applies local semantic, tree, family,
reference, mandatory-value, and declared-schema checks. It does not compile, save, publish, activate, or execute.
`valid=true` applies only to implemented local semantic checks, not JavaScript behavior, runtime side effects, or business correctness.
Unsupported native representations, unresolved script/schema semantics, partial reads, dependency drift, and revision races return `valid=null`, `complete=false`, and safe diagnostic paths.
Ordinary supported validation failures return `valid=false`. The result includes opaque flow and dependency revisions.
Local saved validation uses the same native structural, Decision, utility, application-trigger, and eligible saved-picker metadata contracts on a copy; it never repairs the target or runs a referenced workflow, flow, provider, or trigger.
Saved picker validation binds the exact selected picker/template revisions and references rather than substituting a classic trigger by type name.
Supported authored input/column scripts are checked through the shared static mode validator: native script maps/mirrors, flags, basic fallbacks, named `fd_data` dependencies, scope, schemas, and table policy.
Their presence alone does not make validation incomplete. Unresolved/computed/stale dependencies remain rejected or incomplete; no JavaScript evaluation, syntax certification, or runtime return-value prediction is performed.

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
Status does not read or return Customer Update capture metadata or selected session context.

Execution status separates context ownership, compiled snapshot ownership, terminal state, and authored-version/content evidence.
`snapshot_sys_id` comes only from the exact context's canonical `snapshot` reference to `sys_hub_snapshot`.
This compiled snapshot is not an authoring `sys_hub_flow_snapshot`.
`snapshot_owner_verified=true` requires an exact read whose `sys_id` and `parent` match the reference and target flow.
The read projects only `sys_id,parent,sys_mod_count,sys_updated_on`. It does not read compiled payloads or execution inputs/outputs.
`snapshot_content_verified=false` and `version_verified=false` remain explicit. Ownership does not prove an authored revision or identical executable content.

Snapshot problems return `complete=false` with a diagnostic at `execution.snapshot`:

- `MISSING_EXECUTION_SNAPSHOT`: the field is absent or null.
- `PENDING_EXECUTION_SNAPSHOT`: the reference is empty.
- `MALFORMED_EXECUTION_SNAPSHOT`: the reference or returned identity/parent is malformed.
- `UNREADABLE_EXECUTION_SNAPSHOT`: the exact compiled-snapshot read fails.
- `EXECUTION_SNAPSHOT_MISMATCH`: the returned canonical identity or parent does not match.

Missing snapshot evidence does not erase a known legacy context state or terminal result.
An available canonical reference remains visible for unreadable/mismatched snapshots. No failure authorizes execution replay.
A bounded read-only live check on an owned Log-subflow context verified context ownership, compiled-snapshot identity/parent, and a known terminal state.
The check used exact record reads without flow mutations or compiled payload access.
It does not establish authored-version/content equality, new execution behavior, or compatibility with other context families.

`expected_scope` checks the target application's `sys_id` or `global`, not the selected session application.
For creation, it must match the explicit `scope`. The application record must resolve independently of update-set access.
`expected_revision` checks an existing flow's `sys_mod_count`, available from `flow.status.flow.revision.mod_count`.
These arguments are independent and optional for inactive draft authoring, but both are required for the supported active/published-owner profile.
Creation does not accept an existing revision.
When either argument binds an existing target, preview and apply check its header identity, scope, and revision.
The immediate pre-write check rejects target drift. After saving, the target revision must advance exactly once.
The final header read must remain stable. Failed persisted-state verification still returns `UNKNOWN_OUTCOME`.
The existing 300-second single-use token, authorization generation, connection, production, policy, definition,
dependency, and independent semantic readback checks remain in force.

Migration: `expected_update_set` is removed, with no compatibility alias. Remove it from existing requests.
The native schema rejects that obsolete argument. Command state no longer stores it.
Flow operations never select, reopen, relabel, repair, query, or validate update sets or Customer Updates.
They never query the current session picker. ServiceNow performs its normal update-set capture.
Missing, ignored, changed, unreadable, ambiguous, or stale update-set/capture metadata cannot block a flow operation.
Success results contain no capture claim. Genuine write ambiguity or failed saved-state verification still forbids automatic replay.

## Explicit lifecycle effects and remaining contract gaps

`flow_preview` accepts explicit lifecycle actions. Preview performs reads only.
Apply requires the single-use token, exact target revision, and application scope from the preview.
It never switches session context. Production policy blocks all lifecycle effects.
Lifecycle commands require `expected_scope` and `expected_revision` even when draft authoring does not.
Activate/publish/test require complete local semantic and dependency validation.
Deactivate requires an active flow owner with complete structural evidence, including supported active working drafts.
Subflow publication can update the version used by parent flows. It is not an isolated metadata operation.

| Action       | Effect and supported contract                                                                                                                               |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `activate`   | Flow-only snapshot POST. Publishes a snapshot and enables triggers. Activation can start real work.                                                         |
| `publish`    | Subflow-only publication POST. Makes the published subflow callable. Flow publish-only is rejected because Studio's flow snapshot operation also activates. |
| `deactivate` | Active flow-only effectful GET. Stops new trigger starts, not existing executions. It does not unpublish subflows.                                          |
| `test`       | Executes the saved editable owner with real action side effects. Sends `flow`, `outputMap`, and `runOnThread=false`.                                        |

No independent remote compiler/validation, production-run, or flow publish-only action is exposed. Saving, testing, and activation are not aliases for those operations.

For example, `flow_preview(action="test", sys_id=..., inputs=..., expected_scope=..., expected_revision=...)`
previews a real execution request. It does not execute until its token is applied.
`inputs` supports static scalar subflow values, declared Object/Array.String/Integer/Boolean/Object values, or supported classic trigger outputs.
Supply every visible subflow root input and nested Object field explicitly, including optional fields. The adapter does not select defaults silently.
Hidden inputs must be omitted. Supplied hidden or read-only overrides reject. Native values must satisfy declared types, choices, maximum sizes, and mandatory constraints.
An optional array can be empty. A mandatory array cannot be empty.
Record-trigger tests still require explicit record sys_ids. The adapter reads their identity and revision and binds both to the preview.
Hidden test outputs cannot be overridden. Trigger-linked table names must match the configured table.

Complex subflow tests use a separate Test-modal serializer, not authoring binding rows or raw Object/Array values in `outputMap`.
It emits named `children` maps with blank structural parent values and repeats the declared item name for array elements.
An empty optional array uses the source-native empty-string representation. Caller inputs remain native objects and arrays.
Nested reference/document-ID widgets, Array.Reference, record lists, `glide_list`, dynamic/dependent schemas, and trigger arrays/complex values remain unsupported.
Nested reference-bearing schemas reject even when their arrays are empty. Root references retain table/access checks and record revision binding.
Targeted offline MCP smoke exercised the source-native complex test request, exact-context readback, and single-use replay rejection.
The scalar Log-only live matrix does not establish complex test compatibility.
Local source: test-input state (bundle:6130-6135), named-child serialization (193691-193770), Array.Object items (93333-93353), and empty arrays (244997-245001).

The test response's `data` is the execution context ID (`testFlowSuccess(sendTest.data)` in the local source).
Apply reads that exact context and checks context ownership and compiled-snapshot identity/parent separately.
It then verifies that the saved owner and header did not change. Test results retain `version_content_verified=false`.
The result's `complete` and safe `diagnostics` identify pending owner/state or incomplete compiled-snapshot evidence.
The native context can initially have `state=QUEUED` and an empty flow reference.
That accepted context returns `context_owner_verified=false`, `terminal=null`, and `execution_succeeded=null` until a later owned status read.
Queued, waiting, and in-progress results return `execution_succeeded=null`. A returned context does not establish completion.
Use `flow(action="status", sys_id=..., execution_sys_id=...)` for bounded read-only follow-up. There is no automatic execution retry or unbounded polling.
Unknown or unreadable post-dispatch results return `UNKNOWN_OUTCOME`. If a valid context ID was received, the error retains that ID for inspection.

Publication/deactivation require matching save echo, independent Studio GET, and header state.
Only the action's explicit lifecycle fields may change. Existing IDs, content, and opaque settings remain strict.
Publication also checks the independently read snapshot identity, owner, type, and master-snapshot flags.
Native publication binds the selected version's persisted content and independently readable release/engine contract.
Only the observed server-owned `attributes.timeFromCreateToActivate` duration may change during publication, with matching echo and GET.
The exact native unset-engine echo value `0` projects to independently verified owner/version engine `2`.
The native deactivation echo's false `userHasRolesAssignedToFlow` UI flag can project to an unchanged true owner GET.
Stored `runAs`, roles, all other content and unknown settings remain strict.
Lifecycle revisions must advance positively and remain stable. A draft save still requires exactly one revision increment.
Independent expected-to-published-content equality and general runtime behavior remain unverified. Responses retain `snapshot_content_verified=false`.
No lifecycle action reads update sets or Customer Updates.
Conservative rejection or `UNKNOWN_OUTCOME` does not authorize replay.

Source: `publishFlowToServer`/`deactivateFlowCall` (97561-97577), `saveFlowSaga` response handling (99271-99297),
`showMessage` flow activation versus subflow publication (31224-31247), `sendTestToServer` (448105-448118),
trigger test mapping (448249-448274), subflow test dispatch (448428-448441), and context response (448297-448301).
All lifecycle implementations use offline fixtures, including captured native shapes and real local stdio.
Live activation, deactivation, published-to-draft saves, scalar subflow test and explicit publication passed on isolated Log fixtures.
The exact test context reached `COMPLETE`. This does not certify business workflows, all variants, or independent production runs.

## Authoring capability and evidence boundaries

The primary contract is draft creation/editing through the [native authoring routes](#native-authoring-routes), with typed input modes, trigger configuration, declarations, assignments, stages, and handlers.
Generated companions, dedicated assignment operations, source-native read-only internals, and transient placeholders are classified separately from standalone authorable blocks.
The `flow(action="describe")` ledger reports authorability and evidence scope. Its lifecycle entries do not turn execution into an authoring requirement.
Independent remote compilation/validation, independent production run, and flow publish-only remain separate unsupported contracts; the tools never alias a save, test, or activation to them.

| Boundary                             | Meaning for a caller                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Installed metadata and permissions   | A family requires its installed readable definition, plugin/licensing, declared schema, exact reference identities/revisions, scope access, and permitted OAuth/local policy. Missing evidence returns a strict error; catalog presence is not a universal compatibility claim.                                                               |
| Native topology and protected fields | Non-nesting, connected branches, jump targets, terminal siblings, disabled-handler read-only state, ten-node handler bounds, closing-order projections, stage placement, and derived/read-only fields remain enforced. No generated identity, output table, or missing closing order is invented.                                             |
| Native complex forms                 | Choice, Date/Time, Reference, and Array.Array declarations use their source-defined item schemas. Literal nested Array.Array values do not survive the native serializer and are rejected; empty/typed-pill forms remain supported. Studio's scalar-only flow variables and Object-child Reference/JSON picker exclusions remain constraints. |
| Saved picker schemas                 | Calendar/timezone/Recurrence configuration follows the selected eligible saved definition and independent template row, including inherited read-only values and native version gates. No recurrence field names or timezone conversion are inferred from a label.                                                                            |
| Dynamic authoring metadata           | Providers run only through separately reviewed `resolve_schema` apply. Single-use server-held evidence binds a later create/edit preview; save/apply never secretly reruns a provider. Unsupported provider output grammar or unresolved dependencies reject rather than guess a schema.                                                      |
| Saved scripts and transforms         | Stored authoring configuration is not executed or behaviorally certified. Source-defined flags, supported reference syntax, transform metadata, and declared connection aliases remain required.                                                                                                                                              |
| Optional lifecycle tests             | Tests have a separate, narrower input serializer and can cause real effects. Their Array.Reference, nested reference/list, dynamic, and complex-trigger limitations are not create/edit authoring gaps.                                                                                                                                       |
| Declaration changes                  | Choice sources, defaults, names, and structure changes remain explicit and consumer-checked. Whole Object/Array parent defaults are not accepted; configure scalar child defaults or Array.Choice item defaults through their declared settings. No consumer or schema is silently discarded.                                                 |

Read-only source inspection establishes serializer and UI contracts. Offline fixtures establish local planner/validation/readback behavior, not live mutation compatibility.
Live compatibility evidence remains limited to the explicitly documented owned native Log fixtures and their release/engine profile.
It does not certify every authoring family, ServiceNow release, licensed feature, stage runtime display, or business workflow.
Working-draft runtime storage preservation is distinct from independent published-content equality or execution-to-authored-version correlation; the latter status claims remain unverified.

Local official lifecycle sources are `flow-test.md`, `flow-activate.md`, `publish-subflow.md`, and `flow-execution-details.md` under `docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/`.
The current-update-set behavior is described in `docs/ServiceNowDocs/markdown/application-development/system-update-sets/select-update-set-system-settings.md`; current Flow operations do not inspect or manage that context.
Historical guarded UI capture and the bounded native lifecycle matrix remain in [live verification](flow-live-verification.md). Those historical records are not rewritten as proof of newly added families.
No effect guard needs to be relaxed to author a draft, and a successful save does not authorize execution.

After a timeout or incomplete response, inspect the exact target before another preview. For create, resolve the reviewed name and scope to avoid a duplicate.
Known returned identities and phase-specific outcomes guide reconciliation; missing evidence never proves rollback.
Never automatically replay, recreate, or compensate for an uncertain save, provider execution, or optional lifecycle effect.
The OAuth session can have a different current update set from the browser session. The tool does not change it or guarantee update-set capture.
