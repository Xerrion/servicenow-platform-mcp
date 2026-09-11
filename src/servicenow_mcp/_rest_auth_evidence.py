"""Bounded REST 401 evidence; unknown text is omitted, never partially echoed."""

import json
import re

import httpx


_MAX_BODY_BYTES = 8192
_MAX_HEADER_CHARS = 4096
_MAX_CHALLENGES = 4
_TRACE_HEADERS = ("x-transaction-id", "x-request-id", "x-correlation-id")
_TRACE_ID = re.compile(r"(?:[0-9a-fA-F]{8,64}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")

# ponytail: exact phrases only; add reviewed static phrases, never free-text redaction.
# Character filters cannot distinguish customer names or opaque secrets from prose.
_SAFE_TEXT = {
    text.casefold(): text
    for text in (
        "Unauthorized",
        "User Not Authenticated",
        "User Not Authorized",
        "Authentication failed",
        "Invalid token",
        "Invalid access token",
        "Access token expired",
        "The access token expired",
        "The access token is invalid",
        "The access token is expired",
        "The access token provided is expired, revoked, malformed, or invalid for other reasons",
        "Insufficient scope",
    )
}
_SCHEMES = {scheme.lower(): scheme for scheme in ("Bearer", "Basic", "Digest", "Negotiate", "NTLM")}
_ERRORS = {"invalid_request", "invalid_token", "insufficient_scope"}
_OMITTED = "[omitted: unrecognized or unsafe]"

_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_QUOTED = r'"(?:[\t !\x23-\x5b\x5d-\x7e]|\\[\t -~])*"'
_PARAM = rf"{_TOKEN}[ \t]*=[ \t]*(?:{_TOKEN}|{_QUOTED})"
_TOKEN68 = r"[A-Za-z0-9._~+/-]+=*"
_AUTH_ITEM = re.compile(
    rf"[ \t]*(?:(?P<parameter>{_PARAM})|(?P<scheme>{_TOKEN})(?: +(?P<credentials>{_TOKEN68}|{_PARAM}))?)"
    r"[ \t]*(?:,|$)"
)


def _safe_text(value: object) -> str:
    if not isinstance(value, str) or len(value) > 160 or any(not " " <= char <= "~" for char in value):
        return _OMITTED
    return _SAFE_TEXT.get(value.strip(" ").removesuffix(".").casefold(), _OMITTED)


def _message(response: httpx.Response) -> str:
    if len(response.content) > _MAX_BODY_BYTES:
        return "[omitted: body exceeds 8192 bytes]"
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError, RecursionError):
        return "[omitted: non-JSON or malformed body]"
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return "[omitted: no error.message]"
    return _safe_text(body["error"].get("message"))


def _challenges(header: str) -> list[dict[str, str]] | None:
    """Parse bounded ASCII challenges, rejecting malformed or duplicate parameters."""
    if len(header) > _MAX_HEADER_CHARS or any(char != "\t" and not " " <= char <= "~" for char in header):
        return None
    challenges: list[dict[str, str]] = []
    position = 0
    can_add_params = False
    while position < len(header):
        # HTTP list grammar permits empty members and optional whitespace.
        if header[position] in " \t,":
            position += 1
            continue
        item = _AUTH_ITEM.match(header, position)
        if item is None:
            return None
        position = item.end()
        scheme = item.group("scheme")
        parameter = item.group("parameter")
        if scheme:
            if len(challenges) == _MAX_CHALLENGES:
                return None
            challenges.append({":scheme": scheme})
            credentials = item.group("credentials") or ""
            can_add_params = re.fullmatch(_PARAM, credentials) is not None
            parameter = credentials if can_add_params else None
        elif not can_add_params:
            return None
        if parameter:
            name, value = parameter.split("=", 1)
            name = name.strip(" \t").lower()
            if name in challenges[-1]:
                return None
            value = value.strip(" \t")
            if value.startswith('"'):
                value = re.sub(r"\\(.)", r"\1", value[1:-1])
            challenges[-1][name] = value
    return challenges


def rest_auth_evidence(response: httpx.Response, sensitive_values: tuple[str, ...]) -> str:
    """Return safe 401 diagnostics, not raw bodies, headers, URLs or credentials.

    JSON error.message and challenge descriptions must match static phrases.
    Only known schemes/error codes and hex/UUID trace IDs are emitted. Known
    credentials also veto otherwise valid values. Oversized or malformed inputs
    get fixed omission markers. Raw response evidence is never logged.
    """
    evidence: dict[str, object] = {"message": _message(response)}
    headers = response.headers.get_list("www-authenticate")
    if headers:
        if sum(map(len, headers)) + len(headers) - 1 > _MAX_HEADER_CHARS:
            challenges = None
        else:
            challenges = _challenges(",".join(headers))
        if challenges is None:
            evidence["www-authenticate"] = "[omitted: malformed or oversized]"
        else:
            selected: list[dict[str, str]] = []
            for challenge in challenges:
                scheme = _SCHEMES.get(challenge[":scheme"].lower())
                if scheme is None:
                    continue
                fields = {"scheme": scheme}
                if "error" in challenge:
                    error = challenge["error"]
                    fields["error"] = error if error in _ERRORS else _OMITTED
                if "error_description" in challenge:
                    fields["error_description"] = _safe_text(challenge["error_description"])
                selected.append(fields)
            evidence["www-authenticate"] = selected
    for name in _TRACE_HEADERS:
        values = response.headers.get_list(name)
        if len(values) != 1 or len(values[0]) > 64 or not _TRACE_ID.fullmatch(values[0]):
            continue
        if any(secret and secret.casefold() in values[0].casefold() for secret in sensitive_values):
            continue
        reflection_sources = (
            response.request.headers.get_list("cookie")
            + response.headers.get_list("set-cookie")
            + list(response.request.url.params.values())
        )
        if any(values[0].casefold() in source.casefold() for source in reflection_sources):
            continue
        evidence[name] = values[0]
        break
    result = json.dumps(evidence, ensure_ascii=True)
    if any(secret and secret.casefold() in result.casefold() for secret in sensitive_values):
        return "[omitted: credential overlap]"
    return result
