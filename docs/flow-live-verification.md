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
