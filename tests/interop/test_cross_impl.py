"""Live interop between second_impl and the reference examples/network_dual_end.py.

Both directions, with HMAC signing and the replay window enabled, plus the
reference receiver pushing an Absorption Result to a second_impl receiver
and the second_impl CLI run as a subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_oriented_a2a import verify_message  # noqa: E402
from minimal_dual_end import Receiver  # noqa: E402
from network_dual_end import A2AHandler, make_packet, send_message  # noqa: E402

from second_impl import a2a_core as core  # noqa: E402
from second_impl import minimal_client as mc  # noqa: E402

KEY = "demo"


@pytest.fixture
def reference():
    servers = []

    def _start(sign_key=None, replay_window=None, peer_url=None):
        A2AHandler.role = "receiver"
        A2AHandler.peer_url = peer_url
        A2AHandler.sign_key = sign_key
        A2AHandler.replay_window = replay_window
        A2AHandler.received = []
        A2AHandler.receiver = Receiver(isolation_n=3, regression_drop_threshold=0.10)
        A2AHandler.seen_nonces = set()
        server = HTTPServer(("127.0.0.1", 0), A2AHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()
    A2AHandler.peer_url = None


@pytest.fixture
def second():
    servers = []

    def _start(**kwargs):
        server, url = mc.start_in_thread(**kwargs)
        servers.append(server)
        return server, url

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _packet():
    return json.loads((EXAMPLES / "experience-packet.json").read_text())


def test_second_client_to_reference_receiver_signed_with_replay(reference):
    url = reference(sign_key=KEY, replay_window=600)
    pkt = _packet()
    reply = mc.send(url, pkt, sign_key=KEY, with_nonce=True)
    assert reply["packet_ref"] == pkt["id"]
    assert reply["status"] in ("absorbed", "rejected", "isolated")
    assert core.verify(reply, KEY)
    if reply["status"] == "absorbed":
        assert reply["effect_summary"]["note"].strip()


def test_second_client_reference_rejections(reference):
    url = reference(sign_key=KEY, replay_window=600)
    pkt = _packet()
    # unknown version: the second_impl client refuses locally ...
    with pytest.raises(mc.LocalMessageError):
        mc.send(url, dict(pkt, version="v0.3"), sign_key=KEY, with_nonce=True)
    # ... and the reference rejects it on the wire with a legal result
    _, body, _ = mc.post(url, dict(pkt, version="v0.3"), sign_key=KEY, with_nonce=True)
    assert body["status"] == "rejected" and "unsupported_version" in body["reason"]
    assert core.check_reply(pkt, body, sign_key=KEY) is None
    # wrong key
    _, body, _ = mc.post(url, pkt, sign_key="wrong", with_nonce=True)
    assert body["reason"] == "signature verification failed"
    # replayed message (same nonce and timestamp)
    signed = core.sign(core.fresh_replay_fields(pkt), KEY)
    mc.post(url, signed)
    _, body, _ = mc.post(url, signed)
    assert body["reason"].startswith("replay_or_stale")


def test_reference_client_to_second_receiver_signed_with_replay(second):
    _, url = second(sign_key=KEY, replay_window=600)
    pkt = make_packet()  # carries nonce + timestamp
    reply = send_message(url, pkt, KEY)
    assert reply["packet_ref"] == pkt["id"]
    assert reply["status"] == "isolated"
    assert reply["reason"] == "isolated: no local regression available"
    assert verify_message(reply, KEY)
    again = send_message(url, pkt, KEY)
    assert again["status"] == "rejected" and again["reason"] == "replay_or_stale: duplicate nonce"
    v = send_message(url, dict(make_packet(), version="v0.3"), KEY)
    assert v["status"] == "rejected" and v["reason"].startswith("unsupported_version")
    bad = send_message(url, make_packet(), "wrong")
    assert bad["reason"] == "signature verification failed"


def test_reference_pushes_result_to_second_receiver(reference, second):
    sink, sink_url = second(sign_key=KEY, replay_window=600)
    url = reference(sign_key=KEY, replay_window=600, peer_url=sink_url)
    pkt = _packet()
    reply = mc.send(url, pkt, sign_key=KEY, with_nonce=True)
    deadline = time.time() + 10
    while time.time() < deadline and not sink.received:
        time.sleep(0.05)
    assert sink.received, "reference receiver did not push its Absorption Result"
    pushed = sink.received[0]
    assert pushed["packet_ref"] == pkt["id"]
    assert pushed["status"] == reply["status"]


def test_second_client_reports_result_to_reference(reference):
    url = reference(sign_key=KEY, replay_window=600)
    result = json.loads((EXAMPLES / "absorption-result-absorbed.json").read_text())
    assert mc.send(url, result, sign_key=KEY, with_nonce=True)["type"] == "result_ack"
    with pytest.raises(mc.LocalMessageError):
        mc.send(url, {"packet_ref": "p", "status": "absorbed"}, sign_key=KEY, with_nonce=True)


def test_second_receiver_rejects_negotiation_from_reference_client(second):
    _, url = second()
    msg = json.loads((EXAMPLES / "negotiation-propose.json").read_text())
    reply = send_message(url, msg)
    assert reply["status"] == "rejected"
    assert reply["packet_ref"] == msg["offered_packet"]["id"]
    assert reply["reason"].startswith("negotiation_unsupported: ")


def test_cli_subprocess_against_reference(reference, tmp_path):
    url = reference(sign_key=KEY, replay_window=600)
    path = tmp_path / "pkt.json"
    path.write_text(json.dumps(_packet()))
    proc = subprocess.run(
        [sys.executable, str(REPO / "second_impl" / "minimal_client.py"), "send", str(path),
         "--url", url, "--sign-key", KEY, "--with-nonce"],
        capture_output=True, text=True, timeout=60,
    )
    reply = json.loads(proc.stdout)
    expected = mc.EXIT_REJECTED if reply["status"] == "rejected" else mc.EXIT_ACCEPTED
    assert proc.returncode == expected, proc.stderr
    assert reply["packet_ref"] == "pkt-experience-7f3a"
