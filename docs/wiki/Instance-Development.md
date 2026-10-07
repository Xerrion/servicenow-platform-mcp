# Instance administration and development

Start with a question about a table, rule, script, or flow in your ServiceNow instance.
Your AI app can inspect records and explain what it finds.
You do not need to write API requests or know the MCP tool names.

You need a working connection and ServiceNow permissions for the relevant configuration records.
Complete [[Getting-Started]] first if you have not checked access.
The `readonly` package includes the inspection and investigation tools used below.

This work changes or explains your ServiceNow instance.
For changes to the Python MCP server itself, use [[Development]].

## Understand a table before using it

Ask for the field names and meanings:

> Describe the incident table. Show its fields, field types, mandatory fields,
> reference targets, and state choices. Include inherited fields.

ServiceNow stores a choice's value separately from its display label.
A reference field points to another record.
These details matter when preparing filters or updates.
Use the instance's metadata instead of assuming a label always has the same stored value.

For a custom table:

> Describe the table u_access_request. Explain its fields in plain language.
> Identify any fields that contain scripts. Do not change anything.

Replace the table name with one from your instance.
If metadata is unavailable, ask your administrator to check access to the table and its dictionary records.

## Read and explain scripts

Use the artifact type and exact name:

> Read the Business Rule named 'Validate priority'. Show its record ID, table,
> conditions, execution timing, and complete script. Explain the behavior.
> Do not change anything.

A Business Rule runs on the server when its configured conditions and record operation match.
The operation can come from a form or a web service.
A Script Include stores reusable JavaScript that runs on the server.
Client Scripts run in the ServiceNow user interface.

The MCP can read configuration records such as Business Rules, Script Includes, and Client Scripts.
For a search across scripts:

> Find scripts that call `gs.eventQueue`. Include the record name, table, and
> matching code. Explain any search limits. Do not change anything.

If a name matches several records, list the matches first.
Select the record by its table and `sys_id`, ServiceNow's unique record ID.

## Inspect a flow

Choose an existing Flow Designer flow or subflow:

> Inspect the 'New starter' flow. Explain its trigger, declared inputs and
> outputs, and configured steps. Include warnings or missing sections.
> Do not change anything.

For automation associated with a table:

> Find flows with record triggers on incident. Show their names and available
> trigger conditions. State any unresolved or incomplete results.

Flow tools read stored configuration.
They do not edit, publish, or run flows.
Inspection does not prove that a flow executed successfully.
Check execution behavior on your instance through your usual ServiceNow workflow.

## Investigate an instance issue

State the affected table or behavior and a time period:

> Investigate script errors from the last 24 hours. Show the log evidence,
> affected records where available, and the limits of the result.
> Do not change anything.

For a slow table or automation:

> Investigate slow transactions during the last 24 hours. Show the evidence.
> Identify any findings associated with incident and any results that need further checking.

Investigations can identify errors, slow transactions, stale automation, and other findings.
They support diagnosis and do not establish a root cause or repair the instance automatically.
Ask the app to distinguish observations from suggested explanations.
See [[Tool-Reference]] for supported investigations and parameters.

## Prepare and apply a script change

Use a development or test instance for the change.
Ask your administrator to enable the required write tools and grant the corresponding ServiceNow permissions.
A focused package can include `query,describe,record_read,record_write`.
Use `SERVICENOW_ENV=dev` for that non-production connection.
The environment setting controls write protection and does not select the instance.

For production connections, `SERVICENOW_ENV=prod` or `production` blocks writes through this server.
Read [[Safety-and-Policy]] before changing write access.

Record writes default to a preview, but callers can request immediate writes.
The server does not enforce human approval. Use this review process:

1. Read the current record, including its complete script and relevant conditions.
2. Ask for an explanation and a proposed change.
3. Review a preview with the complete replacement script.
4. Apply the preview when you approve it.
5. Read the saved record again and test the behavior on your instance.

For example:

> Read the Business Rule named 'Validate priority'. Suggest a correction for
> [describe the observed problem]. Explain the change and propose checks.
> Show a preview with the complete replacement script. Wait for my approval.

Replace the bracketed text with the behavior you observed and the behavior you need.
Check the intended instance, record ID, application, conditions, and script before applying the change.
Follow your team's update-set or application delivery process for promoting changes between instances.

Script updates replace the complete field value.
The server does not check JavaScript syntax or prove that the script behaves correctly.
A successful write confirms that ServiceNow accepted the request.
It does not replace testing on the instance.

Previews expire after five minutes and disappear when the server restarts.
Prepare a new preview if the old one expires or the intended change changes.

## Check a result before relying on it

Ask the app to include record IDs, filters, time periods, and warnings with its answer.
Access rules and result limits can hide records or fields.
A missing match does not prove that the artifact does not exist.

Keep one bounded question in each request.
Inspect the evidence before widening a search or applying a change.
Use [[Tool-Reference]] for exact actions and [[Configuration]] for configured limits.
