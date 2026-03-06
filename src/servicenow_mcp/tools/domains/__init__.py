"""Domain-specific ServiceNow tool modules.

This package contains higher-level, intent-driven tools organized by
ITIL domain (Incident, Change, CMDB, Problem, Request, Knowledge).

Each domain module provides a curated set of tools with explicit parameters
for common workflows, complementing the generic table tools.
"""

from servicenow_mcp.tools.domains import change, cmdb, incident, knowledge, problem, request, service_catalog


__all__ = ["change", "cmdb", "incident", "knowledge", "problem", "request", "service_catalog"]
