# EUC-2389 LogView acceptance assessment

## Scope and evidence

Assessment date: 2026-10-20. Repository baseline: `c571175bdf467db558d54359f09e93f7d9747a94`.
The acceptance target is **TV2 LogView - Ny adgang** on `https://tv2dev.service-now.com`.
This report uses the caller-supplied EUC-2389 requirements and Confluence page `6628737271`, version 17.
Jira and Confluence were not queried or changed by this session.

Live reads used the current local Python 3.15 stdio server and normal cached OS OAuth.
Browser authorization was blocked. No credentials, request records, or person-membership rows were exported.
No mutations were in flight when the target changed. No test artifacts were created.
No remote preview/apply on the business target, execution, AD operation, publication, activation, or deactivation occurred.
The earlier context-only creation preview was rejected before any write.

## Target and update-set context

| Item                           | Observed identity and state                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Flow                           | `60a34629c38c53905cf89fcd2b01318d`, Global, active, published, revision `21`                                             |
| Master/latest snapshot         | `7beb1aa5874497508643202cbbbb35d0`, equal references                                                                     |
| Catalog item                   | `aca34669c38c53905cf89fcd2b0131bb`, **Adgang til LogView**, inactive                                                     |
| Existing connector root        | `9e858befc3340f105cf89fcd2b01317d`, **Provision AD Group Membership**, active/published, revision `28`                   |
| Referenced connector snapshot  | `8502df488715cb508643202cbbbb35b6`                                                                                       |
| Current OAuth update set       | `bc562d2dc38853905cf89fcd2b0131fe`, **LASN - EUC-2389 - LogView access ordering**, Global, **in progress**, revision `0` |
| Previously required update set | `29fd021387b343108643202cbbbb358b`, **LASN - Test flows**, Global, **ignore**, revision `1`                              |

The current set's name, application, state, and revision came from one exact read of that record.
The caller accepted `bc562d2dc38853905cf89fcd2b0131fe` as the expected EUC-2389 context for subsequent work.
The ignored set must not be reopened automatically.
Any later write requires a fresh session-context read and fresh target revision, not these cached assessment values.

The flow-header Customer Update `74a3c669c38c53905cf89fcd2b0131b7` currently references the current set at revision `21`.
This is observed header metadata only. It does not prove XML equality, child-artifact capture, or attribution to this session.

## Requirement coverage

| EUC-2389 requirement                                   | Configuration evidence                                                                                                                                           | Acceptance result                                                                             |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Four scoped LogViews have function groups/managers     | Kundecenter, MCR, Play Publicering, and TEKO each have an active group and active manager. `AllowMembershipRequests=true`.                                       | Configuration observed. Responsible-manager ownership still needs business confirmation.      |
| Users without access can find and submit the request   | The item is inactive. No Available-for relation was found. Its exclusion criterion is SNC External.                                                              | FAIL: inactive item cannot establish orderability. Non-member portal submission NOT RUN.      |
| ADD/REMOVE for self or others                          | `requested_user` defaults to the current user and permits another active user. Only `requested_user` and `logview` variables exist. No variable-set links exist. | FAIL: no action variable. Connector call is hardcoded `ADD`.                                  |
| Only the function-group manager approves both actions  | The flow captures manager/group/user before approval. The rule is `ApprovesRejectsAnyU[{{flow_variable.approved_manager}}]`.                                     | ADD configuration observed. REMOVE and runtime approval authorization NOT RUN.                |
| Rejection never changes membership                     | A non-approved approval result reaches End before the connector.                                                                                                 | Static control path observed. Rejection execution NOT RUN.                                    |
| Approved action uses existing AD provisioning          | The existing published connector supports ADD and REMOVE. The target calls it with ADD and waits for completion.                                                 | ADD binding observed. REMOVE binding missing. No connector execution performed.               |
| Portal menu/direct URL after successful import         | Menu and portal helpers check imported ServiceNow group membership. The flow creates a manual verification task after provisioning.                              | No successful-import correlation or automated completion established. Runtime checks NOT RUN. |
| Provisioning/import failures reach an agreed recipient | Normal continuation and Catch create catalog tasks for End-User Computing.                                                                                       | Configured route observed. Agreement and import-failure routing remain unresolved.            |
| AD remains authoritative                               | Application import coalesces on AD GUID, removes the `ACL_ServiceNow-` name prefix, and reconciles membership. No Manager field mapping exists on that map.      | Configuration observed. AD execution/import outcome NOT RUN.                                  |

All four function groups have an empty `type`. An exact `LogViewFunction` group-type lookup returned no record.
Their source metadata has the expected CN prefix and ServiceNow OU, but not the specified ApplicationGrupper OU.
Actual AD placement and the intended OU naming must be confirmed outside this read-only assessment.
The import completion script does not filter unsuccessful import rows before membership reconciliation. Do not change it as part of this small patch.
Deleted-group handling remains EUC-2348, outside this scope.
The Kundecenter direct-member proposal remains subject to its technical review.

## Read coverage and limits

- Read all 15 configured target nodes and all 36 nodes of the connector root. The connector's published snapshot reference matches its root header.
- Read catalog variables, variable-set links, client scripts, UI policies, user-criteria relations, and the exclusion criterion.
- Read the four group/configuration mappings without returning manager identities or person-membership data.
- Read complete relevant access helpers, portal header code, and application-import field maps and transform scripts.
- The contract reports incomplete hidden-Try semantics and missing trigger-active metadata. Exact structural counts do not establish complete semantics.
- Snapshot header ownership was read. Complete published-snapshot content equality was not established.
- Final target read still reported revision `21`. No request, approval, provisioning, or import runtime behavior was tested.

## Bounded ADD/REMOVE patch specification

This section is a **local, non-deployable patch specification**, not a remote request body or an approved apply command.
It does not copy the published flow, persisted execution identities, scripts, or request values.
The existing target remains unchanged until an approved draft route and context are available.

### Catalog variable

Create one variable owned by catalog item `aca34669c38c53905cf89fcd2b0131bb`:

| Property  | Proposed value                                     |
| --------- | -------------------------------------------------- |
| Name      | `membership_action`                                |
| Question  | `Hvad skal der ske med adgangen?`                  |
| Type      | Select Box with predefined question choices        |
| Mandatory | true                                               |
| Default   | `ADD`, preserving the existing new-request default |
| Order     | 150, between LogView and requested user            |
| Choices   | `ADD`: `Giv adgang`; `REMOVE`: `Fjern adgang`      |

Resolve the installed Select Box type value and choice-table contract before preparing any native write.
New variable and question-choice IDs are server assigned. Do not invent or copy them.
Local reference: `ServiceNowDocs/markdown/servicenow-platform/service-catalog/select-box.md`.
Do not enable the catalog item, change its visibility, or alter existing variable qualifiers in this patch.
The default applies to new form submissions only. A missing action on an existing or malformed request must not silently become ADD.

### Flow changes and identity mapping

The table identifies **source steps only**. An isolated copy must receive new native step/UI identities and remap every binding.
These IDs must never be pasted into another flow as new instances.

| Source record / UI identity                                                                                        | Bounded change                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Get Catalog Variables before approval: `1ed88eed874097508643202cbbbb358b` / `04aea688-8928-4bcf-a7c6-e785c5b3b545` | Include the new `membership_action` variable in its native selected-variable metadata. Retain the existing two variables.                                                                                                        |
| New pre-approval guard                                                                                             | Allow only exact, case-sensitive ADD or REMOVE. Missing, blank, unknown, or malformed values must terminate before approval/provisioning. No implicit coercion or server fallback.                                               |
| Existing capture step: `026452ed87c097508643202cbbbb35af` / `ab9610be-1960-49e8-a1be-6a68f4f341d2`                 | Preserve captured manager/group/username. Add a new captured `approved_action` value from the validated first variable read. Do not overwrite it after approval.                                                                 |
| Approval: `40d45221870497508643202cbbbb350a` / `dc814dc6-3ead-4fdc-a432-19954eef5d6c`                              | Keep the same single manager rule for both actions. Present the captured action in the approval reason with the existing target context.                                                                                         |
| Existing approval check / End: `7d655a61870497508643202cbbbb35e2`, `f9655a61870497508643202cbbbb35e4`              | Preserve the non-approved termination path. No connector call on rejection, cancellation, or any other non-approved outcome.                                                                                                     |
| Second variable read: `dec55ea1870497508643202cbbbb3508` / `692e6b4c-e3d3-4e18-9d44-59892f3dc864`                  | Re-read all three variables, including action, after approval.                                                                                                                                                                   |
| New post-approval action guard                                                                                     | Require the re-read action to equal the captured action and remain in the ADD/REMOVE allowlist. On mismatch, terminate before the connector.                                                                                     |
| Existing revalidation: `dac55ea1870497508643202cbbbb3509`, `ba261265870497508643202cbbbb35ab`                      | Retain approved user/username and group/manager equality checks. No re-selection of an approver after approval.                                                                                                                  |
| Connector call: `b65612a5870497508643202cbbbb351f` / `1c69ef98-9780-4fac-92eb-7d62e74df285`                        | Replace literal ADD with a native choice-compatible binding to the captured approved action. Keep existing user/group bindings, published connector snapshot, and wait-for-completion.                                           |
| Existing continuation task: `77e71ea9870497508643202cbbbb3503`                                                     | Update only action-specific task wording to distinguish the requested imported membership state. ADD expects membership present; REMOVE expects membership absent. Connector success alone must not claim imported access state. |

The last wording change must not alter assignment or introduce automatic membership updates.
Existing operational failure recipients remain unchanged. Their agreement is a separate acceptance gap.
No changes to the shared connector, AD groups, group type/permissions, Manager, import scripts, catalog activation, or portal authorization are included.

### Control-flow invariant

The following is semantic pseudocode, **not Studio's serialized condition syntax**:

```text
Read requested user, LogView, and action.
If action is neither ADD nor REMOVE: terminate without provisioning.
Resolve and capture the approved user/username, group, manager, and action.
Request approval from that captured manager for that captured action.
If outcome is not approved: terminate without provisioning.
Re-read the requested user, LogView, and action.
Require unchanged approved user/username, group, manager, and action.
Require action to remain exactly ADD or REMOVE.
Only then call the existing connector with the captured action.
Continue with the existing manual import-verification path.
```

### Current authoring boundary and draft strategy

The current tool **cannot edit an active published target while retaining its running snapshot**.
`editing.require_editable` requires inactive, draft, unpublished, non-snapshot state and an empty master-snapshot reference (`editing.py:139-169`).
`semantics.studio_semantics` independently enforces those lifecycle prerequisites (`semantics.py:429-454`).
The tool has no supported published-to-draft transition or clone operation. Do not deactivate the live target or change flags to evade these guards.

The existing `create` operation can create a new inactive isolated flow header after context approval.
Ordered native insertions can then add supported steps with new identities. They do not automatically make a faithful copy of this target:

- Get Catalog Variables has a `slushbucket` input, and approval uses `approval_rules`/`schedule_date_time`. Those insertion values lack dedicated adapters.
- The observed Catch carries `__status__` plus `enabled`. The current handler-creation contract accepts only `enabled` (`handlers.py:98-115`).
- A newly captured string variable cannot directly satisfy the connector's choice-pill check. It requires a declared compatible choice set (`editing.py:519-527`).

If the approved native draft route cannot retain that choice schema, keep `approved_action` as a validated string.
After approval and revalidation, branch on its exact value and use literal ADD or REMOVE in the corresponding connector call.
Retain the original call for ADD. Create a new native call identity for REMOVE, and never reuse the original instance ID.
This alternative adds one connector instance and explicit branches. It must not call both branches or fall back to ADD.
It avoids a fabricated choice schema, but does not resolve the active-target and specialized-input authoring limits.

These were the compatibility limits at the recorded baseline. The fixes below address the observed widget and Catch shapes without weakening the active-target guard.
No remote copy or draft preview was prepared in this session.

### Native copy contract inspection

The local source establishes an actual copy route, not a guessed endpoint:

- `getCopyFlowServerModel` (8138-8142) sends only `name` and `scope`.
- `copyFlowServerCall` (448601-448610) posts to `flow-designer/{source id}/copy` and reads `response.value.data` as the new flow ID.
- `newGlideRESTParams` (161331-161338) maps `flow-designer` to the ordinary `/api/now/processflow/flow` prefix.
- `copyFlowSaga` (448630-448656) supplies `sysparm_transaction_scope` and opens the returned ID.
- Local `copy-flow.md:25` says the new flow has the same properties, trigger, actions, and logic as the source.

Neither that request nor its response handler establishes that copying an active source creates an inactive, unpublished target.
There is no source-proven `active=false` copy parameter. Do not add a guessed flag, then deactivate a possibly active copy to repair it.
Therefore the native copy POST was **NOT RUN**. Its unresolved creation-state guarantee is the remaining safe-copy blocker.
The supported empty-header create route is inactive, but faithful reconstruction still needs native template-value fields/transforms and their identity handling.
The target's catalog tasks use those fields. They must not be omitted, flattened, or replaced by invented payloads.

### Compatibility fixes from the observed live shapes

The implementation now handles these bounded cases through the existing planner and serializers:

- Catalog variable selections remain exact native strings, including `sys_id:item_option_new`. Selected leaf schemas come from the source-proven GET `flow/{catalog id}/service_catalog_variables` (217777-217791).
- `collectAllProperties` (130997-131039) and `generateActionDynamicOutputs` (207454-207473) establish selection-to-output binding. Existing selected output metadata is preserved. Removed, changed, ambiguous, or unavailable schemas block the operation.
- Catalog-schema evidence binds preview/apply. Session-personal catalog default values are excluded from persisted new output defaults and from dependency fingerprints.
- Approval rules retain their original wire text. The bounded Any/All U/G clauses from `encode`/`_getApprovers` (50075-50162) require matching user/group reference pills, not generic text interpolation.
- Approval no-deadline JSON retains `action=none` and the native `date={{}}` sentinel. The sentinel is not a malformed pill. The scalar widget's max length does not limit its serialized JSON envelope (48618-48646,48770-48774,126172-126193).
- Dependent approval record/field inputs resolve the native table from a typed record pill. Read-only table parameters remain unavailable as caller overrides. The inherited dictionary resolver checks selected fields.
- The native two-input Catch accepts `__status__` with the catalog's `FDACTIONSTATUS` schema plus `enabled`. Toggling changes only enabled/read-only ownership. Opaque runtime status values remain strict and unchanged.
- Catch Error Status leaf pills come from the definition's input schema, as `getActionInstanceOutputs` does (16445-16464), rather than invented variables.
- A reference input's 32-character literal sys_id limit no longer rejects a longer exact native reference-pill expression. Literal validation remains unchanged.

Regression fixtures use synthetic metadata and identities. They do not contain target scripts, user defaults, credentials, or request data.
Automatic approval deadlines, manual/unknown approval forms, complex catalog-variable selections, and template-value edits remain excluded.
No business changes, catalog activation, provisioning, or import execution were performed by these fixes.

### Post-fix read-only live evidence

A current-code Python 3.15 stdio helper read the exact target and action/handler schemas, then checked values in memory.
It issued GET requests only. It did not call preview/apply, the copy endpoint, or any execution endpoint.

| Check                        | Observed result                                                                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------- |
| Accepted context             | Global / `bc562d2dc38853905cf89fcd2b0131fe` remained selected throughout the check.                |
| Active-target guard          | PASS: current target remains rejected for authoring. No guard bypass.                              |
| Two-input Catch catalog      | PASS: actual `__status__` plus `enabled` definition accepted without replacing the runtime schema. |
| First catalog-variable read  | PASS: both selected output schemas matched independent catalog metadata.                           |
| Approval rules               | PASS: actual manager-pill rule validated against the declared user reference type.                 |
| Disabled due date            | PASS: actual JSON and empty-date sentinel survived in-memory serialization.                        |
| Second catalog-variable read | PASS: both selected output schemas matched independent catalog metadata.                           |

The fresh header now reports **revision 23, active=true, status=draft**, with master snapshot still `7beb1aa5874497508643202cbbbb35d0`.
This differs from the earlier revision-21 published assessment. The target changed outside this session between assessments.
The helper checked identical before/after header and context for its own read window. This session issued no writes.
Do not apply the revision-21 patch manifest to the revised draft. Refresh and reconcile the other writer's changes before authoring.
No copied flow, isolated catalog item, new variable, or other test artifact was created.
These PASS results prove schema/value compatibility only, not live save/readback or request/approval/provisioning/import execution.

### Required acceptance tests before integration

| Test                                            | Expected result; currently NOT RUN                                                                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ADD for self and another user                   | Captured function-group manager alone receives approval. Approved ADD reaches the existing connector with unchanged approved subject/group.             |
| REMOVE for self and another user                | The same manager rule applies. Approved REMOVE reaches the same connector with REMOVE.                                                                  |
| Rejected/cancelled/non-approved ADD and REMOVE  | No provisioning call or membership change.                                                                                                              |
| Missing/blank/unknown action                    | Fail closed before approval/provisioning, regardless of the catalog default.                                                                            |
| Action changed while approval waits             | Post-approval mismatch blocks the connector. No silent ADD-to-REMOVE or REMOVE-to-ADD conversion.                                                       |
| User/group/manager changed while approval waits | Existing equality guards still block provisioning.                                                                                                      |
| Connector failure or unavailable import proof   | Existing manual path remains. No automatic replay and no claim that access is ready.                                                                    |
| Successful later AD import                      | Verify imported membership present for ADD and absent for REMOVE before claiming the requested access state. Execution requires separate authorization. |
| Configuration persistence                       | New IDs remain stable across echo/independent GET. Draft inactive/unpublished, original revision/snapshot unchanged, expected header capture verified.  |

Portal orderability, group-type/OU alignment, agreed recipients, and successful import handling remain separate EUC-2389 acceptance gaps.
They are not marked passing by this proposed ADD/REMOVE change.
Main integration remains blocked pending authorized draft verification and the required live acceptance evidence.

## Isolated complete-create fixture, 2026-10-22

The authorized log-only submission created `c6c7f496c38c1b905cf89fcd2b013115`, named
`MCP Live - Complete Flow 20261022T131324541263Z`, in Global scope.
One complete create preview contained nine edits. One apply sent one native property POST and one complete-definition PUT.
The definition contained seven steps: four Logs, Set Flow Variables, If, and Else.
It also contained one Service Catalog trigger and one string variable.
The start Log used `flow_variable.message`. The If used the Requested Item number pill.
No activation, publication, execution, AD call, or context change occurred. Existing LogView targets were not changed.

Apply returned `UNKNOWN_OUTCOME` during `save_complete_definition`:
`request.echo`, `both:flow.flowVariables`, `NEW_FIELD_IDENTITY_MISMATCH`.
The returned progress reported a verified header and an unknown definition outcome.
Independent readback observed all seven steps, one trigger, and one variable.
Status reported revision `1`, inactive draft state, and no published snapshot.
Read-only local validation returned `valid=true`, `complete=true`. These results did not establish request-to-save equality.

During the later read-only investigation, the exact Studio GET showed variable `name="message"` unchanged.
Its database ID was separate from its UI ID, which existed in `attributes.uiUniqueId` without a top-level UI cache.
The Set Flow Variables parameter referenced that database ID and the same stored UI identity.
This matches the client restoration contract in the local bundle (22009-22035, 232977-232978).
The verifier previously searched only the absent top-level cache when matching generated fields.

The historical request body, preview field UUID, and PUT response were not retained after the live child exited.
The later GET is not a substitute for that response. It cannot prove all historical IDs, schema fields, defaults, or values matched.
The local fix covers identity representation and the owned assignment-ID relationship only.
All other metadata remains strict. Further server transformations are not certified by this identity projection.
The consumed operation remains historically unknown. No remote replay, recreation, edit, deletion, or execution occurred during this fix.
Keep the fixture for a separately authorized incremental check with a fresh revision. Do not replay the original token.

## Distinct identity-fix live gate, 2026-10-22

A fresh local stdio server used the current working source: HEAD `cf0d941`, the staged update-set removal,
and the unstaged variable-identity fix. Its native schema omitted `expected_update_set`.
HTTP used the real `tv2dev` instance and normal OS-cached OAuth. No mock transport was used.
Before creation, an exact read reconciled the earlier fixture `c6c7f496c38c1b905cf89fcd2b013115`:
revision `1`, inactive draft, no master snapshot. That fixture was not edited or deleted.

The distinct new case was `MCP Live - Complete Flow Identity Fix 20261022T140950139167Z`.
Its complete preview passed outline, variable, alias, pill, and inactive-state checks.
The request contained nine edits for seven steps, four Logs, three assignment/logic steps,
one Service Catalog trigger, and one string variable. Its token was applied exactly once.
The native API received one property POST and one full-definition PUT.

The new flow is `c1b4c9da874cdb508643202cbbbb35b8`, Global, revision `1`, inactive draft, with no published snapshot.
Echo and subsequent GET both contained all seven steps and the expected If/Else parents and orders.
The requested UI ID `b16daa36-c312-4285-a37e-67652e8daeb6` survived in `attributes.uiUniqueId`.
The variable database ID was `31b44dda874cdb508643202cbbbb35e8`.
The assignment parameter referenced that database ID and UI ID in both echo and GET.
This directly verifies the identity relationship addressed by the local fix. It does not establish full save equality.

Apply returned `UNKNOWN_OUTCOME` during `save_complete_definition`, at:
`request.echo`, `expected:flow.flowVariables[0].attributes[field:0]`, `MISSING_FIELD`.
The first key in the comparator's sorted attribute union was `element_mapping_provider`, absent from the request.
The echo also added `uiType` and `uiTypeLabel`. The request supplied empty `sourceId`, `sourceType`, and
`sourceUiUniqueId` attributes, which the echo omitted. These are observed key-presence differences, not an approved normalization rule.
No credential, value content, or raw payload is retained in this report.

One read-only reconciliation reported complete status and local validation `valid=true`, `complete=true`.
The definition outcome remains unknown relative to the request. No retry or recreation occurred.
The planned combined reorder/insert/message-update was not sent because the create apply did not pass verification.
There was no edit PUT, activation, publication, execution, AD call, context switch, update-set read, or Customer Update read.
Both retained fixtures and the original LogView targets remain unchanged after that reconciliation.
The live gate toward main is blocked by the demonstrated variable-DTO comparison difference, not by identity mapping.

Apply returned no verified ref mapping. The following observed mapping joins preview UI refs to identical echo and GET identities.
It is retained for read-only reconciliation, not as evidence that apply succeeded.

| Ref          | Persisted step ID                  | UI ID                                  | Order |
| ------------ | ---------------------------------- | -------------------------------------- | ----- |
| `start`      | `bdb44dda874cdb508643202cbbbb35fd` | `382b8589-3483-4484-a404-0ad09908bf8f` | 2     |
| `has_number` | `71b44dda874cdb508643202cbbbb35ff` | `8f92f327-748d-42c6-8c6b-6038f830f16d` | 3     |
| `then_log`   | `b1b48dda874cdb508643202cbbbb3500` | `d85ae49d-70c1-469d-9717-04d11b913eea` | 4     |
| `otherwise`  | `75b48dda874cdb508643202cbbbb3501` | `67251084-d70d-43c9-adc5-b72c716f8e58` | 5     |
| `else_log`   | `fdb48dda874cdb508643202cbbbb3501` | `97b92463-645a-404f-ad24-e36f579bb073` | 6     |
| `finish`     | `f9b48dda874cdb508643202cbbbb3502` | `7c93c300-d7e1-4826-9d24-6c5ec372b38e` | 7     |

Both echo and GET placed `then_log` under `has_number`, and `else_log` under `otherwise`.

## Variable DTO correction and incremental preview, 2026-10-23

Resume inspection found the partial source changes and no retained incremental apply journal from the cancelled task.
Exact header and Studio reads confirmed that no incremental edit persisted on `c1b4c9da874cdb508643202cbbbb35b8`.
It remained Global, revision `1`, inactive draft, without a published snapshot.
It contained seven steps, four Logs, three logic/assignment steps, one Service Catalog trigger, one `message` variable, and one assignment.
`roundtrip_probe` was absent. Neither historical complete-create `UNKNOWN_OUTCOME` is reclassified by these reads.

The correction constructs new String variable requests with the complete observed native DTO defaults.
It explicitly requests the mapper provider, `uiType`, and `uiTypeLabel`. All attributes remain strictly compared.
Existing declarations and other types are not rewritten. A complete sanitized current GET fixture supports the regression tests.
Two focused regressions failed before the constructor correction and passed afterward.
Adversarial cases retain strict checks for provider, type, reference, name, value, default, unknown metadata, identities, and pills.

A fresh local Python 3.15 stdio server then used the working source and normal OS-cached OAuth against exactly `https://tv2dev.service-now.com`.
Its schema omitted `expected_update_set`. Real HTTP had an explicit endpoint allowlist and allowed at most one definition PUT to this fixture.
Creation, lifecycle effects, other flow targets, update sets, and Customer Updates were outside the allowlist.
The first harness invocation stopped before preview because it expected the wrong revision result shape.
The corrected harness read `revision.mod_count` and performed the only live preview request.
Initial local validation returned `valid=true`, `complete=true`, with no diagnostics and no remote compile.

The ordered request proposed these five edits:

1. Create String variable `roundtrip_probe`, labelled `Roundtrip probe`.
2. Insert its benign assignment before `start`.
3. Move `finish` immediately before `has_number`.
4. Insert `probe_log` under `has_number`, consuming `{{flow_variable.roundtrip_probe}}`.
5. Update only the existing `then_log` message.

The preview returned `UNAVAILABLE`: `The existing native instance has non-default or unknown settings. No new instance is inferred.`
No token was issued or applied. No PUT, property POST, or other remote mutation occurred.
Read-only diagnosis isolated the rejected field to the existing Set Flow Variables instance's nonempty String `flowBlockId`.
All other neutral-profile values matched, and its embedded definition equalled the independently read logic definition.
The Log profile passed the independent published-schema check.
The local bundle lists `flowBlockId` at 472392 but provides no further references that establish how a new instance must obtain it.
The field was not ignored, blanked, or copied into a new instance without that contract.
No second preview was requested. A regression now preserves this fail-closed case without a write.

The live result is blocked at preview. Echo/GET save equality, new persisted refs, revision `2`, and nine-node counts remain unverified.
The fixture retains its seven-node revision `1` definition. The old fixture and both protected LogView targets received no requests.
There was no activation, publication, deactivation, execution, catalog write, integration call, or update-set access.
The temporary sanitized journal records the initial reads, one preview request, and its rejection without tokens or payload values.

Final local verification passed `mise run schemas`, `mise run check`, and `mise run test`: 3489 passed, 66 deselected.
The focused identity, DTO, field, and stdio regressions passed 134 tests, including the added `flowBlockId` preview rejection.
That run included the three real local stdio mock-HTTP variants for ordinary, attribute-only identity, and native String DTO responses.
The native-profile fixtures prove only the supported neutral shape. They do not establish that this retained live assignment fits that shape.

## Fresh assignment construction and one live incremental save, 2026-10-23

The earlier `flowBlockId` preview rejection came from cloning a saved assignment profile.
It did not establish that Studio could not construct a new assignment. The Set Flow Variables path now constructs a fresh native DTO.
The Log profile behavior is unchanged. Existing saved assignment fields, identities, and opaque metadata are not copied into new instances or rewritten.

The bounded source trace followed `addNewInstance` (206651-206672), `buildNewFlowLogicInstance` (221783-221799),
the assignment reducer (207302-207318), component splitting (130432-130460), and the save path (99193-99197, 97521-97527).
New instances do not receive a saved block reference from these constructors.
The optional serializer names `flowBlockId` at 472392 but omits undefined values at 471736-471743.
The complete observed instance-field projection and parameter shape are retained in `tests/fixtures/flow_native_assignment_dto.json` with synthetic IDs.

Bounded dictionary reads established `sys_hub_flow_logic_instance_v2.block -> sys_hub_flow_block`,
`logic_definition -> sys_hub_flow_logic_definition`, and inherited `sys_hub_flow_component.flow -> sys_hub_flow_base`.
An exact read of the existing assignment confirmed its flow, UI ID, class, definition, block, and `block.sys_id` relationship.
For a newly assigned block reference, verification now reads that exact native instance record and checks these relationships.
Only that independently verified new relation can enter request comparison. Existing references and all semantic settings remain strict.
The formerly rejected nonempty-block regression now passes without cloning it. Wrong-owner, wrong-definition, changed-input, and metadata-drift cases still fail.

Before the live operation, `mise run schemas`, `mise run check`, and `mise run test` passed: 3504 passed, 66 deselected.
The focused identity, variable DTO, field, and Python 3.15 stdio cases passed 149 tests.
The native stdio case includes a second variable, a fresh assignment, a consuming action, a move, and an existing message update.
These offline results do not certify real-server metadata transformations.

A fresh local stdio server used the working source and OS-cached OAuth against exactly `https://tv2dev.service-now.com`.
Status confirmed revision `1`, inactive draft, no published snapshot, and absence of `roundtrip_probe`.
The native schema omitted `expected_update_set`. The real-HTTP allowlist excluded creation, lifecycle endpoints, other flows, and update-set access.
One new preview contained the same five ordered operations documented above. Its outgoing plan passed nine-node, branch, original-ID, pill, and inactive-state checks.
One fresh token was applied once. One definition PUT returned HTTP 200. No further write followed.

Apply returned `UNKNOWN_OUTCOME` with `The draft save did not return a completed result`.
The live harness raised during its post-response assertions before the native save verifier received the HTTP 200 result.
This is a harness-induced unverified outcome, not evidence of a remote HTTP failure or a successful apply result.
The harness's first reconciliation also failed its post-read assertion. Its header read nevertheless reported revision `2`.
One corrected read-only diagnostic then read the exact saved Studio definition without those assertions and performed local validation.

That GET showed nine nodes, five Logs, four logic/assignment nodes, two assignments, and variables `roundtrip_probe` and `message`.
The header reported revision `2`, `active=false`, `status=draft`, and an empty published-snapshot reference.
Local validation returned `valid=true`, `complete=true`, and no diagnostics. No remote compile or execution occurred.
Observed new identities are:

| Object     | Persisted ID                       | UI ID                                  |
| ---------- | ---------------------------------- | -------------------------------------- |
| Variable   | `0cb534ea87489f508643202cbbbb3574` | `4bb0d826-bbd7-4afc-928e-c7c4037e55f7` |
| Assignment | `50b534ea87489f508643202cbbbb358f` | `4406fedf-3bbb-459d-8358-dd3a2f6b8709` |
| Probe Log  | `18b534ea87489f508643202cbbbb3595` | `b01063d5-4d32-48fd-91f1-f5e26a428e0d` |

These are observed GET identities, not a verified apply ref result.
An independent Table API read verified the new assignment's block ownership relationship.
Comparing the fresh constructor against the current GET still found a real metadata difference:
`both:flow.flowVariables[0].attributes[field:2] VALUE_MISMATCH`, where the sorted attribute key is `uiTypeLabel`.
The existing `message` declaration's `attributes.uiTypeLabel` also differed from its captured pre-save DTO.
The complete newly observed assignment-parameter difference paths were `attributes.uiTypeLabel`, `maxsize`, and `type_label`, in both `inputs` and `flowVariables`.
No attributes or semantic values were ignored to accept these changes.
This diagnostic reconstructs the new assignment contract from source. It does not replace the unavailable historical request/echo comparison.

The live acceptance result remains unknown. Persistence and current local validity are established, but successful apply and full echo/GET equality are not.
All writes stopped after the single PUT. There was no retry, second token, fallback edit, new flow, lifecycle action, or original LogView request.
The previous complete-create outcomes remain historically unknown. The retained nine-node fixture must not receive a replay of this scenario.

Final local gates after self-review passed `mise run schemas`, `mise run check`, and `mise run test`: 3505 passed, 66 deselected.
The focused command for `test_flow_variable_dto.py`, `test_flow_variable_identity.py`, `test_flow_fields.py`, and `test_flow_creation_stdio.py` passed 150 tests.
The additional self-review regression rejects a conflicting nonempty UI cache instead of discarding it during fresh parameter construction.

## Display metadata, harness correction, and message-only save, 2026-10-23

The cancelled task left source, tests, and a sanitized metadata capture but no new message-edit script or operation journal.
Git remained at `fa244ad`. The final check command had no completion result, so its cancelled run was not counted as passing.
The completed metadata investigation and focused test evidence were reused. The persisted structural edit was not repeated.

### Separate harness and DTO findings

The previous harness called `verify(echoed, True)` inside its HTTP `guard`, after the PUT returned HTTP 200.
An offline reproduction using the captured old/new metadata raises `AssertionError` in `verify`, at embedded child line 62:
`assert actual == expected`. This compares the old `message` declaration, including its changed display attribute.
The historical stack itself was not retained. The source and journal place this assertion before normal application verification.
The journal contains no forbidden-request event, and ClientSession returned a structured error rather than timing out.
The failure was therefore separate from a denied ownership GET, child cleanup, or a demonstrated application comparison failure.

`FlowLiveObserver` now retains exact request, echo, and GET objects in memory without exposing them in payload logs.
It catches observation failures and cannot replace the API result with an observer exception.
The stdio regressions use this same observer. Effect boundaries are checked before sending, not by post-response assertions.
The live read guard accepts legitimate same-instance verification GETs while still excluding update-set/session reads and forbidden lifecycle effects.

The bounded exact revision-2 GET established these public schema values:

| Location                          | `uiTypeLabel`, before to after | `type_label`, before to after | `maxsize`, before to after |
| --------------------------------- | ------------------------------ | ----------------------------- | -------------------------- |
| Existing `message` declaration    | `String` to `string`           | `string` unchanged            | `0` unchanged              |
| New `roundtrip_probe` declaration | `String` to `string`           | `string` unchanged            | `0` unchanged              |
| Existing assignment parameter     | `String` to `string`           | `String` unchanged            | `8000` unchanged           |
| New assignment parameter          | `String` to `string`           | `string` to `String`          | `0` to `8000`              |

The parameter observations apply to both `inputs` and `flowVariables` copies.
The older parameter's `order` also refreshed from `1` to declaration order `0`. That historical change is retained in the fixture and is not normalized.
Its revision-2 value is the baseline for the new message edit.
Before values are retained pre-save GET metadata or constructor output. After values are exact revision-2 GET metadata, not the lost historical echo.

Local Studio source hydrates type labels from `GLIDE_TYPES` (232966-232973), supplies the picker attribute (344082-344085), and renders it as type text (491623-491638).
Only the observed `String`/`string` display spellings in scalar String variable and assignment-parameter DTO slots compare equivalently.
User labels, types, names, references, provider, IDs, values, defaults, sizes, order, and unknown metadata remain strict.
Attribute dictionaries are replaced on the comparison copies, preventing an aliased label-cache dictionary from changing outside these slots.
New String parameter construction uses its observed native `8000` default only when the declaration has no explicit positive size.
Explicit positive sizes remain unchanged. Size changes are never ignored during save comparison.
The String control label follows `getAdvancedInputNewItem` (147809-147822); old saved metadata is not rewritten to English.

### New live operation and outcome

Final local checks passed before the new operation: `mise run schemas`, `mise run check`, and `mise run test`, with 3528 passed and 66 deselected.
The five focused identity, DTO, field, observer, and stdio files passed 173 tests on Python 3.15.
The baseline comparator rejected the captured display difference; the corrected comparator passed the source-grounded metadata regression.
Adversarial type, value, default, binding, identity, provider, unknown metadata, explicit size, and parameter-order changes still reject.

A fresh local stdio server used the current source, exact `https://tv2dev.service-now.com`, and normal OS-cached OAuth.
Fresh status and definition reads confirmed revision `2`, Global, inactive and unpublished, with nine nodes and absence of the new marker.
One preview proposed only a static `then_log.log_message` update. One fresh token was consumed once, and one PUT returned HTTP 200.
The prior multi-operation edit and its token were not reused. The schema still omitted `expected_update_set`.

This time application verification completed its echo comparison and returned its own precise failure:
`UNKNOWN_OUTCOME`, `request.echo`, `both:flow[field:29] VALUE_MISMATCH`.
The observer reported no error and did not interrupt the effect. This root-setting mismatch is separate from the corrected variable display metadata.
No rule was added to ignore the unresolved root setting or infer its identity from the numeric diagnostic index.

One read-only reconciliation followed. Its header reported revision `3`, inactive draft, and no published snapshot.
The new marker was present. There were still nine nodes, five Logs, four logic/assignment nodes, two variables, and two assignments.
Local validation returned `valid=true`, `complete=true`, with no diagnostics.
Same-harness evidence verified raw variable and assignment equality, unchanged node IDs, parents, and orders, and full echo-to-GET semantic equality.
Public schema projections were identical in the actual request, echo, and GET, including all three previously changed metadata fields.
Request-to-echo and request-to-GET comparisons both retained the root-setting mismatch above.
The observer retained only privacy-safe projections and value-free comparison results outside memory. The root key and value remain unresolved.

The live acceptance result remains unknown, despite persisted message content and valid current structure. No apply success or complete-create success is claimed.
All writes stopped after the single PUT. There was no retry, second marker edit, new flow, execution, lifecycle action, update-set query, or protected LogView request.

## Named root-cache correction and verified message edit, 2026-10-23

The root field is `labelCacheAsJsonString`, not a variable DTO or identity field.
The comparator sorts the root-setting key union after removing the permitted `updated` revision field.
The exact revision-3 GET mapped ordinal 29 at that location to this fixed native field. Its value was a String containing a three-entry JSON list.
Decoding it produced exact equality with the same GET's `label_cache`.

The previous full request and echo existed only in the exited child process. The retained files contained schema projections and first-difference results, not root strings or key lists.
Therefore, no historical before/after string contents or byte-formatting direction are claimed.
Read-only reconstruction against revision 3 showed that planning retained the old serialized getter while rebuilding `label_cache`.
It added eight null projections across `attributes`, `choices`, `column_name`, and `parent_table_name`.
It also introduced the editor alias `startOfErrorHandlingInstances` beside the existing native `startingIndexOfErrorHandlingInstances=-1`.
These were all reconstructed differences. The investigation did not infer or expose arbitrary root keys or private values.

Source grounds the narrow correction:

- `passedInFlow` explicitly deletes `labelCacheAsJsonString` (227783-227788).
- `cleanupFlowBeforePut` selects native `FLOW_SCHEMA` keys (101697-101712); that schema excludes the getter (350835-350903).
- `processLabelCacheOutgoing` emits the four observed optional cache-entry fields as null (101724-101727).
- The native schema names `startingIndexOfErrorHandlingInstances`. The shorter alias is reconstructed editor state.

Planning and transport now omit only the fixed root getter. Returned getter values must parse as JSON lists and match the authoritative cache semantically.
JSON whitespace/property-order changes and the four source-proven null/absent entry forms do not change cache meaning.
Nonempty fields, unknown keys, meaningful cache types/providers/consumers, and same-named fields outside this root slot remain strict.
Ordinary native drafts no longer acquire an unrequested editor boundary alias. The native boundary index and existing handler behavior remain checked.
A corrected read-only no-op plan matched the exact revision-3 GET with zero remaining semantic differences before any new preview.

Three focused regressions failed before the correction: cache encoding/null projection, invented editor alias, and outgoing getter inclusion.
They passed afterward. Native getter and nullable-field behavior now appear in complete-create/edit HTTP fixtures and a fourth Python 3.15 stdio variant.
Adversarial malformed JSON, wrong types, contradictory mirrors, changed real settings, wrong scope, unknown metadata, and incorrect native boundaries still reject.
`mise run schemas`, `mise run check`, and `mise run test` passed: 3545 passed, 66 deselected. The six focused files passed 190 tests.

Only after these checks, one fresh local stdio server used current source and normal OS-cached OAuth against exact `https://tv2dev.service-now.com`.
Fresh status confirmed Global revision `3`, inactive and unpublished, with nine nodes and no new marker.
One preview proposed a new static `then_log.log_message` marker, not a replay of either earlier edit.
Its fresh token was consumed once. One PUT returned HTTP 200, and normal MCP apply returned success with revision `4`.

Application request-to-echo, request-to-independent-GET, and echo-to-GET verification all passed.
Same-harness evidence retained actual public root-field projections: the request omitted `labelCacheAsJsonString`; echo and GET each returned a String decoding to three entries, exactly equal to their own `label_cache`.
The source-proven mirror presence/encoding, nullable cache fields, reconstructed step UI projections, and advancing `updated` timestamp accounted for raw representation differences.
The before/after semantic check passed with only the intended Log message change. Variables and all logic/assignment records were also raw-equal.
All node IDs, parents, and orders remained unchanged. The edited Log retained persisted ID `b1b48dda874cdb508643202cbbbb3500` and UI ID `d85ae49d-70c1-469d-9717-04d11b913eea`.

Final status was complete: Global revision `4`, inactive draft, no published/latest snapshot.
The definition still contained nine nodes, five Logs, four logic/assignment nodes, two variables, and two assignments.
Local validation returned `valid=true`, `complete=true`, with no diagnostics. The observer recorded no errors and did not interrupt verification.
The live message-only incremental-edit acceptance result is PASS. No further write occurred.
The historical complete-create and earlier uncertain edits remain historically unknown. This result does not certify live complete creation or execution.
No flow was created, activated, published, deactivated, tested, or executed. No update-set/session read, catalog change, or protected LogView request occurred.

## Final distinct one-shot acceptance attempt, 2026-10-23

The explicitly authorized new inert case was `MCP Live - One Shot Acceptance 20261023T083730151295Z`.
A fresh local Python 3.15 stdio server used source HEAD `c2c39bb`, the preserved unrelated worktree changes, full tool package, and normal OS-cached OAuth against exact `https://tv2dev.service-now.com`.
No application source changed for this attempt. The native schema still omitted `expected_update_set`.
The ordinary preview/apply path used one exact generated name in Global scope, including its normal pre-write rechecks.
There were three bounded exact-name conflict queries from those native rechecks, not a broad flow search or a separate manual creation path.

One complete preview contained nine ordered edits: Service Catalog trigger, String `message` declaration, assignment, variable-consuming start Log,
If on the Requested Item number pill, then Log, Else, else Log, and trailing Log.
The preview verified seven steps, four Logs, three logic/assignment steps, one variable, two expected pill paths, six aliases, and inactive state.
One fresh token was consumed exactly once. The native operation sent one property POST and one complete-definition PUT, both returning HTTP 200.
No client-side header/add-step workaround, raw PUT submission, retry, or second fixture creation occurred.

The new retained flow is `ca324deac30c9b905cf89fcd2b01315d`.
The created header was verified at revision `0`. The final header observed revision `1`, `active=false`, `status=draft`, and an empty published-snapshot reference.
Apply returned `UNKNOWN_OUTCOME` during `save_complete_definition`, with `header="verified"`, `definition="unknown"`, and `retry_safe=false`.
The exact application verification failure was:
`request.echo`, `both:flow.inputs COUNT_MISMATCH expected_count=0 actual_count=2`.
No input metadata was ignored, and no speculative correction or further remote write followed.

One exact-ID read-only reconciliation returned `valid=true`, `complete=true`, no diagnostics, and no remote compile.
The saved definition had seven ordered steps, four Logs, three logic/assignment steps, one String variable, one assignment, and one trigger.
Echo and GET retained the expected If/Else parents, variable pill, Requested Item condition pill, and assignment mirrors.
Same-session echo-to-GET semantic comparison passed. This does not establish equality with the submitted request, whose empty flow-input interface differed from the two returned inputs.
The observer recorded no errors and did not interrupt the application result.
The planned post-success contract inspection was not run because apply did not succeed. The reconciliation above was the only Studio GET after the PUT.
No successful apply result or verified ref map was returned. The following joins are observed preview/GET identities only:

| Ref          | Observed persisted step ID         | Preview and GET UI ID                  | Order |
| ------------ | ---------------------------------- | -------------------------------------- | ----- |
| `start`      | `a6328deac30c9b905cf89fcd2b013150` | `28936ec3-ed75-48d4-8dbe-013a47938bd9` | 2     |
| `has_number` | `6a328deac30c9b905cf89fcd2b013152` | `47e8e6b6-e171-4404-807e-6cd2fe537811` | 3     |
| `then_log`   | `ee328deac30c9b905cf89fcd2b013153` | `1dbc6fc5-ab13-4589-a79c-0a301c6686e7` | 4     |
| `otherwise`  | `e2328deac30c9b905cf89fcd2b013155` | `569d9133-4d73-42e5-af94-1fe827a4c43f` | 5     |
| `else_log`   | `ae328deac30c9b905cf89fcd2b013155` | `5761f50b-e9dd-47aa-ab7d-1b9e63ca396f` | 6     |
| `finish`     | `ea328deac30c9b905cf89fcd2b013156` | `172bbfd5-f707-4a0a-81f5-9b426cce953e` | 7     |

The assignment was observed as `26328deac30c9b905cf89fcd2b01314e`, UI ID `ea31deb8-7afd-4f16-be34-33c9cd42b185`, order `1`.
The fixture remains inert and must not receive a replay of this complete-create request or its token.
The prior revision-4 incremental PASS remains valid, but the required one-shot-create gate is blocked by the native flow-input count difference.
The old fixtures and protected LogViews received no requests. There was no activation, publication, deactivation, test, execution, catalog change, integration call, or update-set/session access.

### Local integration preparation

At the tested source revision, local `main` was `fd937711416e42d15f050d396bc109f9c794a76f` and was the merge base with `c2c39bb`.
The comparison had zero main-only commits and 28 feature-only commits. Cached `origin/main` was `e5f4daa7b129f329e9444ad5572819f52c9eb16a`, also contained in the feature history.
No fetch or remote change occurred. The two existing worktrees were the dirty feature tree and another contributor's `codex/shared-dev-tooling` tree.
The 14 tracked and four untracked unrelated changes were preserved. No stash, reset, cleanup, branch switch, main update, merge, integration worktree, push, or PR occurred.
Integration-tree checks are NOT RUN because the prerequisite live create gate did not pass.
After that gate is resolved, `main..feat/flow-authoring` identifies the committed integration range. An isolated integration branch can merge that committed range without importing dirty worktree changes.
The last unchanged-source local gates remain `mise run schemas`, `mise run check`, and `mise run test`: 3545 passed, 66 deselected, plus 190 focused isolated-checkpoint tests.

## Read-only Service Catalog input diagnosis and local reconciliation, 2026-10-23

This task sent no live preview, create, apply, save, edit, delete, or lifecycle request.
Only exact read-only Studio definition/snapshot GETs used normal OS-cached OAuth against `tv2dev`; no update-set, session, old fixture, or LogView reads occurred.
The retained fixture remains `ca324deac30c9b905cf89fcd2b01315d`. Its historical one-shot result remains `UNKNOWN_OUTCOME`.
The historical full PUT and echo were not retained outside the old child process. The saved operation journal retains the empty-request/two-returned-input count and the verified-empty header progress.
The new sanitized fixture is the exact later GET's complete input DTO projection, not a substitute historical echo.

The two concrete flow-owned declarations are:

| Name           | Type         | Reference/default                         | Native metadata                                    |
| -------------- | ------------ | ----------------------------------------- | -------------------------------------------------- |
| `table_name`   | `table_name` | Default and display default `sc_req_item` | Hidden test input, nonmandatory, maximum size 40.  |
| `request_item` | `reference`  | Reference `sc_req_item`, empty default    | Mandatory, maximum size 32, search field `number`. |

Both have native declaration `readonly=false`, separate assigned database IDs, and mapper/UI attributes.
Their IDs differ from the trigger-output parameter IDs. After removing only those new IDs and applying the captured native declaration projection,
the independently retained catalog's table-name and reference output parameters exactly match both complete observed input DTOs.
The projection preserves all other catalog metadata. Its field names are selected from metadata; a renamed-catalog regression verifies that no Service Catalog field names are hardcoded.

The ownership timing is established by the successful empty-header verification in creation.py:107-115, followed by the full save and two returned flow inputs.
They are backend-materialized call inputs for the newly configured trigger, not unexpected authored subflow inputs.
Official local `create-sc-flow.md:27` identifies the requested item as the trigger record.
The trigger catalog creates output parameter bindings at 58112-58179. The client runtime/test selector reads these parameter types, references, defaults, and hidden-field metadata (63325-63380, 8031-8058).
Crucially, `createFlowInputs` at 184408-184414 includes only REST/external triggers; the Service Catalog type does not satisfy either predicate (106624-106625, 148726-148734).
The fix does not claim this unrelated frontend adapter constructs the two Service Catalog declarations.

Local verification now uses a narrow backend-generated-input expectation bound before the PUT to the actual selected catalog definition.
The initial header-empty guard is unchanged. During echo and GET checks, the exact `sys_hub_flow_input` records must verify ID, `model` owner, and catalog-supplied name before generated IDs enter comparison.
The entire expected declaration DTO still compares strictly. Ordinary `compare_saved` without this bound expectation still rejects zero-to-two input changes.
Echo-to-GET identity changes, extra/missing inputs, type/reference/value/default changes, unknown attributes, wrong owners, and unrelated setting changes reject.
Later edits preserve saved declarations and opaque attributes. Different-trigger complete creation and authored triggerless subflow interfaces do not use this allowance.
The input table is included in the existing write-policy gate for Service Catalog trigger operations; no update/delete permission was added.

The captured input DTOs now drive backend behavior in complete-create/edit HTTP fixtures and all four real local Python 3.15 stdio mock-HTTP variants.
A nine-edit/seven-step regression verifies the generated interfaces, variable pill, trigger condition, owner reads, and subsequent preservation.
Two older passthrough save mocks failed because they incorrectly kept the interface empty. They now materialize the captured native records without weakening their assertions.
Focused combined checks passed 271 tests; final targeted editing/input/stdio checks passed 70 tests.
Final code gates passed `mise run check`, `mise run test`, and `mise run schemas`: 3561 passed, 66 deselected.
The isolated staged checkpoint passed 321 focused editing, complete-create, trigger, DTO, cache, and stdio tests.

### Remaining fresh-instance DTO evidence

The bounded pass also compared fresh current constructors with the same native seven-step GET, using an exact independently read published Log snapshot and the retained trigger catalog.
Logic definitions and persisted identities were joined from that GET solely to expose shape differences. This is reconstruction, not the unavailable historical request/echo proof.
It found 944 differences: 934 missing-field forms, six value differences, and four numeric-order representation differences.
They group into fresh Log DTOs (757 repeated differences across four instances), If (89), Else (32), and the trigger (66). The existing fresh assignment constructor had no differences in that reconstruction.
Examples include editor descriptor fields versus seven-field stored bindings, fresh `isNew`/`isDirty` fields versus native defaults, embedded action-status/definition DTO projections, and trigger wrapper fields.
These differences are not normalized by the input fix. Their exact root cause is the fresh-instance path: `calls.step` builds editor state, while `_native_existing_step` has no saved prototype on an empty created flow; `tree.new_logic_step` and `new_trigger` also emit editor forms.
The complete captured-input HTTP regression does not certify these other native instance forms.
This is a concrete remaining serialization gap, not a reason to issue another blind create or to ignore unknown metadata.
The existing live fixture and prior successful incremental fixture received no writes during this task. Main integration remains withheld until the complete native one-shot path is actually verified.

## Complete native DTO gate, 2026-10-23

`flow_full_native.json` retains one complete sanitized seven-step native GET and independently read published Log, logic, and Service Catalog definitions.
It comes from the retained revision-1 fixture. IDs and user identities are synthetic. It is not the unavailable historical request or echo.
The earlier 944-difference reconstruction remains historical evidence. The new exhaustive constructor comparison covers all ACTION, SETFLOWVARIABLES, IF, ELSE, and TRIGGER fields together.

`flows/native.py` constructs native ACTION and conditional DTOs and the Service Catalog trigger wrappers from those independent schemas.
It maps only declared nullable editor descriptor slots, retains unknown metadata, constructs the embedded status/cache DTOs, and supplies explicit native defaults.
Fresh actions no longer learn an instance profile from another saved action. Existing instances never pass through the fresh constructors.
The existing new-variable, generated-interface, root-cache, stage, handler, subflow, revision, and authorization contracts remain in place.
New IF/ELSE blocks use the same exact instance/flow/UI/definition/block ownership checks as new variable assignments.

Source paths: editor constructors at 156128-156235, 221647-221799, and 58112-58179.
Native parameter/binding fields are at 471783-471840 and 472022-472030, with trigger/action/logic DTO maps at 472278-472430.
The REST split/save/cleanup path is 130432-130460, 97521-97527, and 101697-101712.
The complete captured native getter forms establish the REST defaults and known nullable descriptor projections.
No save-comparison metadata allowance was added. The earlier narrowly scoped String presentation and root-cache rules remain the only such projections used by this fixture.

The new stateful HTTP server returns captured native instance schemas, not outgoing editor-shaped instances.
Only authored values, placement, and stable UI identities come from the request. Generated interfaces, variables, IDs, blocks, wrappers, and embedded definitions use native forms.
Its complete-create regression performs the nine edits/seven steps, then reorder/insert/update while preserving existing identities and metadata.
The real local Python 3.15 stdio matrix includes this full native response model in addition to the four earlier compatibility models.
Adversarial tests reject changed values, types, defaults, references, names, pills, order, parents, unknown metadata, declaration IDs, and block IDs.
All seven owner fields are tested independently for new assignment, IF, and ELSE blocks.

Final offline gates passed `mise run check`, `mise run test`, and `mise run schemas`: 3600 passed, 66 deselected.
The focused native/compatibility/create/stdio selection passed 225 tests. All five real Python 3.15 stdio variants passed.
The exhaustive fresh-constructor comparison found zero remaining differences after the previously approved exact String presentation projection and joins of generated identities.
Live acceptance is not established by this offline fixture. The single authorized fresh live attempt follows these green local gates.

## Final full-native live attempt, 2026-10-23

Source checkpoint `2d9eb85` was SSH-signed and verified. Its isolated checkpoint passed the same 225 focused tests.
The new inert fixture was `MCP Live - Full Native Acceptance 20261023T095634972995Z`, Global scope, exact `https://tv2dev.service-now.com`.
A fresh local Python 3.15 stdio server used the full package and normal OS-cached OAuth.
One complete preview verified all nine edits, seven steps, two generated interfaces, one String variable, both pill paths, branches, and six aliases.
One fresh apply token was consumed once. Native freshness checks made three bounded exact-name queries, with no extra manual conflict query.
There was one properties POST and one definition PUT, both HTTP 200. The created empty header was verified.

The actual apply result was `UNKNOWN_OUTCOME`, during `request.echo` input ownership verification.
The retained flow is `b05415aa87cc9f508643202cbbbb35ab`, revision 1.
No write was replayed. No second flow or post-create edit was attempted.
The single read-only flow reconciliation observed valid/complete local validation with no diagnostics.
The same-session observer established echo/GET equality, seven steps, four Logs, three logic/assignment nodes, one variable and one Service Catalog trigger.
Orders, branches, variable/condition pills, and mirrored assignment bindings were correct. The flow remained inactive and unpublished.
This later read does not turn the failed apply into success. Independent contract/status calls were not run after the failure.

| Alias        | Saved instance ID                  | UI identity                            |
| ------------ | ---------------------------------- | -------------------------------------- |
| Assignment   | `2d541daa87cc9f508643202cbbbb3575` | `aa915969-2a9a-4866-901f-c68c7d686bc7` |
| `start`      | `61541daa87cc9f508643202cbbbb3578` | `b26d1ece-1a0a-4496-b0c9-20bb056aa14e` |
| `has_number` | `65541daa87cc9f508643202cbbbb357a` | `d2027724-2892-4621-a240-0c2cf76b9aa8` |
| `then_log`   | `69541daa87cc9f508643202cbbbb357b` | `e48a7f9e-d1ce-4994-b226-a54e71e1a67e` |
| `otherwise`  | `6d541daa87cc9f508643202cbbbb357c` | `985993e8-a0e7-4c23-a79d-5888147bb14a` |
| `else_log`   | `29541daa87cc9f508643202cbbbb357d` | `a82822ef-6da3-4e41-841c-5a3d12d9e2ce` |
| `finish`     | `65541daa87cc9f508643202cbbbb357e` | `dede3a1f-d8ea-4e3c-a523-5df065ee4a8a` |

### Exact remaining ownership blocker

The first generated declaration was expected to have technical name `table_name`.
The exact queried input record was `sys_hub_flow_input/2154d9aa87cc9f508643202cbbbb35df`.
One further exact read of that already queried input record, not another flow read, established:

- `sys_id` matches the assigned declaration ID.
- `model` matches target flow `b05415aa87cc9f508643202cbbbb35ab`.
- `sys_class_name` is `sys_hub_flow_input`.
- The stored record's `name` differs from the catalog declaration's technical name. Its raw value is not included in the evidence report.

The named failure is therefore **actual `sys_hub_flow_input.name` versus expected declaration name**, not an unknown DTO index or a model-owner mismatch.
The strict naming assumption introduced in `f8c90e7` is not established by the actual storage record.
The guard remains fail-closed. A storage-name mapping must be independently established before that check can change.
The second generated input's owner read and the three logic-block owner reads were not reached in this apply.

### Writable trigger label correction after the stopped attempt

The observer retained every named request/echo structural difference without logging payload values.
Beyond the previously approved generated identities, UI tree fields, cache getter, numeric order forms, and String presentation slots,
it found `triggerInstances[0].inputs[run_flow_in].parameter.uiDisplayTypeLabel` present in the expected native DTO but absent in the response.
The constructor sent a getter-only alias instead of the writable parameter `type_label` declared at 471829.
The older captured native getter contained this label because the editor-shaped input supplied the writable field.

The local correction maps that one label slot back to `type_label` on the REST wire, leaving the expected native DTO and strict comparison intact.
Conflicting copies reject before saving. Active adapters and other trigger types are unchanged.
The stateful model now derives this getter field from the submitted writable label, so the complete roundtrip reproduces the failure without that correction.
The regression first failed at the exact missing trigger field, then passed with the wire correction.
Input-owner failures now name the failing owner field, including `.owner.name`, instead of reporting every owner mismatch at `.id`.
No further live write was used to test either change. Main readiness remains blocked by the actual failed one-shot result and the unresolved storage-name contract.

Final retained-source gates passed `mise run check`, `mise run test`, and `mise run schemas`: 3603 passed, 66 deselected.
The final focused selection passed 278 tests, including all five real local Python 3.15 stdio variants and the complete native create/reorder/insert/update path.
The trigger-label conflict regression also proves rejection before an effect is attempted, without misclassifying it as an unknown save outcome.
The isolated final staged checkpoint also passed all 278 focused tests. The 14 unrelated tracked changes and four untracked files were excluded from both signed checkpoints.

## Input storage-name correction without live writes, 2026-10-23

This task used read-only local Python 3.15 stdio, the full package, and normal OS-cached OAuth against exact `https://tv2dev.service-now.com`.
It read only the retained fixture `b05415aa87cc9f508643202cbbbb35ab`, its two exact input records, and the definitions needed for local validation.
No preview, apply, flow write, update-set query, activation, publication, or old-fixture operation occurred.
Local validation again reported valid/complete without diagnostics.

Both exact records have `name=var__m_sys_hub_flow_input_b05415aa87cc9f508643202cbbbb35ab`.
That value identifies their common virtual table. The technical declaration name is in `element`:

| Position | Catalog/DTO name | Table API `element` | Exact input record ID              |
| -------- | ---------------- | ------------------- | ---------------------------------- |
| 0        | `table_name`     | `table_name`        | `2154d9aa87cc9f508643202cbbbb35df` |
| 1        | `request_item`   | `request_item`      | `5554d9aa87cc9f508643202cbbbb35d9` |

Each record's `sys_id` equals its Studio declaration ID. Each `model.value` equals the target flow ID, and each class is `sys_hub_flow_input`.
The `model` response is a reference object, not a scalar string. The existing verifier already unwraps its `value` correctly.
Both labels also match the respective DTO labels. Zip order is correct, despite both records having the same virtual-table `name` and order 100.
The retained same-session echo/GET equality covers those declaration names. The new bounded read supplies the exact current DTO names and storage records, without claiming a new echo or changing the historical API result.

The mapping is already explicit in the repository's contract adapter: `flow._contract_field` uses `row.element` for `name` (flow.py:1037-1051).
The platform input reader selects by `model` (`_client_flow.py:282-288`), while the Studio DTO field map uses parameter `name` (bundle:471812).
The concrete two-record capture confirms the distinction. No dictionary query or broad platform audit was necessary.
This was a false cross-contract equality, not a swapped declaration, wrong owner, display-value conversion, or unwanted business change.

`_bind_trigger_inputs` now checks exact record ID, `model`, `element`, and class. It no longer mistakes the virtual-table name for a declaration name.
The independently bound catalog still supplies every expected input DTO field. The entire DTO, including name, label, type, order, default, reference, value and unknown metadata, still compares strictly.
Echo and GET assigned identities remain strict. No fallback accepts a missing `element` or arbitrary owner.

The complete native fixture now contains the latest actual seven-step GET, the two exact owner records, and the independently read definitions, with consistent synthetic IDs.
The stateful server uses those owner records independently of its outgoing DTOs, so swapping a DTO ID cannot manufacture a matching owner record.
The original `777a618` owner check was reproduced offline against this capture and failed at `flow.inputs[0].owner.name`.
With the corrected mapping, both actual declarations bind and the complete comparison passes.

The exhaustive constructor comparison accounts for all ACTION, SETFLOWVARIABLES, IF, ELSE, and TRIGGER fields.
The retained pre-777a618 response still lacks `triggerInstances[0].inputs[0].parameter.uiDisplayTypeLabel`.
That historical difference is asserted explicitly. The prospective response reconstructs only this getter slot from the corrected writable `type_label` field (bundle:471829).
No comparator ignores the missing field or changes the retained GET. All other captured constructor fields compare without differences, using the existing source-proven identity/presentation rules.
The current complete-create HTTP/stdio case verifies request/echo, request/GET, and echo/GET under that corrected wire behavior, followed by reorder/insert/update.
Negative cases reject swapped inputs, duplicate names/IDs/elements, changed order, extra records, wrong owners/classes/IDs, missing elements, and semantic or unknown DTO metadata drift.

The historical one-shot remains `UNKNOWN_OUTCOME`. A passing reconstructed offline roundtrip is not a live apply PASS.

Final local gates passed `mise run check`, `mise run test`, and `mise run schemas`: 3625 passed, 66 deselected.
The focused command below passed 300 tests, including all five real local Python 3.15 stdio variants.
The same 300 tests also passed against the isolated staged checkpoint, without the 14 unrelated tracked changes or four untracked files.

```sh
.venv/bin/python -m pytest -q --no-cov --tb=short tests/test_flow_input_ownership.py tests/test_flow_full_native.py tests/test_flow_creation_stdio.py tests/test_flow_trigger_inputs.py tests/test_flow_variable_dto.py tests/test_flow_native_compat.py tests/test_flow_complete_creation.py tests/test_flow_editing.py
```

## Actual one-shot create and three-way verification PASS, 2026-10-23

The new retained fixture is `MCP Live - One Shot Verified 20261023T103201316019Z`:
flow `4c7c19e28740df508643202cbbbb353d`, revision `1`, Global, inactive draft, no published snapshot.
A fresh local Python 3.15 stdio server used source HEAD `7422a65`, the unchanged existing worktree modifications, the full package, and normal OS-cached OAuth.
The instance was exactly `https://tv2dev.service-now.com`. The fresh native preview schema omitted `expected_update_set`.

One complete create preview checked nine edits, seven nodes, branch indentation, six aliases, scalar types, both pill paths, generated inputs, and inactive state.
One fresh apply token was consumed once in the same child session.
There was exactly one `POST /api/now/processflow/flow` and one `PUT /api/now/processflow/flow`, both HTTP 200.
The native path performed three bounded exact-name freshness queries. There was no extra manual conflict query or duplicate creation.
The observer recorded returned effects without assertions inside response callbacks.

The actual `mutation_apply` result was **success**, with the returned flow ID, revision, and all six persisted ref mappings below.
The application verifier completed **request/echo, request/independent GET, and echo/GET** comparisons before that success returned.
This is an actual API success, not a reconstruction or a later read used to reclassify an unknown outcome.
All earlier unknown outcomes remain historical. No old fixture received a write or replay.

The saved definition has seven nodes: four Logs, Set Flow Variables, If, and Else.
It has one String variable, one assignment, one Service Catalog trigger, and two generated inputs.
Orders, branch parents, the variable-consuming start Log, the Requested Item number condition, and mirrored assignment bindings all matched the request.

| Ref          | Returned instance ID               | Returned UI identity                   |
| ------------ | ---------------------------------- | -------------------------------------- |
| `start`      | `347c59e28740df508643202cbbbb3520` | `ada4397d-9f42-4f7c-b8b5-6f7017c909d9` |
| `has_number` | `387c59e28740df508643202cbbbb3522` | `f3059a9b-95bb-4f4d-bb52-6d39c61b967e` |
| `then_log`   | `3c7c59e28740df508643202cbbbb3523` | `b47be114-102f-468b-a465-eeae328d0061` |
| `otherwise`  | `307c59e28740df508643202cbbbb3525` | `059c2875-5f74-48c3-876a-f419fe1487eb` |
| `else_log`   | `f87c59e28740df508643202cbbbb3525` | `d409144a-39a3-47b0-85d7-859bd0512c30` |
| `finish`     | `387c59e28740df508643202cbbbb3526` | `a2fa5896-015d-4006-b087-b110d8073b84` |

The input IDs were `3c7c19e28740df508643202cbbbb35cb` (`table_name`) and `bc7c19e28740df508643202cbbbb350b` (`request_item`).
Their native metadata matched the independently bound catalog: hidden Table Name default `sc_req_item`, maximum size 40;
mandatory Requested Item reference `sc_req_item`, maximum size 32, search field `number`.
The normal verifier checked each exact record's ID, model owner, `element`, and class in both echo and GET phases.
All four input-owner checks and all six assignment/conditional-block owner checks passed. Echo/GET assigned IDs matched exactly.

Independent local validation returned `valid=true`, `complete=true`, with no diagnostics.
Independent status returned `complete=true`, revision `1`, `active=false`, and `has_published_snapshot=false`.
The bounded independent contract returned seven steps, one variable, two inputs, and exact complete structural counts.

### Contract reader limitation, not a save mismatch

The contract did **not** establish complete trigger-activation metadata.
Its top-level `truncation.triggers.incomplete_metadata` reports: `Trigger active status is missing or invalid. The flow header does not establish trigger activation.`
`structural_summary.complete=true` covers counts only. `structural_summary.metadata_complete=false` must not be reported as a fully complete contract.
A bounded read-only repeat confirmed this same field limitation. No write followed it.

The acceptance helper initially tested `data.truncation` instead of the response's top-level `truncation`.
Its final journal marker therefore establishes the actual successful creation and three-way comparison, not complete contract metadata.
The API success and three comparison results are independently recorded before that helper check.
This report keeps the contract limitation explicit and leaves its main-readiness assessment to the caller.
Flow-root inactivity and absence of publication were verified separately. Unknown trigger activation is not normalized to false.

### Coverage and integration candidate

Together, this case and the earlier actual incremental PASS on `c1b4c9da874cdb508643202cbbbb35b8`, revision `4`, cover complete native creation and incremental save/readback.
The earlier fixture retained nine nodes, five Logs, two variables and two assignments after its successful message edit.
No workflow execution or publication was attempted in either acceptance path.

Unchanged root-source checks from the prior task remain applicable: 3625 tests passed, 66 deselected, plus 300 isolated focused tests.
No application source changed for this live attempt or report.

An isolated candidate was created on `feat/flow-authoring-integration` from local main `fd937711416e42d15f050d396bc109f9c794a76f`.
`git merge --ff-only 7422a6568adbd8cfb2d5b71eba4ff76dc782ad3c` advanced only that candidate branch, not main.
The candidate excludes all 14 unrelated tracked modifications and four untracked files.
Its Python interpreter is a link to the existing installed environment. No dependencies were installed or synced.
`UV_NO_SYNC=1`, `UV_FROZEN=1`, `UV_OFFLINE=1`, `UV_NO_ENV_FILE=1`, disabled Python downloads, and `MISE_AUTO_INSTALL=0` prevented environment resolution or installation.
Mise trust was scoped to this shell's candidate path. No global config or credential configuration changed. The candidate did not copy credential files.

Before checks, candidate module imports and their Git blob identities were verified for editing, native DTOs, semantics, and the full-native tests.
`uv run` independently confirmed that it imported candidate source rather than the original editable checkout.
From that candidate directory, **`mise run check`, `mise run test`, and `mise run schemas` passed**: 3496 tests passed, 66 deselected.
Generated schemas left no tracked diff. Candidate tracked files were clean. Its only untracked item was the owned `.venv` environment link.
The original main ref, unrelated work, stashes, and safety refs remain unchanged. No push, PR, or main merge was performed.

## Activation ownership correction and read-only contract verification, 2026-10-23

The fix starts from `b30c861` on `fix/flow-trigger-active-readback` in the isolated integration worktree.
The original worktree's 14 tracked modifications and four untracked files are excluded and unchanged.
The existing installed environment is reused with sync, downloads, and automatic installation disabled.

### Physical metadata and source evidence

A bounded local stdio probe read the existing verified flow `4c7c19e28740df508643202cbbbb353d`, its one V2 trigger, the relevant table hierarchy, and only dictionary rows for `active` in that hierarchy.
The exact trigger was `fc7c59e28740df508643202cbbbb3512`, type `service_catalog`, with its `flow` reference matching the requested parent.
Its full Table API row has no `active` field. The Studio trigger DTO also has no such field.
`sys_db_object` reports no superclass for `sys_hub_trigger_instance_v2` or `sys_hub_trigger_instance`.
The only `active` dictionary row in the bounded hierarchy belongs to `sys_hub_flow_base`, with Boolean type, inherited by `sys_hub_flow`.
This is not an omitted field projection or an inferred false value.

Local Studio source constructs the trigger without an activation field (58126-58137) and omits it from the trigger save schema (472278-472291).
The separate flow schema owns activation. The official local `flow-activate.md:27` states that flow activation makes the flow available to run.
`create-sc-flow.md:75-85` separates flow activation from catalog-item association.
No definition-active flag or catalog-item association was used to invent an instance state.

### Correct read semantics

Native V2 Service Catalog and classic scheduled configurations now expose `activation.mode="flow_managed"`.
`active=null` and `active_source=null` remain unchanged: the instance has no independent switch.
`activation.instance_active_applicable=false` makes this distinction explicit.
The separately named `activation.flow_active` is read from the exact owner header, with `activation.flow_sys_id` and source `flow.active`.
The flow reference, header identity, and flow type must agree before that owned parent state is exposed.
Parent enablement is not an effective-trigger or execution-readiness certificate.

Explicit readable instance flags retain their existing source. Remote V2 record-trigger flags retain `record_trigger.active`.
A present null/invalid flag never falls through to an inferred parent value. Missing V1 flags now remain unknown rather than silently becoming false.
Missing parent state, inconsistent ownership, unreadable definitions, malformed bindings, row/field caps, and unmodeled activation families retain incompleteness warnings.
The flow header's own missing or invalid Boolean also remains null.
No truncation or schema-completeness rule was relaxed.

### Executed read proof and tests

Four baseline regressions first failed because the actual native row was incorrectly marked incomplete for a nonexistent instance flag.
The sanitized trigger row and table/dictionary proof are retained in `tests/fixtures/flow_trigger_activation.json`.
Regressions cover absent/null/true/false and malformed flags, parent-active/inactive boundaries, owner mismatches, V1 flags, V2 remote record flags, native schedules, definition-state separation, and preserved metadata limits.
A real local Python 3.15 stdio smoke test reads a native contract through captured mock HTTP responses, with network/browser access denied and no writable endpoint.

A fresh read-only stdio session then used the isolated fix source and normal OS-cached OAuth against exact `https://tv2dev.service-now.com`.
The actual contract for the same verified flow returned:

- `status=success`, no top-level `truncation`, and no warnings.
- One Service Catalog trigger with `active=null`, `active_source=null`, `activation.mode="flow_managed"`, and `activation.flow_active=false`.
- Exact owner `activation.flow_sys_id=4c7c19e28740df508643202cbbbb353d`, source `flow.active`, and `instance_active_applicable=false`.
- Trigger `metadata_complete=true`. Structural summary `complete=true`, with seven nodes, four actions, three logic nodes, and one trigger.
- Two inputs and one variable. Summary `metadata_complete=null` remains the documented structural-counts-only convention, not a global certificate.

Independent status remained complete at revision `1`, inactive, with no published snapshot.
There were zero flow POSTs/PUTs, no preview/apply, and no activation, deactivation, publication, execution, or update-set operation.
No timeout retry or interactive browser authorization was needed. The earlier actual authoring PASS is unchanged.

Focused read/model/stdio checks passed 331 tests.
The isolated worktree passed `mise run check`, `mise run test`, and `mise run schemas`: 3562 passed, 66 deselected.
The generated tool schemas left no diff. No dependency or global configuration change was required.
