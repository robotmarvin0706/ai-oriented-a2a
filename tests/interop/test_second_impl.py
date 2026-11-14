"""Unit tests for the independent second implementation (second_impl/).

Covers the behaviours that tests/interop exercises against the reference:
version-first rejection, the absorption hard gate, schema checks, legal
rejected Absorption Results, canonical HMAC signing, the replay window,
the receiver's replies (never ``absorbed``), CLI options, and that
second_impl does not depend on ai_oriented_a2a or examples/.
See docs/COMPLIANCE.md for the mapping.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from second_impl import a2a_core as core  # noqa: E402
from second_impl import minimal_client as mc  # noqa: E402

EXAMPLES = REPO / "examples"
DATASETS = REPO / "datasets"
SCHEMA_DIR = REPO / "schema"
SECOND_IMPL = REPO / "second_impl"


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
    json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text()), registry=_registry()
)


def _example(name):
    return json.loads((EXAMPLES / name).read_text())


def _packet(**extra):
    base = _example("experience-packet.json")
    base.update(extra)
    return base


def _assert_legal_rejection(result, reason_part, packet_ref=None):
    assert result["status"] == "rejected"
    assert isinstance(result["packet_ref"], str) and result["packet_ref"].strip()
    assert reason_part in result["reason"]
    if packet_ref is not None:
        assert result["packet_ref"] == packet_ref
    RESULT_V.validate(result)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.3", "0.3.0", "0.3.9"])
def test_supported_versions_pass(version):
    assert core.version_problem({"version": version}) is None


def test_omitted_version_is_current():
    assert core.version_problem({}) is None


@pytest.mark.parametrize("version", ["v0.3", "0.4", "0.2", "1.0", "3", "", "0.3.x", 0.3, None, ["0.3"]])
def test_unknown_versions_are_rejected(version):
    reason = core.version_problem({"version": version})
    assert reason is not None and reason.startswith("unsupported_version: received ")


def test_version_is_checked_before_everything():
    # unknown version + invalid schema + bad signature + no replay fields
    msg = {"id": "pkt-1", "version": "v0.3", "bogus": True, "signature": {"alg": "hmac-sha256", "value": "x"}}
    result = core.check_incoming(msg, sign_key="k", replay=core.ReplayWindow(60))
    _assert_legal_rejection(result, "unsupported_version", "pkt-1")
    assert "v0.3" in result["reason"]


def test_unknown_version_without_id_uses_reserved_ref():
    result = core.check_incoming({"version": "0.4", "constraints": []})
    _assert_legal_rejection(result, "unsupported_version", "unknown")


# ---------------------------------------------------------------------------
# Hard gate and schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        {"packet_ref": "p", "status": "absorbed"},
        {"packet_ref": "p", "status": "absorbed", "effect_summary": {}},
        {"packet_ref": "p", "status": "absorbed", "effect_summary": {"note": ""}},
        {"packet_ref": "p", "status": "absorbed", "effect_summary": {"note": "   "}},
        {"packet_ref": "unknown", "status": "absorbed", "effect_summary": {"note": "ok"}},
    ],
)
def test_hard_gate_rejects_unproven_absorption(result):
    assert core.gate_problem(result)
    rejection = core.check_incoming(result)
    _assert_legal_rejection(rejection, "hard-gate violation")


def test_hard_gate_runs_before_schema():
    # also schema-invalid (extra field), but the gate reason wins
    rejection = core.check_incoming({"packet_ref": "p", "status": "absorbed", "extra": 1})
    _assert_legal_rejection(rejection, "hard-gate violation", "p")


def test_compliant_absorbed_result_passes():
    assert core.check_incoming(_example("absorption-result-absorbed.json")) is None


def test_schema_invalid_reason_prefix():
    rejection = core.check_incoming(_packet(extra_field=1))
    _assert_legal_rejection(rejection, "schema_invalid: ", "pkt-experience-7f3a")
    assert core.check_incoming(_packet(extra_field=1), schema=False) is None


@pytest.mark.parametrize(
    "result",
    [
        {"packet_ref": "p", "status": "rejected"},
        {"packet_ref": "p", "status": "isolated"},
        {"packet_ref": "p", "status": "rejected", "reason": "   "},
        {"packet_ref": "", "status": "rejected", "reason": "x"},
    ],
)
def test_schema_catches_illegal_results(result):
    rejection = core.check_incoming(result)
    _assert_legal_rejection(rejection, "schema_invalid")


EXAMPLE_KINDS = {
    "agent-profile.json": "agent-profile",
    "agent-profile-with-identity.json": "agent-profile",
    "experience-packet.json": "experience-packet",
    "experience-packet-with-provenance.json": "experience-packet",
    "experience-packet-trajectory-replay.json": "experience-packet",
    "experience-packet-with-security.json": "experience-packet",
    "negotiation-propose.json": "negotiation",
    "negotiation-propose-conditional.json": "negotiation",
    "negotiation-accept.json": "negotiation",
    "negotiation-reject.json": "negotiation",
    "negotiation-counter.json": "negotiation",
    "absorption-result-reject.json": "absorption-result",
    "absorption-result-absorbed.json": "absorption-result",
    "absorption-result-isolated.json": "absorption-result",
    "absorption-result-with-effect.json": "absorption-result",
    "absorption-result-early-reject.json": "absorption-result",
}


@pytest.mark.parametrize("name", sorted(EXAMPLE_KINDS))
def test_examples_classify_and_validate(name):
    msg = _example(name)
    assert core.kind_of(msg) == EXAMPLE_KINDS[name]
    assert core.schema_problem(msg) is None
    assert core.check_incoming(msg) is None


@pytest.mark.parametrize(
    "path",
    sorted(DATASETS.glob("**/*.json")),
    ids=lambda p: str(p.relative_to(DATASETS)),
)
def test_dataset_messages_pass_second_impl_checks(path):
    msg = json.loads(path.read_text())
    assert core.check_incoming(msg) is None


def test_schema_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("A2A_SCHEMA_DIR", str(tmp_path))
    assert core.schema_dir() == tmp_path
    with pytest.raises(FileNotFoundError):
        core.schema_problem(_packet())
    monkeypatch.setenv("A2A_SCHEMA_DIR", str(SCHEMA_DIR))
    assert core.schema_problem(_packet()) is None


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_published_security_example_verifies():
    msg = _example("experience-packet-with-security.json")
    assert core.verify(msg, "demo")
    assert not core.verify(msg, "other")


def test_canonical_form():
    msg = {"b": 1, "a": {"z": "é", "y": [1, 2]}, "signature": {"alg": "hmac-sha256", "value": "x"}}
    assert core.canonical_bytes(msg) == '{"a":{"y":[1,2],"z":"é"},"b":1}'.encode("utf-8")


def test_signature_matches_reference_bytes():
    # Cross-check against the reference package from the test side only.
    from ai_oriented_a2a import sign_message, verify_message

    msg = _packet(content={"text": "超时后指数退避"}, nonce="n", timestamp=1731480000)
    assert core.sign(msg, "k") == sign_message(msg, "k")
    assert verify_message(core.sign(msg, "k"), "k")


def test_signature_failure_and_order():
    msg = _packet()
    _assert_legal_rejection(core.check_incoming(msg, sign_key="k"), "signature verification failed", msg["id"])
    bad = core.sign(msg, "wrong")
    _assert_legal_rejection(core.check_incoming(bad, sign_key="k"), "signature verification failed")
    assert core.check_incoming(core.sign(msg, "k"), sign_key="k") is None
    # schema runs before signature
    _assert_legal_rejection(core.check_incoming(core.sign(_packet(x=1), "wrong"), sign_key="k"), "schema_invalid")


def test_signature_off_by_default():
    assert core.check_incoming(_packet(signature={"alg": "hmac-sha256", "value": "junk"})) is None


# ---------------------------------------------------------------------------
# Replay window
# ---------------------------------------------------------------------------


def test_replay_requires_both_fields():
    window = core.ReplayWindow(600)
    now = int(time.time())
    for msg in (_packet(), _packet(nonce="n"), _packet(timestamp=now)):
        _assert_legal_rejection(core.check_incoming(msg, replay=window), "replay_or_stale")
    assert core.check_incoming(_packet(nonce="n", timestamp=now), replay=window) is None


def test_replay_duplicate_and_stale():
    window = core.ReplayWindow(600)
    now = int(time.time())
    msg = _packet(nonce="dup", timestamp=now)
    assert core.check_incoming(msg, replay=window) is None
    _assert_legal_rejection(core.check_incoming(msg, replay=window), "replay_or_stale: duplicate nonce")
    # same nonce with a different timestamp is still a duplicate
    _assert_legal_rejection(
        core.check_incoming(_packet(nonce="dup", timestamp=now + 1), replay=window), "duplicate nonce"
    )
    _assert_legal_rejection(
        core.check_incoming(_packet(nonce="old", timestamp=now - 10_000), replay=window), "outside window"
    )


def test_replay_iso8601_and_bad_timestamps():
    window = core.ReplayWindow(600)
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    assert core.check_incoming(_packet(nonce="iso", timestamp=iso), replay=window) is None
    assert core.parse_timestamp(True) is None
    assert core.parse_timestamp("not-a-time") is None
    assert core.parse_timestamp("2026-11-14T00:00:00") == core.parse_timestamp("2026-11-14T00:00:00Z")


def test_replay_entries_expire():
    window = core.ReplayWindow(10)
    t0 = 1_000_000.0
    assert window.check({"nonce": "a", "timestamp": t0}, now=t0) is None
    assert window.check({"nonce": "a", "timestamp": t0 + 15}, now=t0 + 15) is None  # expired, accepted again
    assert len(window._expiry) == 1


def test_replay_runs_after_signature():
    window = core.ReplayWindow(600)
    msg = _packet(nonce="forged", timestamp=int(time.time()))
    _assert_legal_rejection(core.check_incoming(msg, sign_key="k", replay=window), "signature")
    # the forged attempt did not consume the nonce
    assert core.check_incoming(core.sign(msg, "k"), sign_key="k", replay=window) is None


# ---------------------------------------------------------------------------
# packet_ref
# ---------------------------------------------------------------------------


def test_ref_of():
    assert core.ref_of({"id": "a"}) == "a"
    assert core.ref_of({"packet_ref": "b"}) == "b"
    assert core.ref_of({"proposal_id": "p", "offered_packet": {"id": "c"}}) == "c"
    assert core.ref_of({"id": "  "}) == "unknown"
    assert core.ref_of(None) == "unknown"
    with pytest.raises(ValueError):
        core.rejected({"id": "a"}, "  ")


# ---------------------------------------------------------------------------
# Receiver replies
# ---------------------------------------------------------------------------


def test_respond_isolates_every_supported_packet():
    for name in ("experience-packet.json", "experience-packet-with-provenance.json"):
        reply = core.respond(_example(name))
        assert reply["status"] == "isolated"
        assert reply["reason"] == "isolated: no local regression available"
        RESULT_V.validate(reply)


def test_respond_rejects_unsupported_methods_and_reserved_id():
    for method in ("parameter_delta", "other", "trajectory_replay", "local_adapter_hint"):
        reply = core.respond(_packet(absorption={"method": method}))
        _assert_legal_rejection(reply, f"unsupported method: {method}", "pkt-experience-7f3a")
    _assert_legal_rejection(core.respond(_packet(id="unknown")), "unknown", "unknown")


@pytest.mark.parametrize(
    "name,ref",
    [
        ("negotiation-propose.json", "pkt-offer-7f3a"),
        ("negotiation-propose-conditional.json", None),
        ("negotiation-accept.json", "unknown"),
        ("negotiation-reject.json", "unknown"),
        ("negotiation-counter.json", None),
    ],
)
def test_respond_rejects_negotiation_with_legal_result(name, ref):
    msg = _example(name)
    reply = core.respond(msg)
    offered = msg.get("offered_packet") or {}
    expected = ref or offered.get("id") or "unknown"
    _assert_legal_rejection(reply, "negotiation_unsupported: ", expected)


def test_respond_acks():
    assert core.respond(_example("agent-profile.json")) == {"ack": True, "type": "profile_ack"}
    assert core.respond(_example("absorption-result-absorbed.json")) == {"ack": True, "type": "result_ack"}


def test_server_never_returns_absorbed():
    server, url = mc.start_in_thread()
    try:
        for name in sorted(EXAMPLE_KINDS):
            status, body, _ = mc.post(url, _example(name))
            assert status == 200
            assert body.get("status") != "absorbed", name
            if core.is_absorption_result(body):
                RESULT_V.validate(body)
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Client-side reply checks and CLI
# ---------------------------------------------------------------------------


def test_check_reply():
    sent = _packet()
    assert core.check_reply(sent, {"packet_ref": sent["id"], "status": "isolated", "reason": "x"}) is None
    assert core.check_reply(sent, {"packet_ref": "unknown", "status": "rejected", "reason": "x"}) is None
    assert "hard gate" in core.check_reply(sent, {"packet_ref": sent["id"], "status": "absorbed"})
    assert "does not match" in core.check_reply(sent, {"packet_ref": "other", "status": "rejected", "reason": "x"})
    assert "reason" in core.check_reply(sent, {"packet_ref": sent["id"], "status": "rejected"})
    assert core.check_reply(sent, {"ack": True}) is not None  # a packet needs an Absorption Result
    assert core.check_reply(_example("agent-profile.json"), {"ack": True, "type": "profile_ack"}) is None
    signed = core.sign({"packet_ref": sent["id"], "status": "isolated", "reason": "x"}, "other")
    assert "signature" in core.check_reply(sent, signed, sign_key="k")


def test_send_has_with_nonce_and_no_replay_window():
    parser = mc.build_parser()
    args = parser.parse_args(["send", "x.json", "--url", "http://h", "--with-nonce"])
    assert args.with_nonce is True
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["send", "x.json", "--url", "http://h", "--replay-window", "600"])
    assert exc.value.code == 2
    assert parser.parse_args(["serve", "--replay-window", "600"]).replay_window == 600
    help_text = " ".join(mc.SEND_HELP.split())
    assert "never checks replies against a replay window" in help_text


def test_cli_exit_codes(tmp_path, capsys):
    server, url = mc.start_in_thread(replay_window=600)
    try:
        good = tmp_path / "pkt.json"
        good.write_text(json.dumps(_packet()))
        assert mc.main(["send", str(good), "--url", url, "--with-nonce"]) == mc.EXIT_ACCEPTED
        assert json.loads(capsys.readouterr().out)["status"] == "isolated"
        # without --with-nonce the peer's replay window rejects it
        assert mc.main(["send", str(good), "--url", url]) == mc.EXIT_REJECTED
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(_packet(version="v0.3")))
        assert mc.main(["send", str(bad), "--url", url]) == mc.EXIT_LOCAL_INVALID
    finally:
        server.shutdown()
        server.server_close()
    assert mc.main(["send", str(good), "--url", url, "--timeout", "2"]) == mc.EXIT_TRANSPORT


# ---------------------------------------------------------------------------
# Independence from ai_oriented_a2a and examples/
# ---------------------------------------------------------------------------

FORBIDDEN = {"ai_oriented_a2a", "examples", "network_dual_end", "minimal_dual_end"}


@pytest.mark.parametrize("path", sorted(SECOND_IMPL.glob("*.py")), ids=lambda p: p.name)
def test_second_impl_does_not_import_reference_code(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            assert name.split(".")[0] not in FORBIDDEN, f"{path.name} imports {name}"
    text = path.read_text(encoding="utf-8")
    assert "importlib" not in text and "__import__" not in text


def test_second_impl_runs_isolated_from_reference(tmp_path):
    """Copy second_impl/ and schema/ out of the repo and run it in a fresh
    interpreter where importing the reference code raises ImportError."""
    shutil.copytree(SECOND_IMPL, tmp_path / "second_impl", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SCHEMA_DIR, tmp_path / "schema")
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        for name in ("ai_oriented_a2a", "examples", "network_dual_end", "minimal_dual_end"):
            sys.modules[name] = None  # any import of these now raises ImportError
        sys.path[:] = [p for p in sys.path if p and "{repo}" not in p]
        sys.path.insert(0, "{root}")
        from second_impl import a2a_core as core, minimal_client as mc
        assert core.schema_dir().resolve() == (Path("{root}") / "schema").resolve()
        server, url = mc.start_in_thread(sign_key="k", replay_window=60)
        pkt = {{"id": "iso-1", "type": "other", "content": {{}}, "absorption": {{"method": "retrieval"}}}}
        reply = mc.send(url, pkt, sign_key="k", with_nonce=True)
        assert reply["status"] == "isolated", reply
        assert core.check_incoming({{"id": "x", "version": "v0.3"}})["reason"].startswith("unsupported_version")
        loaded = sorted(m for m in sys.modules if sys.modules[m] is not None)
        assert not [m for m in loaded if m.split(".")[0] in ("ai_oriented_a2a", "network_dual_end", "minimal_dual_end")]
        print("ISOLATED_OK")
        """
    ).format(root=str(tmp_path), repo=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ISOLATED_OK" in proc.stdout
