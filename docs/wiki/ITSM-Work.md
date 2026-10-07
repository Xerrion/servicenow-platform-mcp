# ITSM work

Ask your AI app to read one ticket you already know.
Use its number so you can check the answer in ServiceNow.
Replace the example ticket numbers and group names with values from your instance.

You need a working connection and access to the relevant records.
Start with [[Getting-Started]] if you have not completed a ServiceNow read.
The `readonly` package supports the reading tasks on this page.

## Find the tickets that need attention

Give the app a record type, filter, and result limit:

> Show 10 active incidents assigned to the Service Desk group, ordered by
> priority. Include number, short description, state, and assigned person.
> Show the filters you used. Do not change anything.

You can also read problem, change, request, requested item, and catalog task records.
State which record type you mean. A request and its requested items are separate records.

For a status count:

> Count active incidents by assignment group. Include the filters used and
> explain any limits or warnings in the result.

A short list is a sample, not a total count.
ServiceNow permissions determine which records the app can access.
Check the filter and any result warnings before using a count in a report.

## Understand a ticket and its history

For a ticket summary:

> Read INC0012345. Summarize the issue, current state, priority, and assignment.
> Include comments and work notes from the last 30 days. Do not change anything.

For a specific question about changes:

> Show the available audit history for assignment and priority changes on
> INC0012345 during the last 30 days. Include who changed each value and when.

Comments and work notes are journal entries.
Audit history records field changes where auditing is available.
These are separate sources, so specify which one you need.

History can be incomplete because of access rules, retention, result limits, or audit configuration.
Ask the app to state the period and any omissions.
An empty history does not prove that a record never changed.

## Review a requested item

Use the requested item number, usually beginning with `RITM`:

> Show the submitted answers for RITM0012345. Include question labels and values.
> Explain any missing or unsupported answers. Do not change anything.

The server reads submitted variables and masks answers identified as sensitive.
Some variable types have limits, including multi-row variable sets.
See [[Tool-Reference]] for the `analysis` actions and their limits.

## Inspect attachments

Start with the file list:

> List the attachments on INC0012345. Include file names, sizes, and attachment IDs.
> Do not upload or delete anything.

Ask for a specific file after checking the list.
The server can return attachment content, but your AI app determines which file types it can interpret.
Attachment downloads have a 10 MiB limit.

ServiceNow data returned by tools becomes available to the app.
Use an approved app for ticket content and attachments.

## Prepare a ticket change

Creating a ticket, adding a work note, or changing an assignment needs write tools and ServiceNow permissions.
Ask your administrator to enable the required tools for a development or test instance first.
Read [[Safety-and-Policy]] for the controls that apply to writes.

Record writes default to a preview, but callers can request immediate writes.
The server does not enforce human approval. Use this review process for a record change:

1. Ask the app to read the current ticket.
2. Ask for a preview of the proposed change.
3. Check the instance, ticket number, and exact field values.
4. Tell the app to apply the preview when you approve it.
5. Read the ticket again and check the result in ServiceNow.

For example:

> Preview adding this work note to INC0012345: "Waiting for the caller to confirm
> the fix." Show the proposed change and wait for my approval.

For an assignment change:

> Preview assigning INC0012345 to the Service Desk group. Resolve the group first.
> Show the current assignment and proposed assignment. Wait for my approval.

Your instance can require additional fields or use custom state values.
Ask the app to inspect the table's fields and choices when a required value is unclear.
Record updates can also trigger configured ServiceNow automation.

Previews expire after five minutes and disappear when the server restarts.
If a preview expires, prepare and review a new preview before applying it.

## Catalog orders and attachment changes

Catalog ordering is separate from reading request records.
The `service_catalog` group includes both browsing and ordering and is absent from `readonly`.
It is available through `full` or a custom package that includes that group.

Catalog orders, cart changes, attachment uploads, and attachment deletions apply directly.
They do not use record previews.
Check the target, requested item or file, and intended action before authorizing these operations.

Use [[Tool-Packages]] to choose the available tools.
Use [[Tool-Reference]] when you need exact inputs or action limits.
