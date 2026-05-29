# Security Policy

**servicenow-platform-mcp** is an MCP server that connects AI clients to ServiceNow instances over the Table API. It handles credentials, enforces access policy, and mediates reads and writes against production data.

## Supported Versions

| Version | Status |
|---------|--------|
| 0.10.x (latest on `main`) | Supported |
| Older minor releases | Critical fixes at maintainer discretion |

## Reporting a Vulnerability

**Do not file a public GitHub issue for security bugs.**

Preferred channels, in order:

1. **GitHub private security advisory** - open one at <https://github.com/Xerrion/servicenow-platform-mcp/security/advisories/new>
2. **Email** - send details to `security@<TODO>` (maintainer: replace with your security contact address)

### What to Include

- Steps to reproduce
- Affected version(s)
- Impact assessment (what an attacker gains)
- Suggested fix, if you have one

### Response Timeline

- Acknowledgment within **5 business days** (subject to maintainer availability)
- We will coordinate disclosure timing with you before any public announcement

## Threat Model

Key trust boundaries specific to this project:

- **ServiceNow user privileges** - the MCP server authenticates as the configured ServiceNow user. All platform operations inherit that user's roles and ACLs. Restrict that user to least-privilege.
- **Untrusted AI prompts** - any MCP client can invoke any tool the active package exposes. Limit exposure with `MCP_TOOL_PACKAGE` (e.g., `readonly` or `core_readonly`) and set `SERVICENOW_ENV=production` to enable write gating.
- **Credentials in environment** - instance URL, username, and password live in env vars or `.env.local`. Never commit them.
- **Defence-in-depth layers** - the denied-tables list, field masking, and write gating are server-side guardrails. They do not replace the platform's own ACLs, which remain the final authority.

## Out of Scope

The following should be reported to the respective upstream maintainers, not here:

- Vulnerabilities in upstream Python dependencies (report to the dependency project)
- Vulnerabilities in ServiceNow itself (report via ServiceNow's HI portal or responsible disclosure program)
- Denial of service caused by misconfiguration (overly broad query limits, etc.)
