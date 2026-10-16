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

| `op`            | Fields                                                    | Effect                                                                                                                                                                                                                                                                                                  |
| --------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `set_trigger`   | `type`, `inputs`, `replace`                               | Authors `service_catalog`. Other catalog families return unavailable until their serializers are established.                                                                                                                                                                                           |
| `insert_logic`  | `logic`, `inputs`, `parent` or `after` or `before`, `ref` | Adds If/Else If/Else, For Each, Try with paired Catch, Parallel/Parallel Branch, End, Exit Loop, or Skip Iteration.                                                                                                                                                                                     |
| `move_step`     | `step`, `parent` or `after` or `before`                   | Moves an existing step, also into or out of a branch.                                                                                                                                                                                                                                                   |
| `update_inputs` | `step`, `inputs`                                          | Changes typed input values. Native objects and arrays use declared child schemas.                                                                                                                                                                                                                       |
| `delete_step`   | `step`, `children`, `cascade`                             | Deletes saved actions or logic with ID tombstones. Deletes saved subflow calls by omission from the complete draft list, not by deleting the referenced definition. Child handling is `reject` (default), `delete`, or `promote`. Connected branches require `cascade=connected` and `children=delete`. |
| `set_variables` | `inputs`, placement                                       | Assigns existing flow variables through Set Flow Variables.                                                                                                                                                                                                                                             |
| `update_field`  | `collection`, `name`, `label`, `hint`                     | Changes existing interface or variable metadata. Types and identities remain unchanged. Trigger-owned interfaces cannot change independently.                                                                                                                                                           |
| `update_stage`  | `stage`, `label`, `value`                                 | Changes an existing keyed stage's unique label or value.                                                                                                                                                                                                                                                |

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
Whole Object and all Array pills require captured serializer evidence and a schema compatibility adapter.
They remain unavailable, including primitive-array pills. Declared complex leaf fields work.
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

Unavailable: interface/variable creation and removal, stage creation/removal, handler creation/toggling,
scheduled and record-trigger serialization, and dynamic family inputs without a dedicated adapter.
Existing opaque settings remain unchanged. Stage attachments follow stable step identities during reordering.
Deleting a stage-attached step or moving it across parent regions fails until an explicit attachment adapter exists.
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

| Family                            | Smallest missing serializer evidence                                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Inputs, outputs, variables        | Scalar and nested create/remove bodies with generated IDs, flattened child registrations, binding cleanup, and output assignment behavior. |
| Stages                            | Create/remove body with stage IDs, states, component indexes, ancestor links, and deletion semantics.                                      |
| Top-level handler                 | Toggle-on/off bodies with hidden order-zero wrappers, enabled input, parent rewriting, and handler boundary index.                         |
| Record/scheduled/dynamic triggers | Final native input values, regenerated interfaces, table dependencies, and stored timezone/schedule representation.                        |
| Whole complex pills               | Object, Array.Object, and primitive Array bindings with value/children fields and complete source/target schemas.                          |
| Try/Catch paired moves            | Pair movement across parent regions with connectedTo, children, order, and ancestry fields.                                                |

Further capture requires an authenticated editor bootstrap that works within the GET-only/no-worker boundary,
or sanitized version-matched request fixtures. Do not permit the blocked POSTs merely to make the UI load.
Blocked requests cannot establish success responses, OAuth effect behavior, runtime results, or capture guarantees.
All remote lifecycle response gaps remain open.

After a timeout or incomplete response, inspect the exact target before another preview.
For create, resolve the reviewed name and scope before retrying to avoid a duplicate.
The OAuth session can have a different current update set from the browser session.
The tool does not change that session setting or guarantee update-set capture.
