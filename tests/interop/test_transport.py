"""ai_oriented_a2a.transport: client, request handler, and import boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_oriented_a2a import reject_result, verify_message  # noqa: E402
from ai_oriented_a2a import transport  # noqa: E402
from ai_oriented_a2a.transport import (  # noqa: E402
    A2ARequestHandler,
    TransportError,
    guard_result,
    make_server,
    send_message,
)
import network_dual_end  # noqa: E402

KEY = "test-key"
SECRET_TEXT = "secret-detail-/etc/passwd-boom"


def _absorb(msg):
    return {
        "packet_ref": msg.get("id", "unknown"),
        "status": "absorbed",
        "effect_summary": {"note": "stored as hint"},
    }


@pytest.fixture
def server_url():
    servers = []

    def _start(on_message=_absorb, **kw):
        server = make_server(on_message, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _post_raw(url, body: bytes, path="/a2a/message"):
    req = urllib.request.Request(url + path, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_send_message_returns_valid_absorption_result(server_url):
    pytest.importorskip("jsonschema")
    from ai_oriented_a2a import validate

    url = server_url()
    reply = send_message(url, network_dual_end.make_packet())
    validate(reply, "absorption-result")
    assert reply["status"] == "absorbed"


def test_signed_with_replay_window(server_url):
    url = server_url(sign_key=KEY, replay_window=300)
    reply = send_message(url, network_dual_end.make_packet(), sign_key=KEY, add_replay_fields=True)
    assert reply["status"] == "absorbed"
    assert verify_message(reply, KEY)


def test_replay_window_rejects_missing_fields_and_duplicates(server_url):
    url = server_url(replay_window=300)
    bare = {k: v for k, v in network_dual_end.make_packet().items() if k not in ("nonce", "timestamp")}
    reply = send_message(url, bare)
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("replay_or_stale:")
    msg = dict(bare, nonce="n-fixed", timestamp=int(time.time()))
    assert send_message(url, msg)["status"] == "absorbed"
    again = send_message(url, msg)
    assert again["reason"] == "replay_or_stale: duplicate nonce"


def test_leading_v_version_rejected_before_handler(server_url):
    called = []

    def on_message(msg):
        called.append(msg)
        return _absorb(msg)

    url = server_url(on_message)
    reply = send_message(url, dict(network_dual_end.make_packet(), version="v0.3"))
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("unsupported_version:")
    assert called == []


def test_bad_signature_rejected(server_url):
    url = server_url(sign_key=KEY)
    reply = send_message(url, network_dual_end.make_packet(), sign_key="wrong")
    assert reply["status"] == "rejected"
    assert reply["reason"] == "signature verification failed"


def test_invalid_json_is_400_with_absorption_result(server_url):
    url = server_url()
    status, body = _post_raw(url, b"{not json")
    assert status == 400
    reply = json.loads(body)
    assert reply == reject_result(None, "invalid json")
    assert reply["packet_ref"] == "unknown"


def test_other_path_is_404(server_url):
    url = server_url()
    status, _ = _post_raw(url, b"{}", path="/other")
    assert status == 404


def test_unreachable_host_raises_transport_error():
    with pytest.raises(TransportError):
        send_message("http://127.0.0.1:9", {"id": "x"}, timeout=2)


def test_send_message_options_are_keyword_only():
    with pytest.raises(TypeError):
        send_message("http://127.0.0.1:9", {}, 5)  # type: ignore[misc]


def test_guard_result():
    ok = _absorb({"id": "p1"})
    assert guard_result(ok) is ok
    no_note = {"packet_ref": "p1", "status": "absorbed", "effect_summary": {}}
    assert guard_result(no_note)["status"] == "rejected"
    assert guard_result(no_note)["reason"].startswith("hard-gate violation")
    reserved = {"packet_ref": "unknown", "status": "absorbed", "effect_summary": {"note": "x"}}
    assert guard_result(reserved)["status"] == "rejected"
    ack = {"ack": True, "type": "profile_ack"}
    assert guard_result(ack) is ack


def test_handler_absorbed_without_note_is_downgraded(server_url):
    url = server_url(lambda msg: {"packet_ref": msg["id"], "status": "absorbed"})
    reply = send_message(url, network_dual_end.make_packet())
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("hard-gate violation")


def test_handler_exception_is_500_internal_error_without_details(server_url):
    def boom(msg):
        raise RuntimeError(SECRET_TEXT)

    url = server_url(boom, sign_key=KEY)
    packet = network_dual_end.make_packet()
    status, body = _post_raw(url, json.dumps(network_dual_end.sign_message(packet, KEY)).encode())
    assert status == 500
    reply = json.loads(body)
    assert reply["status"] == "rejected"
    assert reply["packet_ref"] == packet["id"]
    assert reply["reason"] == transport.INTERNAL_ERROR_REASON
    assert reply["reason"].startswith("internal_error:")
    for leak in (SECRET_TEXT, "RuntimeError", "Traceback", ".py"):
        assert leak not in body.decode()
    assert verify_message(reply, KEY)
    # The client returns the 500 body like any reply.
    assert send_message(url, packet, sign_key=KEY)["reason"] == transport.INTERNAL_ERROR_REASON


def test_base_handler_without_override_is_500(server_url):
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (A2ARequestHandler,), {"seen_nonces": set()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        reply = send_message(url, network_dual_end.make_packet())
        assert reply["reason"] == transport.INTERNAL_ERROR_REASON
    finally:
        server.shutdown()
        server.server_close()


def test_make_handler_gives_each_server_its_own_replay_set():
    a = transport.make_handler(_absorb, replay_window=300)
    b = transport.make_handler(_absorb, replay_window=300)
    assert a.seen_nonces is not b.seen_nonces
    assert a.seen_nonces is not A2ARequestHandler.seen_nonces


def test_demo_handler_subclasses_package_handler():
    assert issubclass(network_dual_end.A2AHandler, A2ARequestHandler)
    assert "do_POST" not in vars(network_dual_end.A2AHandler)


def test_demo_positional_send_message_still_works(server_url):
    url = server_url(sign_key=KEY)
    reply = network_dual_end.send_message(url, network_dual_end.make_packet(), KEY)
    assert reply["status"] == "absorbed"
    assert verify_message(reply, KEY)


def test_top_level_import_does_not_load_http_server():
    code = (
        "import sys, ai_oriented_a2a; "
        "bad = [m for m in ('http.server', 'ai_oriented_a2a.transport') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == ""


def test_transport_not_reexported():
    import ai_oriented_a2a

    for name in ("send_message", "make_server", "serve", "A2ARequestHandler"):
        assert name not in ai_oriented_a2a.__all__
