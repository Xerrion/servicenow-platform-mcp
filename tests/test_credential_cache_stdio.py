"""Two real stdio servers reuse a synthetic grant via a test-only memory store.

The parent owns a multiprocessing manager. No credential file or production
fallback is created. OAuth exchange and the exact record read are mock HTTP.
"""

import multiprocessing
import os
import sys
from collections.abc import Callable
from multiprocessing.managers import BaseManager
from pathlib import Path

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.loopback()

CHILD = r"""
import json, os, socket, sys, webbrowser
from multiprocessing.managers import BaseManager
import httpx2
from servicenow_mcp import auth, credential_cache
from servicenow_mcp.config import Settings

class FixtureManager(BaseManager):
    pass
FixtureManager.register('store')
manager = FixtureManager(address=('127.0.0.1', int(os.environ['TEST_STORE_PORT'])), authkey=b'offline-fixture-only')
manager.connect()
store = manager.store()
class MemoryBackend:
    def get_password(self, service, account):
        return store.get((service, account))
    def set_password(self, service, account, value):
        store.set((service, account), value)
    def delete_password(self, service, account):
        store.delete((service, account))
credential_cache._native_backend = MemoryBackend
Settings.model_config = {**Settings.model_config, 'env_file': None}
def denied(*args, **kwargs):
    raise RuntimeError('No external network or interactive browser in fixture')
def connect(sock, address):
    if not isinstance(address, tuple) or address[0] != '127.0.0.1' or address[1] != int(os.environ['TEST_STORE_PORT']):
        denied()
    return original_connect(sock, address)
original_connect = socket.socket.connect
socket.socket.connect = connect
socket.socket.connect_ex = denied
socket.socket.sendto = denied
webbrowser.open = denied
mode = os.environ['TEST_CACHE_PHASE']
async def receive(*args):
    if mode != 'first':
        raise AssertionError('Second subprocess must not authorize in browser')
    store.event('synthetic_browser_grant')
    return 'synthetic-code'
auth.receive_authorization_code = receive
async def transport(self, request):
    if request.url.host != 'test.service-now.com':
        raise AssertionError('Wrong mock host')
    if request.method == 'POST' and request.url.path == '/oauth_token.do':
        from urllib.parse import parse_qs
        form = parse_qs(request.content.decode())
        if mode == 'first':
            assert form['grant_type'] == ['authorization_code']
        else:
            assert form['grant_type'] == ['refresh_token']
            assert form['refresh_token'] == ['synthetic-grant-one']
            store.event('synthetic_refresh_reused')
        return httpx2.Response(200, json={'access_token':'synthetic-access', 'token_type':'Bearer', 'expires_in':3600,
                                        'refresh_token':'synthetic-grant-one' if mode=='first' else 'synthetic-grant-two'}, request=request)
    if request.method == 'GET' and request.url.path == '/api/now/table/sys_hub_flow/' + 'a'*32:
        assert request.headers['Authorization'] == 'Bearer synthetic-access'
        return httpx2.Response(200,json={'result':{'sys_id':'a'*32,'sys_updated_on':'2026-10-16 00:00:00'}},request=request)
    raise AssertionError('Unexpected mock HTTP operation')
httpx2.AsyncHTTPTransport.handle_async_request = transport
from servicenow_mcp.server import main
main()
"""


class SharedStore:
    def __init__(self):
        self.records = {}
        self.events = []

    def get(self, key):
        return self.records.get(key)

    def set(self, key, value):
        self.records[key] = value

    def delete(self, key):
        del self.records[key]

    def event(self, name):
        self.events.append(name)

    def evidence(self):
        return len(self.records), list(self.events)


_STORE = SharedStore()


def shared_store():
    return _STORE


class FixtureManager(BaseManager):
    store: Callable[..., SharedStore]


FixtureManager.register("store", callable=shared_store)


async def test_sequential_stdio_servers_reuse_refresh_grant_without_browser(tmp_path):
    manager = FixtureManager(
        address=("127.0.0.1", 0),
        authkey=b"offline-fixture-only",
        ctx=multiprocessing.get_context("spawn"),
    )
    manager.start()
    try:
        store = manager.store()
        address = manager.address
        assert isinstance(address, tuple)
        for phase in ("first", "second"):
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path.cwd() / "src"),
                "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
                "SERVICENOW_OAUTH_CLIENT_ID": "offline-client",
                "SERVICENOW_ENV": "dev",
                "SERVICENOW_OAUTH_CACHE": "os",
                "MCP_TOOL_PACKAGE": "full",
                "SENTRY_DSN": "",
                "TEST_CACHE_PHASE": phase,
                "TEST_STORE_PORT": str(address[1]),
            }
            parameters = StdioServerParameters(
                command=sys.executable, args=["-B", "-c", CHILD], cwd=tmp_path, env=env
            )
            with (
                (tmp_path / f"{phase}-stderr").open("w") as errors,
                anyio.fail_after(20),
            ):
                async with (
                    stdio_client(parameters, errlog=errors) as streams,
                    ClientSession(*streams) as session,
                ):
                    await session.initialize()
                    tools = {
                        tool.name: tool for tool in (await session.list_tools()).tools
                    }
                    assert len(tools) == 18
                    args = {
                        "table": "sys_hub_flow",
                        "sys_id": "a" * 32,
                        "fields": ["sys_id", "sys_updated_on"],
                    }
                    Draft202012Validator(tools["query"].input_schema).validate(args)
                    result = await session.call_tool("query", args)
                    assert not result.is_error
                    payload = result.structured_content
                    assert payload["status"] == "success"
            assert "synthetic-grant" not in (tmp_path / f"{phase}-stderr").read_text()
        assert store.evidence() == (
            1,
            ["synthetic_browser_grant", "synthetic_refresh_reused"],
        )
    finally:
        manager.shutdown()
