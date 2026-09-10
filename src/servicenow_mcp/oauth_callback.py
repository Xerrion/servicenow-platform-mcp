"""Bounded, one-shot loopback receiver for outbound OAuth authorization."""

import asyncio
import secrets
import webbrowser
from urllib.parse import parse_qs, urlsplit

from servicenow_mcp.errors import AuthError


def _callback_result(request: bytes, redirect_uri: str, state: str) -> str | AuthError | None:
    try:
        lines = request.decode("ascii").split("\r\n")
        method, target, version = lines[0].split(" ")
        redirect = urlsplit(redirect_uri)
        url = urlsplit(target)
        hosts = [line[5:].strip() for line in lines[1:] if line.lower().startswith("host:")]
        if (
            method != "GET"
            or version != "HTTP/1.1"
            or url.scheme
            or url.netloc
            or url.path != redirect.path
            or url.fragment
            or hosts != [redirect.netloc]
        ):
            return None
        params = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16)
        if any(len(values) != 1 for values in params.values()):
            return None
        actual_state = params.get("state", [""])[0]
        if not actual_state.isascii() or not secrets.compare_digest(actual_state, state):
            return None
        if "error" in params:
            return AuthError("ServiceNow authorization was denied or failed. Retry to authorize again.")
        code = params.get("code", [""])[0]
        if not code or not code.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in code):
            return AuthError("ServiceNow callback did not contain a valid authorization code.")
        return code
    except (ValueError, UnicodeDecodeError):
        return None


async def receive_authorization_code(
    authorization_url: str, redirect_uri: str, state: str, timeout_seconds: int
) -> str:
    """Open the local browser and return one state-bound code or raise AuthError.

    Only the configured IPv4 loopback socket is bound. Invalid requests cannot
    consume the flow. The listener and all connections close on every exit path.
    """
    result: asyncio.Future[str | AuthError] = asyncio.get_running_loop().create_future()
    connections: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
    is_closing = False

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(5):
                request = await reader.readuntil(b"\r\n\r\n")
                outcome = _callback_result(request, redirect_uri, state)
                is_accepted = outcome is not None and not result.done()
                if is_accepted and outcome is not None:
                    result.set_result(outcome)
                body = b"Authorization received. You may close this window." if is_accepted else b"Invalid callback."
                status = b"200 OK" if is_accepted else b"400 Bad Request"
                writer.write(
                    b"HTTP/1.1 " + status + b"\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    b"Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
                    b"Content-Security-Policy: default-src 'none'\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            # Incomplete or oversized local requests must not consume the pending grant.
            return
        finally:
            writer.close()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if is_closing or len(connections) >= 8:
            writer.close()
            return
        task = asyncio.create_task(handle(reader, writer))
        connections[task] = writer
        task.add_done_callback(connections.pop)

    try:
        # The platform default allows POSIX retries during TCP TIME_WAIT without
        # sharing an active listener. Own the server before starting acceptance.
        server = await asyncio.start_server(
            connected, "127.0.0.1", urlsplit(redirect_uri).port, limit=8192, start_serving=False
        )
    except OSError:
        raise AuthError(
            "Cannot bind OAuth loopback port. Close the other listener or configure another redirect URI."
        ) from None
    try:
        async with asyncio.timeout(timeout_seconds):
            await server.start_serving()
            try:
                is_opened = await asyncio.to_thread(webbrowser.open, authorization_url, new=1)
            except (webbrowser.Error, OSError):
                raise AuthError("Cannot open the local browser for ServiceNow authorization.") from None
            if not is_opened:
                raise AuthError("Cannot open the local browser for ServiceNow authorization.")
            outcome = await result
            if isinstance(outcome, AuthError):
                raise outcome
            return outcome
    except TimeoutError:
        raise AuthError("ServiceNow authorization timed out. Retry the tool call to authorize again.") from None
    finally:
        # wait_closed() waits for clients too. Close them first, including those
        # whose handler task was cancelled before its finally block could run.
        is_closing = True
        server.close()
        pending = list(connections.items())
        for task, writer in pending:
            writer.close()
            task.cancel()
        await asyncio.gather(*(task for task, _writer in pending), return_exceptions=True)
        await server.wait_closed()
