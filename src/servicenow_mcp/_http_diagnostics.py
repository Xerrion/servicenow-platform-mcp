"""Bounded HTTP evidence without query values, bodies, or unrestricted headers."""

import re

import httpx2
from pydantic import ValidationError

from servicenow_mcp._json import JSON
from servicenow_mcp._rest_auth_evidence import safe_trace_header


_MAX_INSPECTION_CHARS = 8192
_MAX_BODY_BYTES = 8192
_OMITTED = "[omitted: unrecognized or unsafe]"
_INTEGER = re.compile(r"-?[0-9]{1,9}")
_TEXT_SEARCH = re.compile(
    r"(?:^|\^)(?:OR|NQ)?(?:123TEXTQUERY321|IR_AND_QUERY|IR_OR_QUERY|IR_AND_OR_QUERY)(?:=|!=|LIKE)"
)
_INTEGER_PARAMETERS = ("sysparm_limit", "sysparm_offset")
_CHOICE_PARAMETERS = {
    "sysparm_display_value": frozenset({"true", "false", "all"}),
    "sysparm_suppress_pagination_header": frozenset({"true", "false"}),
    "sysparm_no_count": frozenset({"true", "false"}),
    "sysparm_count": frozenset({"true", "false"}),
    "sysparm_exclude_reference_link": frozenset({"true", "false"}),
}
_CONTENT_TYPES = frozenset(
    {"application/json", "text/html", "text/plain", "application/xml", "text/xml", "application/octet-stream"}
)
_SAFE_ERROR_TEXT = {
    text.casefold(): text
    for text in (
        "Pagination not supported",
        "Request failed",
        "Invalid query",
        "Invalid table",
        "No records found",
        "Record not found",
        "Resource not found",
        "Not found",
        "Access forbidden",
        "Access denied",
        "User Not Authorized",
        "User Not Authenticated",
        "Unauthorized",
        "Authentication failed",
        "Insufficient scope",
        "Failed API level ACL Validation",
        "Rate limit exceeded",
        "Too many requests",
        "ServiceNow server error",
        "Server error",
        "Internal server error",
    )
}


def request_diagnostics(request: httpx2.Request) -> dict[str, object]:
    """Describe transmitted controls and query features, never their search values."""
    params = request.url.params
    controls: dict[str, int | str] = {}
    for name in _INTEGER_PARAMETERS:
        values = params.get_list(name)
        if values:
            controls[name] = int(values[0]) if len(values) == 1 and _INTEGER.fullmatch(values[0]) else _OMITTED
    for name, choices in _CHOICE_PARAMETERS.items():
        values = params.get_list(name)
        if values:
            controls[name] = values[0] if len(values) == 1 and values[0] in choices else _OMITTED

    queries = params.get_list("sysparm_query")
    query: dict[str, object] = {"present": bool(queries)}
    if len(queries) > 1:
        query["inspection"] = "omitted: duplicate query parameters"
    elif queries:
        value = queries[0]
        query["character_count"] = len(value)
        if len(value) > _MAX_INSPECTION_CHARS:
            query["inspection"] = "omitted: query exceeds 8192 characters"
        else:
            clauses = value.split("^")
            query.update(
                {
                    "inspection": "shape only",
                    "clause_count": len(clauses),
                    "has_text_search": bool(_TEXT_SEARCH.search(value)),
                    "has_javascript": "javascript:" in value.casefold(),
                    "has_or": any(clause.startswith("OR") and not clause.startswith("ORDERBY") for clause in clauses),
                    "has_new_query": any(clause.startswith("NQ") for clause in clauses),
                    "has_ordering": any(clause.startswith("ORDERBY") for clause in clauses),
                }
            )
    return {"parameters": controls, "query_summary": query}


def _safe_error_text(value: object, sensitive_values: tuple[str, ...]) -> str:
    if not isinstance(value, str) or len(value) > 160:
        return _OMITTED
    text = _SAFE_ERROR_TEXT.get(value.strip(" ").removesuffix(".").casefold(), _OMITTED)
    if any(secret and secret.casefold() in text.casefold() for secret in sensitive_values):
        return _OMITTED
    return text


def response_diagnostics(response: httpx2.Response, sensitive_values: tuple[str, ...]) -> dict[str, object]:
    """Retain static error phrases, response shape, and one vetted transaction ID."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    result: dict[str, object] = {
        "content_type": content_type if content_type in _CONTENT_TYPES else "other",
        **safe_trace_header(response, sensitive_values),
    }
    if not hasattr(response, "_content"):
        result["body_format"] = "omitted: unread stream"
        return result

    result["body_bytes"] = len(response.content)
    if len(response.content) > _MAX_BODY_BYTES:
        result["body_format"] = "omitted: body exceeds 8192 bytes"
        return result
    try:
        payload = JSON.validate_json(response.content)
    except ValidationError:
        result["body_format"] = "non-JSON or malformed"
        return result

    result["body_format"] = "JSON"
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        result["error"] = {
            name: _safe_error_text(payload["error"][name], sensitive_values)
            for name in ("message", "detail")
            if name in payload["error"]
        }
    return result
