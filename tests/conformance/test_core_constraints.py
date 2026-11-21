"""SPEC §3 (v0.4) conformance cases: core constraints and the round cap.

Not part of the 12 HTTP cases copied from tests/interop/test_optional_fields.py.
Runs against every implementation of the ``start_receiver`` fixture
(reference, second_impl, and an external peer).

- A propose or counter whose ``target_constraint`` is one of the receiver's
  ``core_constraints`` gets HTTP 200 and a legal rejected Absorption Result
  whose reason starts with ``immutable_constraint:``.
- With the default cap of 3, the 4th message carrying the same
  ``negotiation_id`` is rejected with ``negotiation_limit:``. Messages
  without ``negotiation_id`` are never counted together, even across
  different ``proposal_id`` values.
- Order: ``content_policy:`` before ``negotiation_limit:`` before
  ``immutable_constraint:``.

Every id is fresh per test, so a long-running external peer
(``A2A_PEER_URL``) does not carry rounds from one test into another.
No ``dict |`` (Python 3.8).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
ENDPOINT = "/a2a/message"
CORE = "no parameter_delta"
OTHER = "web_search allowed"
DEFAULT_CAP = 3

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:  # the checks below still run; only the schema check is skipped
    Draft202012Validator = None


def _validators():
    if Draft202012Validator is None:
        return None, None
    resources = []
    for path in (REPO / "schema").glob("*.schema.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        if contents.get("$id"):
            resources.append((contents["$id"], resource))
    registry = Registry().with_resources(resources)

    def make(name):
        schema = json.loads((REPO / "schema" / name).read_text(encoding="utf-8"))
        return Draft202012Validator(schema, registry=registry)

    return make("absorption-result.schema.json"), make("negotiation.schema.json")


RESULT_V, NEG_V = _validators()


def _post(url, msg, timeout=10):
    if NEG_V is not None and "proposal_id" in msg:
        NEG_V.validate(msg)  # every message sent here is schema-valid
    req = urllib.request.Request(
        url.rstrip("/") + ENDPOINT,
        data=json.dumps(msg).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read().decode("utf-8"))
    if RESULT_V is not None and "packet_ref" in body:
        RESULT_V.validate(body)
    return status, body


def _fresh(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _offered():
    return {
        "id": _fresh("pkt-offer"),
        "type": "strategy_fragment",
        "content": {"compressed_signal": "apply exponential backoff on timeout"},
        "absorption": {"method": "retrieval", "target": "local_strategy_store"},
    }


def _propose(target, negotiation_id=None):
    msg = {
        "proposal_id": _fresh("prop"),
        "target_constraint": target,
        "proposed_change": "allow one extra retry",
        "offered_packet": _offered(),
    }
    if negotiation_id is not None:
        msg["negotiation_id"] = negotiation_id
    return msg


def _counter(negotiation_id=None, target=None):
    msg = {
        "proposal_id": _fresh("prop"),
        "status": "counter",
        "revised_change": "allow web_search with 15s timeout only",
    }
    if negotiation_id is not None:
        msg["negotiation_id"] = negotiation_id
    if target is not None:
        msg["target_constraint"] = target
    return msg


def _reason(body):
    return body.get("reason", "") if body.get("status") == "rejected" else ""


def _assert_rejected(status, body, prefix, packet_ref=None):
    assert status == 200, (status, body)
    assert body.get("status") == "rejected", body
    assert body["reason"].startswith(prefix), body["reason"]
    if packet_ref is not None:
        assert body["packet_ref"] == packet_ref


# --- core constraints -------------------------------------------------------


def test_propose_targeting_core_constraint_is_immutable(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    msg = _propose(CORE)
    status, body = _post(url, msg)
    _assert_rejected(status, body, "immutable_constraint:", msg["offered_packet"]["id"])


def test_counter_targeting_core_constraint_is_immutable(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    status, body = _post(url, _counter(target=CORE))
    _assert_rejected(status, body, "immutable_constraint:")


def test_other_target_is_not_immutable(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    status, body = _post(url, _propose(OTHER))
    assert status == 200, body
    assert not _reason(body).startswith("immutable_constraint:"), body


def test_core_constraint_match_is_exact(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    status, body = _post(url, _propose(CORE.upper()))
    assert status == 200, body
    assert not _reason(body).startswith("immutable_constraint:"), body


def test_profile_with_core_constraints_is_not_rejected(start_receiver):
    url = start_receiver()
    profile = json.loads((EXAMPLES / "agent-profile.json").read_text(encoding="utf-8"))
    profile["core_constraints"] = [CORE]
    status, body = _post(url, profile)
    assert status == 200, body
    assert body.get("status") != "rejected", body


# --- round cap ---------------------------------------------------------------


def test_fourth_round_of_one_negotiation_id_is_limited(start_receiver):
    url = start_receiver()
    nid = _fresh("neg")
    chain = [_propose(OTHER, nid)] + [_counter(nid) for _ in range(DEFAULT_CAP)]
    replies = [_post(url, msg) for msg in chain]
    for status, body in replies[:DEFAULT_CAP]:
        assert status == 200, body
        assert not _reason(body).startswith("negotiation_limit:"), body
    status, body = replies[DEFAULT_CAP]
    _assert_rejected(status, body, "negotiation_limit:")


def test_messages_without_negotiation_id_are_not_counted_together(start_receiver):
    url = start_receiver()
    for msg in [_propose(OTHER)] + [_counter() for _ in range(DEFAULT_CAP + 2)]:
        status, body = _post(url, msg)
        assert status == 200, body
        assert not _reason(body).startswith("negotiation_limit:"), body


def test_round_cap_is_per_negotiation_id(start_receiver):
    url = start_receiver()
    first = _fresh("neg")
    for _ in range(DEFAULT_CAP):
        _post(url, _counter(first))
    status, body = _post(url, _propose(OTHER, _fresh("neg")))
    assert status == 200, body
    assert not _reason(body).startswith("negotiation_limit:"), body


# --- precedence --------------------------------------------------------------


def test_content_policy_comes_before_immutable_constraint(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    pkt = json.loads(
        (REPO / "datasets" / "content_policy" / "packets" / "pkt-synth-inject-001.json").read_text(
            encoding="utf-8"
        )
    )
    msg = _propose(CORE)
    msg["offered_packet"] = pkt
    status, body = _post(url, msg)
    _assert_rejected(status, body, "content_policy:", pkt["id"])


def test_negotiation_limit_comes_before_immutable_constraint(start_receiver):
    url = start_receiver(core_constraints=[CORE])
    nid = _fresh("neg")
    for _ in range(DEFAULT_CAP):
        _post(url, _counter(nid))
    status, body = _post(url, _propose(CORE, nid))
    _assert_rejected(status, body, "negotiation_limit:")


@pytest.mark.parametrize("version", ["0.3", "0.4"])
def test_immutable_constraint_applies_to_both_wire_versions(start_receiver, version):
    url = start_receiver(core_constraints=[CORE])
    msg = _propose(CORE)
    msg["version"] = version
    status, body = _post(url, msg)
    _assert_rejected(status, body, "immutable_constraint:")
