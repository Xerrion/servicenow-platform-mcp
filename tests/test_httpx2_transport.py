"""Real httpx2 transport behavior against local loopback fixtures only."""

import asyncio
import datetime
import ipaddress
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


pytestmark = [pytest.mark.loopback(), pytest.mark.real_http()]

BINARY = bytes(range(256)) * 64


class _Server:
    def __init__(self) -> None:
        self.request_lines: list[str] = []
        self.bodies: list[bytes] = []
        self.port: int = 0
        self.hang: asyncio.Event = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.request_lines.append(lines[0])
        length = next((int(line.split(":", 1)[1]) for line in lines if line.lower().startswith("content-length:")), 0)
        body = await reader.readexactly(length)
        self.bodies.append(body)
        if lines[0].split()[1].endswith("/hang"):
            await self.hang.wait()
        payload = body or BINARY
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()


@asynccontextmanager
async def _serve(ssl_context: ssl.SSLContext | None = None) -> AsyncGenerator[_Server]:
    state = _Server()
    server = await asyncio.start_server(state.handle, "127.0.0.1", 0, ssl=ssl_context)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        state.hang.set()
        server.close()
        await server.wait_closed()


def _write_ca_and_leaf(directory: Path) -> tuple[Path, ssl.SSLContext]:
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-only CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path = directory / "leaf.pem"
    key_path = directory / "leaf.key"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(cert_path, key_path)
    return ca_path, server_context


async def test_custom_ca_is_trusted_only_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path, server_context = _write_ca_and_leaf(tmp_path)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    async with _serve(server_context) as server:
        url = f"https://127.0.0.1:{server.port}/secure"
        async with httpx2.AsyncClient(trust_env=False) as client:
            with pytest.raises(httpx2.ConnectError):
                await client.get(url)
        async with httpx2.AsyncClient(verify=ssl.create_default_context(cafile=ca_path)) as client:
            assert (await client.get(url)).content == BINARY
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
        async with httpx2.AsyncClient() as client:
            assert (await client.get(url)).status_code == 200


async def test_proxy_env_no_proxy_and_trust_env(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _serve() as proxy, _serve() as origin:
        target = f"http://127.0.0.1:{origin.port}/direct"
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.port}")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        async with httpx2.AsyncClient() as client:
            await client.get(target)
        assert proxy.request_lines == [f"GET {target} HTTP/1.1"]
        assert origin.request_lines == []

        async with httpx2.AsyncClient(trust_env=False) as client:
            await client.get(target)
        assert origin.request_lines == ["GET /direct HTTP/1.1"]

        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        async with httpx2.AsyncClient() as client:
            await client.get(target)
        assert len(proxy.request_lines) == 1
        assert len(origin.request_lines) == 2


async def test_binary_request_and_response_bodies_round_trip() -> None:
    async with _serve() as server, httpx2.AsyncClient(trust_env=False) as client:
        url = f"http://127.0.0.1:{server.port}/binary"
        assert (await client.get(url)).content == BINARY
        echoed = await client.post(url, content=BINARY[::-1], headers={"Content-Type": "application/octet-stream"})
    assert echoed.content == BINARY[::-1]
    assert server.bodies[-1] == BINARY[::-1]


async def test_cancellation_propagates_from_real_transport() -> None:
    async with _serve() as server, httpx2.AsyncClient(trust_env=False) as client:
        task = asyncio.create_task(client.get(f"http://127.0.0.1:{server.port}/hang"))
        while not server.request_lines:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
