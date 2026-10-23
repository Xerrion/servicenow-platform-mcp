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
