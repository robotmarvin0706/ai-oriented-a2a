"""Tests for the one-way MCP bridge (mcp_a2a/server.py).

Skipped unless the optional ``mcp`` extra is installed (Python >= 3.10).
Uses the SDK's in-memory ``Client`` and receivers on 127.0.0.1 only; no
external network and no stdio subprocess.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from mcp import Client  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcp_a2a import server as bridge  # noqa: E402
from second_impl import a2a_core as core  # noqa: E402
from second_impl import minimal_client as mc  # noqa: E402

PACKET = json.loads((EXAMPLES / "experience-packet.json").read_text())
ABSORBED = json.loads((EXAMPLES / "absorption-result-absorbed.json").read_text())


@pytest.fixture
def peer(monkeypatch):
    for name in ("A2A_PEER_URL", "A2A_ALLOWED_PEERS", "A2A_SIGN_KEY", "A2A_NO_SCHEMA"):
        monkeypatch.delenv(name, raising=False)
    servers = []

    def _start(**kwargs):
        server, url = mc.start_in_thread(**kwargs)
        servers.append(server)
        return server, url

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _call(tool, args):
    async def run():
        async with Client(bridge.mcp) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(run())


def _error_text(result):
    assert result.is_error
    return " ".join(getattr(c, "text", "") for c in result.content)


def test_tools_are_listed():
    async def run():
        async with Client(bridge.mcp) as client:
            return await client.list_tools()

    names = {tool.name for tool in asyncio.run(run()).tools}
    assert names == {"send_packet", "report_absorption"}


def test_send_packet_returns_bare_absorption_result(peer, monkeypatch):
    server, url = peer(sign_key="k", replay_window=600)
    monkeypatch.setenv("A2A_PEER_URL", url)
    monkeypatch.setenv("A2A_SIGN_KEY", "k")
    result = _call("send_packet", {"packet": PACKET})
    assert not result.is_error
    body = result.structured_content
    assert body["packet_ref"] == PACKET["id"]
    assert body["status"] == "isolated"
    assert set(body) == {"packet_ref", "status", "reason", "signature"}  # no wrapper
    sent = server.received[0]
    assert core.verify(sent, "k") and sent["nonce"] and sent["timestamp"]


def test_peer_rejection_is_returned_not_raised(peer, monkeypatch):
    _, url = peer()
    monkeypatch.setenv("A2A_PEER_URL", url)
    body = _call("send_packet", {"packet": dict(PACKET, absorption={"method": "parameter_delta"})}).structured_content
    assert body["status"] == "rejected" and body["reason"] == "unsupported method: parameter_delta"


def test_report_absorption_returns_peer_ack(peer, monkeypatch):
    _, url = peer()
    monkeypatch.setenv("A2A_ALLOWED_PEERS", url + "/")
    result = _call("report_absorption", {"result": ABSORBED, "peer_url": url})
    assert result.structured_content == {"ack": True, "type": "result_ack"}


def test_report_absorption_to_reference_receiver(monkeypatch, peer):
    from minimal_dual_end import Receiver
    from network_dual_end import A2AHandler

    A2AHandler.role, A2AHandler.peer_url, A2AHandler.sign_key = "receiver", None, "k"
    A2AHandler.replay_window, A2AHandler.seen_nonces = 600, set()
    A2AHandler.received, A2AHandler.receiver = [], Receiver(isolation_n=3, regression_drop_threshold=0.10)
    server = HTTPServer(("127.0.0.1", 0), A2AHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        monkeypatch.setenv("A2A_PEER_URL", url)
        monkeypatch.setenv("A2A_SIGN_KEY", "k")
        body = _call("report_absorption", {"result": ABSORBED}).structured_content
        assert body["ack"] is True and body["type"] == "result_ack"
        sent = _call("send_packet", {"packet": PACKET}).structured_content
        assert sent["packet_ref"] == PACKET["id"]
    finally:
        server.shutdown()
        server.server_close()


def test_hard_gate_violation_is_a_tool_error(peer, monkeypatch):
    _, url = peer()
    monkeypatch.setenv("A2A_PEER_URL", url)
    text = _error_text(_call("report_absorption", {"result": {"packet_ref": "p", "status": "absorbed"}}))
    assert "hard-gate violation" in text


def test_wrong_message_type_is_a_tool_error(peer, monkeypatch):
    _, url = peer()
    monkeypatch.setenv("A2A_PEER_URL", url)
    assert "Experience Packet" in _error_text(_call("send_packet", {"packet": ABSORBED}))
    assert "Absorption Result" in _error_text(_call("report_absorption", {"result": PACKET}))


def test_peer_allowlist(peer, monkeypatch):
    _, url = peer()
    assert "no peers configured" in _error_text(_call("send_packet", {"packet": PACKET, "peer_url": url}))
    monkeypatch.setenv("A2A_PEER_URL", url)
    text = _error_text(_call("send_packet", {"packet": PACKET, "peer_url": "http://169.254.169.254"}))
    assert "not in A2A_PEER_URL" in text


def test_transport_failure_is_a_tool_error(peer, monkeypatch):
    _, url = peer()
    monkeypatch.setenv("A2A_PEER_URL", url)
    dead = "http://127.0.0.1:9"
    monkeypatch.setenv("A2A_ALLOWED_PEERS", dead)
    assert "transport failure" in _error_text(_call("send_packet", {"packet": PACKET, "peer_url": dead}))


def test_bad_reply_signature_is_a_tool_error(peer, monkeypatch):
    _, url = peer(sign_key="other")
    monkeypatch.setenv("A2A_PEER_URL", url)
    monkeypatch.setenv("A2A_SIGN_KEY", "k")
    # the peer rejects our signature and signs its reply with a different key
    assert "reply signature verification failed" in _error_text(_call("send_packet", {"packet": PACKET}))


def test_sign_key_is_not_a_tool_argument():
    async def run():
        async with Client(bridge.mcp) as client:
            return await client.list_tools()

    for tool in asyncio.run(run()).tools:
        assert "sign_key" not in json.dumps(tool.input_schema)
