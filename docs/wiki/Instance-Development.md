# Investigate an instance

Your AI app can help you inspect configuration and investigate issues. Its
answers depend on the access granted to your ServiceNow account.

## Questions to try

Use names from your instance in these examples.

- "Find scripts that contain this function name. Show where each match occurs."
- "Inspect the trigger and saved steps for the New starter flow."
- "Investigate recent script errors and show the evidence for each finding."
- "Show this configuration item's details and relationships."

The flow tool inspects saved information. It cannot edit or run flows. A finding
is a lead to investigate, not proof of a root cause. Check the source record and
the time range before making a decision.

## Prepare a configuration change

Work in a development or test instance first. Ask the app to read the existing
record and show the full proposed change. Review it and tell the app to wait
for your approval. Applying a change requires write tools and ServiceNow access.
The server does not verify human approval.

Script changes replace the selected field value. Review and test the entire
replacement script. See [Safety and Policy](Safety-and-Policy.md) for more on
preview, applying, and uncertain results.
