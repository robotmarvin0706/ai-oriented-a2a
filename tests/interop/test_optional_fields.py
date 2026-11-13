"""Optional on-wire version and security fields; legal error responses.

Covers:
  - New OPTIONAL fields (version, nonce, timestamp, signature, identity)
    do not invalidate existing examples; additionalProperties stays false.
  - version pattern: "0.3" / "0.3.0" valid, "v0.3" invalid at schema and
    rejected by the reference handler with unsupported_version.
  - Canonical HMAC-SHA256 signing (json.dumps separators=(',', ':'),
    sort_keys=True, ensure_ascii=False, UTF-8, excluding signature).
  - HTTP 200 + valid Absorption Result for signature failure, replay
    rejection, unknown version, and hard-gate failure.
  - Early failures without an id use reserved packet_ref "unknown",
    which MUST NOT appear on a successful absorption.
  - Signing and replay are independently enabled; default leaves both off.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
SCHEMA_DIR = REPO / "schema"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from minimal_dual_end import Receiver  # noqa: E402
from network_dual_end import (  # noqa: E402
    A2AHandler,
    CLIENT_TIMEOUT_SECONDS,
    RESERVED_PACKET_REF,
    canonical,
    check_replay,
    make_packet,
    packet_ref_of,
    reject_result,
    sign_message,
    supported_version,
    verify_message,
    version_rejection,
)


def _local_registry():
    resources = []
    for path in SCHEMA_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        schema_id = contents.get("$id")
        if schema_id:
            resources.append((schema_id, resource))
    return Registry().with_resources(resources)


REGISTRY = _local_registry()
PACKET_SCHEMA = json.loads((SCHEMA_DIR / "experience-packet.schema.json").read_text())
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
PROFILE_SCHEMA = json.loads((SCHEMA_DIR / "agent-profile.schema.json").read_text())
NEG_SCHEMA = json.loads((SCHEMA_DIR / "negotiation.schema.json").read_text())
PACKET_V = Draft202012Validator(PACKET_SCHEMA, registry=REGISTRY)
RESULT_V = Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY)
PROFILE_V = Draft202012Validator(PROFILE_SCHEMA, registry=REGISTRY)
NEG_V = Draft202012Validator(NEG_SCHEMA, registry=REGISTRY)


def _valid_packet(**extra):
    base = json.loads((EXAMPLES / "experience-packet.json").read_text())
    base.update(extra)
    return base


def _valid_result(**extra):
    base = json.loads((EXAMPLES / "absorption-result-absorbed.json").read_text())
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Schema: old examples remain valid; new optional fields accepted
# ---------------------------------------------------------------------------


def test_existing_examples_still_valid_after_optional_fields():
    mapping = {
        "agent-profile.json": PROFILE_V,
        "experience-packet.json": PACKET_V,
        "experience-packet-with-provenance.json": PACKET_V,
        "experience-packet-trajectory-replay.json": PACKET_V,
        "negotiation-propose.json": NEG_V,
        "negotiation-propose-conditional.json": NEG_V,
        "negotiation-accept.json": NEG_V,
        "negotiation-reject.json": NEG_V,
        "negotiation-counter.json": NEG_V,
        "absorption-result-reject.json": RESULT_V,
        "absorption-result-absorbed.json": RESULT_V,
        "absorption-result-isolated.json": RESULT_V,
        "absorption-result-with-effect.json": RESULT_V,
    }
    for name, validator in mapping.items():
        instance = json.loads((EXAMPLES / name).read_text())
        validator.validate(instance)


def test_optional_security_fields_on_packet_and_result():
    pkt = _valid_packet(
        version="0.3",
        nonce="nonce-ab12cd34",
        timestamp=1731480000,
        signature={"alg": "hmac-sha256", "value": "abc"},
    )
    PACKET_V.validate(pkt)
    pkt["timestamp"] = "2026-11-13T12:00:00Z"
    PACKET_V.validate(pkt)

    result = _valid_result(
        version="0.3",
        nonce="n1",
        timestamp=1731480000,
        signature={"alg": "hmac-sha256", "value": "abc"},
    )
    RESULT_V.validate(result)


def test_absorbed_hard_gate_still_enforced_with_optional_fields():
    """Adding optional fields must not relax the absorbed hard gate."""
    bad = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "version": "0.3",
        "nonce": "n1",
    }
    with pytest.raises(jsonschema.ValidationError):
        RESULT_V.validate(bad)
    still_bad = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {"note": ""},
        "signature": {"alg": "hmac-sha256", "value": "abc"},
    }
    with pytest.raises(jsonschema.ValidationError):
        RESULT_V.validate(still_bad)


def test_version_pattern_accepts_0_3_and_rejects_leading_v():
    pkt = _valid_packet(version="0.3")
    PACKET_V.validate(pkt)
    pkt["version"] = "0.3.0"
    PACKET_V.validate(pkt)
    pkt["version"] = "v0.3"
    with pytest.raises(jsonschema.ValidationError):
        PACKET_V.validate(pkt)
    pkt["version"] = "0.4"
    PACKET_V.validate(pkt)  # pattern matches; protocol still rejects (see below)


def test_identity_optional_on_profile():
    profile = json.loads((EXAMPLES / "agent-profile.json").read_text())
    PROFILE_V.validate(profile)
    profile["identity"] = {"fingerprint": "sha256:abc", "alg": "ed25519"}
    PROFILE_V.validate(profile)
    profile["identity"] = {"fingerprint": ""}
    with pytest.raises(jsonschema.ValidationError):
        PROFILE_V.validate(profile)


def test_negotiation_every_branch_accepts_optional_fields():
    extras = {
        "version": "0.3",
        "nonce": "n1",
        "timestamp": 1,
        "signature": {"alg": "hmac-sha256", "value": "x"},
    }
    propose = json.loads((EXAMPLES / "negotiation-propose.json").read_text())
    propose.update(extras)
    NEG_V.validate(propose)
    accept = json.loads((EXAMPLES / "negotiation-accept.json").read_text())
    accept.update(extras)
    NEG_V.validate(accept)
    reject = json.loads((EXAMPLES / "negotiation-reject.json").read_text())
    reject.update(extras)
    NEG_V.validate(reject)
    counter = json.loads((EXAMPLES / "negotiation-counter.json").read_text())
    counter.update(extras)
    NEG_V.validate(counter)


def test_additional_properties_still_false():
    pkt = _valid_packet(not_a_field=True)
    with pytest.raises(jsonschema.ValidationError):
        PACKET_V.validate(pkt)
    result = _valid_result(not_a_field=True)
    with pytest.raises(jsonschema.ValidationError):
        RESULT_V.validate(result)
    profile = json.loads((EXAMPLES / "agent-profile.json").read_text())
    profile["not_a_field"] = True
    with pytest.raises(jsonschema.ValidationError):
        PROFILE_V.validate(profile)


def test_early_reject_unknown_packet_ref_example_validates():
    instance = json.loads((EXAMPLES / "absorption-result-early-reject.json").read_text())
    assert instance["packet_ref"] == "unknown"
    assert instance["status"] == "rejected"
    assert "unsupported_version" in instance["reason"]
    RESULT_V.validate(instance)


def test_reserved_unknown_is_schema_legal_on_rejected_not_convention_for_absorbed():
    """Schema does not forbid packet_ref=unknown on absorbed; convention + library do."""
    RESULT_V.validate(
        {
            "packet_ref": "unknown",
            "status": "rejected",
            "reason": "signature verification failed",
        }
    )


# ---------------------------------------------------------------------------
# Version helper and canonical signing
# ---------------------------------------------------------------------------


def test_version_rejection_omitted_and_supported():
    assert version_rejection({}) is None
    assert version_rejection({"version": "0.3"}) is None
    assert version_rejection({"version": "0.3.0"}) is None
    assert supported_version("0.3.1") is True
    reason = version_rejection({"version": "v0.3"})
    assert reason is not None and "unsupported_version" in reason and "v0.3" in reason
    reason = version_rejection({"version": "0.4"})
    assert reason is not None and "unsupported_version" in reason and "0.4" in reason
    reason = version_rejection({"version": "0.2"})
    assert reason is not None and "unsupported_version" in reason


def test_canonical_form_and_signature_excludes_signature_field():
    body = {"b": 1, "a": 2, "note": "回归通过"}
    expected = json.dumps(
        body, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    assert canonical(body) == expected
    assert canonical({"a": 2, "b": 1, "note": "回归通过"}) == expected
    # ensure_ascii=False is a deliberate, documented choice (non-ASCII stays UTF-8)
    escaped = json.dumps(
        body, separators=(",", ":"), sort_keys=True, ensure_ascii=True
    ).encode("utf-8")
    assert canonical(body) != escaped

    signed = sign_message({"id": "pkt-1", "note": "回归通过", "b": 1}, "secret")
    assert signed["signature"]["alg"] == "hmac-sha256"
    assert verify_message(signed, "secret")
    assert not verify_message(signed, "other")
    tampered = dict(signed)
    tampered["note"] = "tampered"
    assert not verify_message(tampered, "secret")
    # Reordering keys and re-signing is stable
    other_order = sign_message({"b": 1, "note": "回归通过", "id": "pkt-1"}, "secret")
    assert other_order["signature"]["value"] == signed["signature"]["value"]
    # The digest is over the body without the signature field
    body_only = {k: v for k, v in signed.items() if k != "signature"}
    import hashlib, hmac
    again = hmac.new("secret".encode(), canonical(body_only), hashlib.sha256).hexdigest()
    assert again == signed["signature"]["value"]


def test_packet_ref_of_and_reserved_literal():
    assert packet_ref_of({"id": "pkt-experience-7f3a"}) == "pkt-experience-7f3a"
    assert packet_ref_of({"packet_ref": "pkt-experience-7f3a"}) == "pkt-experience-7f3a"
    assert packet_ref_of({}) == RESERVED_PACKET_REF
    assert packet_ref_of(None) == RESERVED_PACKET_REF
    assert packet_ref_of({"id": ""}) == RESERVED_PACKET_REF
    rejected = reject_result({}, "signature verification failed")
    assert rejected["packet_ref"] == "unknown"
    assert rejected["status"] == "rejected"
    RESULT_V.validate(rejected)


def test_client_timeout_meets_recommendation():
    assert CLIENT_TIMEOUT_SECONDS >= 30


# ---------------------------------------------------------------------------
# HTTP handler behaviour
# ---------------------------------------------------------------------------


def _post(url, payload=None, raw=None, timeout=5):
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/a2a/message",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed


@pytest.fixture
def start_receiver():
    servers = []

    def _start(sign_key=None, replay_window=None):
        A2AHandler.role = "receiver"
        A2AHandler.peer_url = None
        A2AHandler.sign_key = sign_key
        A2AHandler.replay_window = replay_window
        A2AHandler.received = []
        A2AHandler.receiver = Receiver(isolation_n=3, regression_drop_threshold=0.10)
        A2AHandler.seen_nonces = set()
        server = HTTPServer(("127.0.0.1", 0), A2AHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        port = server.server_address[1]
        time.sleep(0.05)
        return f"http://127.0.0.1:{port}"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _assert_valid_rejection(status, body, reason_part, packet_ref=None):
    assert status == 200
    assert body["status"] == "rejected"
    assert isinstance(body["packet_ref"], str) and body["packet_ref"].strip()
    if packet_ref is not None:
        assert body["packet_ref"] == packet_ref
    assert reason_part in body["reason"]
    # Strip optional response signature before schema check if we want the
    # unsigned shape; with no sign_key there is no signature. When present,
    # the new schema accepts it.
    RESULT_V.validate(
        {
            k: body[k]
            for k in body
            if k in ("packet_ref", "status", "reason", "effect_summary",
                     "version", "nonce", "timestamp", "signature")
        }
    )


def test_http_unknown_version_returns_200_rejected(start_receiver):
    url = start_receiver()
    pkt = _valid_packet(version="0.4")
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "unsupported_version", pkt["id"])
    assert "0.4" in body["reason"]

    pkt2 = _valid_packet(version="v0.3")
    status, body = _post(url, pkt2)
    _assert_valid_rejection(status, body, "unsupported_version", pkt2["id"])
    assert "v0.3" in body["reason"]


def test_http_unknown_version_without_id_uses_reserved_ref(start_receiver):
    url = start_receiver()
    status, body = _post(url, {"version": "0.4", "constraints": []})
    _assert_valid_rejection(status, body, "unsupported_version", "unknown")


def test_http_signature_failure_returns_200_rejected(start_receiver):
    url = start_receiver(sign_key="secret")
    pkt = _valid_packet()
    # no signature at all
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "signature verification failed", pkt["id"])
    # bad signature
    bad = sign_message(pkt, "wrong")
    status, body = _post(url, bad)
    _assert_valid_rejection(status, body, "signature verification failed", pkt["id"])
    # good signature is not rejected for this reason (may absorb or reject on gate)
    good = sign_message(_valid_packet(), "secret")
    status, body = _post(url, good)
    assert status == 200
    assert body["status"] in ("absorbed", "rejected", "isolated")
    if body["status"] == "absorbed":
        assert body["packet_ref"] != "unknown"
        assert body["effect_summary"]["note"].strip()
        RESULT_V.validate({k: body[k] for k in body if k != "signature"} | (
            {"signature": body["signature"]} if "signature" in body else {}
        ))


def test_http_replay_only_when_window_enabled(start_receiver):
    pkt = _valid_packet(nonce="same-nonce", timestamp=int(time.time()))
    # Default: replay off, duplicate is not a replay rejection
    url_off = start_receiver()
    status1, body1 = _post(url_off, pkt)
    status2, body2 = _post(url_off, pkt)
    assert status1 == 200 and status2 == 200
    assert "replay_or_stale" not in body2.get("reason", "")

    url_on = start_receiver(replay_window=600)
    status1, body1 = _post(url_on, pkt)
    assert status1 == 200
    assert "replay_or_stale" not in body1.get("reason", "")
    status2, body2 = _post(url_on, pkt)
    _assert_valid_rejection(status2, body2, "replay_or_stale", pkt["id"])

    stale = _valid_packet(nonce="stale-n", timestamp=int(time.time()) - 10_000)
    status, body = _post(url_on, stale)
    _assert_valid_rejection(status, body, "replay_or_stale", stale["id"])


def test_http_replay_accepts_iso8601_timestamp(start_receiver):
    url = start_receiver(replay_window=600)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pkt = _valid_packet(nonce="iso-nonce", timestamp=now)
    status, body = _post(url, pkt)
    assert status == 200
    assert "replay_or_stale" not in body.get("reason", "")
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "replay_or_stale", pkt["id"])


def test_http_sign_and_replay_independent(start_receiver):
    """Replay can be on without a sign key; sign can be on without a window."""
    url = start_receiver(sign_key="secret", replay_window=None)
    pkt = sign_message(_valid_packet(nonce="n", timestamp=int(time.time())), "secret")
    status, body = _post(url, pkt)
    assert status == 200
    # duplicate is allowed because replay is off
    status2, body2 = _post(url, pkt)
    assert status2 == 200
    assert "replay_or_stale" not in body2.get("reason", "")

    url2 = start_receiver(sign_key=None, replay_window=600)
    pkt2 = _valid_packet(nonce="only-replay", timestamp=int(time.time()))
    # unsigned is fine when no key was passed
    status, body = _post(url2, pkt2)
    assert status == 200
    assert "signature" not in body.get("reason", "")


def test_http_omitted_version_still_absorbed_or_valid_result(start_receiver):
    url = start_receiver()
    pkt = _valid_packet()
    assert "version" not in pkt
    status, body = _post(url, pkt)
    assert status == 200
    assert body["status"] in ("absorbed", "rejected", "isolated")
    assert body["packet_ref"] == pkt["id"]
    if body["status"] == "absorbed":
        assert body["effect_summary"]["note"].strip()
        assert body["packet_ref"] != "unknown"
    RESULT_V.validate({k: body[k] for k in ("packet_ref", "status", "reason", "effect_summary") if k in body})


def test_http_invalid_json_is_json_absorption_result_not_html(start_receiver):
    url = start_receiver()
    status, body = _post(url, raw=b"not-json{")
    assert status == 400
    assert isinstance(body, dict), f"expected JSON body, got {body!r}"
    assert body["packet_ref"] == "unknown"
    assert body["status"] == "rejected"
    assert "invalid json" in body["reason"]
    RESULT_V.validate(body)
    assert "<html" not in str(body).lower()


def test_http_incoming_hard_gate_violation_returns_200_rejected(start_receiver):
    url = start_receiver()
    bad = {"packet_ref": "pkt-experience-7f3a", "status": "absorbed"}
    status, body = _post(url, bad)
    _assert_valid_rejection(status, body, "hard-gate", "pkt-experience-7f3a")


def test_http_successful_absorption_never_uses_reserved_ref(start_receiver):
    url = start_receiver()
    # A well-formed packet with a real id
    pkt = _valid_packet()
    status, body = _post(url, pkt)
    assert status == 200
    if body["status"] == "absorbed":
        assert body["packet_ref"] != "unknown"
        assert body["packet_ref"] == pkt["id"]

    # A packet whose id is the reserved literal is not absorbed under that ref
    reserved = _valid_packet()
    reserved["id"] = "unknown"
    status, body = _post(url, reserved)
    assert status == 200
    if body["status"] == "absorbed":
        raise AssertionError("must not absorb with reserved packet_ref 'unknown'")
    assert body["packet_ref"] == "unknown"
    assert body["status"] == "rejected"


def test_published_security_example_verifies_with_demo_key():
    """examples/experience-packet-with-security.json is signed with HMAC key 'demo'."""
    inst = json.loads((EXAMPLES / "experience-packet-with-security.json").read_text())
    PACKET_V.validate(inst)
    assert verify_message(inst, "demo")
    assert not verify_message(inst, "other")


def test_new_security_examples_validate():
    PACKET_V.validate(json.loads((EXAMPLES / "experience-packet-with-security.json").read_text()))
    PROFILE_V.validate(json.loads((EXAMPLES / "agent-profile-with-identity.json").read_text()))
