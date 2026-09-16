#!/usr/bin/env python3
# ruff: noqa: T201
"""Test ServiceNow public-client PKCE and refresh-token grants with curl."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast, final, override
from urllib.parse import parse_qs, urlencode, urlsplit


DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"
DEFAULT_SCOPE = "useraccount"
DEFAULT_TIMEOUT_SECONDS = 180


def _load_dotenv(path: Path) -> dict[str, str]:
    """Read the small KEY=VALUE subset used by this project's dotenv files."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _settings() -> dict[str, str]:
    """Load dotenv settings with the same precedence as the MCP server."""
    root = Path(__file__).resolve().parent.parent
    values = _load_dotenv(root / ".env")
    values.update(_load_dotenv(root / ".env.local"))
    for key in (
        "SERVICENOW_INSTANCE_URL",
        "SERVICENOW_OAUTH_CLIENT_ID",
        "SERVICENOW_OAUTH_REDIRECT_URI",
        "SERVICENOW_OAUTH_SCOPE",
        "SERVICENOW_OAUTH_TIMEOUT_SECONDS",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def _base64url(value: bytes) -> str:
    """Encode bytes using unpadded base64url."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@final
class _CallbackServer(ThreadingHTTPServer):
    """One-shot loopback server that retains only a validated authorization code."""

    daemon_threads: bool = True

    def __init__(self, address: tuple[str, int], callback_path: str, expected_state: str) -> None:
        super().__init__(address, _CallbackHandler)
        self.callback_path: str = callback_path
        self.expected_state: str = expected_state
        self.authorization_code: str | None = None
        self.oauth_error: str | None = None
        self.completed: threading.Event = threading.Event()


@final
class _CallbackHandler(BaseHTTPRequestHandler):
    """Validate the OAuth callback without logging its query string."""

    def do_GET(self) -> None:
        server = cast("_CallbackServer", self.server)
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]

        if parsed.path != server.callback_path or not secrets.compare_digest(state, server.expected_state):
            self._respond(HTTPStatus.BAD_REQUEST, "Invalid OAuth callback. You can close this tab.")
            return
        if error:
            server.oauth_error = error
            server.completed.set()
            self._respond(HTTPStatus.BAD_REQUEST, "ServiceNow denied authorization. You can close this tab.")
            return
        if not code or "\r" in code or "\n" in code:
            self._respond(HTTPStatus.BAD_REQUEST, "Missing authorization code. You can close this tab.")
            return

        server.authorization_code = code
        server.completed.set()
        self._respond(HTTPStatus.OK, "Authorization received. Return to the terminal; you can close this tab.")

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Suppress request logging because the URL contains an authorization code."""
        del format, args

    def _respond(self, status: HTTPStatus, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _curl_quote(value: str) -> str:
    """Quote a value for curl's config-file format."""
    if "\r" in value or "\n" in value:
        raise ValueError("OAuth values must not contain line breaks.")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _curl_token_request(token_url: str, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """POST a token request through curl without placing token values in argv."""
    config_lines = [
        f'url = "{_curl_quote(token_url)}"',
        'request = "POST"',
        "silent",
        "show-error",
        'header = "Accept: application/json"',
    ]
    config_lines.extend(f'data-urlencode = "{_curl_quote(key)}={_curl_quote(value)}"' for key, value in fields.items())
    config_lines.append('write-out = "\\n%{http_code}"')
    config = "\n".join(config_lines) + "\n"

    completed = subprocess.run(
        ["curl", "--config", "-"],
        input=config,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"curl exited with status {completed.returncode}"
        raise RuntimeError(detail)

    body, separator, status_text = completed.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise RuntimeError("curl did not return an HTTP status code.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ServiceNow returned non-JSON content (HTTP {status_text}).") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"ServiceNow returned an unexpected JSON value (HTTP {status_text}).")
    return int(status_text), payload


def _safe_summary(label: str, status: int, payload: dict[str, Any]) -> None:
    """Print token response metadata without printing credentials."""
    summary = {
        "http_status": status,
        "token_type": payload.get("token_type"),
        "expires_in": payload.get("expires_in"),
        "has_access_token": isinstance(payload.get("access_token"), str) and bool(payload["access_token"]),
        "has_refresh_token": isinstance(payload.get("refresh_token"), str) and bool(payload["refresh_token"]),
        "error": payload.get("error"),
        "error_description": payload.get("error_description"),
    }
    print(f"{label}: {json.dumps(summary, indent=2)}")


def _parse_args() -> argparse.Namespace:
    """Parse command-line overrides while defaulting to the project configuration."""
    settings = _settings()
    parser = argparse.ArgumentParser(
        description="Test whether a ServiceNow public PKCE client receives and can use a refresh token.",
    )
    parser.add_argument(
        "--instance-url",
        default=settings.get("SERVICENOW_INSTANCE_URL"),
        help="ServiceNow base URL (default: SERVICENOW_INSTANCE_URL)",
    )
    parser.add_argument(
        "--client-id",
        default=settings.get("SERVICENOW_OAUTH_CLIENT_ID"),
        help="Public OAuth client ID (default: SERVICENOW_OAUTH_CLIENT_ID)",
    )
    parser.add_argument(
        "--redirect-uri",
        default=settings.get("SERVICENOW_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        help="Registered loopback callback URI",
    )
    parser.add_argument(
        "--scope",
        default=settings.get("SERVICENOW_OAUTH_SCOPE", DEFAULT_SCOPE),
        help=f"OAuth scope (default: {DEFAULT_SCOPE})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(settings.get("SERVICENOW_OAUTH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        help=f"Seconds to wait for browser authorization (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser.parse_args()


def main() -> int:
    """Run authorization-code and refresh-token grants and report redacted results."""
    args = _parse_args()
    if not args.instance_url:
        print("Missing instance URL. Set SERVICENOW_INSTANCE_URL or pass --instance-url.", file=sys.stderr)
        return 2
    if not args.client_id:
        print("Missing OAuth client ID. Set SERVICENOW_OAUTH_CLIENT_ID or pass --client-id.", file=sys.stderr)
        return 2
    if shutil.which("curl") is None:
        print("curl is required but was not found on PATH.", file=sys.stderr)
        return 2
    if not 1 <= args.timeout <= 600:
        print("--timeout must be between 1 and 600 seconds.", file=sys.stderr)
        return 2

    redirect = urlsplit(args.redirect_uri)
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or redirect.port is None:
        print("The redirect URI must use http://127.0.0.1:<port>/<path>.", file=sys.stderr)
        return 2

    verifier = secrets.token_urlsafe(64)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(32)
    authorization_url = f"{args.instance_url.rstrip('/')}/oauth_auth.do?{
        urlencode(
            {
                'response_type': 'code',
                'client_id': args.client_id,
                'redirect_uri': args.redirect_uri,
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'scope': args.scope,
                'state': state,
            }
        )
    }"

    try:
        server = _CallbackServer(("127.0.0.1", redirect.port), redirect.path, state)
    except OSError as exc:
        print(f"Cannot bind callback port {redirect.port}: {exc}", file=sys.stderr)
        print("Stop the MCP server or another listener using that port, then retry.", file=sys.stderr)
        return 2

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print("Opening ServiceNow authorization in the default browser...")
        if not webbrowser.open(authorization_url, new=1):
            print("The browser could not be opened automatically. Open this URL manually:")
            print(authorization_url)
        if not server.completed.wait(args.timeout):
            print("Timed out waiting for the OAuth callback.", file=sys.stderr)
            return 2
        if server.oauth_error:
            print("ServiceNow denied OAuth authorization.", file=sys.stderr)
            return 2
        if not server.authorization_code:
            print("The callback did not contain an authorization code.", file=sys.stderr)
            return 2
        authorization_code = server.authorization_code
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    token_url = f"{args.instance_url.rstrip('/')}/oauth_token.do"
    try:
        status, token_payload = _curl_token_request(
            token_url,
            {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": args.redirect_uri,
                "client_id": args.client_id,
                "code_verifier": verifier,
            },
        )
        _safe_summary("Authorization-code exchange", status, token_payload)
        refresh_token = token_payload.get("refresh_token")
        if status != HTTPStatus.OK or not isinstance(refresh_token, str) or not refresh_token:
            print("No usable refresh token was issued; the refresh test cannot continue.")
            return 1

        refresh_status, refresh_payload = _curl_token_request(
            token_url,
            {
                "grant_type": "refrtesh_token",
                "refresh_token": refresh_token,
                "client_id": args.client_id,
            },
        )
        _safe_summary("Refresh-token exchange", refresh_status, refresh_payload)
        if refresh_status != HTTPStatus.OK or not refresh_payload.get("access_token"):
            print("Refresh failed without a client secret.")
            return 1
    except (RuntimeError, ValueError) as exc:
        print(f"OAuth test failed: {exc}", file=sys.stderr)
        return 2
    finally:
        authorization_code = ""
        verifier = ""

    print("Success: ServiceNow accepted a public-client refresh grant without a client secret.")
    print("No tokens were printed or persisted by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
