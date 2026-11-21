"""Robustness cases for any AI-oriented A2A v0.3 receiver (library 0.3.3).

Not part of the 12 HTTP cases copied from tests/interop/test_optional_fields.py,
so nothing here needs a second copy.

- Non-finite numeric timestamps (JSON ``NaN``, ``Infinity``, ``-Infinity``)
  are rejected with ``replay_or_stale: invalid timestamp`` when the replay
  window is on (HTTP 200, legal Absorption Result, connection intact).
- A missing, invalid, or too-large ``Content-Length`` gets HTTP 413 and a
  legal rejected Result whose reason starts with ``payload_too_large:``. The
  oversize case sends only headers: a receiver that tried to read the
  announced body would block and the test would time out, so a pass also
  shows the length is checked before the body is read.
- A negotiation message gets a legal rejected Absorption Result
  (``negotiation_unsupported:``), never a bare ack.
- 16 threads posting distinct nonces at once all get a legal JSON reply.
  This case needs a threaded server, so it does not use ``start_receiver``
  (whose ``reference`` receiver is examples/network_dual_end.py on a
  synchronous ``HTTPServer``). It runs against
  ``ai_oriented_a2a.transport.make_server`` and
  ``second_impl.minimal_client.make_server``, both ``ThreadingHTTPServer``.

No ``dict |`` (Python 3.8).
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

MAX_BODY_BYTES = 1_048_576  # docs/TRANSPORT.md
ENDPOINT = "/a2a/message"
WINDOW = 600
SCHEMA_DIR = REPO / "schema"


def _registry():
    resources = []
    for path in SCHEMA_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        if contents.get("$id"):
            resources.append((contents["$id"], resource))
    return Registry().with_resources(resources)


RESULT_V = Draft202012Validator(
    json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text()),
    registry=_registry(),
)


def _packet(**extra):
    base = json.loads((EXAMPLES / "experience-packet.json").read_text())
    base.update(extra)
    return base


def _assert_legal_result(body, status=None, reason_prefix=None, packet_ref=None):
    assert isinstance(body, dict), body
    RESULT_V.validate(body)
    assert isinstance(body["packet_ref"], str) and body["packet_ref"].strip()
    if status is not None:
        assert body["status"] == status
    if reason_prefix is not None:
        assert body["reason"].startswith(reason_prefix), body["reason"]
    if packet_ref is not None:
        assert body["packet_ref"] == packet_ref


def _post_bytes(url, data, timeout=10):
    req = urllib.request.Request(
        url.rstrip("/") + ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_headers_only(url, content_length, timeout=5):
    """POST with a chosen Content-Length header (None = omit) and no body."""
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.putrequest("POST", ENDPOINT)
        conn.putheader("Content-Type", "application/json")
        if content_length is not None:
            conn.putheader("Content-Length", content_length)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Non-finite timestamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["NaN", "Infinity", "-Infinity"])
def test_non_finite_timestamp_rejected(start_receiver, value):
    url = start_receiver(replay_window=WINDOW)
    pkt = _packet(nonce="nonce-" + uuid.uuid4().hex, timestamp=value)
    raw = json.dumps(pkt).encode()  # emits NaN / Infinity / -Infinity
    assert b"NaN" in raw or b"Infinity" in raw
    status, body = _post_bytes(url, raw)
    assert status == 200
    _assert_legal_result(body, "rejected", "replay_or_stale: invalid timestamp", pkt["id"])
    assert body["reason"] == "replay_or_stale: invalid timestamp"
    # The receiver is still up and answers normally afterwards.
    ok = _packet(nonce="nonce-" + uuid.uuid4().hex, timestamp=int(time.time()))
    status, body = _post_bytes(url, json.dumps(ok).encode())
    assert status == 200
    _assert_legal_result(body)
    assert not body.get("reason", "").startswith("replay_or_stale")


# ---------------------------------------------------------------------------
# Request body limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_length",
    [str(MAX_BODY_BYTES + 1), str(50 * MAX_BODY_BYTES), None, "abc", "-1", "1.5", ""],
    ids=["max+1", "50x", "missing", "abc", "negative", "decimal", "empty"],
)
def test_payload_too_large_or_bad_length_is_413(start_receiver, content_length):
    url = start_receiver()
    status, body = _post_headers_only(url, content_length)
    assert status == 413
    _assert_legal_result(body, "rejected", "payload_too_large:", "unknown")


def test_body_at_limit_is_not_413(start_receiver):
    url = start_receiver()
    pkt = _packet()
    raw = json.dumps(pkt).encode()
    raw = raw + b" " * (MAX_BODY_BYTES - len(raw))  # trailing whitespace is valid JSON
    assert len(raw) == MAX_BODY_BYTES
    status, body = _post_bytes(url, raw)
    assert status == 200
    _assert_legal_result(body)


# ---------------------------------------------------------------------------
# Negotiation is never answered with a bare ack
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "example", ["negotiation-propose.json", "negotiation-counter.json", "negotiation-accept.json"]
)
def test_negotiation_gets_legal_rejection_not_ack(start_receiver, example):
    url = start_receiver()
    msg = json.loads((EXAMPLES / example).read_text())
    offered = msg.get("offered_packet")
    expected_ref = offered["id"] if isinstance(offered, dict) and offered.get("id") else "unknown"
    status, body = _post_bytes(url, json.dumps(msg).encode())
    assert status == 200
    assert "ack" not in body
    _assert_legal_result(body, "rejected", "negotiation_unsupported:", expected_ref)


# ---------------------------------------------------------------------------
# Concurrency (threaded servers only)
# ---------------------------------------------------------------------------


def _reference_callback(msg):
    return {
        "packet_ref": msg.get("id", "unknown"),
        "status": "isolated",
        "reason": "isolated: no local regression available",
    }


def _threaded_impls():
    raw = os.environ.get("A2A_CONFORMANCE_IMPLS")
    names = [x.strip() for x in raw.split(",")] if raw else ["reference", "second_impl"]
    return [n for n in names if n in ("reference", "second_impl")]


@pytest.fixture(params=_threaded_impls())
def threaded_receiver(request):
    if request.param == "reference":
        from ai_oriented_a2a.transport import make_server

        server = make_server(_reference_callback, replay_window=WINDOW)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
    else:
        from second_impl.minimal_client import start_in_thread

        server, url = start_in_thread(replay_window=WINDOW)
    yield url
    server.shutdown()
    server.server_close()


THREADS = 16
PER_THREAD = 8


def test_concurrent_distinct_nonces_all_get_legal_replies(threaded_receiver):
    url = threaded_receiver
    barrier = threading.Barrier(THREADS)
    results = []
    errors = []
    lock = threading.Lock()

    def worker(t):
        try:
            barrier.wait(timeout=10)
            for i in range(PER_THREAD):
                pkt = _packet(
                    id=f"pkt-conc-{t}-{i}",
                    nonce=f"nonce-{t}-{i}-{uuid.uuid4().hex}",
                    timestamp=int(time.time()),
                )
                status, body = _post_bytes(url, json.dumps(pkt).encode())
                with lock:
                    results.append((pkt, status, body))
        except Exception as exc:  # dropped connection, empty body, timeout
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    # In-process receivers share this interpreter: switch threads as often as
    # possible so an unguarded replay set would fail with
    # "Set changed size during iteration" (the 0.3.2 reference did).
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=60)
    finally:
        sys.setswitchinterval(old_interval)
    assert not errors, errors
    assert len(results) == THREADS * PER_THREAD
    for pkt, status, body in results:
        assert status == 200, (status, body)
        _assert_legal_result(body, packet_ref=pkt["id"])
        assert not body.get("reason", "").startswith(("replay_or_stale", "internal_error")), body

    # One shared replay window across request threads: a replayed nonce is rejected.
    replayed = results[0][0]
    status, body = _post_bytes(url, json.dumps(replayed).encode())
    assert status == 200
    _assert_legal_result(body, "rejected", "replay_or_stale: duplicate nonce", replayed["id"])
